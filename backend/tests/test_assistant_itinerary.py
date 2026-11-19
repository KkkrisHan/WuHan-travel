import unittest
from unittest.mock import AsyncMock, patch

from app.assistant import chat
from app.models import Preferences, Snapshot


class UnifiedItineraryChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_generated_document_is_the_current_itinerary_even_without_map_stops(self):
        snapshot = Snapshot(
            tripId="test-trip", tripSecret="x" * 32, revision=0,
            preferences=Preferences(),
            itinerary=[],
            generatedItineraries=[{
                "title": "武汉一日旅行计划",
                "markdown": "# 武汉一日旅行计划\n## Day 1 · 日期未定\n黄鹤楼",
                "mustVisitPlaces": ["黄鹤楼"],
                "recommendedPlaces": [],
            }],
        )
        model = AsyncMock(return_value="当前行程是武汉一日旅行计划。")
        with patch("app.assistant._model", new=model):
            answer = await chat(snapshot, "我当前的行程安排")
        self.assertIn("武汉一日旅行计划", answer)
        system_prompt, user_prompt = model.await_args.args[:2]
        self.assertIn("最新生成的行程文档就是当前已保存的完整行程", system_prompt)
        self.assertIn("当前行程文档", user_prompt)
        self.assertNotIn("正式行程", user_prompt)
        self.assertNotIn("独立行程文档", user_prompt)


if __name__ == "__main__":
    unittest.main()
