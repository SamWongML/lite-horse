"""Configuration loading for lite-horse.

`load_config()` reads `~/.litehorse/config.yaml` (creating it on first run from
`DEFAULT_CONFIG_YAML`) and loads `~/.litehorse/.env` into the process
environment. The location of the state dir is controlled by
`LITEHORSE_HOME`; see `constants.litehorse_home`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

from lite_horse.constants import DEFAULT_MAX_TURNS, litehorse_home

ReasoningEffort = Literal["none", "low", "medium", "high"]

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
gateway:
  telegram:
    enabled: false
    allowed_user_ids: []        # int Telegram user IDs
tools:
  web_search: false             # WebSearchTool — billed per call by OpenAI
sandbox:
  enabled: false
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


class TelegramSettings(BaseModel):
    enabled: bool = False
    allowed_user_ids: list[int] = Field(default_factory=list)


class GatewaySettings(BaseModel):
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)


class SandboxSettings(BaseModel):
    enabled: bool = False


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
    gateway: GatewaySettings = Field(default_factory=GatewaySettings)
    tools: ToolsSettings = Field(default_factory=ToolsSettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
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
    """Load config from disk, materializing defaults on first run."""
    home = _ensure_state_dir()
    _load_env(home)
    config_path = _ensure_config_file(home)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(raw)
