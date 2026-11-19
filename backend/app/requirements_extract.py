"""Ground must-visit locations from free-form pre-trip requirements."""
from __future__ import annotations

import asyncio
import json
import re

from .assistant import _model
from .models import Place
from .places import FEATURED, search_places

# Conservative fallback when the model or search is unavailable.
_VISIT_WORDS = re.compile(r"一定|必须|必去|想去|想看|打卡|安排|要去|要看|希望去|希望看|非去不可")
_NEGATIONS = re.compile(r"不去|不要去|不想去|避免去|避开|取消|别去")


def _clauses(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[；;，,。\n]", text) if item.strip()]


def _fallback_names(text: str) -> list[str]:
    result: list[str] = []
    for clause in _clauses(text):
        if _NEGATIONS.search(clause) or not _VISIT_WORDS.search(clause):
            continue
        for place in FEATURED:
            if place.name in clause and place.name not in result:
                result.append(place.name)
    return result[:8]


def _decode_names(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw.strip())
        value = json.loads(text)
        names = value.get("mustVisit", []) if isinstance(value, dict) else []
        return list(dict.fromkeys(name.strip() for name in names if isinstance(name, str) and 1 <= len(name.strip()) <= 40))[:8]
    except (ValueError, AttributeError, TypeError):
        return []


async def extract_required_places(requirements: str) -> dict:
    requirements = requirements.strip()[:1000]
    if not requirements:
        return {"places": [], "unresolved": []}

    fallback = _fallback_names(requirements)
    answer = None
    try:
        answer = await asyncio.wait_for(
            _model(
                "你是旅行要求提取器。只识别用户明确想去、必须去或希望打卡的真实具体地点，"
                "用户表示不想去、避免去、仅仅举例或纯背景提及的地点不可提取。"
                "例如『一定要在晚上去一次黄鹤楼』输出 {\"mustVisit\": [\"黄鹤楼\"]}。"
                "只返回JSON对象 {\"mustVisit\": [\"地点全名\"]}，不要创造地点，最多8个。",
                requirements,
            ),
            timeout=16,
        )
    except (asyncio.TimeoutError, RuntimeError):
        pass

    candidates = list(dict.fromkeys([*_decode_names(answer), *fallback]))[:8]
    selected: list[Place] = []
    unresolved: list[str] = []

    async def find(name: str) -> Place | None:
        # Avoid accepting fuzzy matches that happen to have a similar name.
        known = next((p for p in FEATURED if p.name == name), None)
        if known:
            return known
        try:
            matches, _ = await asyncio.wait_for(search_places(name), timeout=12)
        except Exception:
            return None
        clean = lambda value: re.sub(r"\s|[（）()·]", "", value).lower()
        exact = next((p for p in matches if clean(p.name) == clean(name)), None)
        return exact

    found = await asyncio.gather(*(find(name) for name in candidates))
    for name, place in zip(candidates, found):
        if place is None:
            unresolved.append(name)
        elif place.placeId not in {p.placeId for p in selected}:
            selected.append(place)

    return {"places": [p.model_dump() for p in selected], "unresolved": unresolved}
