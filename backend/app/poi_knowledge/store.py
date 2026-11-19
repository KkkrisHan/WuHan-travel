"""SQLite schema and conservative date-aware POI queries.

Unknown is distinct from closed. Rules are stored separately from the text
which justified them, and special-date rules supersede recurring rules.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..config import settings

TTL_DAYS = {"openingHours": 30, "adultPrice": 14, "reservation": 14,
            "reservationUrl": 14, "todayNotice": 1, "visitorNotes": 90,
            "address": 365, "phone": 90, "discounts": 14}


@contextmanager
def connection():
    path = Path(settings.task_database)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        initialize(db)
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def initialize(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS poi_places (
      poi_id TEXT PRIMARY KEY, name TEXT NOT NULL, aliases TEXT NOT NULL DEFAULT '[]',
      category TEXT, address TEXT, lng REAL, lat REAL, description TEXT,
      official_url TEXT, city TEXT NOT NULL DEFAULT '武汉',
      checked_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', error TEXT);
    CREATE TABLE IF NOT EXISTS poi_sources (
      id INTEGER PRIMARY KEY, poi_id TEXT NOT NULL REFERENCES poi_places(poi_id),
      url TEXT NOT NULL, kind TEXT NOT NULL, title TEXT, published_at TEXT,
      checked_at TEXT NOT NULL, UNIQUE(poi_id,url));
    CREATE TABLE IF NOT EXISTS poi_facts (
      poi_id TEXT NOT NULL REFERENCES poi_places(poi_id), field TEXT NOT NULL,
      value TEXT NOT NULL, source_id INTEGER REFERENCES poi_sources(id),
      evidence TEXT, checked_at TEXT NOT NULL, valid_from TEXT, valid_to TEXT,
      status TEXT NOT NULL, error TEXT, PRIMARY KEY(poi_id,field,source_id));
    CREATE TABLE IF NOT EXISTS poi_sessions (
      id INTEGER PRIMARY KEY, poi_id TEXT NOT NULL REFERENCES poi_places(poi_id),
      kind TEXT NOT NULL, start_minute INTEGER, end_minute INTEGER,
      start_date TEXT, end_date TEXT, weekdays TEXT, month_start TEXT, month_end TEXT,
      is_closed INTEGER NOT NULL DEFAULT 0, last_entry_minute INTEGER,
      reservation TEXT, ticket TEXT, source_id INTEGER NOT NULL REFERENCES poi_sources(id),
      checked_at TEXT NOT NULL, status TEXT NOT NULL, note TEXT,
      UNIQUE(poi_id,kind,start_minute,end_minute,start_date,end_date,weekdays,month_start,month_end,is_closed,source_id));
    CREATE INDEX IF NOT EXISTS poi_sessions_lookup ON poi_sessions(poi_id,start_date,end_date);
    CREATE UNIQUE INDEX IF NOT EXISTS poi_sessions_identity ON poi_sessions
      (poi_id,kind,ifnull(start_minute,-1),ifnull(end_minute,-1),
       ifnull(start_date,''),ifnull(end_date,''),ifnull(weekdays,''),
       ifnull(month_start,''),ifnull(month_end,''),is_closed,source_id);
    """)


def _iso(value):
    return value or datetime.now(timezone.utc).isoformat()


def save_place(place, *, aliases=(), status="pending", error=None):
    if not re.fullmatch(r"[A-Za-z0-9]{6,32}", place.placeId):
        raise ValueError("A verified AMap POI ID is required")
    with connection() as db:
        db.execute("""INSERT INTO poi_places
        (poi_id,name,aliases,category,address,lng,lat,description,official_url,checked_at,status,error)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(poi_id) DO UPDATE SET
        name=excluded.name,aliases=CASE WHEN excluded.aliases='[]' THEN poi_places.aliases ELSE excluded.aliases END,
        category=excluded.category,address=excluded.address,
        lng=excluded.lng,lat=excluded.lat,
        description=COALESCE(NULLIF(excluded.description,''),poi_places.description),
        official_url=COALESCE(excluded.official_url,poi_places.official_url),
        checked_at=excluded.checked_at""",
        (place.placeId, place.name, json.dumps(list(aliases), ensure_ascii=False),
         place.category, place.address, place.location.lng, place.location.lat,
         place.description, place.officialUrl, _iso(None), status, error))


def save_research(place, research: dict):
    """Persist only source-backed fields; a failed refresh preserves old facts."""
    save_place(place)
    sources = research.get("sources") or []
    checked = _iso(research.get("checkedAt"))
    with connection() as db:
        source_ids = {}
        for index, source in enumerate(sources, 1):
            url = source.get("url")
            if not isinstance(url, str) or not url.startswith(("https://", "http://")):
                continue
            kind = source.get("sourceType") or ("official" if source.get("official") else "other")
            db.execute("""INSERT INTO poi_sources(poi_id,url,kind,title,published_at,checked_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(poi_id,url) DO UPDATE SET checked_at=excluded.checked_at,
                published_at=COALESCE(excluded.published_at,poi_sources.published_at)""",
                (place.placeId, url, str(kind), source.get("title"), source.get("publishedAt"), checked))
            source_ids[index] = db.execute("SELECT id FROM poi_sources WHERE poi_id=? AND url=?",
                                           (place.placeId, url)).fetchone()[0]
        fields = research.get("fields") or {}
        for field, item in fields.items():
            if not isinstance(item, dict) or not isinstance(item.get("value"), str):
                continue
            source_id = source_ids.get(item.get("sourceId"))
            if not source_id:
                continue
            db.execute("""INSERT INTO poi_facts(poi_id,field,value,source_id,evidence,checked_at,valid_from,valid_to,status,error)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(poi_id,field,source_id) DO UPDATE SET
                value=excluded.value,evidence=excluded.evidence,checked_at=excluded.checked_at,
                valid_from=excluded.valid_from,valid_to=excluded.valid_to,status=excluded.status,error=excluded.error""",
                (place.placeId, field, item["value"], source_id, item.get("evidence"),
                 item.get("checkedAt") or checked, item.get("validFrom"), item.get("validTo"),
                 "conflict" if field in (research.get("conflict") or "") else "verified",
                 research.get("conflict") if field in (research.get("conflict") or "") else None))
        if fields:
            db.execute("UPDATE poi_places SET status=?,error=? WHERE poi_id=?",
                       ("conflict" if research.get("conflict") else "verified", research.get("conflict") or None, place.placeId))
    from .parse import parse_opening_evidence
    parse_opening_evidence(place.placeId, research)


def add_session(poi_id: str, *, kind: str, start_minute: int | None, end_minute: int | None,
                source_url: str, source_kind: str = "official", checked_at: str | None = None,
                start_date: str | None = None, end_date: str | None = None,
                weekdays: list[int] | None = None, month_start: str | None = None,
                month_end: str | None = None, is_closed: bool = False,
                last_entry_minute: int | None = None, reservation: str | None = None,
                ticket: str | None = None, status: str = "verified", note: str | None = None):
    if not source_url.startswith(("https://", "http://")):
        raise ValueError("source URL required")
    if not is_closed and (start_minute is None or end_minute is None or not 0 <= start_minute < end_minute <= 1440):
        raise ValueError("invalid session")
    if kind not in ("day", "night", "special", "public", "closure"):
        raise ValueError("invalid session kind")
    checked = _iso(checked_at)
    with connection() as db:
        db.execute("INSERT INTO poi_sources(poi_id,url,kind,checked_at) VALUES(?,?,?,?) ON CONFLICT(poi_id,url) DO NOTHING",
                   (poi_id, source_url, source_kind, checked))
        source_id = db.execute("SELECT id FROM poi_sources WHERE poi_id=? AND url=?", (poi_id, source_url)).fetchone()[0]
        db.execute("""INSERT INTO poi_sessions
        (poi_id,kind,start_minute,end_minute,start_date,end_date,weekdays,month_start,month_end,
         is_closed,last_entry_minute,reservation,ticket,source_id,checked_at,status,note)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET
        checked_at=excluded.checked_at,status=excluded.status,note=excluded.note,
        last_entry_minute=excluded.last_entry_minute,reservation=excluded.reservation,ticket=excluded.ticket""",
        (poi_id, kind, start_minute, end_minute, start_date, end_date,
         json.dumps(weekdays) if weekdays is not None else None, month_start, month_end,
         int(is_closed), last_entry_minute, reservation, ticket, source_id, checked, status, note))


def _fresh(checked: str, days: int) -> bool:
    try:
        parsed = datetime.fromisoformat(checked.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - parsed <= timedelta(days=days)
    except (ValueError, TypeError):
        return False


def get_place_knowledge(place_id: str, visit_date: date | None = None) -> dict | None:
    with connection() as db:
        row = db.execute("SELECT * FROM poi_places WHERE poi_id=?", (place_id,)).fetchone()
        if row is None:
            return None
        facts = {}
        for fact in db.execute("""SELECT f.*,s.url,s.kind FROM poi_facts f JOIN poi_sources s ON s.id=f.source_id
                                   WHERE f.poi_id=? ORDER BY f.checked_at DESC""", (place_id,)):
            if fact["field"] in facts:
                if facts[fact["field"]]["value"] != fact["value"]:
                    facts[fact["field"]]["status"] = "conflict"
                continue
            fresh = _fresh(fact["checked_at"], TTL_DAYS.get(fact["field"], 90))
            if visit_date and ((fact["valid_from"] and visit_date < date.fromisoformat(fact["valid_from"])) or
                               (fact["valid_to"] and visit_date > date.fromisoformat(fact["valid_to"]))):
                fresh = False
            facts[fact["field"]] = {"value": fact["value"], "sourceUrl": fact["url"],
                                    "sourceType": fact["kind"], "checkedAt": fact["checked_at"],
                                    "status": fact["status"] if fresh else "expired",
                                    "error": fact["error"]}
        return {"poiId": place_id, "name": row["name"], "aliases": json.loads(row["aliases"]),
                "category": row["category"], "address": row["address"],
                "location": {"lng": row["lng"], "lat": row["lat"]},
                "description": row["description"], "officialUrl": row["official_url"],
                "status": row["status"], "fields": facts,
                "sessions": get_opening_sessions(place_id, visit_date) if visit_date else None}


def find_place(name: str, lng: float, lat: float, visit_date: date | None = None) -> dict | None:
    """Resolve a name or explicit alias only when its map point is nearby."""
    with connection() as db:
        rows = db.execute("SELECT poi_id,name,aliases,lng,lat FROM poi_places WHERE lng IS NOT NULL AND lat IS NOT NULL").fetchall()
    matched = []
    normalized = re.sub(r"\s+", "", name).casefold()
    for row in rows:
        names = [row["name"], *json.loads(row["aliases"])]
        if normalized not in {re.sub(r"\s+", "", n).casefold() for n in names}:
            continue
        meters = (((row["lng"] - lng) * 95000) ** 2 + ((row["lat"] - lat) * 111000) ** 2) ** .5
        if meters <= 350:
            matched.append((meters, row["poi_id"]))
    return get_place_knowledge(min(matched)[1], visit_date) if len(matched) == 1 else None


def get_opening_sessions(place_id: str, visit_date: date | None) -> dict:
    if visit_date is None:
        return {"status": "unknown", "sessions": [], "reason": "旅行日期未知"}
    with connection() as db:
        rows = db.execute("""SELECT r.*,s.url,s.kind AS source_kind FROM poi_sessions r
                             JOIN poi_sources s ON s.id=r.source_id WHERE r.poi_id=?""", (place_id,)).fetchall()
    applicable = []
    mmdd = visit_date.strftime("%m-%d")
    for row in rows:
        if row["start_date"] and visit_date < date.fromisoformat(row["start_date"]):
            continue
        if row["end_date"] and visit_date > date.fromisoformat(row["end_date"]):
            continue
        if row["weekdays"] is not None and visit_date.weekday() not in json.loads(row["weekdays"]):
            continue
        if row["month_start"] and row["month_end"]:
            first, last = row["month_start"], row["month_end"]
            if not (first <= mmdd <= last if first <= last else mmdd >= first or mmdd <= last):
                continue
        applicable.append(row)
    # Explicit dated notices override the recurring schedule for the date.
    special = [r for r in applicable if r["start_date"] and r["end_date"]
               and (r["is_closed"] or r["kind"] == "special")]
    if special:
        applicable = special if any(r["is_closed"] for r in special) else special + [
            r for r in applicable if r["kind"] == "night" and r not in special]
    if not applicable:
        return {"status": "unknown", "sessions": [], "reason": "该日期无可核实开放规则"}
    sessions = []
    for row in applicable:
        status = row["status"] if _fresh(row["checked_at"], 30 if row["kind"] not in ("night", "special", "closure") else 7) else "expired"
        sessions.append({"kind": row["kind"], "start": row["start_minute"], "end": row["end_minute"],
                         "closed": bool(row["is_closed"]), "lastEntry": row["last_entry_minute"],
                         "reservation": row["reservation"], "ticket": row["ticket"],
                         "sourceUrl": row["url"], "sourceType": row["source_kind"],
                         "checkedAt": row["checked_at"], "status": status, "note": row["note"]})
    valid = [s for s in sessions if s["status"] == "verified"]
    if not valid:
        status = "expired" if any(s["status"] == "expired" for s in sessions) else "conflict"
    elif any(s["closed"] for s in valid) and any(not s["closed"] for s in valid):
        status = "conflict"
    elif any(len({(s["start"], s["end"]) for s in valid if s["kind"] == kind and not s["closed"]}) > 1
             for kind in {s["kind"] for s in valid}):
        status = "conflict"
    else:
        status = "closed" if all(s["closed"] for s in valid) else "verified"
    return {"status": status, "sessions": sessions, "reason": None}
