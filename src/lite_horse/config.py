"""Configuration loading for lite-horse.

Two surfaces:

* `Config` (YAML) — agent-runtime tuning loaded from
  `~/.litehorse/config.yaml` for the v0.3 CLI / embedded library. Used by
  `agent.factory`, `cli/*`, `cron/scheduler`, `api`. `load_config()`
  materialises defaults on first run.

* `Settings` (pydantic-settings, env-prefix `LITEHORSE_`) — cloud
  deployment knobs (DATABASE_URL, REDIS_URL, S3 buckets, JWT, KMS).
  Read by `web/`, `storage/`, `scheduler/`, `worker/`. `get_settings()`
  is a cached singleton.

The `sandbox.*` and `gateway.telegram.*` subtrees from v0.2 / v0.3 are
removed in v0.4 (no readers; see plan §"What deletes from v0.3 code").
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from lite_horse.constants import DEFAULT_MAX_TURNS, litehorse_home

ReasoningEffort = Literal["none", "low", "medium", "high"]
LiteHorseEnv = Literal["local", "dev", "prod"]

DEFAULT_CONFIG_YAML = """# lite-horse config (edit to taste)
model: gpt-5.4
model_settings:
  reasoning_effort: medium      # none | low | medium | high
  parallel_tool_calls: true
agent:
  max_turns: 90
memory:
  enabled: true
  user_profile_enabled: true
tools:
  web_search: false             # WebSearchTool — billed per call by OpenAI
mcp_servers: []                 # list of {name, url, cache_tools_list}
"""


class ModelSettings(BaseModel):
    reasoning_effort: ReasoningEffort = "medium"
    parallel_tool_calls: bool = True


class AgentSettings(BaseModel):
    max_turns: int = DEFAULT_MAX_TURNS


class MemorySettings(BaseModel):
    enabled: bool = True
    user_profile_enabled: bool = True


class ToolsSettings(BaseModel):
    web_search: bool = False


class MCPServerConfig(BaseModel):
    """One MCP server attached to the agent. URL must be http(s)."""

    name: str
    url: str
    cache_tools_list: bool = True

    @field_validator("url")
    @classmethod
    def _scheme_is_http(cls, v: str) -> str:
        scheme = urlparse(v).scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError(f"mcp_servers[].url must be http or https, got {scheme!r}")
        return v


class Config(BaseModel):
    model: str = "gpt-5.4"
    model_settings: ModelSettings = Field(default_factory=ModelSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    tools: ToolsSettings = Field(default_factory=ToolsSettings)
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)


def _ensure_state_dir() -> Path:
    home = litehorse_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


def _ensure_config_file(home: Path) -> Path:
    path = home / "config.yaml"
    if not path.exists():
        path.write_text(DEFAULT_CONFIG_YAML, encoding="utf-8")
    return path


def _load_env(home: Path) -> None:
    env_path = home / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)


def load_config() -> Config:
    """Load YAML config from disk, materializing defaults on first run."""
    home = _ensure_state_dir()
    _load_env(home)
    config_path = _ensure_config_file(home)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    # Silently drop legacy keys removed in v0.4.
    for legacy in ("sandbox", "gateway"):
        raw.pop(legacy, None)
    return Config.model_validate(raw)


# ---------------------------------------------------------------------------
# v0.4 cloud deployment settings (env-driven via pydantic-settings).
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Cloud-deployment settings, read from env (prefix `LITEHORSE_`).

    Loaded from `.env` at repo root by default. Used by `storage/`, `web/`,
    `scheduler/`, `worker/`. Not consumed by the v0.3 CLI / agent runtime
    (those use `Config`).
    """

    model_config = SettingsConfigDict(
        env_prefix="LITEHORSE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: LiteHorseEnv = "local"

    database_url: str = (
        "postgresql+asyncpg://litehorse:litehorse@localhost:5432/litehorse"
    )
    redis_url: str = "redis://localhost:6379/0"

    # S3 / MinIO
    s3_endpoint: str | None = None  # None → real AWS S3
    s3_region: str = "us-east-1"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket_attachments: str = "litehorse-local-attachments"
    s3_bucket_evolve: str = "litehorse-local-evolve"
    s3_bucket_exports: str = "litehorse-local-exports"
    s3_bucket_audit: str = "litehorse-local-audit-archive"

    # KMS — local impl uses Fernet; prod impl uses AWS KMS
    local_kms_key: str | None = None  # base64-urlsafe Fernet key
    aws_kms_key_id: str | None = None  # alias/litehorse-prod or key ARN

    # JWT (JWKS verification — webapp issues JWTs verifiable via JWKS)
    jwt_jwks_url: str = "http://localhost:9999/.well-known/jwks.json"
    jwt_issuer: str = "http://localhost:9999"
    jwt_audience: str = "lite-horse"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cloud Settings singleton."""
    return Settings()
