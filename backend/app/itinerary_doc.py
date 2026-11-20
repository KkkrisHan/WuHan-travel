"""One-pass map-grounded itinerary: collect facts, decide once, schedule once."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date
from typing import Awaitable, Callable

from fastapi import HTTPException

from .assistant import _model
from .config import settings
from .models import Place, Point
from .one_pass_plan import decide_days_once
from .opening_hours import collect_opening_hours
from .places import search_places, verify_required_place
from .request_context import request_metrics
from .trip_intent import Router, TripIntent, allowed_modes, meters
from .trip_schedule import schedule_day, validate

LOG = logging.getLogger(__name__)


async def _lodging_point(name: str) -> Point:
    matches, _ = await search_places(name)
    if matches:
        return matches[0].location
    raise HTTPException(422, "无法定位住宿地点，请提供更完整的酒店名称或地图位置")


def _unique(places: list[Place]) -> list[Place]:
    seen = set()
    result = []
    for place in places:
        name = "".join(place.name.split()).lower()
        if place.placeId and name and name not in seen:
            result.append(place)
            seen.add(name)
    return result


def _planning_pool(pool: list[Place], required: list[Place], opening: dict[str, str],
                   requirements: str, days: int) -> list[Place]:
    """Keep optional indoor places with unknown entry rules out of a feasible pool."""
    required_ids = {place.placeId for place in required}

    def needs_entry_proof(place: Place) -> bool:
        return place.category in ("博物馆", "科教文化服务", "校园") or any(
            word in place.name for word in ("博物馆", "美术馆", "寺", "黄鹤楼"))

    ready = [place for place in pool if place.placeId in required_ids
             or place.name in requirements
             or opening.get(place.placeId)
             or not needs_entry_proof(place)]
    return ready if len(ready) >= max(days, len(required) + 2) else pool


async def _route_hints(required: list[Place], hotel: Point | None, router: Router) -> list[dict]:
    """Use available real route data with a fixed, small preplanning budget."""
    anchors = [(place.name, place.location) for place in required[:5]]
    if hotel:
        anchors.append(("住宿", hotel))
    pairs = [(a, p, b, q) for i, (a, p) in enumerate(anchors)
             for b, q in anchors[i + 1:]]
    hints = [{"from": a, "to": b, "straightLineMeters": round(meters(p, q))}
             for a, p, b, q in pairs]
    if not pairs:
        return hints

    async def fetch(index: int, p: Point, q: Point):
        try:
            mode, route = await router.choose(p, q)
            return index, mode, route
        except HTTPException:
            return index, None, None

    tasks = [asyncio.create_task(fetch(i, p, q))
             for i, (_, p, _, q) in enumerate(pairs)]
    done, pending = await asyncio.wait(tasks, timeout=9)
    for task in pending:
        task.cancel()
    for task in done:
        if task.cancelled() or task.exception():
            continue
        index, mode, route = task.result()
        if mode and route and "估算" not in route.get("source", ""):
            hints[index].update({"mode": mode, "minutes": route["minutes"],
                                 "walkingMinutes": route.get("walkingMinutes")})
    return hints


async def build_itinerary(request, progress: Callable[[int, str], Awaitable[None]] | None = None) -> dict:
    started = time.monotonic()
    counters: dict[str, int] = {}
    request_metrics.set(counters)

    async def report(percent: int, label: str) -> None:
        LOG.info("one_pass_phase percent=%d elapsed_ms=%d label=%s",
                 percent, round((time.monotonic() - started) * 1000), label)
        if progress:
            await progress(percent, label)

    await report(5, "整理用户输入与必须满足的要求")
    if not settings.amap_web_service_key:
        raise HTTPException(503, "未配置高德 Web 服务密钥，无法核实推荐地点")
    try:
        first_date = date.fromisoformat(request.startDate) if request.startDate else None
    except ValueError as exc:
        raise HTTPException(422, "开始日期格式无效") from exc
    required = list({p.placeId: p for p in request.mustVisitPlaces}.values())
    intent = TripIntent(
        days=request.days, people=request.people,
        company=getattr(request, "company", ""),
        pace=getattr(request, "pace", "适中"),
        budget=getattr(request, "budget", "灵活"),
        interests=getattr(request, "interests", []),
        requirements=request.requirements,
        modes=allowed_modes(request.transport, request.requirements),
        must_visit=[p.name for p in required],
        start_date=request.startDate,
        lodging=request.lodging.strip(),
    )

    # Search once, without requesting a preliminary AI strategy. All provided
    # preferences are passed to the single planning model call later.
    await report(18, "检索高德真实地点")
    # Verify user supplied places before they enter the model candidate pool.
    # The source label and browser supplied coordinates are never proof of identity.
    required = list(await asyncio.gather(*(verify_required_place(p) for p in required)))
    required = list({p.placeId: p for p in required}.values())
    intent.must_visit = [p.name for p in required]
    queries = list(dict.fromkeys(
        ["武汉 景点"]
        + [f"武汉 {interest}" for interest in intent.interests]
        + (["武汉 特色美食 餐厅"] if "美食" in intent.interests else [])
    ))[:8]
    search_limit = asyncio.Semaphore(3)

    async def search_one(query: str):
        async with search_limit:
            return await asyncio.wait_for(search_places(query), timeout=13)

    searches = await asyncio.gather(*(search_one(q) for q in queries),
                                    return_exceptions=True)
    for query, result in zip(queries, searches):
        if isinstance(result, BaseException):
            LOG.warning("candidate_search_failed query=%s error=%s", query, type(result).__name__)
    batches = [result[0] for result in searches if not isinstance(result, BaseException)]
    interleaved = [batch[index] for index in range(max(map(len, batches), default=0))
                   for batch in batches if index < len(batch)]
    required_names = [p.name for p in required]

    def useful(place: Place) -> bool:
        if place.placeId in {p.placeId for p in required}:
            return True
        if any(place.name.startswith(name) for name in required_names):
            return False
        if any(token in place.name for token in (
            "停车场", "公厕", "售票处", "服务中心", "出入口", "检票口", "考试中心"
        )):
            return False
        if "-" in place.name and place.category != "餐饮服务":
            return False
        if place.category == "科教文化服务" and not any(
            token in place.description + place.name
            for token in ("博物馆", "展览馆", "科技馆", "艺术馆", "美术馆", "文化馆")
        ):
            return False
        return place.category in (
            "风景名胜", "餐饮服务", "购物服务", "科教文化服务",
            "公园广场", "历史地标", "自然风光", "博物馆", "街区"
        )

    # Keep the model context and metadata lookup bounded. Mandatory places
    # are always first, regardless of the search-results ranking.
    cap = max(12, min(25, intent.days * 3 + 5, 25))
    pool = _unique([*required, *(p for p in interleaved if useful(p))])[:cap]
    if len(pool) < intent.days:
        raise HTTPException(422, "真实地点不足以安排指定天数；请减少天数或调整兴趣")
    await report(38, f"已找到 {len(pool)} 个候选地点，预先核实事实")
    hotel = (getattr(request, "lodgingLocation", None)
             or (await _lodging_point(intent.lodging) if intent.lodging else None))
    router = Router(intent.modes, intent.budget)

    # The AI makes a single decision only AFTER opening-hours evidence and
    # available hotel/mandatory route hints have been gathered.
    opening, hints = await asyncio.gather(
        collect_opening_hours(pool, max_wait=16, visit_date=first_date, visit_days=intent.days,
                              required_ids={p.placeId for p in required}),
        _route_hints(required, hotel, router),
    )
    pool = _planning_pool(pool, required, opening, intent.requirements, intent.days)
    await report(58, "根据完整要求一次性决定逐日路线")
    groups, details = await decide_days_once(
        intent, required, pool, opening, hotel, hints, _model
    )
    counters["fullPlanningDecisions"] = 1
    selected = list({p.placeId: p for group in groups for p in group}.values())

    # Only timetable computation follows the one AI decision. No group swaps,
    # optional-stop removals, missing-place insertion, model retries or review
    # feedback loops are permitted.
    await report(74, "按唯一规划方案计算各天交通与时间")
    all_stops = [place for group in groups for place in group]
    results = await asyncio.gather(*(
        schedule_day(index, group, intent, details, opening, hotel, router,
                     first_date, _soft=False, all_places=all_stops,
                     exact_order=True)
        for index, group in enumerate(groups)
    ))
    days = [result[0] for result in results]
    sections = [line for _, lines, _ in results for line in lines]
    walk_minutes = [result[2] for result in results]

    header = [
        f"# 武汉{intent.days}日旅行计划", "", "## 旅行策略", "",
        *[f"- {priority}" for priority in intent.priorities],
        *[f"- 待协调要求：{conflict}" for conflict in intent.conflicts],
        "", f"日期：{first_date.isoformat() if first_date else '未指定'}；人数：{intent.people}；"
        f"节奏：{intent.pace}；预算：{intent.budget}。", "",
    ]
    markdown = "\n".join([
        *header, *sections, "## 出行说明", "",
        "地点顺序来自单次 AI 规划，时间与交通由地图路线计算；"
        "开放时段未经核实的地点不视为确认开放。", "",
    ])
    await report(91, "校验必去地点、时段、路线与表格一致性")
    issues = validate(days, required, intent.modes, markdown)
    if issues:
        raise HTTPException(422, "单次规划未通过约束校验（未自动修补）："
                            + "；".join(issues))
    recommended = [p for p in selected if p.placeId not in {r.placeId for r in required}]
    await report(97, "行程已通过校验")
    LOG.info("one_pass_completed days=%d stops=%d transport=%s walks=%s",
             len(days), len(selected), [day.transportMinutes for day in days], walk_minutes)
    return {
        "title": f"武汉{intent.days}日旅行计划",
        "markdown": markdown,
        "recommendedPlaces": [p.model_dump() for p in recommended],
        "routeSource": "高德地图路线规划或标明的距离估算",
        "dayPlans": [day.model_dump() for day in days],
        "strategy": {
            "priorities": intent.priorities, "dayThemes": intent.day_themes,
            "hardConstraints": intent.hard_constraints, "softGoals": intent.soft_goals,
            "avoid": intent.avoid, "conflicts": intent.conflicts,
            "reviewIssues": [],
        },
        "metrics": {
            "transportMinutes": [day.transportMinutes for day in days],
            "walkingMinutes": walk_minutes, "requiredCoverage": 1.0,
            "elapsedSeconds": round(time.monotonic() - started, 2),
            "networkCalls": counters.copy(),
            "routeCacheEntries": len(router.cache),
        },
    }
