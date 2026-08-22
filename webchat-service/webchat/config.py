from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-owned settings; secret values are never serialized to clients."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    llm_provider: Literal["openai", "azure"] = "openai"
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5-mini"
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_deployment: str | None = None
    northstar_api_key: SecretStr = SecretStr("northstar-local-development")
    northstar_base_url: str = "http://dealership-platform:4010"
    webchat_port: int = 4020
    webchat_database_path: Path = Path("/data/webchat.sqlite3")
    webchat_cookie_secure: bool = False
    webchat_allowed_origin: str = "http://localhost:4173"
    webchat_retention_days: int = 30
    log_level: str = "INFO"

    @field_validator("openai_model", "northstar_base_url")
    @classmethod
    def non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def blank_key_is_unconfigured(cls, value):
        return None if value in (None, "") else value

    @field_validator("webchat_retention_days")
    @classmethod
    def positive_retention(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be at least one day")
        return value

    def validate_runtime(self) -> None:
        if self.llm_provider == "azure":
            missing = [
                name
                for name, value in (
                    ("AZURE_OPENAI_ENDPOINT", self.azure_openai_endpoint),
                    ("AZURE_OPENAI_API_KEY", self.azure_openai_api_key),
                    ("AZURE_OPENAI_DEPLOYMENT", self.azure_openai_deployment),
                )
                if value is None or (isinstance(value, str) and not value.strip())
            ]
            if missing:
                raise ValueError(f"Azure LLM configuration is missing: {', '.join(missing)}")
        elif self.environment == "production" and self.openai_api_key is None:
            raise ValueError("OPENAI_API_KEY is required in production")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_runtime()
    return settings
