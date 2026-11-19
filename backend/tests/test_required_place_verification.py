import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.models import Place, Point
from app.places import FEATURED, verify_required_place


def place(identifier="featured-jianghan", name="江汉路步行街", source="内置地点参考",
          lng=114.2911, lat=30.5824, category="街区"):
    return Place(placeId=identifier, name=name, source=source, category=category,
                 address="武汉市江汉区江汉路", location=Point(lng=lng, lat=lat))


class RequiredPlaceVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_featured_category_aliases_and_extended_greenway(self):
        cases = (
            (0, "B001B0I4K0", "风景名胜", Point(lng=114.3021, lat=30.5471)),
            (2, "B0IATO2XHL", "地名地址信息", Point(lng=114.415888, lat=30.574937)),
            (4, "B001B0IZY1", "科教文化服务", Point(lng=114.3675, lat=30.5404)),
        )
        for index, identifier, category, location in cases:
            claimed = FEATURED[index]
            canonical = claimed.model_copy(update={"placeId": identifier, "category": category,
                                                  "location": location, "source": "高德地图"})
            with patch("app.places.settings.amap_web_service_key", "test"), \
                 patch("app.places.search_places", new=AsyncMock(return_value=([canonical], "高德地图 POI"))):
                self.assertEqual((await verify_required_place(claimed)).placeId, identifier)

    async def test_exact_name_outranks_closer_subvenue(self):
        claimed = FEATURED[1]
        exact = claimed.model_copy(update={"placeId": "B001B0JYXS", "category": "科教文化服务",
                                           "source": "高德地图"})
        subvenue = claimed.model_copy(update={"placeId": "B0MAGRZAH9", "name": "湖北省博物馆东馆",
                                              "category": "科教文化服务", "source": "高德地图"})
        with patch("app.places.settings.amap_web_service_key", "test"), \
             patch("app.places.search_places", new=AsyncMock(return_value=([subvenue, exact], "高德地图 POI"))):
            self.assertEqual((await verify_required_place(claimed)).placeId, exact.placeId)

    async def test_real_poi_id_is_checked_by_detail_without_search(self):
        selected = place("B001B0J07F", source="hotspot")
        canonical = place("B001B0J07F", source="高德地图", category="购物服务")
        detail = AsyncMock(return_value=canonical)
        search = AsyncMock(side_effect=AssertionError("无需关键词搜索"))
        with patch("app.places.settings.amap_web_service_key", "test"), \
             patch("app.places.place_details", detail), patch("app.places.search_places", search):
            self.assertEqual((await verify_required_place(selected)).placeId, canonical.placeId)
        detail.assert_awaited_once_with("B001B0J07F")
        search.assert_not_awaited()

    async def test_featured_place_matches_nearby_canonical_poi(self):
        match = place("B001B0J07F", source="高德地图", lng=114.288045,
                      lat=30.58364, category="购物服务")
        far = place("B0LKYU4AHF", source="高德地图", lng=114.296975,
                    lat=30.576025, category="购物服务")
        with patch("app.places.settings.amap_web_service_key", "test"), \
             patch("app.places.place_details", new=AsyncMock(side_effect=AssertionError("内置 ID 不是 POI ID"))), \
             patch("app.places.search_places", new=AsyncMock(return_value=([far, match], "高德地图 POI"))):
            self.assertEqual((await verify_required_place(place())).placeId, match.placeId)

    async def test_search_rejects_wrong_district_and_shop_with_place_name(self):
        wrong_district = place("BZA7PF01X8", source="高德地图", category="地名地址信息")
        wrong_district.address = "江岸区"
        shop = place("B0K03S440R", name="意尔康(江汉路步行街店)",
                     source="高德地图", category="购物服务")
        right = place("BZA8PF00LW", source="高德地图", category="地名地址信息")
        right.address = "江汉区"
        with patch("app.places.settings.amap_web_service_key", "test"), \
             patch("app.places.search_places", new=AsyncMock(return_value=([wrong_district, shop, right], "高德地图 POI"))):
            self.assertEqual((await verify_required_place(place())).placeId, right.placeId)

    async def test_source_claim_does_not_bypass_verification(self):
        selected = place("B001B0J07F", source="高德地图", name="另一地点")
        canonical = place("B001B0J07F", source="高德地图")
        with patch("app.places.settings.amap_web_service_key", "test"), \
             patch("app.places.place_details", new=AsyncMock(return_value=canonical)), \
             patch("app.places.search_places", new=AsyncMock(return_value=([canonical], "高德地图 POI"))):
            with self.assertRaises(HTTPException) as failure:
                await verify_required_place(selected)
        self.assertEqual(failure.exception.status_code, 422)
        self.assertIn("不符", failure.exception.detail)

    async def test_search_failure_is_temporary_not_nonexistent(self):
        with patch("app.places.settings.amap_web_service_key", "test"), \
             patch("app.places.search_places", new=AsyncMock(side_effect=HTTPException(502, "timeout"))):
            with self.assertRaises(HTTPException) as failure:
                await verify_required_place(place())
        self.assertEqual(failure.exception.status_code, 503)
        self.assertIn("暂时", failure.exception.detail)

    async def test_empty_search_and_mismatch_have_distinct_errors(self):
        for matches, expected in (([], "未找到"), ([place("B001B0J07F", name="江汉路地铁站", category="交通设施服务")], "不符")):
            with patch("app.places.settings.amap_web_service_key", "test"), \
                 patch("app.places.search_places", new=AsyncMock(return_value=(matches, "高德地图 POI"))):
                with self.assertRaises(HTTPException) as failure:
                    await verify_required_place(place())
            self.assertEqual(failure.exception.status_code, 422)
            self.assertIn(expected, failure.exception.detail)


if __name__ == "__main__":
    unittest.main()
