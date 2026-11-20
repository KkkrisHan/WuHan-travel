"""Refresh verified Wuhan POIs: python -m app.poi_knowledge.refresh --all."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from datetime import date

from ..place_info import get_place_info
from ..places import _normalized_name, search_places
from .store import connection, get_place_knowledge, save_place, save_research

LOG = logging.getLogger(__name__)
FIRST_BATCH = (
    "黄鹤楼", "湖北省博物馆", "江汉路步行街", "古德寺", "东湖绿道", "武汉大学",
    "晴川阁", "户部巷", "黎黄陂路", "巴公房子", "江汉关博物馆", "武汉长江大桥",
    "古琴台", "武汉美术馆", "汉口江滩", "武昌江滩", "东湖听涛景区",
    "武汉植物园", "归元禅寺", "湖北美术馆",
)

# IDs observed in the live AMap result list. The matching result must still be
# returned by AMap on each run; these hints never manufacture a POI.
IDENTITY_HINTS = {
    "江汉路步行街": "B001B0J07F", "古德寺": "B001B1IGH4",
    "东湖绿道": "B0FFHONIK0", "户部巷": "B0FFF5V6F4",
    "武汉长江大桥": "B001B0JP4S", "武汉植物园": "B001B0IRKH",
    "湖北美术馆": "B001B0JG8G",
}


def _candidate(name, matches):
    """Accept exact map names only, avoiding another same-name POI."""
    norm = _normalized_name(name)
    hinted = next((p for p in matches if p.placeId == IDENTITY_HINTS.get(name)), None)
    if hinted:
        return hinted
    exact = [p for p in matches if _normalized_name(p.name) == norm and p.placeId]
    return exact[0] if len(exact) == 1 else None


async def refresh_one(name: str, *, expired_only: bool = False) -> dict:
    try:
        matches, _ = await asyncio.wait_for(search_places(name), timeout=15)
        if not matches and name == "古琴台":
            matches, _ = await asyncio.wait_for(search_places("武汉 古琴台"), timeout=15)
        place = _candidate(name, matches)
        if place is None:
            return {"name": name, "status": "failed", "reason": "高德未返回唯一的同名 POI"}
        save_place(place)
        old = get_place_knowledge(place.placeId)
        if expired_only and old and all(
                old["fields"].get(field, {}).get("status") == "verified"
                for field in ("openingHours", "reservation", "adultPrice")):
            return {"name": name, "poiId": place.placeId, "status": "skipped"}
        research = await asyncio.wait_for(get_place_info(place, refresh=True), timeout=72)
        save_research(place, research)
        return {"name": name, "poiId": place.placeId,
                "status": research.get("status", "unknown"),
                "fields": sorted((research.get("fields") or {}).keys()),
                "reason": research.get("error") or research.get("conflict") or ""}
    except (Exception, asyncio.TimeoutError) as exc:
        LOG.exception("poi_refresh_failed name=%s", name)
        return {"name": name, "status": "failed", "reason": type(exc).__name__}


async def refresh_many(names, *, expired_only=False):
    semaphore = asyncio.Semaphore(3)
    async def one(name):
        async with semaphore:
            return await refresh_one(name, expired_only=expired_only)
    return await asyncio.gather(*(one(name) for name in names))


def main():
    parser = argparse.ArgumentParser(description="Refresh evidence-backed Wuhan POI knowledge")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--all", action="store_true")
    target.add_argument("--place")
    target.add_argument("--failures", action="store_true")
    parser.add_argument("--expired-only", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.failures:
        with connection() as db:
            rows = db.execute("SELECT poi_id,name,status,error FROM poi_places WHERE status!='verified'").fetchall()
            all_rows = db.execute("SELECT poi_id,name FROM poi_places").fetchall()
            known_ids = {row["poi_id"] for row in all_rows}
            known_names = {row["name"] for row in all_rows}
            missing = [{"name": name, "status": "unresolved", "error": "无唯一高德 POI ID"}
                       for name in FIRST_BATCH if name not in known_names and
                       IDENTITY_HINTS.get(name) not in known_ids and
                       not any(row["name"].startswith(name + "(") or row["name"].startswith("中国科学院" + name)
                               for row in all_rows)]
            print(json.dumps([dict(row) for row in rows] + missing, ensure_ascii=False, indent=2))
        return
    results = asyncio.run(refresh_many(FIRST_BATCH if args.all else [args.place], expired_only=args.expired_only))
    if args.all or args.place in ("黄鹤楼", "湖北省博物馆", "江汉关博物馆"):
        from .official_seed import apply_official_seed
        apply_official_seed()
    print(json.dumps({"results": results, "counts": {status: sum(r["status"] == status for r in results)
                                               for status in sorted({r["status"] for r in results})}},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
