"""Async database access."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import event, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import settings
from .models import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        if settings.is_sqlite:
            tail = settings.database_url.split("///")[-1]
            if tail and tail != ":memory:":
                Path(tail).parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict = {"echo": False, "pool_pre_ping": True}
        if settings.is_sqlite:
            kwargs["connect_args"] = {"timeout": 30}
        else:
            kwargs.update(pool_size=10, max_overflow=10, pool_recycle=1800)
        _engine = create_async_engine(settings.database_url, **kwargs)

        if settings.is_sqlite:
            @event.listens_for(_engine.sync_engine, "connect")
            def _pragmas(dbapi_conn, _record):  # pragma: no cover - infra glue
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=" + ("OFF" if settings.app_env == "local" else "NORMAL"))
                cur.execute("PRAGMA busy_timeout=15000")
                cur.execute("PRAGMA temp_store=MEMORY")
                cur.execute("PRAGMA cache_size=-64000")
                cur.close()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False, autoflush=False)
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def missing_tables() -> list[str]:
    """Expected tables that do not exist yet. A read, so no write lock."""

    def _inspect(sync_conn) -> list[str]:
        present = set(inspect(sync_conn).get_table_names())
        return sorted(set(Base.metadata.tables) - present)

    async with get_engine().connect() as conn:
        return await conn.run_sync(_inspect)


async def init_db(retries: int = 10, base_delay_s: float = 0.4) -> None:
    """Idempotent schema creation that tolerates replicas booting together.

    ``create_all`` checks then creates, so simultaneous replicas against an empty
    database can race and one loses the CREATE. Checking first means only the
    first process takes a write lock; the rest verify and continue.
    """
    for attempt in range(1, retries + 1):
        try:
            missing = await missing_tables()
        except Exception:
            missing = sorted(Base.metadata.tables)

        if not missing:
            return

        try:
            async with get_engine().begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            if not await missing_tables():
                return
        except (OperationalError, ProgrammingError, IntegrityError):
            pass

        if attempt == retries:
            raise RuntimeError(f"schema still incomplete after {retries} attempts: {missing}")
        await asyncio.sleep(min(2.0, base_delay_s * attempt))


async def ping() -> bool:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    aware = as_utc(value)
    return aware.isoformat() if aware else None
