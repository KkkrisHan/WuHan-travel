import os
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch
from datetime import date

from fastapi.testclient import TestClient

os.environ["TASK_DATABASE"] = os.path.join(tempfile.gettempdir(), "wuhan-concierge-test.sqlite3")
os.environ["OPENAI_API_KEY"] = ""
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["AI_PROVIDER"] = "openai"
os.environ["AMAP_WEB_SERVICE_KEY"] = ""
os.environ["AMAP_JS_SECURITY_CODE"] = ""
from app.main import app  # noqa: E402
from app.main import REQUEST_TIMES  # noqa: E402
from app.config import settings  # noqa: E402
from app.places import FEATURED  # noqa: E402


class ApiTest(unittest.TestCase):
    def setUp(self):
        REQUEST_TIMES.clear()
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)

    def wait_task(self, task):
        for _ in range(100):
            response = self.client.get(f"/api/tasks/{task['taskId']}", headers={"X-Task-Token": task["accessToken"]})
            self.assertEqual(response.status_code, 200)
            data = response.json()
            if data["status"] in ("completed", "failed", "cancelled"):
                return data
            time.sleep(0.05)
        self.fail("task did not finish")

    def snapshot(self):
        return {"tripId": "test-trip-1", "tripSecret": "a" * 64, "revision": 0, "preferences": {"startDate": date.today().isoformat(), "days": 1, "people": 2, "pace": "适中", "transport": "步行与公共交通"}, "pendingPlaces": [FEATURED[0].model_dump(), FEATURED[1].model_dump()], "savedPlaces": [], "itinerary": [], "userRequest": ""}

    def test_health_search_and_task_authorization(self):
        self.assertTrue(self.client.get("/api/health").json()["ok"])
        search = self.client.post("/api/places/search", json={"query": "黄鹤楼"})
        self.assertEqual(search.status_code, 200)
        self.assertGreaterEqual(len(search.json()["places"]), 1)
        intro = self.client.post("/api/places/intro", json={"tripId": "test-trip-1", "tripSecret": "a" * 64, "revision": 0, "place": FEATURED[0].model_dump()}).json()
        self.assertEqual(self.client.get(f"/api/tasks/{intro['taskId']}").status_code, 403)
        finished = self.wait_task(intro)
        self.assertEqual(finished["status"], "completed")
        self.assertIn("联网资料暂不可用", finished["result"]["text"])
        self.assertEqual(self.client.get(f"/api/tasks/{intro['taskId']}/events").status_code, 403)
        events = self.client.get(f"/api/tasks/{intro['taskId']}/events", headers={"X-Task-Token": intro["accessToken"]})
        self.assertEqual(events.status_code, 200)
        self.assertIn("task_completed", events.text)

    def test_chat_worker_failure_finishes_task_and_emits_error(self):
        with patch("app.main.chat_graph.ainvoke", new=AsyncMock(side_effect=RuntimeError("AI 服务返回 HTTP 503，请稍后重试"))):
            created = self.client.post("/api/chat", json={"snapshot": self.snapshot(), "message": "你好"})
            self.assertEqual(created.status_code, 200)
            task = created.json()
            finished = self.wait_task(task)
        self.assertEqual(finished["status"], "failed")
        self.assertIn("HTTP 503", finished["error"])
        events = self.client.get(f"/api/tasks/{task['taskId']}/events", headers={"X-Task-Token": task["accessToken"]})
        self.assertIn("task_failed", events.text)

    def test_planning_and_delete_auth(self):
        created = self.client.post("/api/plans/generate", json={"snapshot": self.snapshot()})
        self.assertEqual(created.status_code, 200, created.text)
        task = created.json()
        finished = self.wait_task(task)
        self.assertEqual(finished["status"], "completed", finished)
        candidates = finished["result"]["candidates"]
        self.assertEqual(len(candidates), 2)
        self.assertNotEqual(candidates[0]["days"][0]["stops"][0]["place"]["placeId"], candidates[1]["days"][0]["stops"][0]["place"]["placeId"])
        self.assertIsInstance(candidates[0]["days"][0]["transportMinutes"], int)
        self.assertEqual(self.client.delete("/api/trips/test-trip-1/tasks", headers={"X-Trip-Secret": "wrong"}).status_code, 403)
        self.assertEqual(self.client.delete("/api/trips/test-trip-1/tasks", headers={"X-Trip-Secret": "a" * 64}).status_code, 200)

    def test_chat_change_proposal_and_fixed_time_warning(self):
        snapshot = self.snapshot()
        snapshot["preferences"]["requirements"] = "周六 14:00 有固定活动，必须去黄鹤楼"
        planned = self.wait_task(self.client.post("/api/plans/generate", json={"snapshot": snapshot}).json())
        self.assertEqual(planned["status"], "completed")
        candidate = planned["result"]["candidates"][0]
        self.assertTrue(any("固定时间" in warning for warning in candidate["warnings"]))
        snapshot["itinerary"] = candidate["days"]
        optimized = self.wait_task(self.client.post("/api/plans/optimize", json={"snapshot": snapshot}).json())
        self.assertEqual(optimized["status"], "completed")
        self.assertEqual(len(optimized["result"]["candidates"]), 2)
        chat_task = self.client.post("/api/chat", json={"snapshot": snapshot, "message": "把黄鹤楼从行程中删除"}).json()
        finished = self.wait_task(chat_task)
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["result"]["patch"]["operation"], "remove_stop")
        self.assertEqual(finished["result"]["patch"]["baseRevision"], 0)

    def test_static_map_requires_key(self):
        self.assertEqual(self.client.get("/api/maps/static", params={"points": "114.3,30.5"}).status_code, 503)
        self.assertEqual(self.client.get("/_AMapService/v3/place/text").status_code, 503)

    def test_personal_map_place_lifecycle_and_isolation(self):
        preflight = self.client.options("/api/places", headers={
            "Origin": settings.frontend_origin.split(",")[0].strip(),
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-trip-id,x-trip-secret",
        })
        self.assertEqual(preflight.status_code, 200, preflight.text)
        trip = {"X-Trip-ID": "personal-map-test", "X-Trip-Secret": "s" * 64}
        wrong = {"X-Trip-ID": trip["X-Trip-ID"], "X-Trip-Secret": "w" * 64}
        place = {"poi_id": "B000000001", "name": "Test Park", "address": "武汉", "type": "公园", "longitude": 114.3, "latitude": 30.5, "source": "hotspot"}
        self.client.delete("/api/trips/personal-map-test/tasks", headers={"X-Trip-Secret": trip["X-Trip-Secret"]})
        self.assertEqual(self.client.get("/api/places", headers=trip).json(), {"places": []})
        created = self.client.post("/api/places", headers=trip, json=place)
        self.assertEqual(created.status_code, 200, created.text)
        first = created.json()["place"]
        self.assertEqual(self.client.post("/api/places", headers=trip, json=place).json()["place"]["id"], first["id"])
        self.assertEqual(len(self.client.get("/api/places", headers=trip).json()["places"]), 1)
        self.assertEqual(self.client.get("/api/places", headers=wrong).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/places/{first['id']}", headers=wrong).status_code, 403)
        coordinate = {"poi_id": None, "name": "地图位置", "address": None, "type": "坐标地点", "longitude": 114.301234, "latitude": 30.501234, "source": "coordinate"}
        saved_coordinate = self.client.post("/api/places", headers=trip, json=coordinate).json()["place"]
        self.assertEqual(self.client.post("/api/places", headers=trip, json=coordinate).json()["place"]["id"], saved_coordinate["id"])
        self.assertEqual(self.client.delete(f"/api/places/{first['id']}", headers=trip).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/places/{first['id']}", headers=trip).status_code, 404)
        self.assertEqual([item["id"] for item in self.client.get("/api/places", headers=trip).json()["places"]], [saved_coordinate["id"]])
        self.assertEqual(self.client.delete("/api/trips/personal-map-test/tasks", headers={"X-Trip-Secret": trip["X-Trip-Secret"]}).status_code, 200)
        self.assertEqual(self.client.get("/api/places", headers=trip).json(), {"places": []})


if __name__ == "__main__":
    unittest.main()
