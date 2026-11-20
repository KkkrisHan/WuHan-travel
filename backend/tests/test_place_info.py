import json
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.models import Place, Point
from app import place_info


class FakeAsyncClient:
    def __init__(self, results, fetched):
        self.results = results
        self.fetched = fetched
        self.search_calls = []
        self.fetch_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, headers=None, json=None):
        request = httpx.Request("POST", url)
        if url == place_info.SEARCH_ENDPOINT:
            self.search_calls.append(json)
            return httpx.Response(200, json={"results": self.results}, request=request)
        if url == place_info.FETCH_ENDPOINT:
            self.fetch_calls.append(json["url"])
            item = self.fetched.get(json["url"])
            if item is None:
                return httpx.Response(404, json={"error": {"message": "not found"}}, request=request)
            return httpx.Response(200, json=item, request=request)
        raise AssertionError(f"Unexpected endpoint: {url}")


class PlaceInfoTest(unittest.IsolatedAsyncioTestCase):
    async def test_robots_disallow_skips_page_fetch(self):
        place_info._ROBOTS_ALLOWED.clear()
        async def handle(request):
            return httpx.Response(200, text="User-agent: *\nDisallow: /private")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            allowed = await place_info._robots_allows(client, "https://example.org/private/hours")
            public = await place_info._robots_allows(client, "https://example.org/public/hours")
        self.assertFalse(allowed)
        self.assertTrue(public)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / "place-info.sqlite3")
        self.settings_patch = patch.object(place_info.settings, "task_database", self.db)
        self.settings_patch.start()
        self.key_patch = patch.object(place_info.settings, "deepseek_api_key", "mock-key")
        self.key_patch.start()
        self.place = Place(
            placeId="mock-venue-1",
            name="测试景点",
            category="景点",
            address="武汉市",
            location=Point(lng=114.3, lat=30.5),
            officialUrl="https://venue.example.cn/",
            description="测试地点简介",
        )

    def tearDown(self):
        place_info._PLACE_LOCKS.clear()
        self.key_patch.stop()
        self.settings_patch.stop()
        self.temp.cleanup()

    async def test_research_total_timeout_returns_explicit_failure(self):
        async def never_search(*_args):
            await asyncio.sleep(1)
            return []

        with patch.object(place_info, "PLACE_INFO_TIMEOUT", 0.05), patch.object(
            place_info, "_search_one", side_effect=never_search
        ), patch("app.place_info.httpx.AsyncClient", return_value=FakeAsyncClient([], {})):
            result = await asyncio.wait_for(place_info.get_place_info(self.place), timeout=0.5)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("超时", result["error"])

    async def test_chat_web_timeout_returns_without_blocking_chat(self):
        async def never_finish(*_args):
            await asyncio.sleep(1)
            return []

        with patch.object(place_info, "CHAT_WEB_TIMEOUT", 0.03), patch.object(
            place_info, "_search_chat_web", side_effect=never_finish
        ):
            result = await asyncio.wait_for(place_info.search_chat_web("黄鹤楼"), timeout=0.5)
        self.assertEqual(result, [])

    async def test_search_fetch_evidence_conflict_and_cache(self):
        official_url = "https://venue.example.cn/info"
        media_url = "https://news.example.com/test-venue"
        results = [
            {
                "title": "测试景点官方网站",
                "url": official_url,
                "summary": "测试景点常规开放时间为每日 09:00 至 17:00。",
                "snippet": "测试景点开放信息",
                "content": "",
                "date": "2026-11-18T00:00:00+00:00",
                "authority": 4,
                "rerank_score": 2.8,
            },
            {
                "title": "测试景点游览报道",
                "url": media_url,
                "summary": "报道提到测试景点的开放时间可能为 08:30 至 17:30。",
                "snippet": "测试景点游览",
                "content": "",
                "date": "2026-11-17T00:00:00+00:00",
                "authority": 3,
                "rerank_score": 2.1,
            },
        ]
        fetched = {
            official_url: {
                "title": "测试景点官方开放信息",
                "content": "# 测试景点\n\n测试景点位于武汉市区，是当地适合游客了解城市文化与周边环境的游览地点。页面介绍了场地的基本情况、参观注意事项以及服务安排。常规开放时间为每日 09:00 至 17:00。建议出发前查看景区发布的最新通知。",
            }
        }
        fake_client = FakeAsyncClient(results, fetched)
        model_json = {
            "introduction": "测试景点位于武汉。这里的建筑和环境形成了独特的游览体验，游客可以结合现场展陈与周边街区理解它的文化背景，并留意空间布局和细节。",
            "fields": {
                "openingHours": {
                    "value": "每日 09:00 至 17:00",
                    "evidence": "常规开放时间为每日 09:00 至 17:00。",
                    "sourceId": 1,
                }
            },
            "conflicts": [
                {
                    "field": "openingHours",
                    "sourceIds": [1, 2],
                    "description": "官方资料与媒体报道给出的时间不同",
                    "evidence": [
                        {"sourceId": 1, "quote": "常规开放时间为每日 09:00 至 17:00。"},
                        {"sourceId": 2, "quote": "开放时间可能为 08:30 至 17:30。"},
                    ],
                }
            ],
        }
        with patch("app.place_info.httpx.AsyncClient", return_value=fake_client), patch(
            "app.place_info._model", new=AsyncMock(return_value=json.dumps(model_json, ensure_ascii=False))
        ):
            result = await place_info.get_place_info(self.place)

        self.assertEqual(len(fake_client.search_calls), 6)
        self.assertTrue(all(call["need_content"] is False for call in fake_client.search_calls))
        self.assertGreaterEqual(len(fake_client.fetch_calls), 2)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["fields"]["openingHours"]["sourceId"], 1)
        self.assertEqual(result["sources"][0]["fetchStatus"], "fetched")
        self.assertEqual(result["sources"][1]["fetchStatus"], "failed")
        self.assertIn("官方资料", result["conflict"])

        with patch("app.place_info.httpx.AsyncClient", side_effect=AssertionError("cache should avoid API calls")):
            cached = await place_info.get_place_info(self.place)
        self.assertTrue(cached["cached"])

    async def test_failed_refresh_preserves_previous_data_as_stale(self):
        old_payload = {
            "status": "partial",
            "checkedAt": place_info._now().isoformat(),
            "fields": {"adultPrice": {"value": "示例价格", "sourceId": 1, "evidence": "示例证据"}},
            "sources": [],
            "introduction": "旧介绍",
            "conflict": "",
            "stale": False,
            "cached": False,
        }
        from contextlib import closing
        with closing(place_info._cache_connection()) as con:
            con.execute(
                "INSERT INTO place_info_cache VALUES (?,?,?)",
                (f"{place_info.PLACE_INFO_CACHE_VERSION}:{self.place.placeId}:{self.place.name}", json.dumps(old_payload, ensure_ascii=False), old_payload["checkedAt"]),
            )
            con.commit()
        fake_client = FakeAsyncClient([], {})
        with patch("app.place_info.httpx.AsyncClient", return_value=fake_client):
            result = await place_info.get_place_info(self.place, refresh=True)
        self.assertTrue(result["stale"])
        self.assertEqual(result["introduction"], "旧介绍")
        self.assertEqual(result["fields"]["adultPrice"]["value"], "示例价格")

    async def test_four_acceptance_place_categories_with_mocked_apis(self):
        cases = [
            ("黄鹤楼", "yellow-crane-tower"),
            ("湖北省博物馆", "hubei-museum"),
            ("东湖", "east-lake"),
            ("武汉欢乐谷", "happy-valley"),
        ]
        model_json = {
            "introduction": "这是一个适合游客了解武汉城市文化与游览环境的地点。参观时可以留意场地布局、展陈或自然景观的细节，并结合周边街区理解它与城市生活的联系。建议出发前查看官方最新公告。",
            "fields": {
                "openingHours": {
                    "value": "每日 09:00 至 17:00",
                    "evidence": "常规开放时间为每日 09:00 至 17:00。",
                    "sourceId": 1,
                }
            },
            "conflicts": [],
        }
        for index, (name, slug) in enumerate(cases):
            with self.subTest(place=name):
                place = Place(
                    placeId=f"acceptance-{index}",
                    name=name,
                    category="景点",
                    address="武汉市",
                    location=Point(lng=114.3, lat=30.5),
                    officialUrl=f"https://{slug}.example.cn/",
                )
                url = f"https://{slug}.example.cn/info"
                result_rows = [{
                    "title": f"{name} 官方资料",
                    "url": url,
                    "summary": f"{name}相关介绍。常规开放时间为每日 09:00 至 17:00。",
                    "snippet": f"{name}开放信息",
                    "content": "",
                    "date": "2026-11-18T00:00:00+00:00",
                    "authority": 4,
                    "rerank_score": 2.8,
                }]
                page = {
                    "title": f"{name}开放信息",
                    "content": f"{name}是武汉的代表性游览地点。页面提供基本介绍、参观服务与游客提示。这里还介绍了景点特色、周边环境与游客服务，适合行前了解基本信息。常规开放时间为每日 09:00 至 17:00。出发前请留意景区最新公告。",
                }
                fake_client = FakeAsyncClient(result_rows, {url: page})
                with patch("app.place_info.httpx.AsyncClient", return_value=fake_client), patch(
                    "app.place_info._model",
                    new=AsyncMock(return_value=json.dumps(model_json, ensure_ascii=False)),
                ):
                    result = await place_info.get_place_info(place, refresh=True)
                self.assertEqual(result["status"], "partial")
                self.assertIn("openingHours", result["fields"])
                self.assertEqual(result["sources"][0]["fetchStatus"], "fetched")
                self.assertEqual(len(fake_client.search_calls), 6)

    async def test_concurrent_requests_for_same_place_share_one_search(self):
        url = "https://venue.example.cn/info"
        results = [{
            "title": "测试景点资料",
            "url": url,
            "summary": "测试景点介绍与开放时间信息。",
            "snippet": "测试景点",
            "content": "",
            "date": "2026-11-18T00:00:00+00:00",
            "authority": 4,
            "rerank_score": 2.8,
        }]
        page = {
            "title": "测试景点资料",
            "content": "测试景点位于武汉，是当地游客可以了解城市环境的地点。网页介绍了景点服务、周边空间和参观注意事项。常规开放时间为每日 09:00 至 17:00。",
        }
        fake_client = FakeAsyncClient(results, {url: page})
        model_json = {
            "introduction": "测试景点位于武汉，是一个适合游客了解城市环境与文化的地点。游览时可以留意现场空间、服务设施和周边街区的联系，并结合个人兴趣安排停留时间。",
            "fields": {},
            "conflicts": [],
        }
        with patch("app.place_info.httpx.AsyncClient", return_value=fake_client) as client_factory, patch(
            "app.place_info._model",
            new=AsyncMock(return_value=json.dumps(model_json, ensure_ascii=False)),
        ):
            first, second = await __import__("asyncio").gather(
                place_info.get_place_info(self.place),
                place_info.get_place_info(self.place),
            )
        self.assertEqual(client_factory.call_count, 1)
        self.assertEqual(len(fake_client.search_calls), 6)
        self.assertEqual(sorted([first["cached"], second["cached"]]), [False, True])

    async def test_second_pass_recovers_dynamic_fields(self):
        source = place_info._source(self.place, {
            "title": "测试景点官方票务",
            "url": "https://venue.example.cn/tickets",
            "summary": "测试景点成人门票 60 元。开放时间 09:00 至 17:00。",
            "snippet": "测试景点",
            "content": "测试景点成人门票 60 元。开放时间 09:00 至 17:00。",
            "authority": 4,
            "date": place_info._now().date().isoformat(),
        }, place_info._now().isoformat())
        narrative = {"introduction": "测试景点是武汉一处具有丰富城市文化体验的地点。游客可以留意周边景观、场地布局和服务设施，结合武汉城市历史了解它的特色。这里也适合慢慢观察公共空间与城市生活的联系，出发前应确认官方的最新通知，结合现场情况灵活调整参观顺序。", "fields": {}, "conflicts": []}
        extraction = {"fields": {
            "adultPrice": {"value": "60 元", "evidence": "测试景点成人门票 60 元。", "sourceId": 1},
            "openingHours": {"value": "09:00 至 17:00", "evidence": "开放时间 09:00 至 17:00。", "sourceId": 1},
        }}
        with patch("app.place_info._model", new=AsyncMock(side_effect=[
            json.dumps(narrative, ensure_ascii=False),
            json.dumps(extraction, ensure_ascii=False),
        ])) as model:
            result = await place_info._generate(self.place, [source], place_info._now().isoformat())
        self.assertEqual(result["fields"]["adultPrice"]["value"], "60 元")
        self.assertIn("openingHours", result["fields"])
        self.assertEqual(model.await_count, 2)

    def test_targeted_search_queries_and_place_relevance_filter(self):
        queries = place_info._search_queries(self.place)
        self.assertEqual(len(queries), 6)
        self.assertTrue(any("成人票价" in query for query in queries))
        self.assertTrue(any("开放时间" in query for query in queries))
        self.assertTrue(any("预约入口" in query for query in queries))
        self.assertTrue(any("site:wuhan.gov.cn" in query for query in queries))
        unrelated = {
            "title": "武汉市国民经济运行情况",
            "url": "https://www.wuhan.gov.cn/economy",
            "summary": "武汉市政府发布经济数据，文中提及测试景点。",
            "snippet": "测试景点",
            "content": "武汉市工业生产和服务业运行情况。",
        }
        relevant = {
            **unrelated,
            "title": "测试景点门票政策",
            "content": "测试景点成人票价及开放时间说明。",
        }
        self.assertFalse(place_info._matches_place(self.place, unrelated))
        self.assertTrue(place_info._matches_place(self.place, relevant))

    def test_source_url_and_evidence_validation(self):
        self.assertIsNone(place_info._safe_url("file:///etc/passwd"))
        self.assertIsNone(place_info._safe_url("http://127.0.0.1/private"))
        self.assertIsNotNone(place_info._safe_url("https://example.com/page"))
        sources = [{
            "content": "开放时间为每日 09:00 至 17:00。",
            "summary": "",
            "snippet": "",
            "official": True,
            "publishedAt": None,
        }]
        fields = place_info._validate_fields({
            "openingHours": {
                "value": "每日 09:00 至 17:00",
                "evidence": "开放时间为每日 09:00 至 17:00。",
                "sourceId": 1,
            },
            "adultPrice": {
                "value": "成人票 80 元",
                "evidence": "开放时间为每日 09:00 至 17:00。",
                "sourceId": 1,
            },
        }, sources, place_info._now().isoformat())
        self.assertIn("openingHours", fields)
        self.assertNotIn("adultPrice", fields)
        old_source = [{**sources[0], "publishedAt": "2009-08-03T16:00:00+00:00"}]
        old_fields = place_info._validate_fields({
            "openingHours": {
                "value": "每日 09:00 至 17:00",
                "evidence": "开放时间为每日 09:00 至 17:00。",
                "sourceId": 1,
            }
        }, old_source, place_info._now().isoformat())
        self.assertNotIn("openingHours", old_fields)

        recent = place_info._now().isoformat()
        third_party_sources = [
            {
                "url": "https://travel-a.example.com/ticket",
                "content": "测试景点成人票价为 70 元。",
                "summary": "", "snippet": "", "official": False, "publishedAt": recent,
            },
            {
                "url": "https://travel-b.example.net/guide",
                "content": "测试景点成人票价为 70 元。",
                "summary": "", "snippet": "", "official": False, "publishedAt": recent,
            },
        ]
        single_source = place_info._validate_fields({
            "adultPrice": {
                "value": "70 元", "evidence": "测试景点成人票价为 70 元。", "sourceId": 1,
            }
        }, third_party_sources, recent)
        self.assertNotIn("adultPrice", single_source)
        corroborated = place_info._validate_fields({
            "adultPrice": {
                "value": "70 元",
                "evidence": "测试景点成人票价为 70 元。",
                "sourceId": 1,
                "corroboratingEvidence": [
                    {"sourceId": 2, "quote": "测试景点成人票价为 70 元。"}
                ],
            }
        }, third_party_sources, recent)
        self.assertIn("adultPrice", corroborated)
        self.assertEqual(corroborated["adultPrice"]["corroboratingSourceIds"], [2])


if __name__ == "__main__":
    unittest.main()
