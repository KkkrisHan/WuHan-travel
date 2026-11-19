"""Bounded geographic repair of AI day grouping, using hotel round trips."""
from __future__ import annotations

import itertools
import math

from .models import Place, Point
from .trip_intent import meters


def _cost(group: list[Place], hotel: Point | None) -> float:
    if not group:
        return 1000000
    if len(group) > 5:
        return 1000000
    def path(order):
        points = [p.location for p in order]
        result = sum(meters(a, b) for a, b in zip(points, points[1:]))
        if hotel:
            result += meters(hotel, points[0]) + meters(points[-1], hotel)
        return result
    return min(path(order) for order in itertools.permutations(group))


def improve_groups(groups: list[list[Place]], hotel: Point | None, max_stops: int,
                   locked_ids: set[str] | None = None) -> list[list[Place]]:
    """Move or swap places only when estimated total travel falls substantially."""
    if len(groups) < 2 or sum(map(len, groups)) > 15:
        return groups
    locked_ids = locked_ids or set()
    original = [group[:] for group in groups]
    current = [group[:] for group in groups]
    original_cost = sum(_cost(g, hotel) for g in current)
    for _ in range(3):
        best = None
        baseline = sum(_cost(g, hotel) for g in current)
        for a in range(len(current)):
            for b in range(len(current)):
                if a == b:
                    continue
                for i, place in enumerate(current[a]):
                    if place.placeId in locked_ids:
                        continue
                    if len(current[a]) > 1 and len(current[b]) < max_stops:
                        candidate = [g[:] for g in current]
                        candidate[a].pop(i)
                        candidate[b].append(place)
                        cost = sum(_cost(g, hotel) for g in candidate)
                        if cost < baseline - 1000 and (best is None or cost < best[0]):
                            best = (cost, candidate)
                    for j, other in enumerate(current[b]):
                        if other.placeId in locked_ids:
                            continue
                        candidate = [g[:] for g in current]
                        candidate[a][i], candidate[b][j] = other, place
                        cost = sum(_cost(g, hotel) for g in candidate)
                        if cost < baseline - 1000 and (best is None or cost < best[0]):
                            best = (cost, candidate)
        if best is None:
            break
        current = best[1]
    if sum(_cost(g, hotel) for g in current) >= original_cost * .85:
        return original
    return current


def include_food_if_requested(groups: list[list[Place]], pool: list[Place],
                              required_ids: set[str], hotel: Point | None,
                              interests: list[str], details: dict[str, dict]) -> list[list[Place]]:
    """An explicit food interest must result in an actual meal venue, when available."""
    if "美食" not in interests or any(p.category == "餐饮服务" for day in groups for p in day):
        return groups
    used = {p.placeId for day in groups for p in day}
    restaurants = [p for p in pool if p.category == "餐饮服务" and p.placeId not in used
                   and not any(word in p.name for word in ("酒店", "食堂", "外卖"))][:25]
    best = None
    for day, group in enumerate(groups):
        for index, old in enumerate(group):
            if old.placeId in required_ids:
                continue
            for restaurant in restaurants:
                candidate = [g[:] for g in groups]
                candidate[day][index] = restaurant
                cost = sum(_cost(g, hotel) for g in candidate)
                if best is None or cost < best[0]:
                    best = (cost, candidate, restaurant)
    if best is None:
        return groups
    details[best[2].placeId] = {"experience": "安排一餐，体验这次旅行关注的美食", "timeWindow": "any"}
    return best[1]


def spread_required_load(groups: list[list[Place]], required_ids: set[str],
                         hotel: Point | None, max_stops: int) -> list[list[Place]]:
    """Keep a day from carrying too many mandatory visits at the expense of pace."""
    if len(groups) < 2:
        return groups
    current = [group[:] for group in groups]
    maximum = max(2, math.ceil(len(required_ids) / len(groups)))
    for _ in range(len(required_ids)):
        counts = [sum(p.placeId in required_ids for p in day) for day in current]
        source = next((day for day, count in enumerate(counts) if count > maximum), None)
        if source is None:
            break
        best = None
        for place in current[source]:
            if place.placeId not in required_ids:
                continue
            for target in range(len(current)):
                if target == source or counts[target] >= maximum:
                    continue
                removable = [None] if len(current[target]) < max_stops else [p for p in current[target] if p.placeId not in required_ids]
                for old in removable:
                    candidate = [day[:] for day in current]
                    candidate[source].remove(place)
                    if old:
                        candidate[target].remove(old)
                    candidate[target].append(place)
                    if not candidate[source]:
                        continue
                    cost = sum(_cost(day, hotel) for day in candidate)
                    if best is None or cost < best[0]:
                        best = (cost, candidate)
        if best is None:
            break
        current = best[1]
    return current


def relieve_overloaded_groups(groups: list[list[Place]], hotel: Point | None,
                              protected_ids: set[str], overloaded: list[int],
                              max_stops: int) -> list[list[Place]]:
    """Reduce a costly day's geographic span without dropping required/meal stops.

    Consider a bounded transfer to another day, removing at most one optional
    stop only if the destination is full. Prefer reducing the worst day over
    minimizing the whole trip's distance at the expense of one exhausting day.
    """
    if not hotel or not overloaded or len(groups) < 2 or sum(map(len, groups)) > 15:
        return groups

    def costs(days: list[list[Place]]) -> list[float]:
        return [_cost(group, hotel) for group in days]

    baseline = costs(groups)
    baseline_peak = max(baseline)
    best_score = baseline_peak + 0.15 * sum(baseline)
    best_groups = groups
    for source in overloaded:
        if source < 0 or source >= len(groups) or len(groups[source]) < 2:
            continue
        for moved in groups[source]:
            for target in range(len(groups)):
                if target == source:
                    continue
                victims = [None] if len(groups[target]) < max_stops else [
                    p for p in groups[target] if p.placeId not in protected_ids]
                for victim in victims:
                    candidate = [group[:] for group in groups]
                    candidate[source].remove(moved)
                    if victim:
                        candidate[target].remove(victim)
                    candidate[target].append(moved)
                    if any(not group or len(group) > max_stops for group in candidate):
                        continue
                    estimates = costs(candidate)
                    peak = max(estimates)
                    score = peak + 0.15 * sum(estimates) + (900 if victim else 0)
                    if peak < baseline_peak - 900 and score < best_score - 300:
                        best_score, best_groups = score, candidate
    return best_groups
