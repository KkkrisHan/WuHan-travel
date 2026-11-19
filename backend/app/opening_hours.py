"""Opening-hour evidence for itinerary scheduling.

Only validated, source-backed time windows affect placement; unknown or
ambiguous schedules are flagged rather than invented.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date, timedelta

from .models import Place
from .place_info import get_place_info
from .poi_knowledge.store import get_place_knowledge, get_opening_sessions, save_research
from .request_context import count_external

_TIME_RANGE = re.compile(
    r"(?<!\d)(\d{1,2})\s*[:：]\s*(\d{2})\s*[-–—~～至到]+\s*(\d{1,2})\s*[:：]\s*(\d{2})(?!\d)"
)


@dataclass(frozen=True)
class OpeningWindow:
    start: int
    end: int
    label: str
    last_entry: int | None = None


def opening_window(value: str | None) -> OpeningWindow | None:
    if not isinstance(value, str):
        return None
    matches: set[tuple[int, int]] = set()
    for match in _TIME_RANGE.finditer(value):
        h1, m1, h2, m2 = map(int, match.groups())
        if not (0 <= h1 <= 23 and 0 <= h2 <= 24 and 0 <= m1 < 60 and 0 <= m2 < 60):
            continue
        start, end = h1 * 60 + m1, h2 * 60 + m2
        if end > start:
            matches.add((start, end))
    # Two seasonal schedules cannot safely be combined without knowing the date rules.
    if len(matches) != 1:
        return None
    start, end = next(iter(matches))
    return OpeningWindow(start, end, value)


def dated_windows(place_id: str, when: date | None) -> tuple[str, list[OpeningWindow]]:
    if when is None:
        return "unknown", []
    result = get_opening_sessions(place_id, when)
    if result["status"] != "verified":
        return result["status"], []
    windows = {(s["start"], s["end"], s["lastEntry"]): OpeningWindow(
        s["start"], s["end"], s["sourceUrl"], s["lastEntry"])
        for s in result["sessions"] if s["status"] == "verified" and not s["closed"]}
    return "verified", sorted(windows.values(), key=lambda w: w.start)


def preferred_time(requirements: str, name: str) -> str | None:
    for clause in re.split(r"[；;。\n]", requirements):
        if name not in clause:
            continue
        if re.search(r"晚上|夜间|夜晚|夜景", clause):
            return "night"
        if re.search(r"早上|上午|清晨", clause):
            return "morning"
        if re.search(r"下午|午后", clause):
            return "afternoon"
    return None


def closed_for_date(hours: str, when: date | None) -> bool:
    if when is None:
        return False
    names = "一二三四五六日"
    weekday = names[when.weekday()]
    return bool(
        re.search(r"(?:周|星期)" + weekday + r"\s*(?:闭馆|闭园|休馆|休园|不开放)", hours)
        or re.search(r"(?:每周|每星期)" + weekday + r"\s*(?:闭馆|闭园|休馆|休园|不开放)", hours)
    )


def _session_label(place_id: str, when: date | None) -> str | None:
    if when is None:
        return None
    result = get_opening_sessions(place_id, when)
    if result["status"] == "closed":
        return "该日期闭馆或闭园（已核实）"
    if result["status"] != "verified":
        return None
    sessions = [s for s in result["sessions"] if s["status"] == "verified" and not s["closed"]]
    distinct = {(s["kind"], s["start"], s["end"]) for s in sessions}
    return "；".join(f"{kind} {start//60:02d}:{start%60:02d}–{end//60:02d}:{end%60:02d}"
                    for kind, start, end in sorted(distinct)) or None


async def collect_opening_hours(places: list[Place], *, max_wait: float = 23,
                                visit_date: date | None = None,
                                visit_days: int = 1,
                                required_ids: set[str] | None = None) -> dict[str, str]:
    unique = list({p.placeId: p for p in places}.values())
    if not unique:
        return {}
    semaphore = asyncio.Semaphore(3)
    found: dict[str, str] = {}
    missing = []
    for place in unique:
        labels = []
        if visit_date:
            for offset in range(max(1, min(visit_days, 7))):
                when = visit_date + timedelta(days=offset)
                label = _session_label(place.placeId, when)
                if label:
                    labels.append(f"{when.isoformat()}：{label}")
        if labels:
            count_external("poiKnowledgeHits")
            found[place.placeId] = "；".join(labels)
            continue
        knowledge = get_place_knowledge(place.placeId, visit_date)
        field = (knowledge or {}).get("fields", {}).get("openingHours")
        if field and field["status"] == "verified":
            count_external("poiKnowledgeHits")
            found[place.placeId] = field["value"]
        else:
            count_external("poiKnowledgeMisses")
            missing.append(place)
    # Only missing mandatory facts and a small number of candidate facts consume
    # the request-time network budget. Batch initialization handles the rest.
    required_ids = required_ids or set()
    missing.sort(key=lambda p: p.placeId not in required_ids)
    async def fetch(place: Place) -> tuple[str, str | None]:
        async with semaphore:
            try:
                info = await get_place_info(place)
                if info.get("stale"):
                    return place.placeId, None
                if info.get("fields"):
                    await asyncio.to_thread(save_research, place, info)
                field = info.get("fields", {}).get("openingHours")
                return place.placeId, field.get("value") if isinstance(field, dict) and isinstance(field.get("value"), str) else None
            except Exception:
                return place.placeId, None

    tasks = [asyncio.create_task(fetch(p)) for p in missing if p.placeId in required_ids]
    if not tasks:
        return found
    try:
        done, pending = await asyncio.wait(tasks, timeout=max_wait)
        for task in pending:
            task.cancel()
        for task in done:
            if task.cancelled():
                continue
            try:
                place_id, hours = task.result()
            except Exception:
                continue
            if hours:
                found[place_id] = hours
        return found
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
