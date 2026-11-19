"""Personal map markers. Trip ownership uses the existing browser trip secret."""

import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .config import settings


class MapPlaceInput(BaseModel):
    poi_id: str | None = Field(default=None, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    address: str | None = Field(default=None, max_length=500)
    type: str | None = Field(default=None, max_length=200)
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    source: str = Field(pattern=r"^(hotspot|nearby_search|coordinate|search|featured)$")


@contextmanager
def connection():
    db = sqlite3.connect(Path(settings.task_database), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize():
    with connection() as db:
        db.execute("CREATE TABLE IF NOT EXISTS map_place_owners (trip_id TEXT PRIMARY KEY, secret_hash TEXT NOT NULL)")
        db.execute("""CREATE TABLE IF NOT EXISTS map_places (
            id INTEGER PRIMARY KEY AUTOINCREMENT, trip_id TEXT NOT NULL,
            place_key TEXT NOT NULL, poi_id TEXT, name TEXT NOT NULL,
            address TEXT, type TEXT, longitude REAL NOT NULL, latitude REAL NOT NULL,
            source TEXT NOT NULL, UNIQUE(trip_id, place_key))""")
        db.execute("CREATE INDEX IF NOT EXISTS map_places_trip ON map_places(trip_id)")


def _authorize(db, trip_id: str, secret: str, create: bool = False):
    if not trip_id or not secret or len(secret) < 32:
        raise HTTPException(403, "旅行访问凭证无效")
    digest = hashlib.sha256(secret.encode()).hexdigest()
    row = db.execute("SELECT secret_hash FROM map_place_owners WHERE trip_id=?", (trip_id,)).fetchone()
    if row:
        if not secrets.compare_digest(row["secret_hash"], digest):
            raise HTTPException(403, "旅行访问凭证无效")
    elif create:
        db.execute("INSERT INTO map_place_owners (trip_id, secret_hash) VALUES (?, ?) ON CONFLICT(trip_id) DO NOTHING", (trip_id, digest))
        bound = db.execute("SELECT secret_hash FROM map_place_owners WHERE trip_id=?", (trip_id,)).fetchone()
        if not secrets.compare_digest(bound["secret_hash"], digest):
            raise HTTPException(403, "旅行访问凭证无效")


def _record(row):
    return dict(row)


def list_places(trip_id: str, secret: str):
    try:
        with connection() as db:
            _authorize(db, trip_id, secret)
            rows = db.execute("SELECT * FROM map_places WHERE trip_id=? ORDER BY id", (trip_id,)).fetchall()
            return [_record(row) for row in rows]
    except sqlite3.Error as exc:
        raise HTTPException(503, "个人地图读取失败") from exc


def add_place(trip_id: str, secret: str, place: MapPlaceInput):
    poi_id = place.poi_id.strip() if place.poi_id else None
    key = f"poi:{poi_id}" if poi_id else f"coord:{place.longitude:.6f},{place.latitude:.6f}"
    try:
        with connection() as db:
            _authorize(db, trip_id, secret, create=True)
            db.execute("""INSERT INTO map_places
                (trip_id, place_key, poi_id, name, address, type, longitude, latitude, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trip_id, place_key) DO NOTHING""",
                (trip_id, key, poi_id, place.name.strip(), place.address, place.type,
                 place.longitude, place.latitude, place.source))
            row = db.execute("SELECT * FROM map_places WHERE trip_id=? AND place_key=?", (trip_id, key)).fetchone()
            return _record(row)
    except sqlite3.Error as exc:
        raise HTTPException(503, "个人地图保存失败") from exc


def delete_place(trip_id: str, secret: str, place_id: int):
    try:
        with connection() as db:
            _authorize(db, trip_id, secret)
            result = db.execute("DELETE FROM map_places WHERE trip_id=? AND id=?", (trip_id, place_id))
            if not result.rowcount:
                raise HTTPException(404, "标记不存在")
            return {"ok": True}
    except sqlite3.Error as exc:
        raise HTTPException(503, "个人地图删除失败") from exc


def delete_trip_places(trip_id: str, secret: str):
    try:
        with connection() as db:
            _authorize(db, trip_id, secret)
            db.execute("DELETE FROM map_places WHERE trip_id=?", (trip_id,))
            db.execute("DELETE FROM map_place_owners WHERE trip_id=?", (trip_id,))
    except sqlite3.Error as exc:
        raise HTTPException(503, "个人地图清理失败") from exc
