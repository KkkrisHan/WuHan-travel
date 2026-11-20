"""Bounded schedule search and hard-constraint checks."""
from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from datetime import date, timedelta
from uuid import uuid4

from fastapi import HTTPException

from .models import DayPlan, Leg, Place, Point, Stop
from .opening_hours import closed_for_date, dated_windows, opening_window, preferred_time
from .trip_intent import Router, TripIntent
from .trip_intent import meters


def clock(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def minute(value: str) -> int:
    hour, minutes = map(int, value.split(":"))
    return hour * 60 + minutes


def visit_minutes(place: Place, pace: str, suggestion: object = None) -> int:
    if place.category == "生活服务":
        return 20
    if place.category == "餐饮服务":
        base = 70 if pace == "舒缓" else 60 if pace == "适中" else 45
        return max(40, min(105, round((base + suggestion) / 2))) if isinstance(suggestion, int) else base
    base = 75 if pace == "紧凑" else 110 if pace == "舒缓" else 90
    if place.category in ("博物馆", "科教文化服务", "校园"):
        base += 25
    if place.category in ("街区", "自然风光", "风景名胜") and pace == "舒缓":
        base += 15
    return max(35, min(210, round((base + suggestion) / 2))) if isinstance(suggestion, int) else base


def route_label(mode: str, route: dict) -> str:
    label = {"walking": "步行", "transit": "公共交通", "driving": "驾车"}[mode]
    estimate = "（距离估算）" if "估算" in route.get("source", "") else ""
    detail = ""
    if mode == "transit" and route.get("transfers") is not None:
        detail = f"，换乘 {route['transfers']} 次"
        if route.get("walkingMinutes"):
            detail += f"，站内外步行约 {route['walkingMinutes']} 分钟"
    return f"{label}约 {route['minutes']} 分钟{detail}{estimate}"


def experience_at_time(value: str, arrival: int, *,
                       category: str = "地点", other_places: tuple[str, ...] = ()) -> str:
    """Keep AI-written experiences inside this scheduled stop.

    A language model proposes activities before the final ordering and lunch
    are known. It must not create extra stops or meal breaks in the document.
    """
    text = re.sub(r"\s+", " ", value).strip()
    text = re.sub(r"^(?:从[^，,。]{0,55}(?:步行|乘公交|乘地铁|打车)[^，,。]{0,55}|(?:乘公交|乘地铁|打车)[^，,。]{0,55})[，,]", "", text)
    # Cut off an onward journey, separate cafe/meal, or other unscheduled
    # activity. The real stops and meal slots come from schedule_day, not prose.
    away = r"前往|转往|返回|离开|下一站|换乘|转乘|走到|移步|咖啡|奶茶|餐厅|餐馆|用餐|午餐|晚餐|早餐|小吃"
    if category == "餐饮服务":
        away = r"前往|转往|返回|离开|下一站|换乘|转乘|走到|移步"
    for name in other_places:
        if len(name) >= 2 and name in text:
            away += "|" + re.escape(name)
    transition = rf"[，,；;。]?\s*(?:随后|接着|然后|之后|最后|游览结束后|参观后|逛完后|离开后)(?=[^。；]{{0,85}}(?:{away}))"
    text = re.split(transition, text, maxsplit=1)[0]
    # An explicit clock or duration generated before scheduling is not valid
    # evidence of a reservation or a feasible activity length.
    text = re.sub(r"(?:[，,；;。]?\s*[^，,；;。]*)(?:\d{1,2}:\d{2}|\d+(?:\.\d+)?\s*(?:小时|分钟)|半天|一整天)[^，,；;。]*", "", text)
    if arrival >= 9 * 60:
        text = re.sub(r"清晨|一早|早晨", "", text)
    if arrival < 12 * 60 or arrival >= 18 * 60:
        text = re.sub(r"午后|下午", "", text)
    if arrival < 16 * 60:
        text = re.sub(r"(?:日落|黄昏|傍晚)(?:时分|时段)", "", text)
        text = re.sub(r"暮色|夕阳|晚霞|落日|日落", "景色", text)
        text = re.sub(r"傍晚|黄昏", "", text)
        text = re.sub(r"与灯光|灯光|华灯初上", "", text)
    if arrival < 18 * 60:
        text = re.sub(r"夜游|夜间|夜晚|夜景|入夜|晚上", "", text)
    return text.strip("，,；;。 ") or "在此处游览并体验当地特色"


def entry_sensitive(place: Place) -> bool:
    return place.category in ("科教文化服务", "博物馆", "风景名胜", "历史地标", "校园") or \
        (place.category != "餐饮服务" and any(
            word in place.name for word in (
                "黄鹤楼", "古德寺", "植物园", "美术馆", "寺", "博物馆",
                "步行街", "江滩", "绿道")))


async def schedule_day(day: int, places: list[Place], intent: TripIntent, details: dict[str, dict],
                       hours: dict[str, str], hotel: Point | None, router: Router,
                       first_date: date | None, _soft: bool = True,
                       all_places: list[Place] | None = None,
                       exact_order: bool = False) -> tuple[DayPlan, list[str], int]:
    when = first_date + timedelta(days=day) if first_date else None
    def windows_for(place: Place):
        status, windows = dated_windows(place.placeId, when)
        if status == "unknown":
            fallback = opening_window(hours.get(place.placeId))
            return status, [fallback] if fallback else []
        return status, windows
    # Rank a few plausible orders before requesting expensive map directions.
    orderings = [tuple(places)]
    by_close = tuple(sorted(places, key=lambda p: windows_for(p)[1][0].end
                            if windows_for(p)[1] else 24 * 60))
    if by_close not in orderings:
        orderings.append(by_close)
    if len(places) > 2:
        remaining = places[:]
        near = []
        point = hotel or places[0].location
        while remaining:
            place = min(remaining, key=lambda p: meters(point, p.location))
            remaining.remove(place)
            near.append(place)
            point = place.location
        if tuple(near) not in orderings:
            orderings.append(tuple(near))
    def order_score(order):
        points = [p.location for p in order]
        distance = sum(meters(a, b) for a, b in zip(points, points[1:]))
        if hotel:
            distance += meters(hotel, points[0]) + meters(points[-1], hotel)
        for index, place in enumerate(order):
            available = windows_for(place)[1]
            window = available[0] if available else None
            preference = preferred_time(intent.requirements, place.name) or details.get(place.placeId, {}).get("timeWindow") or intent.time_preferences.get(place.name)
            if window and window.end <= 18 * 60:
                distance += index * 2200
            if not window and entry_sensitive(place):
                distance += index * 4500
            if preference == "morning":
                distance += index * 2400
            if preference in ("evening", "night"):
                distance += (len(order) - index - 1) * 2400
            if place.category == "餐饮服务" and preference not in ("evening", "night"):
                distance += abs(index - min(1, len(order) - 1)) * 900
        return distance
    orderings = [tuple(places)] if exact_order else [min(orderings, key=order_score)]
    # Routes for a chosen order do not depend on the departure hour. Prefetch
    # them concurrently through Router's shared semaphore/cache rather than
    # waiting for every external map request serially during each schedule.
    for order in orderings:
        points = ([hotel] if hotel else []) + [p.location for p in order] + ([hotel] if hotel else [])
        await asyncio.gather(*(router.choose(a, b) for a, b in zip(points, points[1:])))
    best = None
    for order in orderings:
        for start in (9 * 60, 10 * 60, 11 * 60):
            now, lunch_done, lunch_time, walk, transport, soft_miss = start, False, None, 0, 0, 0
            rows, issues, warnings = [], [], []
            previous = hotel
            for index, place in enumerate(order):
                movement = None
                if previous:
                    movement = await router.choose(previous, place.location)
                    mode, route = movement
                    now += route["minutes"]
                    transport += route["minutes"]
                    if mode == "walking":
                        walk += route["minutes"]
                    else:
                        walk += route.get("walkingMinutes") or 0
                arrived_at = now
                minutes = visit_minutes(place, intent.pace, details.get(place.placeId, {}).get("minutes"))
                label = hours.get(place.placeId)
                session_status, available = windows_for(place)
                window = available[0] if available else None
                requested = details.get(place.placeId, {}).get("timeWindow")
                target_name = requested if requested in ("morning", "afternoon", "evening") else intent.time_preferences.get(place.name)
                explicit = preferred_time(intent.requirements, place.name)
                if explicit:
                    target_name = explicit
                elif not window and entry_sensitive(place) and target_name in ("evening", "night"):
                    # Model timeWindow is a soft preference. Unknown indoor
                    # evening entry should be placed in daytime when feasible.
                    target_name = "afternoon"
                target = {"morning": 10 * 60, "afternoon": 14 * 60, "evening": 17 * 60, "night": 18 * 60}.get(target_name)
                if available and target:
                    window = next((w for w in available if w.start <= target and target + minutes <= w.end),
                                  next((w for w in available if w.start >= target and w.start + minutes <= w.end), window))
                if target and window and target + minutes > window.end and not explicit:
                    # AI experience timing is soft; preserve a feasible visit inside verified hours.
                    target = max(window.start, window.end - minutes)
                if target:
                    now = max(now, target)
                if window:
                    now = max(now, window.start)
                if place.category == "餐饮服务":
                    if now < 11 * 60 and target_name not in ("evening", "night"):
                        now = 11 * 60 + 30
                    elif 14 * 60 < now < 17 * 60:
                        now = 17 * 60
                is_lunch_stop = place.category == "餐饮服务" and 11 * 60 <= now <= 14 * 60
                if not lunch_done:
                    if is_lunch_stop:
                        # The restaurant stop itself is lunch: never add a second meal break.
                        lunch_time = (now, now + minutes)
                        lunch_done = True
                    elif now >= 12 * 60 and (index > 0 or now >= 13 * 60):
                        # Use available waiting time first; otherwise delay the next visit.
                        meal_start = max(12 * 60, arrived_at)
                        lunch_time = (meal_start, meal_start + 60)
                        now = max(now, lunch_time[1])
                        lunch_done = True
                if session_status == "closed" or (session_status == "unknown" and label and closed_for_date(label, when)):
                    issues.append(f"{place.name} 在 {when} 闭馆或闭园")
                if window and now + minutes > window.end:
                    issues.append(f"{place.name} 到访超出开放时段：{label}")
                if window and window.last_entry is not None and now > window.last_entry:
                    issues.append(f"{place.name} 到达时间晚于最晚入园时间 {clock(window.last_entry)}")
                if not window and entry_sensitive(place):
                    warnings.append(f"{place.name} 入内时段尚未核实")
                    if now + minutes > 17 * 60 + 30:
                        issues.append(f"{place.name} 晚间入内资料不足，不能确认这一时段可游览")
                if target_name == "morning" and now >= 12 * 60:
                    if explicit:
                        issues.append(f"{place.name} 无法在上午到访")
                    else:
                        soft_miss += 1
                if target_name == "afternoon" and now >= 18 * 60:
                    if explicit:
                        issues.append(f"{place.name} 无法在下午到访")
                    else:
                        soft_miss += 1
                if label and not window and session_status != "closed":
                    warnings.append(f"{place.name} 开放时段无法明确解析")
                rows.append((place, now, now + minutes, minutes, movement))
                now += minutes
                previous = place.location
            if not lunch_done and now > 12 * 60:
                lunch_time = (now, now + 60)
                now += 60
                lunch_done = True
            return_route = None
            if hotel:
                return_route = await router.choose(order[-1].location, hotel)
                transport += return_route[1]["minutes"]
                if return_route[0] == "walking":
                    walk += return_route[1]["minutes"]
                else:
                    walk += return_route[1].get("walkingMinutes") or 0
                now += return_route[1]["minutes"]
            if now > 21 * 60:
                issues.append(f"第 {day+1} 天结束晚于 21:00")
            meal_is_stop = bool(lunch_time and any(place.category == "餐饮服务" and start <= lunch_time[0] < end
                                                for place, start, end, _, _ in rows))
            day_load = transport + sum(row[3] for row in rows) + (60 if lunch_done and not meal_is_stop else 0)
            target_load = 570 if intent.pace == "舒缓" else 630 if intent.pace == "适中" else 690
            if day_load > target_load + 60:
                issues.append(f"第 {day+1} 天累计活动与交通 {day_load} 分钟，超过可接受上限 {target_load + 60} 分钟")
            elif day_load > target_load:
                warnings.append(f"第 {day+1} 天累计活动与交通约 {day_load} 分钟，略高于{intent.pace}节奏目标 {target_load} 分钟")
            score = len(issues) * 10000 + soft_miss * 25 + transport * 1.1 + max(0, walk - 70) * 3 + max(0, now - 19 * 60) + abs(start - 10 * 60) / 10 + max(0, day_load - target_load) * 5
            if best is None or score < best[0]:
                best = (score, rows, return_route, warnings, issues, transport, walk, lunch_done, lunch_time, now)
    assert best is not None
    _, rows, return_route, warnings, issues, transport, walk, lunch_done, lunch_time, finish = best
    meal_place = next((place for place, start, end, _, _ in rows
                       if place.category == "餐饮服务" and lunch_time and start <= lunch_time[0] < end), None)
    if issues:
        if _soft:
            stripped = {pid: {key: value for key, value in info.items() if key != "timeWindow"}
                        for pid, info in details.items()}
            plain_intent = replace(intent, time_preferences={})
            return await schedule_day(day, places, plain_intent, stripped, hours, hotel, router, first_date, False, all_places)
        raise HTTPException(422, "无法满足开放或时间约束：" + "；".join(dict.fromkeys(issues)))
    stops = [Stop(id=uuid4().hex, place=p, arrival=clock(a), departure=clock(b), minutes=m,
                  warning="入内时段尚未核实" if p.placeId not in hours and entry_sensitive(p) else None)
             for p, a, b, m, _ in rows]
    legs = []
    for index in range(1, len(rows)):
        mode, route = rows[index][4]
        legs.append(Leg(fromStopId=stops[index-1].id, toStopId=stops[index].id, mode=mode,
                        minutes=route["minutes"], minutesMin=route.get("minutesMin"),
                        minutesMax=route.get("minutesMax"), distanceMeters=route.get("distanceMeters"),
                        walkingMinutes=route.get("walkingMinutes"), transfers=route.get("transfers"),
                        source=route["source"], polyline=route.get("polyline") or []))
    date_label = when.isoformat() if when else f"第{day+1}天（日期未定）"
    plan = DayPlan(id=uuid4().hex, date=date_label, stops=stops, legs=legs,
                   transportMinutes=transport, sightseeingMinutes=sum(s.minutes for s in stops if s.place.category != "餐饮服务"),
                   meals=([f"午餐：{meal_place.name}（{clock(lunch_time[0])}–{clock(lunch_time[1])}，已计入地点停留）"]
                          if meal_place and lunch_time else
                          [f"午餐与休息 {clock(lunch_time[0])}–{clock(lunch_time[1])}"] if lunch_time else
                          ["根据当天到访时间安排用餐"]),
                   budgetNote=f"预算偏好：{intent.budget}；费用以实际票价和交通选择为准", warnings=warnings)
    theme = intent.day_themes[day] if day < len(intent.day_themes) else ""
    other_names = [p.name for p in (all_places or []) if p not in places]
    if not theme or any(name in theme for name in other_names):
        theme = f"{rows[0][0].name}与{rows[-1][0].name}慢游" if len(rows) > 1 else f"{rows[0][0].name}深度游"
    lines = [f"## Day {day+1} · {date_label}", "",
             f"### {theme}", "",
             "| 时间 | 安排 | 交通 |", "| --- | --- | --- |"]
    def add_lunch_row() -> None:
        # Render an actual scheduled break at its chronological position.
        lines.append(f"| {clock(lunch_time[0])}–{clock(lunch_time[1])} | 午餐与休息：预留用餐时间（具体餐厅待选） | — |")

    lunch_rendered = False
    for index, (place, arrival, departure, _, movement) in enumerate(rows):
        if lunch_time and not meal_place and not lunch_rendered and lunch_time[0] < arrival:
            add_lunch_row()
            lunch_rendered = True
        transport_text = route_label(*movement) if movement else "从首站开始"
        if index == 0 and hotel:
            transport_text = f"从{intent.lodging}出发，{transport_text}"
        others = tuple(p.name for p in (all_places or places) if p.placeId != place.placeId)
        experience = experience_at_time(
            str(details.get(place.placeId, {}).get("experience") or "结合本次兴趣探索此处"),
            arrival, category=place.category, other_places=others,
        )[:100]
        window_note = f"；开放资料：{hours[place.placeId]}" if place.placeId in hours else "；入内时段待核实" if entry_sensitive(place) else ""
        marker = "【必去】" if place.name in intent.must_visit else ""
        activity = f"{marker}{place.name}：{experience}{window_note}".replace("|", "｜")
        lines.append(f"| {clock(arrival)}–{clock(departure)} | {activity} | {transport_text.replace('|', '｜')} |")
    if lunch_time and not meal_place and not lunch_rendered:
        add_lunch_row()
    # Blank line closes the GFM table before the return and daily summary.
    lines.append("")
    if return_route:
        lines += [f"返回{intent.lodging}：{route_label(*return_route)}。", ""]
    lines += [f"当天预计交通 {transport} 分钟，其中步行路段约 {walk} 分钟；返回约 {clock(finish)}。", ""]
    for warning in warnings:
        if "节奏目标" in warning:
            lines += [f"行程提醒：{warning}。", ""]
    return plan, lines, walk


def validate(days: list[DayPlan], required: list[Place], modes: tuple[str, ...], markdown: str) -> list[str]:
    issues = []
    stops = [s for day in days for s in day.stops]
    ids = [s.place.placeId for s in stops]
    for place in required:
        if place.placeId not in ids:
            issues.append(f"缺少必去地点：{place.name}")
    if len(ids) != len(set(ids)):
        issues.append("地点被重复安排")
    for day in days:
        if len(day.legs) != max(0, len(day.stops) - 1):
            issues.append(f"{day.date} 路线段不完整")
        for previous, later in zip(day.stops, day.stops[1:]):
            if minute(previous.departure) > minute(later.arrival):
                issues.append(f"{day.date} 时间重叠")
        for leg in day.legs:
            if leg.mode not in modes:
                issues.append(f"{day.date} 交通方式不符合偏好")
            if leg.mode == "walking" and (leg.minutes or 0) > 65:
                issues.append(f"{day.date} 单段步行负担过高")
    # Verify that the rendered timetable follows the same order as the
    # structured stops and includes independently scheduled meal breaks.
    sections = re.split(r"(?=^## Day \d+ · )", markdown, flags=re.M)[1:]
    if len(sections) != len(days):
        issues.append("文档天数与结构化日程不一致")
    for day, section in zip(days, sections):
        last_end = -1
        lines = section.splitlines()
        for line in lines:
            match = re.match(r"^\| (\d{2}:\d{2})–(\d{2}:\d{2}) \|", line)
            if not match:
                continue
            begin, end = minute(match.group(1)), minute(match.group(2))
            if begin < last_end or end <= begin:
                issues.append(f"{day.date} 文档中的时间顺序冲突")
                break
            last_end = end
        for meal in day.meals:
            if meal.startswith("午餐与休息 "):
                span = meal.removeprefix("午餐与休息 ")
                if not any(line.startswith(f"| {span} | 午餐与休息") for line in lines):
                    issues.append(f"{day.date} 文档缺少已安排的午餐时段")
    for stop in stops:
        if not any(stop.place.name in line and f"{stop.arrival}–{stop.departure}" in line
                   for line in markdown.splitlines() if line.startswith("| ")):
            issues.append(f"文档与结构化日程不一致：{stop.place.name}")
    return issues
