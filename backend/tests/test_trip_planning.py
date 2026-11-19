import asyncio
import json
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.experience_plan import select_days, strategy
from app.group_routes import _cost, improve_groups, include_food_if_requested, relieve_overloaded_groups, spread_required_load
from app.models import Place, Point
from app.places import calculate_route
from app.trip_intent import Router, TripIntent, allowed_modes
from app.trip_schedule import experience_at_time, schedule_day, validate


def poi(identifier, name, lng, lat, category="风景名胜"):
    return Place(placeId=identifier, name=name, location=Point(lng=lng, lat=lat), category=category)


def intent(**changes):
    values = dict(days=1, people=2, company="", pace="舒缓", budget="适中", interests=["自然风光"],
                  requirements="", modes=("walking", "transit"), must_visit=[], start_date="2026-11-22", lodging="")
    values.update(changes)
    return TripIntent(**values)


class PlanningTests(unittest.IsolatedAsyncioTestCase):
    def test_geographic_repair_keeps_all_places_and_reduces_repeated_cross_city_travel(self):
        hotel = Point(lng=114.28, lat=30.58)
        west = [poi("w1", "西岸一", 114.281, 30.58), poi("w2", "西岸二", 114.285, 30.58)]
        east = [poi("e1", "东岸一", 114.37, 30.55), poi("e2", "东岸二", 114.38, 30.55)]
        repaired = improve_groups([[west[0], east[0]], [west[1], east[1]]], hotel, 3)
        self.assertEqual({p.placeId for day in repaired for p in day}, {"w1", "w2", "e1", "e2"})
        self.assertTrue(any({"e1", "e2"}.issubset({p.placeId for p in day}) for day in repaired))

    def test_overloaded_geographic_day_preserves_required_stops(self):
        hotel = Point(lng=114.30, lat=30.58)
        east = poi("east", "东边必去", 114.40, 30.58)
        west = poi("west", "西边必去", 114.25, 30.58)
        nearby = [poi("a", "近处甲", 114.252, 30.58),
                  poi("b", "近处乙", 114.255, 30.58),
                  poi("c", "近处必去", 114.26, 30.58)]
        local = [poi(str(i), f"其他{i}", 114.30 + i * .002, 30.58) for i in range(3)]
        groups = [[east, west], nearby, local]
        protected = {"east", "west", "c"}
        repaired = relieve_overloaded_groups(groups, hotel, protected, [0], 3)
        self.assertNotEqual(groups, repaired)
        self.assertTrue(protected.issubset({p.placeId for g in repaired for p in g}))
        self.assertTrue(all(1 <= len(g) <= 3 for g in repaired))
        self.assertLess(max(_cost(g, hotel) for g in repaired), max(_cost(g, hotel) for g in groups))

    def test_explicit_food_interest_reaches_a_real_meal_stop(self):
        park = poi("park", "公园", 114.30, 30.55)
        restaurant = poi("meal", "餐厅", 114.301, 30.55, "餐饮服务")
        details = {}
        groups = include_food_if_requested([[park]], [park, restaurant], set(), None, ["美食"], details)
        self.assertEqual(groups[0][0].placeId, "meal")
        self.assertIn("一餐", details["meal"]["experience"])

    def test_mandatory_stops_are_spread_when_one_day_is_overloaded(self):
        required = [poi(str(i), f"必去{i}", 114.30 + i * .01, 30.55) for i in range(3)]
        optional = [poi("a", "可选甲", 114.29, 30.58), poi("b", "可选乙", 114.28, 30.58)]
        groups = spread_required_load([required[:], [optional[0]], [optional[1]]],
                                      {p.placeId for p in required}, Point(lng=114.29, lat=30.58), 3)
        self.assertEqual(sorted(sum(p.placeId in {r.placeId for r in required} for p in day) for day in groups), [0, 1, 2])

    def test_transport_parser_handles_mixed_modes_and_exclusions(self):
        self.assertEqual(allowed_modes("步行与公共交通"), ("walking", "transit"))
        self.assertEqual(allowed_modes("步行、地铁、打车", "不打车"), ("walking", "transit"))
        self.assertEqual(allowed_modes("打车 / 驾车"), ("driving",))

    def test_descriptions_do_not_claim_night_activities_in_morning(self):
        self.assertEqual(experience_at_time("夜游老街，欣赏灯光", 11 * 60), "老街，欣赏")

    def test_experience_does_not_add_an_unscheduled_cafe_or_following_stop(self):
        original = "在巴公房子红砖老建筑前漫步拍照，感受老汉口街区的浪漫氛围，随后在楼内咖啡店小坐"
        rendered = experience_at_time(original, 10 * 60 + 9, other_places=("兰陵路17号Cafe&Bar",))
        self.assertIn("红砖老建筑前漫步拍照", rendered)
        self.assertNotIn("随后", rendered)
        self.assertNotIn("咖啡店", rendered)
        self.assertEqual(experience_at_time("登黄鹤楼赏江城暮色与白鸟", 13 * 60 + 51),
                         "登黄鹤楼赏江城景色与白鸟")
        self.assertEqual(experience_at_time(
            "黄鹤楼参观，之后前往巴公房子", 14 * 60, other_places=("巴公房子",)), "黄鹤楼参观")
        self.assertEqual(experience_at_time("上午参观2小时，随后前往另一景点", 10 * 60),
                         "在此处游览并体验当地特色")

    async def test_markdown_table_contains_lunch_in_order_and_closes_before_return(self):
        first = poi("first", "巴公房子", 114.30, 30.55)
        second = poi("second", "黄鹤楼", 114.31, 30.55)
        hotel = Point(lng=114.29, lat=30.55)
        async def route(*_):
            return {"minutes": 5, "distanceMeters": 350,
                    "source": "高德地图路线规划", "polyline": []}
        details = {
            "first": {"experience": "在老楼拍照，随后在咖啡店小坐"},
            "second": {"timeWindow": "afternoon", "experience": "参观黄鹤楼"},
        }
        with patch("app.trip_intent.calculate_route", side_effect=route):
            day, lines, _ = await schedule_day(0, [first, second],
                intent(lodging="测试酒店"), details, {}, hotel,
                Router(("walking",), "适中"), date(2026, 11, 22))
        text = "\n".join(lines)
        table = [line for line in lines if line.startswith("| ")]
        self.assertEqual(len(table), 5)  # header, separator, 2 stops, 1 lunch
        self.assertTrue(any("午餐与休息" in line for line in table))
        lunch_index = next(i for i, line in enumerate(lines) if "午餐与休息" in line)
        self.assertLess(lines.index(next(line for line in table if "巴公房子" in line)), lunch_index)
        self.assertLess(lunch_index, lines.index(next(line for line in table if "黄鹤楼" in line)))
        self.assertNotIn("咖啡店小坐", text)
        return_index = next(i for i, line in enumerate(lines) if line.startswith("返回测试酒店"))
        self.assertEqual(lines[return_index - 1], "")
        self.assertEqual(lines[return_index + 1], "")
        self.assertIn(day.meals[0].split()[1], text)
        self.assertFalse(validate([day], [], ("walking",), text))

    async def test_restaurant_is_scheduled_as_a_meal(self):
        restaurant = poi("meal", "餐厅", 114.30, 30.55, "餐饮服务")
        day, markdown, _ = await schedule_day(0, [restaurant], intent(), {}, {}, None,
                                       Router(("walking",), "适中"), date(2026, 11, 22))
        self.assertGreaterEqual(day.stops[0].arrival, "11:30")
        self.assertIn("午餐", day.meals[0])
        self.assertIn("餐厅", day.meals[0])
        self.assertNotIn("午餐与休息：", "\n".join(markdown))

    async def test_restaurant_as_second_stop_does_not_add_another_lunch(self):
        park = poi("park", "公园", 114.30, 30.55)
        restaurant = poi("meal", "餐厅", 114.301, 30.55, "餐饮服务")
        async def route(*_):
            return {"minutes": 5, "distanceMeters": 350, "source": "高德地图路线规划", "polyline": []}
        with patch("app.trip_intent.calculate_route", side_effect=route):
            day, markdown, _ = await schedule_day(0, [park, restaurant], intent(),
                {"park": {"minutes": 210, "timeWindow": "morning"}}, {}, None,
                Router(("walking",), "适中"), date(2026, 11, 22))
        self.assertEqual(day.stops[1].place.placeId, "meal")
        self.assertEqual(day.stops[1].arrival, "12:53")
        self.assertEqual(day.sightseeingMinutes, day.stops[0].minutes)
        self.assertEqual(day.transportMinutes, 5)
        self.assertIn("已计入地点停留", day.meals[0])
        self.assertNotIn("午餐与休息：", "\n".join(markdown))

    async def test_evening_restaurant_keeps_a_separate_lunch(self):
        restaurant = poi("dinner", "晚餐餐厅", 114.30, 30.55, "餐饮服务")
        day, markdown, _ = await schedule_day(0, [restaurant], intent(),
            {"dinner": {"timeWindow": "evening"}}, {}, None,
            Router(("walking",), "适中"), date(2026, 11, 22))
        self.assertGreaterEqual(day.stops[0].arrival, "17:00")
        self.assertEqual(sum("午餐与休息：" in line for line in markdown), 1)
        self.assertIn("12:00–13:00", day.meals[0])

    async def test_day_load_and_unverified_evening_entry_are_rejected(self):
        stops = [poi(str(i), f"地点{i}", 114.30 + i * .01, 30.55) for i in range(3)]
        async def slow(_, __, ___):
            return {"minutes": 120, "distanceMeters": 1000, "source": "高德地图路线规划", "polyline": []}
        with patch("app.trip_intent.calculate_route", side_effect=slow):
            with self.assertRaises(HTTPException):
                await schedule_day(0, stops, intent(), {}, {}, None,
                                   Router(("transit",), "适中"), date(2026, 11, 22))
        gated = poi("museum", "展馆", 114.30, 30.55, "科教文化服务")
        with self.assertRaises(HTTPException):
            await schedule_day(0, [gated], intent(requirements="展馆晚上要去"), {}, {}, None,
                               Router(("walking",), "适中"), date(2026, 11, 22))

    async def test_long_walk_uses_real_transit_and_unavailable_route_fails(self):
        start, end = Point(lng=114.30, lat=30.54), Point(lng=114.37, lat=30.58)
        async def route(_, __, mode):
            return {"minutes": 194 if mode == "walking" else 42, "distanceMeters": 8000,
                    "source": "高德地图路线规划", "polyline": []}
        with patch("app.trip_intent.calculate_route", side_effect=route):
            mode, result = await Router(("walking", "transit"), "适中").choose(start, end)
        self.assertEqual((mode, result["minutes"]), ("transit", 42))
        with patch("app.trip_intent.calculate_route", new=AsyncMock(return_value={
            "minutes": 40, "distanceMeters": 8000, "source": "公交距离估算（高德请求失败）", "polyline": []})):
            with self.assertRaises(HTTPException):
                await Router(("transit",), "适中").choose(start, end)

    async def test_route_requests_are_prefetched_concurrently_with_a_bound(self):
        hotel = Point(lng=114.29, lat=30.55)
        first = poi("a", "地点甲", 114.30, 30.55)
        second = poi("b", "地点乙", 114.31, 30.55)
        active = 0
        peak = 0
        async def route(*_):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {"minutes": 5, "distanceMeters": 500, "source": "高德地图路线规划", "polyline": []}
        with patch("app.trip_intent.calculate_route", side_effect=route):
            await schedule_day(0, [first, second], intent(), {}, {}, hotel,
                               Router(("walking",), "适中"), date(2026, 11, 22))
        self.assertGreaterEqual(peak, 2)
        self.assertLessEqual(peak, 3)

    async def test_transit_alternatives_account_for_walking_and_transfers(self):
        data = {"status": "1", "route": {"transits": [
            {"cost": {"duration": "2700"}, "segments": [
                {"walking": {"distance": "1600"}, "bus": {"buslines": [{}]}},
                {"bus": {"buslines": [{}]}}]},
            {"cost": {"duration": "3000"}, "segments": [
                {"walking": {"distance": "200"}, "bus": {"buslines": [{}]}}]},
        ]}}
        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *_): return None
            async def get(self, *_args, **_kwargs):
                class Response:
                    def raise_for_status(self): pass
                    def json(self): return data
                return Response()
        with patch("app.places.settings.amap_web_service_key", "test"), patch("app.places.httpx.AsyncClient", return_value=Client()):
            route = await calculate_route(Point(lng=114.30, lat=30.54), Point(lng=114.32, lat=30.55), "transit")
        self.assertEqual(route["minutes"], 50)
        self.assertEqual(route["walkingMinutes"], 2)
        self.assertEqual(route["transfers"], 0)

    async def test_strategy_and_selection_use_explicit_interests_without_company_templates(self):
        art = poi("art", "艺术馆", 114.30, 30.55, "博物馆")
        park = poi("park", "湖滨公园", 114.31, 30.55, "自然风光")
        seen = []
        async def model(_, user):
            payload = json.loads(user)
            seen.append(payload)
            if "candidates" not in payload:
                return json.dumps({"priorities": ["艺术探索"], "dayThemes": ["艺术与街区"], "searchQueries": ["武汉 艺术馆"]})
            chosen = "art" if "艺术" in payload["intent"]["interests"] else "park"
            return json.dumps({"days": [[{"placeId": chosen, "experience": "探索展览或湖岸"}]]})
        for company in ("情侣", "朋友", "亲子、长辈"):
            trip = intent(company=company, interests=["艺术"])
            await strategy(trip, model)
            groups, details = await select_days(trip, [], [art, park], model)
            self.assertEqual(groups[0][0].placeId, "art")
            self.assertEqual(details["art"]["experience"], "探索展览或湖岸")
        self.assertTrue(all(item["intent"]["interests"] == ["艺术"] for item in seen if "intent" in item))

    async def test_opening_window_reorders_and_reports_impossible_day(self):
        early = poi("early", "早闭馆", 114.30, 30.55)
        late = poi("late", "晚间展馆", 114.301, 30.55)
        async def route(_, __, ___):
            return {"minutes": 5, "distanceMeters": 350, "source": "高德地图路线规划", "polyline": []}
        with patch("app.trip_intent.calculate_route", side_effect=route):
            day, markdown, _ = await schedule_day(0, [late, early], intent(must_visit=["早闭馆"]), {},
                {"early": "09:00-11:30", "late": "09:00-22:00"}, None, Router(("walking",), "适中"), date(2026, 11, 22))
            self.assertEqual(day.stops[0].place.placeId, "early")
            self.assertFalse(validate([day], [early], ("walking",), "\n".join(markdown)))
            with self.assertRaises(HTTPException):
                await schedule_day(0, [early], intent(requirements="早闭馆晚上要去"),
                    {"early": {"timeWindow": "evening"}}, {"early": "09:00-17:30"},
                    None, Router(("walking",), "适中"), date(2026, 11, 22))


if __name__ == "__main__":
    unittest.main()
