import httpx
import math
import logging
import re
import time
from fastapi import HTTPException

from .config import settings
from .models import Leg, Place, Point


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
            response = await client.get("https://restapi.amap.com/v5/place/text", params={"key": settings.amap_web_service_key, "keywords": query, "region": "420100", "city_limit": "true", "page_size": 20})
            response.raise_for_status()
            data = response.json()
        if data.get("status") != "1":
            raise HTTPException(502, f"高德搜索失败：{data.get('info', '未知错误')}")
        places = []
        for item in data.get("pois", []):
            try:
                lng, lat = map(float, item["location"].split(","))
                places.append(Place(placeId=item["id"], name=item["name"], category=item.get("type", "地点").split(";")[0], address=item.get("address") or "地址未知", location=Point(lng=lng, lat=lat), source="高德地图", description=item.get("type", "")))
            except (ValueError, KeyError, TypeError):
                continue
        return places, "高德地图 POI"
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"高德搜索暂不可用：{exc.__class__.__name__}") from exc


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
            response = await client.get(f"https://restapi.amap.com/v5/direction/{endpoint}", params=params)
            response.raise_for_status()
            data = response.json()
        paths = data.get("route", {}).get("transits" if mode == "transit" else "paths", [])
        if data.get("status") != "1" or not paths:
            return estimate("高德未返回路线")
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
        return {"minutes": max(1, round(float(seconds) / 60)), "minutesMin": None, "minutesMax": None, "distanceMeters": int(float(path["distance"])) if path.get("distance") else None, "source": "高德地图路线规划", "polyline": points[::max(1, len(points)//100)]}
    except (httpx.HTTPError, ValueError, TypeError):
        return estimate("高德请求失败")
