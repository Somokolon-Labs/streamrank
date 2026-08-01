"""Configuration for the StreamRank serving stack."""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"), env_file_encoding="utf-8", extra="ignore", protected_namespaces=()
    )

    app_env: str = "local"
    version: str = "1.0.0"
    log_level: str = "INFO"
    log_json: bool = False

    # ---- storage -------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./data/streamrank.db"
    redis_enabled: bool = False
    redis_url: str = "redis://localhost:6379/0"

    # ---- serving -------------------------------------------------------
    api_keys: str = "demo-key-streamrank"
    require_api_key: bool = False
    cors_origins: str = "*"
    artifacts_dir: str = "ml/artifacts"
    catalog_path: str = "data/catalog.json"

    # retrieval
    candidates_ann: int = 120           # nearest neighbours from the embedding index
    candidates_covisit: int = 60        # co-visitation neighbours
    candidates_trending: int = 40       # trending / popularity fallback
    ann_probe_clusters: int = 6         # IVF cells scanned per query
    max_results: int = 24
    mmr_lambda: float = 0.82            # diversification: 1.0 = pure relevance
    category_cap: int = 6               # max items per category in one response

    # streaming features
    session_decay: float = 0.86         # EMA weight for the running session vector
    trending_halflife_s: float = 900.0
    session_ttl_s: int = 3600
    feature_flush_interval_s: float = 2.0

    # experiment
    experiment_name: str = "ranker-v1-vs-popularity"
    control_share: float = 0.5
    control_variant: str = "popularity"
    treatment_variant: str = "two-stage"

    @field_validator("database_url")
    @classmethod
    def _async_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            value = value.replace("postgres://", "postgresql+asyncpg://", 1)
        elif value.startswith("postgresql://"):
            value = value.replace("postgresql://", "postgresql+asyncpg://", 1)
        if value.startswith("sqlite://") and "+aiosqlite" not in value:
            value = value.replace("sqlite://", "sqlite+aiosqlite://", 1)
        if "asyncpg" in value and "?" in value:
            base, _, query = value.partition("?")
            keep = [p for p in query.split("&") if not p.startswith(("sslmode", "channel_binding"))]
            value = base + ("?" + "&".join(keep) if keep else "")
        return value

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
