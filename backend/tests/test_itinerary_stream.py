import json
import unittest
from unittest.mock import patch

import httpx

from app.main import app


class ItineraryStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_has_stage_updates_and_final_result(self):
        async def fake_builder(_request, progress=None):
            self.assertIsNotNone(progress)
            await progress(5, "校验设置")
            await progress(50, "选择地点")
            await progress(97, "整理行程")
            return {"title": "已生成", "markdown": "# 旅程", "dayPlans": [{"id": "day-1"}], "recommendedPlaces": [], "routeSource": "test"}

        with (
            patch("app.main.settings.ai_provider", "deepseek"),
            patch("app.main.settings.deepseek_api_key", "test-key"),
            patch("app.main.build_itinerary", new=fake_builder),
        ):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/itineraries/generate/stream",
                    json={"mustVisitPlaces": [], "people": 1, "days": 1},
                )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("application/x-ndjson"))
        events = [json.loads(line) for line in response.text.splitlines() if line]
        self.assertEqual([event["type"] for event in events], ["progress", "progress", "progress", "complete"])
        self.assertEqual([event["percent"] for event in events], [5, 50, 97, 100])
        self.assertEqual(events[-1]["result"]["title"], "已生成")


if __name__ == "__main__":
    unittest.main()
