from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, time, timedelta
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from .assistant import _model
from .models import Candidate, DayPlan, Leg, Place, Snapshot, Stop
from .places import FEATURED, calculate_route, search_places


class PlanState(TypedDict, total=False):
    snapshot: Snapshot
    requested_names: list[str]
    fixed_events: list[str]
    places: list[Place]
    candidates: list[Candidate]
    warnings: list[str]


async def analyze_requirements(state: PlanState) -> dict:
    snapshot = state["snapshot"]
    request = "\n".join([snapshot.preferences.requirements, snapshot.userRequest]).strip()
    requested = [place.name for place in FEATURED if place.name in request]
    fixed = [part.strip() for part in re.split(r"[；;。\n]", request) if re.search(r"\d{1,2}[:：]\d{2}|固定活动|预约", part)]
    if request:
        answer = await _model("从武汉旅行要求中提取明确的必去地点与固定时间活动。只返回 JSON 对象，格式为 {\"mustVisit\": [\"地点名\"], \"fixedEvents\": [\"原文片段\"]}。不要猜测地点，不要补充用户没说的事项。", request)
        if answer:
            try:
                parsed = json.loads(answer.strip().removeprefix("```json").removesuffix("```").strip())
                requested = list(dict.fromkeys([*requested, *[name.strip() for name in parsed.get("mustVisit", []) if isinstance(name, str) and 1 <= len(name.strip()) <= 30]]))[:8]
                fixed = list(dict.fromkeys([*fixed, *[item.strip() for item in parsed.get("fixedEvents", []) if isinstance(item, str) and 1 <= len(item.strip()) <= 100]]))[:10]
            except (ValueError, AttributeError, TypeError):
                pass
    return {"requested_names": requested, "fixed_events": fixed}


async def select_places(state: PlanState) -> dict:
    snapshot = state["snapshot"]
    seen: set[str] = set()
    selected: list[Place] = []
    requested_places: list[Place] = []
    missing: list[str] = []
    for name in state.get("requested_names", []):
        known = next((place for place in [*snapshot.pendingPlaces, *snapshot.savedPlaces, *FEATURED] if place.name == name), None)
        if known:
            requested_places.append(known)
        else:
            results, _ = await search_places(name)
            match = next((place for place in results if place.name == name), None)
            if match:
                requested_places.append(match)
            else:
                missing.append(name)
    for place in [*requested_places, *snapshot.pendingPlaces, *snapshot.savedPlaces, *[stop.place for day in snapshot.itinerary for stop in day.stops], *FEATURED]:
        if place.placeId not in seen:
            selected.append(place)
            seen.add(place.placeId)
    warnings: list[str] = []
    if missing:
        warnings.append("未找到明确要求的地点：" + "、".join(missing) + "。请先搜索并加入待安排后重新规划。")
    if state.get("fixed_events"):
        warnings.append("固定时间活动尚未自动排入路线：" + "；".join(state["fixed_events"][:3]) + "。请在采用前核对。")
    if len(requested_places) > snapshot.preferences.days * 4:
        warnings.append("必去地点超过当前天数可安排的数量，部分地点未纳入方案。请增加天数。")
    if snapshot.preferences.requirements.strip() and not warnings:
        warnings.append("个性化要求已用于选点；请在采用前核对路线是否满足你的偏好。")
    if not snapshot.preferences.startDate:
        warnings.append("未填写出发日期，行程按第 1 天起展示；开放状态无法按日期核实。")
    return {"places": selected[:min(12, snapshot.preferences.days * 4)], "warnings": warnings}


def _time_label(start: datetime | None) -> str:
    return start.strftime("%H:%M") if start else "待核实"


async def _build_day(places: list[Place], label: str, mode: str, pace: str) -> DayPlan:
    visit = 75 if pace == "紧凑" else 120 if pace == "舒缓" else 90
    legs: list[Leg] = []
    stops: list[Stop] = []
    clock: datetime | None = datetime.combine(date.today(), time(9, 30))
    warnings = ["开放时间、票价与预约规则尚未从官方渠道核实，请出行前确认。"]
    for index, place in enumerate(places):
        if index:
            origin = places[index - 1].location
            route_mode = "walking" if mode == "mixed" and abs(origin.lng - place.location.lng) + abs(origin.lat - place.location.lat) < 0.018 else "transit" if mode == "mixed" else mode
            route = await calculate_route(origin, place.location, route_mode)
            legs.append(Leg(fromStopId=stops[-1].id, toStopId="", mode={"walking": "步行", "transit": "公共交通", "driving": "驾车"}[route_mode], **route))
            if route["minutes"] is None:
                clock = None
                warnings.append(f"{places[index - 1].name} → {place.name} 的交通耗时未知。")
            elif clock:
                clock += timedelta(minutes=route["minutes"])
        stop = Stop(id=uuid4().hex, place=place, arrival=_time_label(clock), departure=_time_label(clock + timedelta(minutes=visit) if clock else None), minutes=visit, warning="开放与预约信息未核实")
        stops.append(stop)
        if legs and not legs[-1].toStopId:
            legs[-1].toStopId = stop.id
        if clock:
            clock += timedelta(minutes=visit)
            if index == 1:
                clock += timedelta(minutes=60)
    return DayPlan(id=uuid4().hex, date=label, stops=stops, legs=legs, transportMinutes=sum(leg.minutes or 0 for leg in legs) if all(leg.minutes is not None for leg in legs) else None, sightseeingMinutes=len(stops) * visit, meals=["12:00 前后安排午餐，具体地点由用户确认"] if len(stops) > 1 else [], budgetNote="门票、餐饮和交通费用尚未核实；请按实际价格预留预算。", warnings=list(dict.fromkeys(warnings)))


async def create_candidates(state: PlanState) -> dict:
    snapshot = state["snapshot"]
    source = state["places"]
    days = snapshot.preferences.days
    per_day = min(4, max(1, (len(source) + days - 1) // days))
    mode = "driving" if "驾" in snapshot.preferences.transport or "打车" in snapshot.preferences.transport else "mixed"
    base_date = date.fromisoformat(snapshot.preferences.startDate) if snapshot.preferences.startDate else None
    candidates = []
    for variant in range(2):
        if variant == 0:
            sequence = source
            title, subtitle = "经典武汉", "优先安排已选地点"
            rationale = "优先纳入待安排与收藏地点，再补充武汉代表地点。"
        else:
            # A genuinely different order, with a compact East Lake cluster first.
            sequence = sorted(source, key=lambda p: (abs(p.location.lng - 114.38) + abs(p.location.lat - 30.56), p.name))
            title, subtitle = "湖光人文", "先游东湖周边"
            rationale = "先集中游览东湖周边，再逐步转向老城；跨区交通仍需核对。"
        day_plans = []
        for index in range(days):
            group = sequence[index * per_day:(index + 1) * per_day]
            label = (base_date + timedelta(days=index)).isoformat() if base_date else f"第 {index + 1} 天"
            if group:
                day_plans.append(await _build_day(group, label, mode, snapshot.preferences.pace))
            else:
                day_plans.append(DayPlan(id=uuid4().hex, date=label, stops=[], legs=[], transportMinutes=0, sightseeingMinutes=0, meals=["自由安排"], budgetNote="费用未估算。", warnings=["地点数量不足，此日保留为空白日。请增加地点后重新规划。"]))
        candidates.append(Candidate(id=uuid4().hex, title=title, subtitle=subtitle, rationale=rationale, days=day_plans, warnings=state.get("warnings", [])))
    return {"candidates": candidates}


graph = (StateGraph(PlanState)
         .add_node("analyze_requirements", analyze_requirements)
         .add_node("select_places", select_places)
         .add_node("create_candidates", create_candidates)
         .add_edge(START, "analyze_requirements")
         .add_edge("analyze_requirements", "select_places")
         .add_edge("select_places", "create_candidates")
         .add_edge("create_candidates", END)
         .compile())


async def plan(snapshot: Snapshot, emit) -> list[Candidate]:
    await emit("progress", {"label": "整理地点与旅行约束", "percent": 18})
    # LangGraph executes the planner's typed selection and routing stages.
    result = await graph.ainvoke({"snapshot": snapshot})
    await emit("progress", {"label": "检查路线、时长与待核实信息", "percent": 82})
    for candidate in result["candidates"]:
        await emit("candidate_created", {"candidate": candidate.model_dump()})
        await asyncio.sleep(0)
    return result["candidates"]
