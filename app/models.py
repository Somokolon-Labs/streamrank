"""Durable state: catalog, interaction log, impressions and experiment results.

Online features live in memory (and optionally Redis); the tables here are the
durable record used for offline training, experiment analysis and replay.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

BIG_PK = BigInteger().with_variant(Integer, "sqlite")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    brand: Mapped[str] = mapped_column(String(80), index=True)
    category: Mapped[str] = mapped_column(String(48), index=True)
    price: Mapped[float] = mapped_column(Float)
    rating: Mapped[float] = mapped_column(Float, default=4.0)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    image_url: Mapped[str] = mapped_column(String(400), default="")
    image_credit: Mapped[str] = mapped_column(String(120), default="")
    alt_text: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Interaction(Base):
    """Every behavioural signal, exactly as it arrived."""

    __tablename__ = "interactions"

    id: Mapped[int] = mapped_column(BIG_PK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(48), index=True)
    session_id: Mapped[str] = mapped_column(String(48), index=True)
    item_id: Mapped[str] = mapped_column(String(32), index=True)
    event: Mapped[str] = mapped_column(String(16), index=True)  # view | click | add_to_cart | purchase
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    position: Mapped[int | None] = mapped_column(Integer)
    request_id: Mapped[str | None] = mapped_column(String(48), index=True)
    variant: Mapped[str | None] = mapped_column(String(24), index=True)
    source: Mapped[str] = mapped_column(String(24), default="api")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


Index("ix_interactions_user_at", Interaction.user_id, Interaction.at)


class Impression(Base):
    """What was served, to whom, by which variant - the denominator for CTR."""

    __tablename__ = "impressions"

    id: Mapped[int] = mapped_column(BIG_PK, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(48), index=True, unique=True)
    user_id: Mapped[str] = mapped_column(String(48), index=True)
    session_id: Mapped[str] = mapped_column(String(48), index=True)
    surface: Mapped[str] = mapped_column(String(24), default="home")
    variant: Mapped[str] = mapped_column(String(24), index=True)
    item_ids: Mapped[list] = mapped_column(JSON, default=list)
    scores: Mapped[list] = mapped_column(JSON, default=list)
    stage_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    retrieval_ms: Mapped[float] = mapped_column(Float, default=0.0)
    ranking_ms: Mapped[float] = mapped_column(Float, default=0.0)
    total_ms: Mapped[float] = mapped_column(Float, default=0.0)
    cold_start: Mapped[bool] = mapped_column(Boolean, default=False)
    clicked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    clicked_position: Mapped[int | None] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class UserProfile(Base):
    """Long-lived taste vector, updated online and persisted periodically."""

    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    vector: Mapped[list] = mapped_column(JSON, default=list)
    events: Mapped[int] = mapped_column(Integer, default=0)
    top_categories: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ItemStat(Base):
    """Rolling counters used by the trending and popularity retrievers."""

    __tablename__ = "item_stats"

    item_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    views: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    purchases: Mapped[int] = mapped_column(Integer, default=0)
    trend_score: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CoVisit(Base):
    """Item-item co-visitation counts, the cheapest useful candidate source."""

    __tablename__ = "covisits"

    item_a: Mapped[str] = mapped_column(String(32), primary_key=True)
    item_b: Mapped[str] = mapped_column(String(32), primary_key=True)
    weight: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExperimentEvent(Base):
    """Aggregated experiment counters, flushed from memory."""

    __tablename__ = "experiment_events"

    id: Mapped[int] = mapped_column(BIG_PK, primary_key=True, autoincrement=True)
    experiment: Mapped[str] = mapped_column(String(64), index=True)
    variant: Mapped[str] = mapped_column(String(24), index=True)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    purchases: Mapped[int] = mapped_column(Integer, default=0)
    revenue: Mapped[float] = mapped_column(Float, default=0.0)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    note: Mapped[str | None] = mapped_column(Text)
