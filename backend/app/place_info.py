"""Evidence-backed place research using DeepSeek Search/Fetch and a short-lived SQLite cache."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from .assistant import _model
from .config import settings
from .models import Place
from .request_context import request_id

FIELDS = ("openingHours", "todayNotice", "adultPrice", "discounts", "reservation", "reservationUrl", "phone", "address", "visitorNotes")
SEARCH_ENDPOINT = "https://api.deepseek.com/v1/tools/search"
FETCH_ENDPOINT = "https://api.deepseek.com/v1/tools/fetch"
CACHE_TTL = timedelta(hours=12)
FAILURE_CACHE_TTL = timedelta(minutes=5)
PLACE_INFO_CACHE_VERSION = "v2"
MAX_SOURCES = 12
MAX_FETCHES = 10
MAX_PAGE_CHARS = 10000
PLACE_INFO_TIMEOUT = 65
CHAT_WEB_TIMEOUT = 22
UPSTREAM_TIMEOUT = 14
_FETCH_LIMIT = asyncio.Semaphore(4)
_PLACE_LOCKS: dict[str, asyncio.Lock] = {}
_LOG = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cache_connection():
    con = sqlite3.connect(settings.task_database, timeout=2)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS place_info_cache (place_key TEXT PRIMARY KEY, payload TEXT NOT NULL, checked_at TEXT NOT NULL)")
    return con


def _read_cache(key: str):
    with closing(_cache_connection()) as con:
        return con.execute("SELECT payload, checked_at FROM place_info_cache WHERE place_key=?", (key,)).fetchone()


def _write_cache(key: str, payload: dict, checked_at: str) -> None:
    with closing(_cache_connection()) as con:
        con.execute("INSERT OR REPLACE INTO place_info_cache VALUES (?,?,?)", (
            key, json.dumps(payload, ensure_ascii=False), checked_at,
        ))
        con.commit()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).casefold()


def _clean_json(text: str) -> dict:
    cleaned = text.strip()
    fence = chr(96) * 3
    if cleaned.startswith(fence):
        cleaned = cleaned[len(fence):]
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    if cleaned.endswith(fence):
        cleaned = cleaned[:-len(fence)]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        return {}
    result = json.loads(cleaned[start:end + 1])
    return result if isinstance(result, dict) else {}


def _safe_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 8192:
        return None
    parsed = urlparse(value.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        return None
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
        return None
    try:
        address = ipaddress.ip_address(host)
        if not address.is_global:
            return None
    except ValueError:
        pass
    return urlunparse(parsed._replace(fragment=""))


def _url_key(url: str) -> str:
    parsed = urlparse(url)
    tracking = {"from", "spm", "source", "ref"}
    query = [
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in tracking
    ]
    return urlunparse(parsed._replace(query=urlencode(query), fragment="")).rstrip("/").casefold()


def _official_host(place: Place, host: str) -> bool:
    candidates = []
    if place.officialUrl:
        parsed = urlparse(place.officialUrl)
        if parsed.hostname:
            candidates.append(parsed.hostname.lower().removeprefix("www."))
    known = {"黄鹤楼": {"cnhhl.com", "www.cnhhl.com"}}
    for name, domains in known.items():
        if name in place.name:
            candidates.extend(domains)
    normalized = host.lower().removeprefix("www.")
    return normalized == "gov.cn" or normalized.endswith(".gov.cn") or any(
        normalized == item or normalized.endswith("." + item)
        for item in candidates
    )


def _source_type(place: Place, url: str, authority: object) -> tuple[str, bool]:
    host = (urlparse(url).hostname or "").lower()
    official = _official_host(place, host)
    if official:
        return ("official_government" if host.endswith(".gov.cn") else "official_venue", True)
    try:
        if int(authority) >= 3:
            return "authoritative_reference", False
    except (TypeError, ValueError):
        pass
    if any(token in host for token in ("museum", "wh.cn", "wuhan", "chinaculture", "gov")):
        return "institutional_or_cultural", False
    if any(token in host for token in ("news", "people.com", "xinhuanet", "cctv", "chinanews")):
        return "media", False
    return "other", False


def _source(place: Place, raw: dict, checked_at: str) -> dict | None:
    url = _safe_url(raw.get("url"))
    if not url:
        return None
    authority = raw.get("authority", -1)
    source_type, official = _source_type(place, url, authority)
    title = str(raw.get("title") or raw.get("site_name") or url).strip()[:300]
    summary = str(raw.get("summary") or "").strip()
    snippet = str(raw.get("snippet") or "").strip()
    content = str(raw.get("content") or "").strip()
    published = raw.get("date")
    if not isinstance(published, str):
        published = None
    return {
        "title": title, "url": url, "publishedAt": published, "checkedAt": checked_at,
        "official": official, "sourceType": source_type, "authority": authority,
        "rerankScore": raw.get("rerank_score"), "summary": summary[:2500],
        "snippet": snippet[:1000], "content": content[:MAX_PAGE_CHARS],
        "fetchStatus": "search_content" if len(content) >= 200 else "not_attempted",
        "fetchError": None,
    }


def _search_queries(place: Place) -> list[str]:
    name = place.name.strip()
    official_host = urlparse(place.officialUrl).hostname if place.officialUrl else None
    if not official_host and "黄鹤楼" in name:
        official_host = "cnhhl.com"
    queries = []
    if official_host:
        host = official_host.removeprefix("www.")
        queries.extend([
            f"{name} site:{host} 门票政策 成人票价 优惠票",
            f"{name} site:{host} 预约入口 游客须知 最新公告",
        ])
    else:
        queries.extend([
            f"{name} 官方 门票政策 成人票价 开放时间",
            f"{name} 官方 预约入口 游客须知 最新公告",
        ])
    queries.extend([
        f"{name} site:wuhan.gov.cn 管理处 联系电话 地址",
        f"{name} 门票政策 成人票价",
        f"{name} 景区开放时间 入园时间 停止售票 清园时间",
        f"{name} 历史文化 建筑特色 游览看点",
    ])
    return list(dict.fromkeys(q.strip()[:128] for q in queries if q.strip()))[:6]


def _matches_place(place: Place, source: dict) -> bool:
    name = _normalize(place.name)
    if not name:
        return False
    # A mention in a search snippet alone is too weak: broad government pages
    # frequently mention many places without being about the selected POI.
    title = _normalize(source["title"])
    # Require the selected POI to be the subject of the page, not merely one
    # incidental mention in a different attraction's travel guide.
    return name in title


async def _post_json(client: httpx.AsyncClient, endpoint: str, headers: dict, body: dict) -> dict:
    for attempt in range(2):
        response = await asyncio.wait_for(client.post(endpoint, headers=headers, json=body), timeout=UPSTREAM_TIMEOUT)
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == 0:
                await asyncio.sleep(0.6)
                continue
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("DeepSeek 返回格式不是 JSON 对象")
        return data
    raise httpx.HTTPError("DeepSeek 请求重试失败")


async def _search_one(client: httpx.AsyncClient, headers: dict, query: str, place: Place, errors: list[str] | None = None) -> list[dict]:
    started = time.monotonic()
    try:
        data = await _post_json(client, SEARCH_ENDPOINT, headers, {
            "query": query, "count": 5, "need_content": True, "ori_question": f"查询武汉景点：{place.name}",
        })
        results = data.get("results", [])
        if not isinstance(results, list):
            return []
        return [item for item in results if isinstance(item, dict)]
    except (httpx.HTTPError, asyncio.TimeoutError, ValueError, TypeError) as exc:
        if errors is not None:
            errors.append("timeout" if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)) else type(exc).__name__)
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        _LOG.warning("place_search request_id=%s place_id=%s category=%s upstream_status=%s elapsed_ms=%d", request_id.get(), place.placeId, type(exc).__name__, status, int((time.monotonic()-started)*1000))
        return []


async def _fetch_one(client: httpx.AsyncClient, headers: dict, source: dict) -> None:
    async with _FETCH_LIMIT:
        started = time.monotonic()
        try:
            data = await _post_json(client, FETCH_ENDPOINT, headers, {"url": source["url"]})
            content = data.get("content")
            if isinstance(content, str) and content.strip():
                source["content"] = content.strip()[:MAX_PAGE_CHARS]
                source["title"] = str(data.get("title") or source["title"])[:300]
                source["fetchStatus"] = "fetched"
                source["fetchError"] = None
            else:
                source["fetchStatus"] = "failed"
                source["fetchError"] = "empty_content"
        except httpx.HTTPStatusError as exc:
            source["fetchStatus"] = "failed"
            source["fetchError"] = f"http_{exc.response.status_code}"
            _LOG.warning("place_fetch request_id=%s source_host=%s category=HTTPStatusError upstream_status=%d elapsed_ms=%d", request_id.get(), urlparse(source["url"]).hostname, exc.response.status_code, int((time.monotonic()-started)*1000))
        except (httpx.TimeoutException, asyncio.TimeoutError):
            source["fetchStatus"] = "failed"
            source["fetchError"] = "timeout"
            _LOG.warning("place_fetch request_id=%s source_host=%s category=timeout elapsed_ms=%d", request_id.get(), urlparse(source["url"]).hostname, int((time.monotonic()-started)*1000))
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            source["fetchStatus"] = "failed"
            source["fetchError"] = "request_error"
            _LOG.warning("place_fetch request_id=%s source_host=%s category=%s elapsed_ms=%d", request_id.get(), urlparse(source["url"]).hostname, type(exc).__name__, int((time.monotonic()-started)*1000))


def _source_evidence(sources: list[dict]) -> list[dict]:
    evidence = []
    for index, source in enumerate(sources, 1):
        text = "\n\n".join(part for part in (
            source["content"], source["summary"], source["snippet"]
        ) if part)
        evidence.append({
            "id": index, "title": source["title"], "url": source["url"],
            "sourceType": source["sourceType"], "official": source["official"],
            "authority": source["authority"], "publishedAt": source["publishedAt"],
            "fetchStatus": source["fetchStatus"], "text": text[:7000],
        })
    return evidence


def _evidence_matches_field(field: str, quote: str) -> bool:
    if field == "openingHours":
        return bool(re.search(r"\d{1,2}[:：]\d{2}|开放时间|营业时间|开放时段", quote))
    if field == "todayNotice":
        return any(word in quote for word in ("公告", "闭馆", "暂停开放", "临时", "通知"))
    if field == "adultPrice":
        return bool(re.search(r"\d+(?:\.\d+)?\s*(?:元|人民币|¥|￥)", quote)) or any(word in quote for word in ("票价", "成人票", "门票"))
    if field == "discounts":
        return any(word in quote for word in ("优惠", "免票", "半价", "免费", "学生票", "老人票"))
    if field == "reservation":
        return any(word in quote for word in ("预约", "预订", "实名制"))
    if field == "reservationUrl":
        return bool(re.search(r"https?://", quote)) or any(word in quote for word in ("预约入口", "预约网址", "预约链接"))
    if field == "phone":
        return bool(re.search(r"(?:\+?86[- ]?)?1?\d{7,12}", quote))
    if field == "address":
        return any(word in quote for word in ("地址", "路", "街", "号", "大道"))
    if field == "visitorNotes":
        return any(word in quote for word in ("游客须知", "游览须知", "注意事项", "禁止", "请勿"))
    return False


def _validate_fields(raw_fields: object, sources: list[dict], checked_at: str) -> dict:
    fields = {}
    if not isinstance(raw_fields, dict):
        return fields
    for field in FIELDS:
        item = raw_fields.get(field)
        if not isinstance(item, dict) or not isinstance(item.get("value"), str):
            continue
        source_id, quote = item.get("sourceId"), item.get("evidence")
        if not isinstance(source_id, int) or not 1 <= source_id <= len(sources):
            continue
        if not isinstance(quote, str) or len(quote.strip()) < 5:
            continue
        source = sources[source_id - 1]
        source_text = "\n".join((source["content"], source["summary"], source["snippet"]))
        if _normalize(quote) not in _normalize(source_text):
            continue
        if not _evidence_matches_field(field, quote):
            continue
        value = item["value"].strip()
        quote_numbers = set(re.findall(r"\d+(?:\.\d+)?", quote))
        value_numbers = set(re.findall(r"\d+(?:\.\d+)?", value))
        if not value_numbers.issubset(quote_numbers):
            value = quote.strip()
        if field == "reservationUrl":
            candidate = _safe_url(value)
            if not candidate or candidate not in source_text:
                continue
            value = candidate
        if field == "todayNotice":
            published = source.get("publishedAt") or ""
            if not source["official"] or not published.startswith(checked_at[:10]):
                continue
        if field in {"phone", "address", "reservation", "reservationUrl", "discounts"} and not source["official"]:
            continue
        if field == "visitorNotes" and not source["official"]:
            published = source.get("publishedAt")
            if not published:
                continue
            try:
                note_date = datetime.fromisoformat(published.replace("Z", "+00:00"))
                if note_date.tzinfo is None:
                    note_date = note_date.replace(tzinfo=timezone.utc)
                if _now() - note_date > timedelta(days=365) or _now() - note_date < timedelta(days=-1):
                    continue
            except (ValueError, TypeError):
                continue
        if field in {"openingHours", "adultPrice", "discounts", "reservation", "phone"}:
            published = source.get("publishedAt")
            if not source["official"] and not published:
                continue
            if published:
                try:
                    published_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                    if published_dt.tzinfo is None:
                        published_dt = published_dt.replace(tzinfo=timezone.utc)
                    age = _now() - published_dt
                    if age > timedelta(days=365) or age < -timedelta(days=1):
                        continue
                except (ValueError, TypeError):
                    continue
        corroborating_ids = []
        if field in {"openingHours", "adultPrice"} and not source["official"]:
            # A recent third-party claim is not enough for live hours/prices:
            # require a second independent, recent source with matching evidence.
            primary_host = (urlparse(source["url"]).hostname or "").lower()
            proofs = item.get("corroboratingEvidence")
            if not isinstance(proofs, list):
                continue
            for proof in proofs:
                if not isinstance(proof, dict):
                    continue
                other_id, other_quote = proof.get("sourceId"), proof.get("quote")
                if not isinstance(other_id, int) or not 1 <= other_id <= len(sources) or other_id == source_id:
                    continue
                other = sources[other_id - 1]
                other_host = (urlparse(other["url"]).hostname or "").lower()
                if not other_host or other_host == primary_host or not isinstance(other_quote, str):
                    continue
                other_text = "\n".join((other["content"], other["summary"], other["snippet"]))
                if _normalize(other_quote) not in _normalize(other_text) or not _evidence_matches_field(field, other_quote):
                    continue
                other_date = other.get("publishedAt")
                if not other["official"]:
                    if not other_date:
                        continue
                    try:
                        parsed_date = datetime.fromisoformat(other_date.replace("Z", "+00:00"))
                        if parsed_date.tzinfo is None:
                            parsed_date = parsed_date.replace(tzinfo=timezone.utc)
                        if _now() - parsed_date > timedelta(days=365) or _now() - parsed_date < timedelta(days=-1):
                            continue
                    except (ValueError, TypeError):
                        continue
                if value_numbers and not value_numbers.issubset(set(re.findall(r"\d+(?:\.\d+)?", other_quote))):
                    continue
                corroborating_ids.append(other_id)
            if not corroborating_ids:
                continue
        fields[field] = {
            "value": value, "sourceId": source_id, "evidence": quote.strip(),
            "corroboratingSourceIds": sorted(set(corroborating_ids)),
            "checkedAt": checked_at, "publishedAt": source.get("publishedAt"),
        }
    return fields


def _validate_conflicts(raw_conflicts: object, sources: list[dict]) -> list[str]:
    if not isinstance(raw_conflicts, list):
        return []
    valid = []
    for item in raw_conflicts[:8]:
        if not isinstance(item, dict):
            continue
        field, ids = item.get("field"), item.get("sourceIds")
        evidence = item.get("evidence")
        if field not in FIELDS or not isinstance(ids, list) or not isinstance(evidence, list):
            continue
        claimed_ids = {i for i in ids if isinstance(i, int) and 1 <= i <= len(sources)}
        verified = []
        for proof in evidence:
            if not isinstance(proof, dict):
                continue
            source_id, quote = proof.get("sourceId"), proof.get("quote")
            if not isinstance(source_id, int) or source_id not in claimed_ids or not isinstance(quote, str):
                continue
            source = sources[source_id - 1]
            text = "\n".join((source["content"], source["summary"], source["snippet"]))
            if len(quote.strip()) >= 5 and _normalize(quote) in _normalize(text) and _evidence_matches_field(field, quote):
                verified.append((source_id, _normalize(quote)))
        verified_ids = {source_id for source_id, _ in verified}
        distinct_claims = {quote for _, quote in verified}
        if len(verified_ids) < 2 or len(distinct_claims) < 2:
            continue
        detail = str(item.get("description") or "不同来源对该信息的表述不一致").strip()[:300]
        valid.append(f"{field}：{detail}（来源{','.join(map(str, sorted(verified_ids)))}）")
    return valid


def _introduction_prompt() -> str:
    return (
        "你是一位熟悉武汉历史、城市文化与旅游体验的中文讲解员，同时负责严谨核实动态信息。"
        "请根据地点基本信息和网页证据，生成一份有内容、有讲解感的中文介绍，通常400—800个汉字；"
        "复杂景点可以更长。不要为了达到字数重复句子。自然地讲清景点来历、历史背景、建筑或自然特色、"
        "重要人物与文化故事、在武汉城市发展中的位置、值得现场观察的细节、游览体验，以及与周边的联系。"
        "不要机械地逐项罗列，也不要每段写成模板。"
        "历史文化内容可以结合你已有的可靠知识补充，但不得杜撰年代、人物关系、事件或建筑细节；"
        "网页证据支持的具体事实用[来源编号]标注。关键历史细节若仅来自模型已有知识、未被本次网页资料支持，"
        "请用自然的短语说明这是背景知识、尚未由本次网页资料独立核实。"
        "明确区分事实、合理解释和游览建议。不要把网页搜索摘要伪装成Fetch正文。"
        "开放时间、今日状态、门票、优惠、预约、电话等动态信息只能依据本次提供的近期可靠证据；"
        "没有可靠证据就不要猜测，也不要在介绍中给出猜测值。"
        "返回严格JSON对象，字段为：introduction（字符串）、fields（对象）、conflicts（数组）。"
        "fields仅允许以下键：openingHours,todayNotice,adultPrice,discounts,reservation,reservationUrl,phone,address,visitorNotes。"
        "每个字段格式为{value:string,evidence:string,sourceId:integer,corroboratingEvidence?:[{sourceId:integer,quote:string}]}，evidence必须是对应来源正文中的连续原文。"
        "如果开放时间或成人票价仅来自第三方来源，必须另外提供至少一个不同网站域名、近期且数值一致的corroboratingEvidence；否则省略该字段。"
        "conflicts格式为[{field:string,sourceIds:[整数,整数],evidence:[{sourceId:整数,quote:string}],description:string}]；每个quote必须是对应来源中的连续原文，且两条证据确实表达不同值。只报告证据确实相互矛盾的情况。"
        "来源编号必须来自输入，绝不能生成来源或URL。没有可靠证据就省略字段。"
    )


async def _generate(place: Place, sources: list[dict], checked_at: str) -> dict:
    user = json.dumps({
        "place": place.model_dump(), "checkedAt": checked_at,
        "sourceCount": len(sources), "sources": _source_evidence(sources),
    }, ensure_ascii=False)
    answer = await _model(_introduction_prompt(), user)
    extracted = _clean_json(answer) if answer else {}
    fields = _validate_fields(extracted.get("fields"), sources, checked_at)
    # Always run a dedicated evidence-extraction pass. The narrative model may
    # correctly produce an introduction while omitting structured ticket facts.
    field_prompt = (
        "你是景点动态信息证据提取器，不要撰写介绍。逐项检查所有网页资料，"
        "只输出JSON对象：{\"fields\":{\"字段名\":{\"value\":\"...\",\"evidence\":\"...\",\"sourceId\":1}}}。"
        "字段名只能是openingHours,todayNotice,adultPrice,discounts,reservation,reservationUrl,phone,address,visitorNotes。"
        "重点检索成人票价、优惠政策、常规开放时间、预约要求和入口、电话、地址、游客须知。"
        "优先使用景区运营官方网站；其次使用明确相关的政府文旅部门或景区管理机构；第三方平台只能作为辅助，不能单独确认动态票价。"
        "每个evidence必须逐字复制对应来源text中的连续原句，至少5字，sourceId必须与来源编号一致。"
        "如果开放时间或成人票价仅来自第三方来源，必须另给corroboratingEvidence数组，至少引用一个不同网站域名、近期且数值一致的独立来源，格式为[{sourceId:整数,quote:原文}]；没有第二个独立来源就不要输出该字段。"
        "必须区分日场/夜场、售票时间/停止入园时间、成人票/演出票；不得混为一个值。"
        "不得把历史公告当作今日公告；不得把文章发布日期当作事实生效日期。"
        "不得编造、推断、使用模型记忆补充动态事实。没有可靠证据的字段不要输出。"
    )
    field_answer = await _model(field_prompt, user)
    if field_answer:
        try:
            field_data = _clean_json(field_answer)
            fields.update({
                key: value for key, value in _validate_fields(
                    field_data.get("fields"), sources, checked_at
                ).items() if key not in fields
            })
        except (ValueError, TypeError, json.JSONDecodeError):
            pass
    conflicts = _validate_conflicts(extracted.get("conflicts"), sources)
    introduction = extracted.get("introduction")
    if not isinstance(introduction, str) or len(introduction.strip()) < 80:
        fallback_prompt = (
            "请用简体中文写一篇自然、有吸引力的武汉景点讲解，约400—800字。"
            "可综合可靠常识和给出的网页资料；不要编造具体史实或动态票务信息。"
            "网页支持的事实标注[来源编号]；未被网页支持的关键历史细节明确标注为未独立核实的背景知识。"
            "只输出介绍正文，不要JSON。"
        )
        fallback = await _model(fallback_prompt, user)
        introduction = fallback if isinstance(fallback, str) and len(fallback.strip()) >= 80 else ""
    if not introduction:
        introduction = (
            f"## {place.name}\n\n{place.description or f'{place.name}是武汉地图上的一个{place.category}。'}"
            "目前抓取到的公开网页资料不足以支持更具体的历史或开放信息，建议以景区现场及官方公告为准。"
        )
    introduction = re.sub(
        r"\[来源\s*(\d+)\]",
        lambda match: match.group(0) if 1 <= int(match.group(1)) <= len(sources) else "（未找到对应网页来源）",
        introduction,
    )
    return {"fields": fields, "conflicts": conflicts, "introduction": introduction.strip()}


def _public_source(source: dict) -> dict:
    return {key: source.get(key) for key in (
        "title", "url", "publishedAt", "checkedAt", "official", "sourceType",
        "authority", "fetchStatus", "fetchError",
    )}


def _failure(place: Place, checked_at: str, old: dict | None, error: str) -> dict:
    if old:
        return {**old, "stale": True, "cached": False, "error": error}
    return {
        "status": "unavailable", "checkedAt": checked_at, "stale": False,
        "cached": False, "fields": {}, "sources": [],
        "introduction": f"{place.name}：联网资料暂不可用，请稍后刷新地点详情。",
        "conflict": "", "error": error,
    }


async def _get_place_info_locked(place: Place, refresh: bool, key: str) -> dict:
    started = time.monotonic()
    old = None
    row = await asyncio.to_thread(_read_cache, key)
    if row:
        try:
            old = json.loads(row[0])
            checked = datetime.fromisoformat(row[1])
            ttl = FAILURE_CACHE_TTL if old.get("status") in {"not_found", "unavailable"} else CACHE_TTL
            if not refresh and _now() - checked < ttl:
                _LOG.info("place_info request_id=%s cache_hit place_id=%s elapsed_ms=%d", request_id.get(), place.placeId, int((time.monotonic()-started)*1000))
                return {**old, "stale": False, "cached": True}
        except (ValueError, TypeError, json.JSONDecodeError):
            old = None

    checked_at = _now().isoformat()
    if not settings.deepseek_api_key:
        return _failure(place, checked_at, old, "未配置 DEEPSEEK_API_KEY")

    headers = {"Authorization": f"Bearer {settings.deepseek_api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(14, connect=5)) as client:
            search_started = time.monotonic()
            search_errors: list[str] = []
            query_results = await asyncio.gather(*(
                _search_one(client, headers, query, place, search_errors) for query in _search_queries(place)
            ))
            _LOG.info("place_info request_id=%s search_done place_id=%s elapsed_ms=%d results=%d", request_id.get(), place.placeId, int((time.monotonic()-search_started)*1000), sum(map(len, query_results)))
            if len(search_errors) == len(query_results) and not any(query_results):
                error = "DeepSeek Search 查询超时" if "timeout" in search_errors else "DeepSeek Search 请求失败"
                failure = _failure(place, checked_at, old, error)
                if not old:
                    await asyncio.to_thread(_write_cache, key, failure, checked_at)
                return failure
            raw_results = [item for group in query_results for item in group]
            unique: dict[str, dict] = {}
            for raw in raw_results:
                source = _source(place, raw, checked_at)
                if not source or not _matches_place(place, source):
                    continue
                key_url = _url_key(source["url"])
                existing = unique.get(key_url)
                if existing is None or len(source["content"] or source["summary"]) > len(existing["content"] or existing["summary"]):
                    unique[key_url] = source
            sources = list(unique.values())
            source_priority = {
                "official_venue": 0,
                "official_government": 1,
                "authoritative_reference": 2,
                "institutional_or_cultural": 3,
                "media": 4,
                "other": 5,
            }
            def _published_rank(source: dict) -> float:
                try:
                    published = source.get("publishedAt")
                    if not published:
                        return 0.0
                    value = datetime.fromisoformat(published.replace("Z", "+00:00"))
                    if value.tzinfo is None:
                        value = value.replace(tzinfo=timezone.utc)
                    return value.timestamp()
                except (ValueError, TypeError, OverflowError):
                    return 0.0
            sources.sort(key=lambda s: (
                source_priority.get(s["sourceType"], 6),
                -_published_rank(s),
                -(float(s["rerankScore"]) if isinstance(s["rerankScore"], (int, float)) else 0),
                -(int(s["authority"]) if str(s["authority"]).lstrip("-").isdigit() else -1),
                -len(s["summary"]),
            ))
            sources = sources[:MAX_SOURCES]
            fetch_started = time.monotonic()
            await asyncio.gather(*(_fetch_one(client, headers, source) for source in sources[:MAX_FETCHES]))
            _LOG.info("place_info request_id=%s fetch_done place_id=%s elapsed_ms=%d attempted=%d fetched=%d", request_id.get(), place.placeId, int((time.monotonic()-fetch_started)*1000), min(len(sources), MAX_FETCHES), sum(s["fetchStatus"] == "fetched" for s in sources))

        usable = [s for s in sources if len(s["content"] or s["summary"] or s["snippet"]) >= 80]
        if not usable:
            if old:
                return _failure(place, checked_at, old, "no_usable_web_content")
            fetch_failed = any(s["fetchStatus"] == "failed" for s in sources)
            payload = {
                "status": "unavailable" if fetch_failed else "not_found", "checkedAt": checked_at, "fields": {},
                "sources": [_public_source(s) for s in sources],
                "introduction": (
                    f"## {place.name}\n\n{place.description or f'{place.name}是武汉的一个{place.category}。'}"
                    "本次没有获取到足够的可读网页正文，因此不对具体历史细节和动态信息作未经核实的断言。"
                ),
                "conflict": "", "stale": False, "cached": False,
            }
            if fetch_failed:
                payload["error"] = "DeepSeek Fetch 请求失败，未能取得可用资料"
        else:
            ai_started = time.monotonic()
            generated = await _generate(place, sources, checked_at)
            _LOG.info("place_info request_id=%s ai_done place_id=%s elapsed_ms=%d", request_id.get(), place.placeId, int((time.monotonic()-ai_started)*1000))
            conflicts = generated["conflicts"]
            status = "conflict" if conflicts else "verified" if len(generated["fields"]) >= 3 else "partial"
            payload = {
                "status": status, "checkedAt": checked_at,
                "fields": generated["fields"], "sources": [_public_source(s) for s in sources],
                "introduction": generated["introduction"],
                "conflict": "\n".join(conflicts), "stale": False, "cached": False,
            }

        if old and old.get("fields") and not payload.get("fields"):
            return {**old, "stale": True, "cached": False, "error": "refresh_returned_no_verified_facts"}
        await asyncio.to_thread(_write_cache, key, payload, checked_at)
        _LOG.info("place_info request_id=%s completed place_id=%s status=%s cache_hit=false elapsed_ms=%d", request_id.get(), place.placeId, payload["status"], int((time.monotonic()-started)*1000))
        return payload
    except (httpx.HTTPError, asyncio.TimeoutError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        _LOG.warning("place_info request_id=%s failed place_id=%s category=%s elapsed_ms=%d", request_id.get(), place.placeId, type(exc).__name__, int((time.monotonic()-started)*1000))
        return _failure(place, checked_at, old, type(exc).__name__)


async def _search_chat_web(query: str, limit: int = 5) -> list[dict]:
    """Search and fetch public web evidence for a chat question; no cache or trip mutation."""
    if not settings.deepseek_api_key or not query.strip():
        return []
    headers = {"Authorization": f"Bearer {settings.deepseek_api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(14, connect=5)) as client:
            data = await _post_json(client, SEARCH_ENDPOINT, headers, {
                "query": query.strip()[:128], "count": max(1, min(limit, 50)),
                "need_content": True, "ori_question": query.strip()[:128],
            })
            raw = data.get("results", [])
            if not isinstance(raw, list):
                return []
            found = []
            seen = set()
            for item in raw:
                if not isinstance(item, dict):
                    continue
                url = _safe_url(item.get("url"))
                if not url or _url_key(url) in seen:
                    continue
                seen.add(_url_key(url))
                found.append({
                    "title": str(item.get("title") or url)[:200],
                    "url": url,
                    "content": str(item.get("content") or item.get("summary") or item.get("snippet") or "")[:7000],
                })
                if len(found) >= limit:
                    break
            async def fetch_missing(source: dict) -> None:
                if len(source["content"]) >= 400:
                    return
                try:
                    fetched = await _post_json(client, FETCH_ENDPOINT, headers, {"url": source["url"]})
                    content = fetched.get("content")
                    if isinstance(content, str) and content.strip():
                        source["content"] = content[:7000]
                except (httpx.HTTPError, asyncio.TimeoutError, ValueError, TypeError):
                    pass
            await asyncio.gather(*(fetch_missing(item) for item in found[:3]))
            return [item for item in found if item["content"].strip()]
    except (httpx.HTTPError, asyncio.TimeoutError, ValueError, TypeError) as exc:
        _LOG.warning("Chat web search failed category=%s", type(exc).__name__)
        return []


async def search_chat_web(query: str, limit: int = 5) -> list[dict]:
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(_search_chat_web(query, limit), timeout=CHAT_WEB_TIMEOUT)
        _LOG.info("chat_web request_id=%s elapsed_ms=%d sources=%d", request_id.get(), int((time.monotonic()-started)*1000), len(result))
        return result
    except asyncio.TimeoutError:
        _LOG.warning("chat_web request_id=%s timeout elapsed_ms=%d", request_id.get(), int((time.monotonic()-started)*1000))
        return []


async def get_place_info(place: Place, refresh: bool = False) -> dict:
    # Bump the key when evidence/query logic changes so old empty-field results
    # do not remain visible for the normal cache TTL after deployment.
    key = f"{PLACE_INFO_CACHE_VERSION}:{place.placeId}:{place.name}"
    lock = _PLACE_LOCKS.setdefault(key, asyncio.Lock())
    try:
        async with asyncio.timeout(PLACE_INFO_TIMEOUT):
            async with lock:
                return await _get_place_info_locked(place, refresh, key)
    except asyncio.TimeoutError:
        _LOG.warning("place_info request_id=%s timeout place_id=%s total_timeout_s=%d", request_id.get(), place.placeId, PLACE_INFO_TIMEOUT)
        return _failure(place, _now().isoformat(), None, "联网资料查询超时")
    except sqlite3.Error as exc:
        _LOG.error("place_info cache_error place_id=%s category=%s", place.placeId, type(exc).__name__)
        return _failure(place, _now().isoformat(), None, "联网资料缓存暂不可用")
