"""Model strategy and POI decisions; all returned IDs must belong to map results."""
from __future__ import annotations

import asyncio
import json
import re

from .models import Place
from .trip_intent import TripIntent, meters


def decode(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


async def strategy(intent: TripIntent, model) -> None:
    system = ("你是旅行规划师。根据完整输入制定可执行的体验策略。同行关系只是上下文，不替代明确兴趣。"
              "综合权衡体验、节奏、预算、交通、时间和必去地点。只返回 JSON 对象："
              '{"hardConstraints":["用户明确指定的必须满足条件"],"softGoals":["用户偏好的体验目标"],'
              '"avoid":["用户明确不接受的安排"],"priorities":["具体目标"],"dayThemes":["每天的体验方向"],'
              '"searchQueries":["武汉 地点类型或区域"],"timePreferences":{"地点名":"morning|afternoon|evening"},'
              '"conflicts":["无法同时满足的明确要求"]}。'
              "不要编造地点或开放时间。conflicts 只列用户明确要求之间真正互斥的情况，"
              "不要把一般提醒、天气推测或尚未查到的开放资料写成需求冲突。")
    try:
        data = decode(await asyncio.wait_for(model(system, json.dumps(intent.context(), ensure_ascii=False)), 23))
    except asyncio.TimeoutError:
        data = {}
    intent.priorities = [x[:90] for x in data.get("priorities", []) if isinstance(x, str)][:6]
    intent.day_themes = [x[:60] for x in data.get("dayThemes", []) if isinstance(x, str)][:intent.days]
    intent.search_queries = [x[:60] for x in data.get("searchQueries", []) if isinstance(x, str)][:5]
    preference = data.get("timePreferences")
    intent.time_preferences = {str(k)[:60]: v for k, v in preference.items()
                               if v in ("morning", "afternoon", "evening")} if isinstance(preference, dict) else {}
    intent.conflicts = [x[:120] for x in data.get("conflicts", []) if isinstance(x, str)][:4]
    intent.hard_constraints = [x[:100] for x in data.get("hardConstraints", []) if isinstance(x, str)][:8]
    intent.soft_goals = [x[:100] for x in data.get("softGoals", []) if isinstance(x, str)][:8]
    intent.avoid = [x[:100] for x in data.get("avoid", []) if isinstance(x, str)][:8]


async def select_days(intent: TripIntent, required: list[Place], pool: list[Place], model, hotel=None, route_hints=None):
    lookup = {p.placeId: p for p in pool}
    payload = {"intent": intent.context(), "hotelLocation": hotel.model_dump() if hotel else None,
               "knownTravelMinutes": route_hints or [],
               "strategy": {"priorities": intent.priorities,
               "dayThemes": intent.day_themes, "timePreferences": intent.time_preferences,
               "hardConstraints": intent.hard_constraints, "softGoals": intent.soft_goals,
               "avoid": intent.avoid},
               "candidates": [{"placeId": p.placeId, "name": p.name, "category": p.category,
                               "address": p.address, "lng": p.location.lng, "lat": p.location.lat}
                              for p in pool[:55]]}
    system = ("从已核实的候选地点组织逐日体验。全部必去地点必须出现一次。"
              "根据明确约束、兴趣、节奏、交通与体验目标决定地点数量和顺序，避开用户明确不接受的安排。knownTravelMinutes 是已查得的路线耗时，"
              "分天时应实际利用它，减少酒店往返和跨区移动。只返回 JSON："
              '{"days":[[{"placeId":"候选ID","minutes":90,"timeWindow":"morning|afternoon|evening|any",'
              '"experience":"此处具体活动及理由"}]]}。days 数量等于旅行天数。'
              "experience 只描述地点本身可做的活动，不描述从别处怎么来、具体时段或价格；"
              "只用给定 ID，不创造路线、时间或事实。")
    try:
        user = json.dumps(payload, ensure_ascii=False)
        data = decode(await asyncio.wait_for(model(system, user), 26))
        if not isinstance(data.get("days"), list):
            data = decode(await asyncio.wait_for(model(system + "上次输出不是有效 JSON；这次仅返回合法 JSON。", user), 18))
    except asyncio.TimeoutError:
        data = {}
    groups: list[list[Place]] = [[] for _ in range(intent.days)]
    details: dict[str, dict] = {}
    used: set[str] = set()
    if isinstance(data.get("days"), list):
        for day, entries in enumerate(data["days"][:intent.days]):
            if not isinstance(entries, list):
                continue
            max_day = 3 if intent.pace == "舒缓" else 4 if intent.pace == "适中" else 5
            for entry in entries[:max_day]:
                if not isinstance(entry, dict):
                    continue
                pid = entry.get("placeId")
                if pid in lookup and pid not in used:
                    place = lookup[pid]
                    if any(place.name.startswith(old.name) or old.name.startswith(place.name)
                           for group in groups for old in group if meters(place.location, old.location) < 350):
                        continue
                    groups[day].append(lookup[pid])
                    details[pid] = entry
                    used.add(pid)
    for day in range(intent.days):
        groups[day] = [p for p in groups[day] if not any(
            p.placeId != r.placeId and (p.name.startswith(r.name) or r.name.startswith(p.name))
            and meters(p.location, r.location) < 350 for r in required)]
    used = {p.placeId for group in groups for p in group}
    for place in required:
        if place.placeId not in used:
            target = min(range(intent.days), key=lambda d: (len(groups[d]),
                         min((meters(place.location, p.location) for p in groups[d]), default=0)))
            groups[target].append(place)
            used.add(place.placeId)
    # Minimum useful density follows the available visit-time budget, not a group label.
    visit_budget = 300 if intent.pace == "舒缓" else 420 if intent.pace == "适中" else 540
    typical_visit = 125 if intent.pace == "舒缓" else 110 if intent.pace == "适中" else 95
    desired = max(1, min(4, visit_budget // typical_visit))
    for target_count in range(1, desired + 1):
        for day in range(intent.days):
            if len(groups[day]) >= target_count:
                continue
            available = [p for p in pool if p.placeId not in used and
                         all(not (p.name.startswith(old.name) or old.name.startswith(p.name))
                             or meters(p.location, old.location) >= 350
                             for group in groups for old in group)]
            if available:
                anchor = groups[day][-1].location if groups[day] else (required[0].location if required else pool[0].location)
                pick = min(available, key=lambda p: meters(p.location, anchor))
                groups[day].append(pick)
                used.add(pick.placeId)
            elif not groups[day]:
                donor = max(range(intent.days), key=lambda d: len(groups[d]))
                if len(groups[donor]) > 1:
                    groups[day].append(groups[donor].pop())
    return groups, details
