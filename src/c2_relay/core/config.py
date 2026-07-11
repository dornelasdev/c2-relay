"""Environment-driven application configuration."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
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
    agent_server_url: str = Field(default="http://127.0.0.1:8000", min_length=1)
    agent_state_path: Path = Path("agent-state.json")
    agent_poll_interval: float = Field(default=5.0, gt=0, le=3600)
    agent_request_timeout: float = Field(default=10.0, gt=0, le=300)
    agent_max_backoff: float = Field(default=60.0, gt=0, le=3600)


@lru_cache
def get_settings() -> Settings:
    return Settings()
