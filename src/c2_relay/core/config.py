"""Environment-driven application configuration."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="C2_RELAY_",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = Environment.DEVELOPMENT
    database_url: str = Field(default="sqlite+pysqlite:///./c2-relay.db", min_length=1)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    bootstrap_token: SecretStr | None = Field(default=None, min_length=32)
    server_host: str = "127.0.0.1"
    server_port: int = Field(default=8000, ge=1, le=65535)
    task_lease_seconds: float = Field(default=30.0, gt=0, le=3600)
    result_clock_skew_seconds: float = Field(default=300.0, ge=0, le=3600)
    auth_failure_limit: int = Field(default=10, ge=1, le=1000)
    auth_failure_window_seconds: float = Field(default=60.0, gt=0, le=3600)
    agent_server_url: str = Field(default="http://127.0.0.1:8000", min_length=1)
    agent_state_path: Path = Path("agent-state.json")
    agent_result_path: Path = Path("pending-result.json")
    agent_poll_interval: float = Field(default=5.0, gt=0, le=3600)
    agent_request_timeout: float = Field(default=10.0, gt=0, le=300)
    agent_max_backoff: float = Field(default=60.0, gt=0, le=3600)

    @field_validator("bootstrap_token", mode="before")
    @classmethod
    def blank_bootstrap_token_disables_enrollment(
        cls, value: SecretStr | str | None
    ) -> SecretStr | str | None:
        if value == "" or (isinstance(value, SecretStr) and not value.get_secret_value()):
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
