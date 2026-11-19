"""Conservative conversion of quoted official opening rules into sessions."""
from __future__ import annotations

import re

from .store import add_session

RANGE = re.compile(r"(?<!\d)(\d{1,2})[:：](\d{2})\s*[-–—~～至到]\s*(\d{1,2})[:：](\d{2})(?!\d)")


def parse_opening_evidence(place_id: str, research: dict) -> int:
    """Add only unambiguous, quoted rules from a recognized official source.

    Seasonal and event prose stays as a sourced raw fact until a dated rule can
    be established. A night interval never inherits a day ticket or schedule.
    """
    field = (research.get("fields") or {}).get("openingHours")
    if not isinstance(field, dict):
        return 0
    index = field.get("sourceId")
    sources = research.get("sources") or []
    if not isinstance(index, int) or not 1 <= index <= len(sources):
        return 0
    source = sources[index - 1]
    if not source.get("official") or not source.get("url"):
        return 0
    quote = field.get("evidence") or ""
    if research.get("conflict") or not quote:
        return 0
    # Do not interpret dates or seasonal labels without a precise interval.
    if re.search(r"夏季|冬季|春季|秋季|节假日|法定假日|活动期间|夜游|夜场|夜间|临时|即日起|另行通知", quote):
        return 0
    matches = list(RANGE.finditer(quote))
    if len(matches) != 1:
        return 0
    h1, m1, h2, m2 = map(int, matches[0].groups())
    start, end = h1 * 60 + m1, h2 * 60 + m2
    if not 0 <= start < end <= 1440:
        return 0
    # A quoted weekly closure overrides the opening rule on that weekday.
    closed_days = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6}
    closed = {closed_days[d] for d in re.findall(r"(?:每)?(?:周|星期)([一二三四五六日])\s*(?:闭馆|闭园|休馆|休园|不开放)", quote)}
    weekdays = [day for day in range(7) if day not in closed]
    add_session(place_id, kind="day", start_minute=start, end_minute=end,
                weekdays=weekdays, source_url=source["url"],
                source_kind=source.get("sourceType") or "official",
                checked_at=field.get("checkedAt"), note=quote[:500])
    for day in closed:
        add_session(place_id, kind="closure", start_minute=None, end_minute=None,
                    weekdays=[day], is_closed=True, source_url=source["url"],
                    source_kind=source.get("sourceType") or "official",
                    checked_at=field.get("checkedAt"), note=quote[:500])
    return 1 + len(closed)
