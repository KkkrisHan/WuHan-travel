"""Build complete, route-backed itinerary documents from verified POI records."""
from __future__ import annotations

import asyncio
import json
import math
import re
from datetime import date, datetime, timedelta
from uuid import uuid4

import httpx
from fastapi import HTTPException

from .assistant import _model
from .config import settings
from .models import Place, Point, DayPlan, Stop, Leg
from .places import calculate_route, search_places


# These are generic POI categories, not unambiguous named destinations.
_GENERIC_POI_NAMES = {
    "口袋公园", "公园", "街心公园", "社区公园", "小游园",
    "游园", "广场", "景点", "景区", "观景点", "休息亭",
}


def _is_specific_destination(place: Place) -> bool:
    name = re.sub(r"\s+", "", place.name).strip()
    return (
        bool(name)
        and name not in _GENERIC_POI_NAMES
        and bool(place.placeId.strip())
        and place.location is not None
    )


def _meters(a: Point, b: Point) -> float:
    return math.hypot((a.lng-b.lng)*95000, (a.lat-b.lat)*111000)


def _route_text(route: dict, mode: str) -> str:
    label = {"walking": "步行", "driving": "驾车", "transit": "公交"}[mode]
    if route.get("minutesMin") is not None:
        return f"预计{label}约 {route['minutesMin']}–{route['minutesMax']} 分钟（距离估算，非实时路线）"
    return f"预计{label}约 {route['minutes']} 分钟（高德路线规划，实际等待与拥堵另计）"


async def _lodging_point(name: str) -> Point:
    found, _ = await search_places(name)
    if found:
        return found[0].location
    async with httpx.AsyncClient(timeout=12) as client:
        response = await client.get("https://restapi.amap.com/v3/geocode/geo", params={"key": settings.amap_web_service_key, "address": name, "city": "武汉"})
        response.raise_for_status()
        data = response.json()
    try:
        lng, lat = map(float, data["geocodes"][0]["location"].split(","))
        return Point(lng=lng, lat=lat)
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise HTTPException(422, "无法定位住宿地点，请输入更完整的武汉酒店名称或地址") from exc


async def build_itinerary(request) -> dict:
    if not settings.amap_web_service_key:
        raise HTTPException(503, "未配置高德 Web 服务密钥，无法核实推荐地点")
    try:
        first_date = date.fromisoformat(request.startDate) if request.startDate else None
    except ValueError as exc:
        raise HTTPException(422, "开始日期格式无效") from exc
    mode = "driving" if any(word in request.transport for word in ("打车", "驾车", "出租")) or "多打车" in request.requirements else "transit" if "公交" in request.transport else "walking"
    required = list({p.placeId: p for p in request.mustVisitPlaces}.values())
    # Search POIs, not prose: only map records may become stops.
    queries = ["武汉 景点", "武汉 博物馆 公园", "武昌 首义 景点", "东湖 景点", "汉口 景点", "汉阳 景点"]
    if required:
        queries.append(required[0].name + " 附近 景点")
    pool: list[Place] = []
    search_results = await asyncio.gather(*(search_places(query) for query in queries), return_exceptions=True)
    for result in search_results:
        if not isinstance(result, BaseException):
            pool.extend(result[0])
    if not pool and not required:
        raise HTTPException(502, "高德地点搜索未返回可用结果")
    used_names = {p.name.strip().lower() for p in required}
    unique: list[Place] = []
    for place in pool:
        name = place.name.strip().lower()
        if (_is_specific_destination(place)
            and name not in used_names and place.placeId not in {p.placeId for p in unique}
            and place.category in ("风景名胜", "科教文化服务", "历史地标", "自然风光", "博物馆", "街区")
            and not any(word in place.name for word in ("暂停开放", "停车场", "售票处", "服务中心", "驿站"))
            and place.name not in ("观景点", "休息亭") and "-" not in place.name
            and "休息亭" not in place.name
            and not any(place.name.startswith(p.name) or p.name.startswith(place.name) for p in required)):
            used_names.add(name)
            unique.append(place)
    anchors = [p.location for p in required] or [Point(lng=114.3055, lat=30.5928)]
    unique.sort(key=lambda p: min(_meters(p.location, anchor) for anchor in anchors))
    # AI ranks a bounded set of real POIs; unrecognized IDs are discarded.
    preference = await _model(
        '你是武汉行程顾问。从提供的真实高德 POI 中选择适合要求和地理邻近性的地点，只返回 JSON 数组，元素为 placeId，不得创造地点。最多选指定数量。',
        json.dumps({"days": request.days, "requirements": request.requirements, "transport": request.transport,
                    "mustVisit": [p.name for p in required], "count": max(request.days*2-len(required), request.days),
                    "candidates": [{"placeId": p.placeId, "name": p.name, "category": p.category, "lng": p.location.lng, "lat": p.location.lat} for p in unique[:25]]}, ensure_ascii=False),
    ) if unique else None
    preferred_ids: list[str] = []
    if preference:
        try:
            decoded = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", preference.strip()))
            if isinstance(decoded, list):
                preferred_ids = [x for x in decoded if isinstance(x, str)]
        except ValueError:
            pass
    lookup = {p.placeId: p for p in unique}
    preferred = [lookup[pid] for pid in preferred_ids if pid in lookup]
    recommendations: list[Place] = []
    for p in preferred + unique:
        if min(_meters(p.location, anchor) for anchor in anchors) > 18000:
            continue
        if any(p.name.startswith(x.name) or x.name.startswith(p.name) for x in recommendations):
            continue
        if any(_meters(p.location, x.location) < 250 for x in recommendations):
            continue
        if p.placeId not in {x.placeId for x in recommendations}:
            recommendations.append(p)
        if len(required) + len(recommendations) >= request.days * 2:
            break
    selected = required + recommendations
    region_groups: list[list[Place]] | None = None
    if request.days == 4 and len(required) == 2 and _meters(required[0].location, required[1].location) < 2000:
        # Four distinct Wuhan districts, with each day's stops chosen from live POIs.
        region_groups = [required[:]]
        regional = []
        for center in (Point(lng=114.38, lat=30.56), Point(lng=114.293, lat=30.58), Point(lng=114.27, lat=30.55)):
            choices = [p for p in unique if p.placeId not in {x.placeId for x in regional + required}
                       and _meters(p.location, center) < 4500
                       and all(_meters(p.location, x.location) >= 350 for x in regional + required)]
            choices.sort(key=lambda p: _meters(p.location, center) - (250 if p.placeId in preferred_ids else 0)
                         + (700 if any(w in p.name for w in ("旧址", "纪念碑", "鼓楼洞", "观景点")) else 0))
            pair = []
            for p in choices:
                if all(_meters(p.location, x.location) >= 350 and not (p.name.startswith(x.name) or x.name.startswith(p.name)) for x in pair):
                    pair.append(p)
                if len(pair) == 2:
                    break
            if len(pair) < 2:
                region_groups = None
                break
            regional.extend(pair)
            region_groups.append(pair)
        if region_groups:
            recommendations = regional
            selected = required + recommendations
    if len(selected) < request.days:
        raise HTTPException(502, "可用的真实地点不足以覆盖所选天数，请缩短行程或补充必去地点")
    # Pair nearby stops before ordering days so a day does not cross the city merely
    # because a global nearest-neighbour path crossed a river at a day boundary.
    remaining = selected[:]
    day_places: list[list[Place]] = region_groups or []
    while remaining and not region_groups:
        group_size = math.ceil(len(remaining) / (request.days - len(day_places)))
        if not day_places and required:
            first = required[0]
        elif len(remaining) > 1:
            first = min(remaining, key=lambda a: min(_meters(a.location, b.location) for b in remaining if b is not a))
        else:
            first = remaining[0]
        remaining.remove(first)
        group = [first]
        while remaining and len(group) < group_size:
            nxt = min(remaining, key=lambda p: min(_meters(p.location, x.location) for x in group))
            remaining.remove(nxt)
            group.append(nxt)
        day_places.append(group)
    if len(day_places) != request.days:
        raise HTTPException(502, "行程分天失败")
    if len(day_places) > 1:
        ordered_groups = [day_places.pop(0)]
        while day_places:
            previous = ordered_groups[-1][-1].location
            nxt = min(day_places, key=lambda group: min(_meters(previous, p.location) for p in group))
            day_places.remove(nxt)
            ordered_groups.append(nxt)
        day_places = ordered_groups
    if any(not items for items in day_places):
        raise HTTPException(502, "行程分天失败")
    lodging = request.lodging.strip() if request.lodging else ""
    lodging_point = getattr(request, "lodgingLocation", None) or (await _lodging_point(lodging) if lodging else None)
    lines = [f"# 武汉{request.days}日旅行计划", "", "## 旅行概览", "",
             f"- **日期**：{first_date.isoformat() if first_date else '未指定'}" + (f" 至 {(first_date+timedelta(days=request.days-1)).isoformat()}" if first_date else ""),
             f"- **人数**：{request.people} 人", f"- **节奏与要求**：{request.requirements or '按舒缓节奏安排'}",
             f"- **交通偏好**：{request.transport}", f"- **住宿/出发地**：{lodging or '未提供；每天从首个地点开始计算路线'}", "",
             "> 开放、票价与预约请以地点详情中的最新来源和出行当日公告为准。高德路线时间是规划值，不含等车与临时拥堵。", ""]
    required_ids = {p.placeId for p in required}
    seen_ids: set[str] = set()
    route_sources: set[str] = set()
    structured_days: list[dict] = []
    for day_index, items in enumerate(day_places):
        day_date = (first_date + timedelta(days=day_index)).isoformat() if first_date else f"第{day_index+1}天（日期未定）"
        lines += [f"## Day {day_index+1} · {day_date}", "", f"### 当日主题：{items[0].category}与邻近区域", "",
                  "| 时段 | 地点与类别 | 安排 | 交通 |", "| --- | --- | --- | --- |"]
        current_time = datetime.combine(date.today(), datetime.strptime("09:00", "%H:%M").time())
        day_stop_ids = [uuid4().hex for _ in items]
        day_stops: list[Stop] = []
        day_legs: list[Leg] = []
        day_transport_minutes = 0
        if lodging_point:
            route = await calculate_route(lodging_point, items[0].location, mode)
            route_sources.add(route["source"])
            lines.append(f"| 出发 | {lodging} | 住宿出发 | {_route_text(route, mode)} |")
        for index, place in enumerate(items):
            if place.placeId in seen_ids:
                raise HTTPException(502, "地点重复分配，行程未保存")
            seen_ids.add(place.placeId)
            stop_minutes = 20 if any(word in place.name for word in ("租赁点", "售票处", "服务中心")) or place.category == "生活服务" else 90 if "慢" in request.requirements or "舒缓" in request.requirements else 75
            previous_service = index > 0 and (items[index-1].category == "生活服务" or "租赁点" in items[index-1].name)
            if index and stop_minutes > 30 and not previous_service and (current_time.hour >= 12 or index == len(items)-1) and not any("午餐" in row for row in lines[-8:]):
                lunch_start = max(current_time, datetime.combine(date.today(), datetime.strptime("12:00", "%H:%M").time()))
                lunch_end = lunch_start + timedelta(minutes=75)
                lines.append(f"| 午餐与休息 | {items[index-1].name}周边 | {lunch_start:%H:%M}–{lunch_end:%H:%M}；就近用餐和休息 | 步行或短途移动，未指定餐厅 |")
                current_time = lunch_end
            arrival = current_time
            departure = arrival + timedelta(minutes=stop_minutes)
            marker = "【必去】" if place.placeId in required_ids else "【AI推荐】"
            phase = "上午" if arrival.hour < 12 else "下午" if arrival.hour < 18 else "晚间"
            action = "停留办理或查看约" if stop_minutes == 20 else "建议游览约"
            lines.append(f"| {phase} | {marker}{place.name}（{place.category}） | {arrival:%H:%M}–{departure:%H:%M}；{action} {stop_minutes} 分钟 | {('首站' if index == 0 else transport_text)} |")
            day_stops.append(Stop(id=day_stop_ids[index], place=place, arrival=f"{arrival:%H:%M}", departure=f"{departure:%H:%M}", minutes=stop_minutes))
            current_time = departure
            if index < len(items)-1:
                leg_mode = "walking" if _meters(place.location, items[index+1].location) < 800 else mode
                route = await calculate_route(place.location, items[index+1].location, leg_mode)
                route_sources.add(route["source"])
                day_legs.append(Leg(fromStopId=day_stop_ids[index], toStopId=day_stop_ids[index+1], mode=leg_mode, minutes=route["minutes"], minutesMin=route.get("minutesMin"), minutesMax=route.get("minutesMax"), distanceMeters=route.get("distanceMeters"), source=route["source"], polyline=route.get("polyline") or []))
                day_transport_minutes += route["minutes"]
                transport_text = _route_text(route, leg_mode)
                current_time += timedelta(minutes=route["minutes"])
        if not any("午餐" in row for row in lines[-len(items)-2:]):
            lunch_start = max(current_time, datetime.combine(date.today(), datetime.strptime("12:00", "%H:%M").time()))
            lunch_end = lunch_start + timedelta(minutes=75)
            lines.append(f"| 午餐与休息 | {items[-1].name}周边 | {lunch_start:%H:%M}–{lunch_end:%H:%M}；就近用餐和休息 | 就近安排 |")
        lines.append("| 晚间 | 自由活动 | 休息或在最后一站周边自由探索，不追加固定景点 | 自行决定 |")
        if lodging_point:
            route = await calculate_route(items[-1].location, lodging_point, mode)
            route_sources.add(route["source"])
            lines.append(f"| 返回住宿 | {lodging} | 当日结束后返回 | {_route_text(route, mode)} |")
        lines += ["", "### 当日路线", "", " → ".join(p.name for p in items) + "。地点按地理邻近性排序，跨江或拥堵时请以当日导航为准。", "",
                  "### 注意事项", "", "- 各地点开放、票价与预约信息请在出发前查看地点详情的来源和核查时间。",
                  "- 交通时间为高德规划值或标明区间的距离估算；实际打车等待、路况和排队需另留弹性。", ""]
        structured_days.append(DayPlan(id=uuid4().hex, date=day_date, stops=day_stops, legs=day_legs, transportMinutes=day_transport_minutes, sightseeingMinutes=sum(stop.minutes for stop in day_stops), meals=["午餐与休息"], budgetNote="费用以当天官方票价与实际交通支出为准", warnings=["地点开放、预约和交通时间请在出发前核实。"]).model_dump())
    missing = [p.name for p in required if p.placeId not in seen_ids]
    if missing or len(day_places) != request.days or any(not day for day in day_places):
        raise HTTPException(502, "行程完整性校验失败：" + "、".join(missing))
    lines += ["## 行程结束与核查清单", "", "- 出发前再次确认景区当天公告、预约入口与票价。", "- 高峰期给餐饮、打车与过江交通预留额外时间。", ""]
    return {"title": f"武汉{request.days}日旅行计划", "markdown": "\n".join(lines),
            "recommendedPlaces": [p.model_dump() for p in recommendations], "routeSource": "；".join(sorted(route_sources)),
            "dayPlans": structured_days}
