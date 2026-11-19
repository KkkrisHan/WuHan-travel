import httpx
import math
import logging
import re
import time
from fastapi import HTTPException

from .config import settings
from .models import Leg, Place, Point
from .request_context import count_external


# Curated entry points for setup/offline use. Coordinates are indicative; live search uses AMap.
FEATURED = [
    Place(placeId="featured-yellow-crane", name="黄鹤楼", category="历史地标", address="武汉市武昌区蛇山西山坡特1号", location=Point(lng=114.3021, lat=30.5471), source="内置地点参考", description="登高俯瞰长江与武汉三镇。"),
    Place(placeId="featured-hubei-museum", name="湖北省博物馆", category="博物馆", address="武汉市武昌区东湖路160号", location=Point(lng=114.3667, lat=30.5617), source="内置地点参考", description="以曾侯乙编钟等馆藏闻名。"),
    Place(placeId="featured-east-lake", name="东湖绿道", category="自然风光", address="武汉市东湖风景区", location=Point(lng=114.4101, lat=30.5582), source="内置地点参考", description="沿东湖步行与骑行的开放空间。"),
    Place(placeId="featured-jianghan", name="江汉路步行街", category="街区", address="武汉市江汉区江汉路", location=Point(lng=114.2911, lat=30.5824), source="内置地点参考", description="适合感受武汉街区与城市生活。"),
    Place(placeId="featured-wuhan-university", name="武汉大学", category="校园", address="武汉市武昌区八一路299号", location=Point(lng=114.3675, lat=30.5404), source="内置地点参考", description="校园建筑与人文景观。入校规则须另行核实。"),
    Place(placeId="featured-hubuxiang", name="户部巷", category="街区", address="武汉市武昌区自由路", location=Point(lng=114.2999, lat=30.5423), source="内置地点参考", description="老城街巷与餐饮区域。"),
]


async def place_details(place_id: str) -> Place:
    if not re.fullmatch(r"[A-Za-z0-9]{6,32}", place_id):
        raise HTTPException(422, "POI ID 无效")
    if not settings.amap_web_service_key:
        raise HTTPException(503, "未配置高德 Web 服务密钥")
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12, connect=5)) as client:
            count_external("amapPlaceDetails")
            response = await client.get("https://restapi.amap.com/v5/place/detail", params={"key": settings.amap_web_service_key, "id": place_id})
            response.raise_for_status()
            data = response.json()
        if data.get("status") != "1":
            logging.getLogger(__name__).warning("poi_detail_failed place_id=%s amap_info=%s elapsed_ms=%d", place_id, data.get("info", "unknown"), int((time.monotonic()-started)*1000))
            raise HTTPException(502, f"高德地点详情查询失败：{data.get('info', '未知错误')}")
        poi = next((item for item in data.get("pois", []) if item.get("id") == place_id), None)
        if not poi:
            raise HTTPException(404, "高德未找到此地点")
        lng, lat = map(float, poi["location"].split(","))
        place = Place(placeId=place_id, name=poi["name"], category=(poi.get("type") or "地点").split(";")[0], address=poi.get("address") or "地址未知", location=Point(lng=lng, lat=lat), source="高德地图", description=poi.get("type") or "")
        logging.getLogger(__name__).info("poi_detail_completed place_id=%s elapsed_ms=%d", place_id, int((time.monotonic()-started)*1000))
        return place
    except httpx.HTTPError as exc:
        logging.getLogger(__name__).warning("poi_detail_failed place_id=%s category=%s elapsed_ms=%d", place_id, type(exc).__name__, int((time.monotonic()-started)*1000))
        raise HTTPException(502, f"高德地点详情请求失败：{type(exc).__name__}") from exc
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(502, "高德地点详情格式无效") from exc


async def search_places(query: str) -> tuple[list[Place], str]:
    if not settings.amap_web_service_key:
        matches = [p for p in FEATURED if query.lower() in (p.name + p.category + p.description).lower()]
        return matches, "内置地点参考；配置 AMAP_WEB_SERVICE_KEY 后可搜索全部 POI"
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            count_external("amapPlaceSearches")
            response = await client.get("https://restapi.amap.com/v5/place/text", params={"key": settings.amap_web_service_key, "keywords": query, "region": "420100", "city_limit": "true", "page_size": 20})
            response.raise_for_status()
            data = response.json()
        if data.get("status") != "1":
            raise HTTPException(502, f"高德搜索失败：{data.get('info', '未知错误')}")
        raw_pois = data.get("pois", [])
        if not isinstance(raw_pois, list):
            raise HTTPException(502, "高德搜索数据格式无效")
        places = []
        for item in raw_pois:
            try:
                lng, lat = map(float, item["location"].split(","))
                places.append(Place(placeId=item["id"], name=item["name"], category=item.get("type", "地点").split(";")[0], address=item.get("address") or "地址未知", location=Point(lng=lng, lat=lat), source="高德地图", description=item.get("type", "")))
            except (ValueError, KeyError, TypeError):
                continue
        if raw_pois and not places:
            raise HTTPException(502, "高德搜索结果缺少地点必要字段")
        return places, "高德地图 POI"
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"高德搜索暂不可用：{exc.__class__.__name__}") from exc


def _normalized_name(value: str) -> str:
    return re.sub(r"[\s（）()·・,，。\-—_]+", "", value).casefold()


def _place_distance(a: Point, b: Point) -> float:
    return math.hypot((a.lng - b.lng) * 95000, (a.lat - b.lat) * 111000)


def _compatible_category(expected: str, actual: str) -> bool:
    # The JS SDK and Web API use different category granularity. Only reject
    # clearly different kinds of POI; do not use category as identity proof.
    if expected in ("", "地点") or actual in ("", "地点"):
        return True
    groups = (
        ("街区", "购物服务", "地名地址信息", "风景名胜"),
        ("博物馆", "科教文化服务"),
        ("公园", "公园广场", "自然风光", "风景名胜"),
        ("历史地标", "风景名胜"),
        ("校园", "科教文化服务"),
        ("自然风光", "地名地址信息"),
        ("餐饮服务", "餐厅", "美食"),
    )
    return expected == actual or any(expected in group and actual in group for group in groups)


_WUHAN_DISTRICTS = ("江岸区", "江汉区", "硚口区", "汉阳区", "武昌区", "青山区",
                    "洪山区", "东西湖区", "汉南区", "蔡甸区", "江夏区", "黄陂区", "新洲区")


def _same_place(claimed: Place, candidate: Place) -> bool:
    name, found = _normalized_name(claimed.name), _normalized_name(candidate.name)
    if not name or not found or not _compatible_category(claimed.category, candidate.category):
        return False
    claimed_district = next((d for d in _WUHAN_DISTRICTS if d in claimed.address), None)
    candidate_district = next((d for d in _WUHAN_DISTRICTS if d in candidate.address), None)
    if claimed_district and candidate_district and claimed_district != candidate_district:
        return False
    distance = _place_distance(claimed.location, candidate.location)
    if name == found:
        # A greenway is an extended route; its representative map point may
        # differ from the curated display marker by several kilometres.
        return distance <= (5000 if "绿道" in name else 1000)
    # A shortened or renamed POI needs stronger independent evidence.
    if min(len(name), len(found)) < 3 or not (name.startswith(found) or found.startswith(name)) or distance > 350:
        return False
    address = _normalized_name(claimed.address)
    candidate_address = _normalized_name(candidate.address)
    return bool(address and candidate_address and address != "地址未知" and
                candidate_address != "地址未知" and
                any(part in candidate_address for part in (address[i:i + 3] for i in range(len(address) - 2))))


async def verify_required_place(place: Place) -> Place:
    """Resolve a client supplied location to a fresh AMap POI or explain why it cannot be resolved."""
    name = place.name.strip()
    if not name:
        raise HTTPException(422, "必去地点缺少名称")
    if not settings.amap_web_service_key:
        raise HTTPException(503, "未配置高德 Web 服务密钥，暂时无法核实必去地点")

    poi_id = next((value for value in (place.poiId, place.placeId)
                   if re.fullmatch(r"[A-Za-z0-9]{6,32}", value or "")), None)
    detail_error: HTTPException | None = None
    if poi_id:
        try:
            detail = await place_details(poi_id)
            if _same_place(place, detail):
                logging.getLogger(__name__).info("required_place_verified method=id requested_id=%s verified_id=%s", poi_id, detail.placeId)
                return detail
            logging.getLogger(__name__).warning("required_place_id_mismatch requested_id=%s name=%s returned_name=%s", poi_id, name, detail.name)
        except HTTPException as exc:
            detail_error = exc
            logging.getLogger(__name__).warning("required_place_detail_unavailable id=%s status=%s", poi_id, exc.status_code)

    try:
        candidates, _ = await search_places(name)
    except HTTPException as exc:
        raise HTTPException(503, f"高德地点查询暂时失败，无法核实必去地点：{name}（{exc.detail}）") from exc
    matches = [item for item in candidates if _same_place(place, item)]
    if matches:
        match = min(matches, key=lambda item: (
            _normalized_name(item.name) != _normalized_name(place.name),
            _place_distance(place.location, item.location),
        ))
        logging.getLogger(__name__).info("required_place_verified method=search requested_id=%s verified_id=%s candidates=%d", place.placeId, match.placeId, len(candidates))
        return match
    if detail_error and detail_error.status_code >= 500:
        raise HTTPException(503, f"高德地点详情暂时失败，且搜索未能确认必去地点：{name}")
    if not candidates:
        raise HTTPException(422, f"高德未找到必去地点：{name}")
    raise HTTPException(422, f"高德找到了候选地点，但名称、类别或位置与必去地点不符：{name}")


async def calculate_route(origin: Point, destination: Point, mode: str) -> dict:
    def estimate(reason: str) -> dict:
        # Straight-line distance is deliberately expanded into a range, never a precise route ETA.
        dx = (origin.lng - destination.lng) * 95000
        dy = (origin.lat - destination.lat) * 111000
        meters = math.hypot(dx, dy)
        if meters < 100:
            low, high = 1, 3
        else:
            factor = 1.25 if mode == "walking" else 1.45 if mode == "driving" else 1.6
            speed = 4.5 if mode == "walking" else 22 if mode == "driving" else 16
            base = meters / 1000 * factor / speed * 60
            low = max(3, 5 * math.floor(base * .85 / 5))
            high = max(low + 5, 5 * math.ceil(base * 1.35 / 5))
        label = {"walking": "步行", "driving": "驾车", "transit": "公交"}.get(mode, mode)
        return {"minutes": round((low + high) / 2), "minutesMin": low, "minutesMax": high,
                "distanceMeters": round(meters), "source": f"{label}距离估算，非实时路线（{reason}）", "polyline": []}
    if not settings.amap_web_service_key:
        return estimate("未配置高德密钥")
    endpoint = "transit/integrated" if mode == "transit" else "walking" if mode == "walking" else "driving"
    params = {"key": settings.amap_web_service_key, "origin": f"{origin.lng:.6f},{origin.lat:.6f}", "destination": f"{destination.lng:.6f},{destination.lat:.6f}", "show_fields": "cost,polyline"}
    if mode == "transit":
        params.update({"city1": "027", "city2": "027", "strategy": 0})
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            count_external("amapRouteRequests")
            response = await client.get(f"https://restapi.amap.com/v5/direction/{endpoint}", params=params)
            response.raise_for_status()
            data = response.json()
        paths = data.get("route", {}).get("transits" if mode == "transit" else "paths", [])
        if data.get("status") != "1" or not paths:
            return estimate("高德未返回路线")
        def transit_metrics(option: dict) -> tuple[int, int]:
            walking_meters = 0.0
            boardings = 0
            for segment in option.get("segments", []):
                if not isinstance(segment, dict):
                    continue
                walking = segment.get("walking") or {}
                if isinstance(walking, dict):
                    try:
                        walking_meters += float(walking.get("distance") or 0)
                    except (ValueError, TypeError):
                        pass
                bus = segment.get("bus") or {}
                if isinstance(bus, dict) and bus.get("buslines"):
                    boardings += 1
            if not walking_meters:
                try:
                    walking_meters = float(option.get("walking_distance") or 0)
                except (ValueError, TypeError):
                    pass
            return round(walking_meters / 80), max(0, boardings - 1)
        if mode == "transit":
            def transit_score(option: dict) -> float:
                try:
                    duration = float((option.get("cost") or {}).get("duration") or option.get("duration")) / 60
                except (ValueError, TypeError):
                    return float("inf")
                walk_minutes, transfers = transit_metrics(option)
                return duration + walk_minutes * .45 + transfers * 7
            path = min(paths, key=transit_score)
        else:
            path = paths[0]
        seconds = path.get("cost", {}).get("duration") or path.get("duration")
        points = []
        def collect_polylines(value):
            if isinstance(value, dict):
                polyline = value.get("polyline")
                if isinstance(polyline, str):
                    for coordinate in polyline.split(";"):
                        try:
                            lng, lat = map(float, coordinate.split(","))
                            points.append(Point(lng=lng, lat=lat))
                        except (ValueError, TypeError):
                            continue
                for key, item in value.items():
                    if key != "polyline":
                        collect_polylines(item)
            elif isinstance(value, list):
                for item in value:
                    collect_polylines(item)
        collect_polylines(path.get("segments" if mode == "transit" else "steps", []))
        if not seconds:
            return estimate("高德未返回耗时")
        walk_minutes, transfers = transit_metrics(path) if mode == "transit" else (None, None)
        return {"minutes": max(1, round(float(seconds) / 60)), "minutesMin": None, "minutesMax": None,
                "distanceMeters": int(float(path["distance"])) if path.get("distance") else None,
                "walkingMinutes": walk_minutes, "transfers": transfers,
                "source": "高德地图路线规划", "polyline": points[::max(1, len(points)//100)]}
    except (httpx.HTTPError, ValueError, TypeError):
        return estimate("高德请求失败")
