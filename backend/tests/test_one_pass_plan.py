"""The production path must never repair or re-plan an AI decision."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.itinerary_doc import _planning_pool, build_itinerary
from app.models import Place, Point
from app.one_pass_plan import decide_days_once
from app.trip_intent import TripIntent


def poi(pid: str, name: str, index: int, category: str = "历史地标") -> Place:
    return Place(placeId=pid, name=name, category=category, address="武汉",
                 location=Point(lng=114.28 + index * 0.012, lat=30.55),
                 source="高德地图")


def trip(days=3):
    return TripIntent(days=days, people=3, company="朋友、长辈",
                      pace="舒缓", budget="适中", interests=["历史文化", "美食"],
                      requirements="不要打车；每天安排午餐；晚上19:00前返回",
                      modes=("walking", "transit"), must_visit=["黄鹤楼"],
                      start_date="2026-11-22", lodging="测试酒店")


class OnePassDecisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_unknown_museum_is_replaced_before_model_decision(self):
        required = poi("r", "必去地点", 0)
        unknown = poi("u", "黎黄陂路街头博物馆", 1, "科教文化服务")
        street = poi("s", "黎黄陂路", 2, "街区")
        restaurant = poi("f", "餐厅", 3, "餐饮服务")
        pool = _planning_pool([required, unknown, street, restaurant], [required], {}, "", 1)
        self.assertEqual([place.placeId for place in pool], ["r", "s", "f"])

    async def test_verified_closed_date_is_explicit_model_constraint(self):
        required = poi("y", "黄鹤楼", 0)
        model = AsyncMock(return_value=json.dumps({"days": [
            [{"placeId": "y", "experience": "登楼"}],
            [{"placeId": "a", "experience": "漫步"}],
            [{"placeId": "b", "experience": "漫步"}],
        ]}))
        with patch("app.one_pass_plan.get_opening_sessions", side_effect=lambda pid, when: {
                "status": "closed" if pid == "y" and when.isoformat() == "2026-11-23" else "unknown"}):
            await decide_days_once(trip(), [required], [required, poi("a", "老街", 1),
                                                       poi("b", "江滩", 2)], {}, None, [], model)
        payload = json.loads(model.await_args.args[1])
        self.assertEqual(payload["candidates"][0]["closedDates"], ["2026-11-23"])

    async def test_single_model_call_receives_input_openings_and_route_hints(self):
        places = [poi("y", "黄鹤楼", 0), poi("a", "老街", 1),
                  poi("b", "美食餐馆", 2, "餐饮服务")]
        model = AsyncMock(return_value=json.dumps({
            "days": [[{"placeId": "a", "experience": "漫步老街"}],
                     [{"placeId": "y", "experience": "登楼观景"}],
                     [{"placeId": "b", "experience": "品尝美食"}]],
            "priorities": ["照顾同行长辈"], "dayThemes": ["老街", "古迹", "美食"],
        }, ensure_ascii=False))
        t = trip()
        hotel = Point(lng=114.28, lat=30.55)
        groups, details = await decide_days_once(
            t, [places[0]], places, {"y": "08:00-17:00"}, hotel,
            [{"from": "住宿", "to": "黄鹤楼", "minutes": 20}], model)
        self.assertEqual(model.await_count, 1)
        payload = json.loads(model.await_args.args[1])
        self.assertEqual(payload["preferences"]["company"], "朋友、长辈")
        self.assertIn("晚上19:00前返回", payload["preferences"]["requirements"])
        self.assertEqual(payload["candidates"][0]["openingHours"], "08:00-17:00")
        self.assertEqual(payload["knownTravel"][0]["minutes"], 20)
        self.assertEqual([[p.placeId for p in d] for d in groups],
                         [["a"], ["y"], ["b"]])
        self.assertEqual(t.day_themes, ["老街", "古迹", "美食"])
        self.assertEqual(details["y"]["experience"], "登楼观景")

    async def test_missing_required_is_rejected_not_inserted(self):
        required = poi("y", "黄鹤楼", 0)
        other = poi("a", "老街", 1)
        model = AsyncMock(return_value='{"days":[[{"placeId":"a","experience":"散步"}]]}')
        with self.assertRaises(HTTPException) as ctx:
            await decide_days_once(trip(1), [required], [required, other],
                                   {}, None, [], model)
        self.assertIn("遗漏必去地点", ctx.exception.detail)
        self.assertEqual(model.await_count, 1)

    async def test_invalid_output_does_not_get_retried_or_filled(self):
        required = poi("y", "黄鹤楼", 0)
        model = AsyncMock(return_value="not JSON")
        with self.assertRaises(HTTPException) as ctx:
            await decide_days_once(trip(1), [required], [required], {}, None, [], model)
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(model.await_count, 1)

    async def test_duplicate_and_empty_days_are_rejected(self):
        places = [poi("y", "黄鹤楼", 0), poi("a", "老街", 1)]
        for output in (
            {"days": [[{"placeId": "y", "experience": "看江"}],
                       [{"placeId": "y", "experience": "看江"}],
                       [{"placeId": "a", "experience": "散步"}]]},
            {"days": [[{"placeId": "y", "experience": "看江"}], [],
                       [{"placeId": "a", "experience": "散步"}]]},
        ):
            with self.subTest(output=output):
                with self.assertRaises(HTTPException):
                    await decide_days_once(trip(), places[:1], places, {}, None, [],
                                           AsyncMock(return_value=json.dumps(output)))

    async def test_end_to_end_preserves_exact_model_order_without_review(self):
        places = [poi("y", "黄鹤楼", 0), poi("a", "老街", 1),
                  poi("b", "滨江路", 2), poi("c", "咖啡餐厅", 3, "餐饮服务"),
                  poi("d", "公园", 4), poi("e", "建筑群", 5)]
        itinerary = {
            "days": [
                [{"placeId": "a", "experience": "老街漫步"},
                 {"placeId": "y", "experience": "登黄鹤楼"}],
                [{"placeId": "c", "experience": "在餐厅吃饭", "timeWindow": "any"},
                 {"placeId": "b", "experience": "江岸漫步"}],
                [{"placeId": "e", "experience": "建筑欣赏"},
                 {"placeId": "d", "experience": "游览公园"}],
            ], "priorities": ["轻松游览"], "dayThemes": ["老街与古迹", "美食与滨江", "建筑与公园"]
        }
        model = AsyncMock(return_value=json.dumps(itinerary, ensure_ascii=False))
        request = SimpleNamespace(
            mustVisitPlaces=places[:1], people=3, days=3, company="朋友、长辈",
            pace="舒缓", budget="适中", interests=["历史文化", "美食"],
            requirements="步行和公交", startDate="2026-11-22",
            transport="步行与公共交通", lodging="", lodgingLocation=None
        )
        route = AsyncMock(return_value={
            "minutes": 8, "distanceMeters": 500,
            "walkingMinutes": 5, "source": "高德地图路线规划", "polyline": []
        })
        # Spy on the actual calendar stage. Exactly one schedule per day is allowed.
        from app.itinerary_doc import schedule_day as actual_schedule
        called_days = []
        async def schedule_spy(*args, **kwargs):
            called_days.append(args[0])
            self.assertTrue(kwargs["exact_order"])
            self.assertFalse(kwargs["_soft"])
            return await actual_schedule(*args, **kwargs)

        with (
            patch("app.itinerary_doc.settings.amap_web_service_key", "test"),
            patch("app.itinerary_doc.verify_required_place", new=AsyncMock(side_effect=lambda p: p)),
            patch("app.itinerary_doc.search_places",
                  new=AsyncMock(return_value=(places[1:], "高德地图"))),
            patch("app.itinerary_doc.collect_opening_hours", new=AsyncMock(return_value={})),
            patch("app.itinerary_doc._model", new=model),
            patch("app.itinerary_doc.schedule_day", side_effect=schedule_spy),
            patch("app.trip_intent.calculate_route", new=route),
        ):
            result = await build_itinerary(request)
        self.assertEqual(model.await_count, 1)
        self.assertEqual(sorted(called_days), [0, 1, 2])
        self.assertEqual(
            [[stop["place"]["placeId"] for stop in day["stops"]]
             for day in result["dayPlans"]],
            [["a", "y"], ["c", "b"], ["e", "d"]]
        )
        self.assertEqual(result["strategy"]["reviewIssues"], [])
        self.assertEqual(result["metrics"]["requiredCoverage"], 1.0)


if __name__ == "__main__":
    unittest.main()
