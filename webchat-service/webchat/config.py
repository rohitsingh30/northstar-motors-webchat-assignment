from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-owned settings; secret values are never serialized to clients."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    llm_provider_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_turn_timeout_seconds: float = 45.0
    mcp_servers_json: str | None = None
    northstar_api_key: SecretStr = SecretStr("northstar-local-development")
    northstar_base_url: str = "http://dealership-platform:4010"
    webchat_database_path: Path = Path("/data/webchat.sqlite3")
    webchat_cookie_secure: bool = False
    webchat_allowed_origin: str = "http://localhost:4173"
    webchat_retention_days: int = 30
    webchat_requests_per_minute: int = 60
    webchat_daily_turn_limit: int = 0
    webchat_max_concurrent_turns: int = 8
    webchat_trust_proxy_headers: bool = False
    log_level: str = "INFO"

    @field_validator("northstar_base_url")
    @classmethod
    def non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("llm_provider_url", "llm_model", "mcp_servers_json")
    @classmethod
    def blank_hosted_value_is_unconfigured(cls, value: str | None) -> str | None:
        return None if value is None or not value.strip() else value.strip()

    @field_validator("llm_api_key", mode="before")
    @classmethod
    def blank_key_is_unconfigured(cls, value):
        return None if value is None or (isinstance(value, str) and not value.strip()) else value

    @field_validator("llm_provider_url")
    @classmethod
    def valid_provider_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("must be an absolute HTTP(S) URL")
        if parsed.query or parsed.fragment:
            raise ValueError("must not contain a query or fragment")
        return value.rstrip("/")

    @field_validator("webchat_retention_days")
    @classmethod
    def positive_retention(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be at least one day")
        return value

    @field_validator("webchat_requests_per_minute")
    @classmethod
    def bounded_request_limit(cls, value: int) -> int:
        if not 0 <= value <= 600:
            raise ValueError("must be zero (disabled) or between 1 and 600")
        return value

    @field_validator("webchat_daily_turn_limit")
    @classmethod
    def non_negative_daily_turn_limit(cls, value: int) -> int:
        if value < 0:
            raise ValueError("must be zero (unlimited) or greater")
        return value

    @field_validator("webchat_max_concurrent_turns")
    @classmethod
    def bounded_concurrent_turn_limit(cls, value: int) -> int:
        if not 1 <= value <= 64:
            raise ValueError("must be between 1 and 64")
        return value

    @field_validator("llm_turn_timeout_seconds")
    @classmethod
    def bounded_llm_turn_timeout(cls, value: float) -> float:
        if not 5 <= value <= 120:
            raise ValueError("must be between 5 and 120 seconds")
        return value

    def validate_runtime(self) -> None:
        configuration = (
            ("LLM_PROVIDER_URL", self.llm_provider_url),
            ("LLM_API_KEY", self.llm_api_key),
            ("LLM_MODEL", self.llm_model),
        )
        configured = [name for name, value in configuration if value is not None]
        missing = [name for name, value in configuration if value is None]
        if configured and missing:
            raise ValueError(f"Hosted LLM configuration is incomplete: {', '.join(missing)}")
        if self.environment != "test" and missing:
            raise ValueError(f"Hosted LLM configuration is required: {', '.join(missing)}")

    @property
    def hosted_llm_configured(self) -> bool:
        return all(
            value is not None
            for value in (
                self.llm_provider_url,
                self.llm_api_key,
                self.llm_model,
            )
        )

    def mcp_servers(self) -> tuple[dict, ...]:
        if self.mcp_servers_json is None:
            return ()
        try:
            payload = json.loads(self.mcp_servers_json)
        except json.JSONDecodeError as error:
            raise ValueError("MCP_SERVERS_JSON must be valid JSON") from error
        if not isinstance(payload, list):
            raise TypeError("MCP_SERVERS_JSON must be a JSON array")
        servers: list[dict] = []
        names: set[str] = set()
        for value in payload:
            if not isinstance(value, dict) or not value.get("name") or not value.get("url"):
                raise ValueError("each MCP server requires name and url")
            name = str(value["name"]).strip()
            if name in names:
                raise ValueError(f"duplicate MCP server name: {name}")
            names.add(name)
            url = str(value["url"]).strip()
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"MCP server {name} requires an absolute HTTP(S) URL")
            raw_headers = value.get("headers") or {}
            if not isinstance(raw_headers, dict):
                raise TypeError(f"MCP server {name} headers must be an object")
            headers = {str(key): str(item) for key, item in raw_headers.items()}
            if any("\r" in item or "\n" in item for item in (*headers, *headers.values())):
                raise ValueError(f"MCP server {name} contains an invalid header")
            servers.append(
                {
                    "name": name,
                    "url": url,
                    "headers": headers,
                }
            )
        return tuple(servers)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_runtime()
    return settings
