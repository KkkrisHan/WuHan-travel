from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Point(BaseModel):
    lng: float = Field(ge=113, le=116)
    lat: float = Field(ge=29, le=32)


class Place(BaseModel):
    placeId: str
    name: str
    category: str = "地点"
    address: str = "地址未知"
    location: Point
    source: str = "高德地图"
    description: str = ""
    officialUrl: str | None = None


class Preferences(BaseModel):
    startDate: str | None = None
    days: int = Field(default=1, ge=1, le=7)
    people: int = Field(default=1, ge=1, le=20)
    company: str = ""
    budget: str = "灵活"
    pace: str = "适中"
    transport: str = "步行与公共交通"
    interests: list[str] = Field(default_factory=list)
    requirements: str = ""
    lodging: str = ""
    lodgingLocation: Point | None = None


class Stop(BaseModel):
    id: str
    place: Place
    arrival: str
    departure: str
    minutes: int = 90
    warning: str | None = None


class Leg(BaseModel):
    fromStopId: str
    toStopId: str
    mode: str
    minutes: int | None = None
    minutesMin: int | None = None
    minutesMax: int | None = None
    distanceMeters: int | None = None
    source: str
    polyline: list[Point] = Field(default_factory=list)


class DayPlan(BaseModel):
    id: str
    date: str
    stops: list[Stop]
    legs: list[Leg]
    transportMinutes: int | None = None
    sightseeingMinutes: int
    meals: list[str] = Field(default_factory=list)
    budgetNote: str
    warnings: list[str] = Field(default_factory=list)


class Candidate(BaseModel):
    id: str
    title: str
    subtitle: str
    rationale: str
    days: list[DayPlan]
    warnings: list[str] = Field(default_factory=list)


class Snapshot(BaseModel):
    tripId: str
    tripSecret: str = Field(min_length=32, max_length=128)
    revision: int = Field(ge=0)
    preferences: Preferences
    pendingPlaces: list[Place] = Field(default_factory=list, max_length=40)
    savedPlaces: list[Place] = Field(default_factory=list, max_length=40)
    itinerary: list[DayPlan] = Field(default_factory=list)
    generatedItineraries: list[dict] = Field(default_factory=list, max_length=5)
    selectedPlace: Place | None = None
    userRequest: str = Field(default="", max_length=2000)
    recentMessages: list[str] = Field(default_factory=list, max_length=10)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=80)


class RouteRequest(BaseModel):
    origin: Point
    destination: Point
    mode: Literal["walking", "driving", "transit"] = "walking"


class IntroRequest(BaseModel):
    tripId: str
    tripSecret: str = Field(min_length=32, max_length=128)
    revision: int
    place: Place


class ChatRequest(BaseModel):
    snapshot: Snapshot
    message: str = Field(min_length=1, max_length=2000)


class PlanRequest(BaseModel):
    snapshot: Snapshot


class TaskEvent(BaseModel):
    taskId: str
    eventId: int
    timestamp: str = Field(default_factory=lambda: datetime.now().astimezone().isoformat())
    type: str
    payload: dict = Field(default_factory=dict)


class TaskCreated(BaseModel):
    taskId: str
    accessToken: str
    baseRevision: int

