"""Structured preferences and general transport decisions shared by planning paths."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from fastapi import HTTPException

from .models import Point
from .places import calculate_route


def meters(a: Point, b: Point) -> float:
    return math.hypot((a.lng - b.lng) * 95000, (a.lat - b.lat) * 111000)


def allowed_modes(text: str, requirements: str = "") -> tuple[str, ...]:
    tokens = {"walking": ("步行", "走路", "徒步"), "transit": ("公共交通", "公交", "地铁", "轨道"),
              "driving": ("打车", "驾车", "出租", "自驾", "网约车")}
    excluded = {"walking": ("不步行", "避免步行"), "transit": ("不坐公交", "不坐地铁", "不用公共交通"),
                "driving": ("不打车", "不驾车", "不开车")}
    modes = [mode for mode, words in tokens.items() if any(w in text for w in words)]
    if not modes:
        modes = ["walking", "transit"]
    restriction_text = f"{text} {requirements}"
    modes = [mode for mode in modes if not any(w in restriction_text for w in excluded[mode])]
    if not modes:
        raise HTTPException(422, "交通偏好排除了所有方式")
    return tuple(modes)


@dataclass
class TripIntent:
    days: int
    people: int
    company: str
    pace: str
    budget: str
    interests: list[str]
    requirements: str
    modes: tuple[str, ...]
    must_visit: list[str]
    start_date: str | None
    lodging: str
    priorities: list[str] = field(default_factory=list)
    day_themes: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    time_preferences: dict[str, str] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
    hard_constraints: list[str] = field(default_factory=list)
    soft_goals: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)

    def context(self) -> dict:
        return {"days": self.days, "people": self.people, "company": self.company,
                "pace": self.pace, "budget": self.budget, "interests": self.interests,
                "requirements": self.requirements, "allowedModes": self.modes,
                "mustVisit": self.must_visit, "startDate": self.start_date, "lodging": self.lodging,
                "hardConstraints": self.hard_constraints, "softGoals": self.soft_goals,
                "avoid": self.avoid}


class Router:
    def __init__(self, modes: tuple[str, ...], budget: str):
        self.modes, self.budget = modes, budget
        self.cache: dict[tuple, dict] = {}
        import asyncio
        self.limit = asyncio.Semaphore(3)

    async def choose(self, start: Point, end: Point) -> tuple[str, dict]:
        import asyncio
        distance = meters(start, end)
        modes = [m for m in self.modes if m != "walking" or distance < 5000 or len(self.modes) == 1]
        async def fetch(mode: str):
            key = (start.lng, start.lat, end.lng, end.lat, mode)
            if key not in self.cache:
                async with self.limit:
                    if key not in self.cache:
                        self.cache[key] = await calculate_route(start, end, mode)
            return mode, self.cache[key]
        options = await asyncio.gather(*(fetch(m) for m in modes))
        def score(option):
            mode, route = option
            minutes = route["minutes"]
            walk = minutes if mode == "walking" else route.get("walkingMinutes") if route.get("walkingMinutes") is not None else min(14, distance / 500)
            cost = 18 if mode == "driving" else 2 if mode == "transit" else 0
            weight = 1.7 if self.budget in ("经济", "节省", "低") else .5
            uncertainty = 8 if "估算" in route.get("source", "") else 0
            unavailable = 100 if any(x in route.get("source", "") for x in ("高德未返回路线", "高德请求失败")) else 0
            transfer_effort = 8 if mode == "transit" and distance < 1800 else 0
            transfers = route.get("transfers") or 0
            return minutes + max(0, walk - 25) * 1.6 + transfers * 7 + cost * weight + uncertainty + transfer_effort + unavailable
        choice = min(options, key=score)
        if distance > 3000 and any(x in choice[1].get("source", "") for x in ("高德未返回路线", "高德请求失败")):
            raise HTTPException(502, "长距离交通缺少可用地图路线，无法可靠排程")
        if choice[0] == "walking" and choice[1]["minutes"] > 65:
            raise HTTPException(422, "仅允许步行的路段超过合理负担；请调整交通偏好或地点")
        return choice
