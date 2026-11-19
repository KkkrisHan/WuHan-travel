import asyncio
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.config import settings
from app.models import Place, Point
from app.poi_knowledge.parse import parse_opening_evidence
from app.opening_hours import collect_opening_hours
from app.poi_knowledge.store import add_session, connection, find_place, get_opening_sessions, get_place_knowledge, save_place, save_research


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = settings.task_database
        settings.task_database = str(Path(self.temp.name) / "test.sqlite3")
        self.place = Place(placeId="B000000001", name="测试博物馆", category="博物馆",
                           address="武汉市", location=Point(lng=114.3, lat=30.5))
        save_place(self.place)
        self.checked = datetime.now(timezone.utc).isoformat()
        self.url = "https://example.org/hours"

    def tearDown(self):
        settings.task_database = self.old_db
        self.temp.cleanup()

    def rule(self, **kwargs):
        add_session(self.place.placeId, source_url=self.url,
                    checked_at=kwargs.pop("checked_at", self.checked), **kwargs)

    def test_identity_and_repeat(self):
        save_place(self.place)
        self.rule(kind="day", start_minute=540, end_minute=1020)
        self.rule(kind="day", start_minute=540, end_minute=1020)
        with connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM poi_places").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM poi_sessions").fetchone()[0], 1)
        self.assertEqual(get_place_knowledge(self.place.placeId)["poiId"], self.place.placeId)

    def test_seasons_weekly_closure_and_exception(self):
        self.rule(kind="day", start_minute=510, end_minute=1080,
                  month_start="04-01", month_end="10-31", weekdays=[1, 2, 3, 4, 5, 6])
        self.rule(kind="day", start_minute=540, end_minute=1020,
                  month_start="11-01", month_end="03-31", weekdays=[1, 2, 3, 4, 5, 6])
        self.rule(kind="closure", start_minute=None, end_minute=None,
                  weekdays=[0], is_closed=True)
        self.assertEqual(get_opening_sessions(self.place.placeId, date(2026, 7, 7))["sessions"][0]["start"], 510)
        self.assertEqual(get_opening_sessions(self.place.placeId, date(2026, 11, 24))["sessions"][0]["start"], 540)
        self.assertEqual(get_opening_sessions(self.place.placeId, date(2026, 11, 23))["status"], "closed")
        self.rule(kind="special", start_minute=600, end_minute=900,
                  start_date="2026-11-23", end_date="2026-11-23")
        self.assertEqual(get_opening_sessions(self.place.placeId, date(2026, 11, 23))["status"], "verified")

    def test_day_and_night_and_temporary_closure(self):
        self.rule(kind="day", start_minute=540, end_minute=1020)
        self.rule(kind="night", start_minute=1140, end_minute=1320,
                  start_date="2026-11-22", end_date="2026-11-22")
        on = get_opening_sessions(self.place.placeId, date(2026, 11, 22))
        self.assertEqual({s["kind"] for s in on["sessions"]}, {"day", "night"})
        self.assertFalse(any(s["kind"] == "night" for s in get_opening_sessions(self.place.placeId, date(2026, 11, 24))["sessions"]))
        self.rule(kind="closure", start_minute=None, end_minute=None, is_closed=True,
                  start_date="2026-11-25", end_date="2026-11-25")
        self.assertEqual(get_opening_sessions(self.place.placeId, date(2026, 11, 25))["status"], "closed")

    def test_unknown_conflict_public_space(self):
        self.assertEqual(get_opening_sessions(self.place.placeId, date(2026, 11, 22))["status"], "unknown")
        self.rule(kind="public", start_minute=0, end_minute=1440)
        self.assertEqual(get_opening_sessions(self.place.placeId, date(2026, 11, 22))["status"], "verified")
        self.rule(kind="public", start_minute=480, end_minute=1200)
        self.assertEqual(get_opening_sessions(self.place.placeId, date(2026, 11, 22))["status"], "conflict")

    def test_sourced_fact_and_failed_refresh(self):
        research = {"checkedAt": self.checked,
                    "sources": [{"url": self.url, "official": True, "sourceType": "official_venue"}],
                    "fields": {"reservation": {"value": "需预约", "sourceId": 1,
                                               "evidence": "参观需预约", "checkedAt": self.checked}}}
        save_research(self.place, research)
        save_research(self.place, {"status": "unavailable", "fields": {}, "sources": []})
        fact = get_place_knowledge(self.place.placeId)["fields"]["reservation"]
        self.assertEqual((fact["value"], fact["sourceUrl"], fact["status"]), ("需预约", self.url, "verified"))

    def test_parser_refuses_season_without_dates(self):
        research = {"sources": [{"url": self.url, "official": True}],
                    "fields": {"openingHours": {"sourceId": 1, "checkedAt": self.checked,
                                                "evidence": "夏季09:00-18:00，冬季09:00-17:00"}}}
        self.assertEqual(parse_opening_evidence(self.place.placeId, research), 0)

    def test_alias_needs_nearby_coordinate(self):
        save_place(self.place, aliases=["测试馆"])
        self.assertEqual(find_place("测试馆", 114.3001, 30.5001)["poiId"], self.place.placeId)
        self.assertIsNone(find_place("测试馆", 114.4, 30.5))
        self.assertIsNone(find_place("另一个测试馆", 114.3, 30.5))

    def test_expired_rule_is_not_trusted(self):
        self.rule(kind="day", start_minute=540, end_minute=1020,
                  checked_at="2025-01-01T00:00:00+00:00")
        result = get_opening_sessions(self.place.placeId, date(2026, 11, 22))
        self.assertEqual(result["status"], "expired")
        self.assertEqual(result["sessions"][0]["status"], "expired")

    def test_known_required_place_needs_no_web_request(self):
        self.rule(kind="day", start_minute=540, end_minute=1020)
        with patch("app.opening_hours.get_place_info") as web:
            result = asyncio.run(collect_opening_hours([self.place], visit_date=date(2026, 11, 22),
                                                       required_ids={self.place.placeId}))
        self.assertIn(self.place.placeId, result)
        web.assert_not_called()


if __name__ == "__main__":
    unittest.main()
