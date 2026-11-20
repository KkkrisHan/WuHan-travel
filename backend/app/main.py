from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import time
from urllib.parse import urlparse
from uuid import uuid4
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.exception_handlers import request_validation_exception_handler
from pydantic import BaseModel, Field

from . import map_places, tasks
from .assistant import _model, introduce
from .chat_graph import graph as chat_graph
from .config import settings
from . import service_config
from .models import ChatRequest, IntroRequest, PlanRequest, RouteRequest, SearchRequest, Place, Point
from .map_places import MapPlaceInput
from .places import FEATURED, calculate_route, place_details, search_places
from .planner import plan
from .itinerary_doc import build_itinerary
from .requirements_extract import extract_required_places
from .place_info import get_cached_place_knowledge, get_place_info
from .request_context import request_id

LOG_PATH = Path(__file__).resolve().parents[1] / "logs" / "backend.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()])
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("wuhan.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    tasks.initialize()
    map_places.initialize()
    watchdog = asyncio.create_task(tasks.lease_watchdog())
    yield
    watchdog.cancel()
    for running in list(tasks.RUNNING.values()):
        running.cancel()


app = FastAPI(title="武汉旅行助手 API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[origin.strip() for origin in settings.frontend_origin.split(",")], allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type", "X-Task-Token", "X-Trip-ID", "X-Trip-Secret"])
REQUEST_TIMES: dict[str, deque[float]] = defaultdict(deque)


@app.exception_handler(RequestValidationError)
async def safe_config_validation_errors(request: Request, exc: RequestValidationError):
    if request.url.path in {"/api/services/amap", "/api/services/ai"}:
        errors = [
            {"loc": list(error.get("loc", ())), "msg": error.get("msg", "字段无效"), "type": error.get("type", "value_error")}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})
    return await request_validation_exception_handler(request, exc)


@app.on_event("startup")
def load_persisted_service_config():
    service_config.apply_runtime()


def _check_local_config_request(request: Request) -> None:
    host = urlparse(f"//{request.headers.get('host', '')}").hostname
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(403, "仅允许本机配置")
    origin = request.headers.get("origin", "")
    parsed = urlparse(origin)
    allowed_origins = {value.strip().rstrip("/") for value in settings.frontend_origin.split(",")}
    normalized_origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or normalized_origin not in allowed_origins:
        raise HTTPException(403, "配置请求来源必须是本机页面")


def _service_status():
    config = service_config.effective_config()
    provider = config["provider"]
    configured_model = config["deepseekModel"] if provider == "deepseek" else config["openaiModel"]
    return {
        "amap": {
            "configured": bool(config["amapJsKey"] and config["amapJsSecurityCode"]),
            "webServiceConfigured": bool(config["amapWebServiceKey"]),
            "jsKey": config["amapJsKey"],
            "revision": service_config.read_saved().get("amapRevision", "legacy"),
        },
        "ai": {
            "configured": bool(config["deepseekApiKey"] if provider == "deepseek" else config["openaiApiKey"]),
            "provider": provider,
            "model": configured_model,
            "models": {"deepseek": config["deepseekModel"], "openai": config["openaiModel"]},
            "availableProviders": {
                "deepseek": bool(config["deepseekApiKey"]),
                "openai": bool(config["openaiApiKey"]),
            },
        },
    }


@app.get("/api/services/status")
def services_status():
    return _service_status()


class AMapConfigRequest(BaseModel):
    jsKey: str = Field(max_length=256)
    securityCode: str = Field(max_length=256)
    webServiceKey: str = Field(default="", max_length=256)


@app.post("/api/services/amap")
async def configure_amap(request: Request, config: AMapConfigRequest):
    _check_local_config_request(request)
    current = service_config.effective_config()
    js_key = config.jsKey.strip() or current["amapJsKey"]
    security_code = config.securityCode.strip() or current["amapJsSecurityCode"]
    if not js_key or not security_code:
        raise HTTPException(422, "需要填写高德 JS API Key 和配套安全密钥")
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            js = await client.get(
                "https://webapi.amap.com/maps",
                params={"v": "2.0", "key": js_key},
                headers={"Referer": request.headers.get("origin", "") + "/"},
            )
            js.raise_for_status()
            if "INVALID_USER_SCODE" in js.text:
                raise HTTPException(422, "高德 JS API 安全密钥未通过验证，请确认它与 JS Key 配套")
            if "INVALID_USER_DOMAIN" in js.text:
                raise HTTPException(422, "高德 JS API Key 域名白名单未包含本机页面来源")
            if "INVALID_USER_KEY" in js.text or "USERKEY_PLAT_NOMATCH" in js.text:
                raise HTTPException(422, "高德 JS API Key 无效或平台类型不匹配")
            if config.webServiceKey.strip():
                web = await client.get("https://restapi.amap.com/v3/geocode/geo", params={"key": config.webServiceKey.strip(), "address": "武汉"})
                web.raise_for_status()
                data = web.json()
                if data.get("status") != "1":
                    raise HTTPException(422, "高德 Web Service Key 验证失败，请检查 Key 和服务权限")
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "无法验证高德服务，请检查网络或稍后重试")
    service_config.save_amap(js_key, security_code, config.webServiceKey.strip())
    return _service_status()


class AIConfigRequest(BaseModel):
    provider: str
    apiKey: str = Field(default="", max_length=512)
    model: str = Field(min_length=1, max_length=128)


@app.post("/api/services/ai")
async def configure_ai(request: Request, config: AIConfigRequest):
    _check_local_config_request(request)
    provider = config.provider.lower().strip()
    model = config.model.strip()
    api_key = config.apiKey.strip()
    if provider not in {"deepseek", "openai"}:
        raise HTTPException(422, "仅支持 DeepSeek 或 OpenAI")
    if any(char.isspace() for char in model):
        raise HTTPException(422, "模型 ID 格式无效")
    if not api_key:
        current = service_config.effective_config()
        api_key = current["deepseekApiKey"] if provider == "deepseek" else current["openaiApiKey"]
    if not api_key:
        raise HTTPException(422, "请填写此供应商的 API Key")
    base_url = "https://api.deepseek.com" if provider == "deepseek" else "https://api.openai.com/v1"
    body = {"model": model, "messages": [{"role": "user", "content": "Reply with OK."}], "stream": False}
    body["max_tokens" if provider == "deepseek" else "max_completion_tokens"] = 1
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{base_url}/chat/completions", headers={"Authorization": f"Bearer {api_key}"}, json=body)
        if response.status_code in {401, 403}:
            raise HTTPException(422, "API Key 无效或没有调用权限")
        if response.status_code == 404:
            raise HTTPException(422, "模型 ID 不存在或当前账户无权使用")
        if response.status_code == 400:
            raise HTTPException(422, "模型服务拒绝验证请求，请检查模型 ID 与供应商")
        if response.status_code == 402:
            raise HTTPException(422, "该供应商账户额度不足，未保存配置")
        if response.status_code == 429:
            raise HTTPException(429, "请求过于频繁，请稍后重试；未保存配置")
        if response.status_code >= 400:
            raise HTTPException(502, f"模型服务验证失败（HTTP {response.status_code}），请检查模型和额度")
    except HTTPException:
        raise
    except httpx.HTTPError:
        raise HTTPException(502, "无法连接模型服务，请检查网络后重试")
    service_config.save_ai(provider, api_key, model)
    return _service_status()


@app.delete("/api/services/{service}")
def delete_service(service: str, request: Request):
    _check_local_config_request(request)
    if service not in {"amap", "ai"}:
        raise HTTPException(404, "未知服务")
    service_config.remove_service(service)
    return _service_status()


@app.middleware("http")
async def basic_protection(request: Request, call_next):
    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    rid = uuid4().hex[:12]
    token = request_id.set(rid)
    status = 500
    try:
        response = await _protected_request(request, call_next)
        status = response.status_code
        response.headers["X-Request-ID"] = rid
        return response
    except Exception:
        logger.exception("request_exception request_id=%s route=%s", rid, request.url.path)
        raise
    finally:
        logger.info("request request_id=%s route=%s method=%s status=%d started_at=%s ended_at=%s elapsed_ms=%d", rid, request.url.path, request.method, status, started_at, datetime.now(timezone.utc).isoformat(), int((time.monotonic()-started)*1000))
        request_id.reset(token)


async def _protected_request(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            if int(request.headers.get("content-length", "0")) > 256_000:
                return JSONResponse({"detail": "请求内容过大"}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Content-Length 无效"}, status_code=400)
    if request.url.path.startswith(("/api/", "/_AMapService/")):
        key = f"{request.client.host if request.client else 'unknown'}:{'map' if request.url.path.startswith('/_AMapService/') else 'api'}"
        window = REQUEST_TIMES[key]
        now = time.monotonic()
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= (600 if key.endswith(":map") else 120):
            return JSONResponse({"detail": "请求过于频繁，请稍后重试"}, status_code=429)
        window.append(now)
    return await call_next(request)


@app.api_route("/_AMapService/{path:path}", methods=["GET", "POST"])
async def amap_js_proxy(path: str, request: Request):
    if not settings.amap_js_security_code:
        raise HTTPException(503, "未配置高德 JS API 安全密钥")
    if not path.startswith(("v3/", "v4/", "v5/")) or ".." in path:
        raise HTTPException(400, "不支持的高德服务路径")
    host = "webapi.amap.com" if path.startswith("v4/map/styles") else "fmap01.amap.com" if path.startswith("v3/vectormap") else "restapi.amap.com"
    params = dict(request.query_params)
    params["jscode"] = settings.amap_js_security_code
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.request(request.method, f"https://{host}/{path}", params=params, content=await request.body() if request.method == "POST" else None, headers={"Content-Type": request.headers.get("content-type", "application/json")})
        return Response(response.content, status_code=response.status_code, media_type=response.headers.get("content-type", "application/json"))
    except httpx.HTTPError as exc:
        raise HTTPException(502, "高德 JS API 代理请求失败") from exc


@app.get("/api/health")
def health():
    return {"ok": True, "amapConfigured": bool(settings.amap_web_service_key), "modelConfigured": bool(settings.deepseek_api_key if settings.ai_provider == "deepseek" else settings.openai_api_key)}


class MustVisitExtractionRequest(BaseModel):
    requirements: str = Field(max_length=1000)


@app.post("/api/preferences/extract-must-visit")
async def extract_must_visit(request: MustVisitExtractionRequest):
    return await extract_required_places(request.requirements)


@app.get("/api/places/featured")
def featured():
    return {"places": [place.model_dump() for place in FEATURED]}


@app.get("/api/places")
def personal_places(x_trip_id: str = Header(default=""), x_trip_secret: str = Header(default="")):
    return {"places": map_places.list_places(x_trip_id, x_trip_secret)}


@app.post("/api/places")
def add_personal_place(place: MapPlaceInput, x_trip_id: str = Header(default=""), x_trip_secret: str = Header(default="")):
    return {"place": map_places.add_place(x_trip_id, x_trip_secret, place)}


@app.delete("/api/places/{place_id}")
def remove_personal_place(place_id: int, x_trip_id: str = Header(default=""), x_trip_secret: str = Header(default="")):
    return map_places.delete_place(x_trip_id, x_trip_secret, place_id)


@app.post("/api/places/search")
async def places_search(request: SearchRequest):
    places, source = await search_places(request.query.strip())
    return {"places": [place.model_dump() for place in places], "source": source}


@app.get("/api/places/{place_id}/detail")
async def places_detail(place_id: str):
    return {"place": (await place_details(place_id)).model_dump()}


@app.get("/api/places/reverse-geocode")
async def reverse_geocode(lng: float, lat: float):
    point = Point(lng=lng, lat=lat)
    if not settings.amap_web_service_key:
        raise HTTPException(503, "尚未配置高德 Web 服务密钥")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=4)) as client:
            response = await client.get(
                "https://restapi.amap.com/v3/geocode/regeo",
                params={"key": settings.amap_web_service_key, "location": f"{point.lng},{point.lat}", "extensions": "base"},
            )
            response.raise_for_status()
            data = response.json()
        address = data.get("regeocode", {}).get("formatted_address", "") if data.get("status") == "1" else ""
        if not isinstance(address, str) or not address.strip():
            raise HTTPException(502, "地图位置未找到可核实地址")
        return {"address": address.strip(), "location": point.model_dump()}
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise HTTPException(502, "地图逆地理编码失败") from exc


class PlaceInfoRequest(BaseModel):
    place: Place
    refresh: bool = False


@app.post("/api/places/info")
async def places_info(request: PlaceInfoRequest):
    return await get_place_info(request.place, request.refresh)


@app.post("/api/places/knowledge")
async def places_knowledge(request: PlaceInfoRequest):
    return await get_cached_place_knowledge(request.place)


class ItineraryRequest(BaseModel):
    mustVisitPlaces: list[Place]
    people: int = Field(ge=1, le=30)
    days: int = Field(ge=1, le=14)
    requirements: str = Field(default="", max_length=2000)
    startDate: str | None = None
    transport: str = "步行与公共交通"
    company: str = ""
    pace: str = "适中"
    budget: str = "灵活"
    interests: list[str] = Field(default_factory=list)
    lodging: str = Field(default="", max_length=120)
    lodgingLocation: Point | None = None


def _meters(a: Point, b: Point) -> float:
    return (((a.lng-b.lng)*95000)**2 + ((a.lat-b.lat)*111000)**2) ** 0.5


@app.post("/api/itineraries/generate")
async def generate_itinerary(request: ItineraryRequest):
    logger.info("itinerary generation started people=%d days=%d must_visit=%d provider=%s model=%s", request.people, request.days, len(request.mustVisitPlaces), settings.ai_provider, settings.deepseek_model if settings.ai_provider == "deepseek" else settings.openai_model)
    if not (settings.deepseek_api_key if settings.ai_provider == "deepseek" else settings.openai_api_key):
        raise HTTPException(503, "未配置当前 AI Provider 的 API Key")
    return await build_itinerary(request)


@app.post("/api/itineraries/generate/stream")
async def generate_itinerary_stream(request: ItineraryRequest):
    if not (settings.deepseek_api_key if settings.ai_provider == "deepseek" else settings.openai_api_key):
        raise HTTPException(503, "未配置当前 AI Provider 的 API Key")

    async def events():
        queue: asyncio.Queue[dict] = asyncio.Queue()
        async def progress(percent: int, label: str):
            await queue.put({"type": "progress", "percent": percent, "label": label})

        async def worker():
            try:
                result = await asyncio.wait_for(build_itinerary(request, progress=progress), timeout=150)
                await queue.put({"type": "complete", "percent": 100, "label": "行程生成完成", "result": result})
            except HTTPException as exc:
                await queue.put({"type": "error", "detail": str(exc.detail)})
            except asyncio.TimeoutError:
                await queue.put({"type": "error", "detail": "规划超过 150 秒，请稍后重试"})
            except Exception:
                logger.exception("itinerary stream failed")
                await queue.put({"type": "error", "detail": "行程生成失败，请稍后重试"})

        job = asyncio.create_task(worker())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=10)
                except asyncio.TimeoutError:
                    yield json.dumps({"type": "heartbeat"}, ensure_ascii=False) + "\n"
                    continue
                yield json.dumps(event, ensure_ascii=False) + "\n"
                if event["type"] in ("complete", "error"):
                    break
        finally:
            if not job.done():
                job.cancel()

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/routes/calculate")
async def route_calculate(request: RouteRequest):
    return await calculate_route(request.origin, request.destination, request.mode)


@app.post("/api/places/intro")
async def place_intro(request: IntroRequest):
    async def worker(emit):
        await emit("tool_started", {"label": "联网查询地点资料与来源"})
        info = await get_place_info(request.place)
        answer = info.get("introduction") or f"{request.place.name}：联网资料暂不可用，请稍后刷新地点详情。"
        sources = [{"id": f"intro-source-{i}", "title": source["title"], "url": source["url"]}
                   for i, source in enumerate(info.get("sources", []), 1)
                   if isinstance(source.get("title"), str) and isinstance(source.get("url"), str)]
        await emit("tool_completed", {"label": "联网资料已整理"})
        for position in range(0, len(answer), 24):
            await emit("message_delta", {"text": answer[position:position + 24]})
            await asyncio.sleep(0.015)
        return {"text": answer, "sources": sources}
    return await tasks.create_task(request.tripId, request.tripSecret, request.revision, "place_intro", worker)


@app.post("/api/chat")
async def chat_task(request: ChatRequest):
    async def worker(emit):
        pending = ""
        last_emit = time.monotonic()
        streamed = False
        message_sources = []

        async def on_sources(sources: list[dict]):
            message_sources.extend(sources)

        async def on_delta(piece: str):
            nonlocal pending, last_emit, streamed
            streamed = True
            pending += piece
            now = time.monotonic()
            if len(pending) >= 24 or now - last_emit >= 0.09:
                await emit("message_delta", {"text": pending})
                pending = ""
                last_emit = now

        result = await chat_graph.ainvoke({
            "snapshot": request.snapshot,
            "message": request.message,
            "locked": request.snapshot.tripId in tasks.TRIP_LOCKS,
            "on_delta": on_delta,
            "on_sources": on_sources,
        })
        if pending:
            await emit("message_delta", {"text": pending})
        patch = result.get("patch")
        if patch:
            await emit("tool_started", {"label": "校验行程修改"})
            await emit("tool_completed", {"label": "修改提案已生成"})
            return {"text": result["answer"], "patch": patch}
        answer = result["answer"]
        if not streamed:
            for position in range(0, len(answer), 80):
                await emit("message_delta", {"text": answer[position:position + 80]})
        return {"text": answer, "action": result.get("action"), "sources": message_sources}
    return await tasks.create_task(request.snapshot.tripId, request.snapshot.tripSecret, request.snapshot.revision, "chat", worker)


@app.post("/api/plans/generate")
async def generate(request: PlanRequest):
    if not request.snapshot.pendingPlaces and not request.snapshot.savedPlaces and not request.snapshot.itinerary:
        raise HTTPException(422, "请先加入至少一个待安排或收藏地点")
    async def worker(emit):
        candidates = await plan(request.snapshot, emit)
        return {"candidates": [candidate.model_dump() for candidate in candidates]}
    return await tasks.create_task(request.snapshot.tripId, request.snapshot.tripSecret, request.snapshot.revision, "planning", worker)


@app.post("/api/plans/optimize")
async def optimize(request: PlanRequest):
    if not request.snapshot.itinerary:
        raise HTTPException(422, "当前没有可优化的行程")
    async def worker(emit):
        candidates = await plan(request.snapshot, emit)
        return {"candidates": [candidate.model_dump() for candidate in candidates]}
    return await tasks.create_task(request.snapshot.tripId, request.snapshot.tripSecret, request.snapshot.revision, "optimization", worker)


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str, x_task_token: str = Header(default="")):
    return tasks.task_info(task_id, x_task_token)


@app.post("/api/tasks/{task_id}/heartbeat")
def heartbeat(task_id: str, x_task_token: str = Header(default="")):
    return tasks.heartbeat(task_id, x_task_token)


@app.post("/api/tasks/{task_id}/cancel")
def cancel(task_id: str, x_task_token: str = Header(default="")):
    return tasks.cancel(task_id, x_task_token)


@app.get("/api/tasks/{task_id}/events")
async def events(task_id: str, x_task_token: str = Header(default=""), after: int = Query(default=0, ge=0)):
    token = x_task_token
    tasks.authorized(task_id, token)
    async def stream():
        last = after
        while True:
            for event in tasks.events_after(task_id, last):
                last = event["eventId"]
                yield f"id: {last}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            status = tasks.task_info(task_id, token)["status"]
            if status in ("completed", "failed", "cancelled"):
                break
            yield ": keepalive\n\n"
            await asyncio.sleep(0.6)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.delete("/api/trips/{trip_id}/tasks")
async def delete_tasks(trip_id: str, x_trip_secret: str = Header(default="")):
    map_places.list_places(trip_id, x_trip_secret)
    await tasks.delete_trip_tasks(trip_id, x_trip_secret)
    map_places.delete_trip_places(trip_id, x_trip_secret)
    return {"ok": True}


@app.get("/api/maps/static")
async def static_map(points: str = Query(max_length=800), path: str = Query(default="", max_length=5000)):
    if not settings.amap_web_service_key:
        raise HTTPException(503, "未配置高德 Web 服务密钥")
    coordinates = []
    for item in points.split("|")[:30]:
        try:
            lng, lat = (float(value) for value in item.split(","))
        except ValueError:
            raise HTTPException(422, "坐标格式错误")
        if not (113 <= lng <= 116 and 29 <= lat <= 32):
            raise HTTPException(422, "坐标超出武汉区域")
        coordinates.append(f"{lng:.6f},{lat:.6f}")
    if not coordinates:
        raise HTTPException(422, "缺少坐标")
    params = {"key": settings.amap_web_service_key, "size": "750*420", "scale": 1, "markers": "mid,0x1db6c4,0:" + "|".join(coordinates)}
    route_coordinates = []
    for item in path.split("|")[:90] if path else []:
        try:
            lng, lat = (float(value) for value in item.split(","))
        except ValueError:
            raise HTTPException(422, "路线坐标格式错误")
        if not (113 <= lng <= 116 and 29 <= lat <= 32):
            raise HTTPException(422, "路线坐标超出武汉区域")
        route_coordinates.append(f"{lng:.6f},{lat:.6f}")
    if len(route_coordinates) > 1:
        params["paths"] = "5,0x1db6c4,0.75,,0:" + ";".join(route_coordinates)
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get("https://restapi.amap.com/v3/staticmap", params=params)
    if response.status_code != 200 or not response.headers.get("content-type", "").startswith("image/"):
        raise HTTPException(502, "静态地图获取失败")
    return Response(response.content, media_type=response.headers["content-type"], headers={"Cache-Control": "private, max-age=300"})
