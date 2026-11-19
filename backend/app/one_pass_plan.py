"""One authoritative AI itinerary decision, based on collected facts.

No automatic insertion, deletion, regrouping, retry, or second model review is
allowed after the model has chosen its day-by-day sequence.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

from fastapi import HTTPException

from .experience_plan import decode
from .models import Place, Point
from .trip_intent import TripIntent, meters
from .trip_schedule import visit_minutes
from .poi_knowledge.store import get_opening_sessions


async def decide_days_once(
    intent: TripIntent,
    required: list[Place],
    candidates: list[Place],
    opening_hours: dict[str, str],
    hotel: Point | None,
    route_hints: list[dict],
    model,
) -> tuple[list[list[Place]], dict[str, dict]]:
    if len(candidates) < intent.days:
        raise HTTPException(422, "可核实地点不足以覆盖旅行天数，请缩短天数或调整条件")

    max_stops = 3 if intent.pace == "舒缓" else 4 if intent.pace == "适中" else 5
    day_budget = 570 if intent.pace == "舒缓" else 630 if intent.pace == "适中" else 690

    def approximate_minutes(a: Point, b: Point) -> int:
        """Only a rough pre-planning screening estimate, NOT a map direction."""
        distance_km = meters(a, b) / 1000
        if intent.modes == ("walking",):
            return round(distance_km * 17)
        if distance_km <= 1.1 and "walking" in intent.modes:
            return max(3, round(distance_km * 17))
        # Wuhan cross-river transfers often have higher transfer overhead.
        crosses_river = (a.lng < 114.315) != (b.lng < 114.315)
        return round(15 + distance_km * 6 + (12 if crosses_river else 0))

    data = {
        "preferences": intent.context(),
        "dailyTimeBudgetMinutes": day_budget,
        "mealMinutesUnlessRestaurantAlreadyScheduled": 60,
        "travelEstimatesAreRoughNotOfficial": True,
        "hotelLocation": hotel.model_dump() if hotel else None,
        "knownTravel": route_hints,
        "maxStopsPerDay": max_stops,
        "candidates": [
            {
                "placeId": p.placeId,
                "name": p.name,
                "category": p.category,
                "address": p.address,
                "location": p.location.model_dump(),
                "openingHours": opening_hours.get(p.placeId, "未知，不能假定开放"),
                "closedDates": [
                    (date.fromisoformat(intent.start_date) + timedelta(days=offset)).isoformat()
                    for offset in range(intent.days)
                    if intent.start_date and get_opening_sessions(
                        p.placeId, date.fromisoformat(intent.start_date) + timedelta(days=offset))["status"] == "closed"
                ],
                "required": p.placeId in {r.placeId for r in required},
                "usualVisitMinutes": visit_minutes(p, intent.pace),
                "approxHotelOneWayMinutes": approximate_minutes(hotel, p.location) if hotel else None,
                "nearbyPlaceIds": [
                    other.placeId for other in candidates
                    if other.placeId != p.placeId and meters(p.location, other.location) <= 2200
                ][:6],
            } for p in candidates
        ],
    }
    system = (
        "你是武汉行程规划师。用户偏好、必去地点、地图候选地点、已查询的开放信息和交通提示均已给出。"
        "请在本次响应中一次性决定完整逐日行程和地点访问顺序；后续程序只执行排程校验，不会替你添加、"
        "删除、重排地点，也不会请你再规划。务必在本次规划中统筹住宿、地理位置、路线耗时、"
        "用餐、开放时段、同行需求、兴趣和每日日程负担。"
        "dailyTimeBudgetMinutes 是一天允许的总负担，必须同时包含所有地点停留、"
        "酒店出发和返程、地点间交通、以及独立午餐60分钟。餐厅作为午餐时不另外加60分钟。"
        "candidates 的 usualVisitMinutes 是各地点通常需要的停留时长，"
        "approxHotelOneWayMinutes 是地理距离初筛估计而非真实路线；"
        "knownTravel 中有 minutes 的路线则为已查询的高德结果。"
        "不能把三个远距离、长时间停留的景点硬塞进同一天；优先附近景点成组。"
        "每天从上午出发，尽量在19:00之前返回住宿地点。"
        "必须包含每个必去地点恰好一次；不同日期不要重复地点；每天至少一个地点；"
        "closedDates 是已核实闭馆的日期，绝对不能把该地点安排在这些日期；"
        f"每日至多 {max_stops} 个停靠地点；餐厅算作停靠地点，不能在活动文案中再安排未列出的餐厅。"
        "优先同区域游览、减少跨江；如果是舒缓节奏，避免因凑数拉长交通时间。"
        "如果用户喜欢美食且候选中有餐厅，优先选用符合地域与路线的真实餐厅。"
        "开放信息未知不能写成已证实。timeWindow 的 morning/afternoon/evening 是到访偏好，"
        "不能创造时间、门票、预约和具体交通数据。experience 只写在该地点停留期间能做的事情，"
        "绝不包含下一站、另一地点、额外用餐、时间长度或具体时钟。"
        "只输出一个 JSON 对象，不要 Markdown、不要解释："
        '{"days":[[{"placeId":"已有候选ID","minutes":90,'
        '"timeWindow":"morning|afternoon|evening|any",'
        '"experience":"仅限当前地点的实际体验"}]],'
        '"priorities":["整趟旅行的体验策略"],"dayThemes":["每一天的主题"],'
        '"conflicts":["无法满足的明确要求"]}。'
        "days 的数组长度必须严格等于旅行天数。所有 placeId 必须来自 candidates，"
        "且不能重复。返回空 days、缺必去地点或错误 JSON 将直接失败，不会自动修补。"
    )
    try:
        raw = await asyncio.wait_for(model(system, json.dumps(data, ensure_ascii=False)), timeout=35)
    except asyncio.TimeoutError as exc:
        raise HTTPException(504, "AI 单次规划超时；本次未生成行程，也未擅自补全") from exc

    output = decode(raw)
    planned = output.get("days")
    if not isinstance(planned, list) or len(planned) != intent.days:
        raise HTTPException(502, "AI 规划返回的天数或 JSON 格式不正确，未执行自动补全")

    lookup = {p.placeId: p for p in candidates}
    seen: set[str] = set()
    groups: list[list[Place]] = []
    details: dict[str, dict] = {}
    for day_index, entries in enumerate(planned):
        if not isinstance(entries, list) or not 1 <= len(entries) <= max_stops:
            raise HTTPException(422, f"AI 第 {day_index + 1} 天安排数量不符合节奏或为空，未自动修改")
        day: list[Place] = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("placeId"), str):
                raise HTTPException(502, f"AI 第 {day_index + 1} 天包含无效的地点格式")
            pid = entry["placeId"]
            if pid not in lookup:
                raise HTTPException(422, f"AI 使用了未核实的地点 ID：{pid}")
            if pid in seen:
                raise HTTPException(422, f"AI 重复安排了地点：{lookup[pid].name}")
            place = lookup[pid]
            if any(
                (place.name.startswith(old.name) or old.name.startswith(place.name))
                and meters(place.location, old.location) < 350
                for group in groups for old in group
            ):
                raise HTTPException(422, f"AI 重复安排了同一地点的不同地图条目：{place.name}")
            if not isinstance(entry.get("experience"), str):
                raise HTTPException(502, f"AI 未给 {place.name} 提供活动说明")
            preference = entry.get("timeWindow", "any")
            if preference not in ("morning", "afternoon", "evening", "any"):
                raise HTTPException(422, f"AI 提供了无效的到访时间偏好：{place.name}")
            details[pid] = {
                "experience": entry["experience"][:180],
                "timeWindow": preference,
            }
            if isinstance(entry.get("minutes"), int) and 20 <= entry["minutes"] <= 240:
                details[pid]["minutes"] = entry["minutes"]
            day.append(place)
            seen.add(pid)
        groups.append(day)

    missing = [place.name for place in required if place.placeId not in seen]
    if missing:
        raise HTTPException(422, "AI 规划遗漏必去地点：" + "、".join(missing) + "；未自动补入")
    if isinstance(output.get("conflicts"), list):
        intent.conflicts = [x[:120] for x in output["conflicts"] if isinstance(x, str)][:5]
    if isinstance(output.get("priorities"), list):
        intent.priorities = [x[:90] for x in output["priorities"] if isinstance(x, str)][:6]
    if isinstance(output.get("dayThemes"), list):
        intent.day_themes = [x[:60] for x in output["dayThemes"] if isinstance(x, str)][:intent.days]
    return groups, details
