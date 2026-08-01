"""API contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EventName = Literal["view", "click", "add_to_cart", "purchase"]


class RecommendRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"user_id": "usr_00042", "session_id": "ses_demo", "limit": 12}})

    user_id: str = Field(default="anonymous", max_length=48)
    session_id: str = Field(default="", max_length=48, description="Defaults to the user id when omitted")
    surface: Literal["home", "detail", "cart", "search"] = "home"
    limit: int = Field(default=12, ge=1, le=48)
    exclude: list[str] = Field(default_factory=list, max_length=64)
    category: str | None = Field(default=None, max_length=48)
    variant: str | None = Field(default=None, description="Force an experiment variant (debugging)")
    diversify: bool = True


class ScoredItem(BaseModel):
    rank: int
    item_id: str
    title: str
    brand: str
    category: str
    price: float
    rating: float
    image_url: str
    image_credit: str
    alt_text: str
    score: float
    reason: str
    features: dict[str, float] = Field(default_factory=dict)


class RecommendResponse(BaseModel):
    request_id: str
    user_id: str
    session_id: str
    variant: str
    surface: str
    cold_start: bool
    items: list[ScoredItem]
    stage_counts: dict[str, int]
    timings_ms: dict[str, float]
    session_signal: dict[str, Any]


class EventRequest(BaseModel):
    user_id: str = Field(max_length=48)
    session_id: str = Field(default="", max_length=48)
    item_id: str = Field(max_length=32)
    event: EventName = "click"
    request_id: str | None = Field(default=None, max_length=48)
    position: int | None = Field(default=None, ge=0, le=500)


class EventResponse(BaseModel):
    accepted: bool
    event: str
    session_items: int
    feature_update_us: float
    profile_strength: float


class SimulateRequest(BaseModel):
    users: int = Field(default=40, ge=1, le=500)
    steps: int = Field(default=6, ge=1, le=40)
    surface: Literal["home", "detail", "cart", "search"] = "home"
    click_noise: float = Field(default=0.15, ge=0.0, le=1.0)


class HealthResponse(BaseModel):
    status: str
    version: str
    checks: dict[str, Any] = Field(default_factory=dict)
