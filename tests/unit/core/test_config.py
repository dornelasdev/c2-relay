from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from c2_relay.core.config import Environment, Settings, get_settings


def test_settings_have_local_development_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings()

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.database_url == "sqlite+pysqlite:///./c2-relay.db"
    assert settings.log_level == "INFO"
    assert settings.bootstrap_token is None
    assert settings.agent_enrollment_token is None
    assert settings.allow_legacy_bootstrap_enrollment is False
    assert settings.operator_server_url == "http://127.0.0.1:8000"
    assert settings.operator_credential is None
    assert settings.operator_request_timeout == 10.0
    assert settings.server_host == "127.0.0.1"
    assert settings.server_port == 8000
    assert settings.task_lease_seconds == 30.0
    assert settings.result_clock_skew_seconds == 300.0
    assert settings.auth_failure_limit == 10
    assert settings.auth_failure_window_seconds == 60.0
    assert settings.agent_result_path == Path("pending-result.json")


def test_settings_read_prefixed_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C2_RELAY_ENVIRONMENT", "test")
    monkeypatch.setenv("C2_RELAY_DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("C2_RELAY_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("C2_RELAY_AUTH_FAILURE_LIMIT", "5")
    monkeypatch.setenv("C2_RELAY_AUTH_FAILURE_WINDOW_SECONDS", "30")
    monkeypatch.setenv("C2_RELAY_AGENT_ENROLLMENT_TOKEN", "g" * 32)
    monkeypatch.setenv("C2_RELAY_ALLOW_LEGACY_BOOTSTRAP_ENROLLMENT", "true")
    monkeypatch.setenv("C2_RELAY_OPERATOR_SERVER_URL", "https://relay.test")
    monkeypatch.setenv("C2_RELAY_OPERATOR_CREDENTIAL", "o" * 32)
    monkeypatch.setenv("C2_RELAY_OPERATOR_REQUEST_TIMEOUT", "5")

    settings = Settings()

    assert settings.environment is Environment.TEST
    assert settings.database_url.endswith(":memory:")
    assert settings.log_level == "DEBUG"
    assert settings.auth_failure_limit == 5
    assert settings.auth_failure_window_seconds == 30.0
    assert settings.agent_enrollment_token == SecretStr("g" * 32)
    assert settings.allow_legacy_bootstrap_enrollment is True
    assert settings.operator_server_url == "https://relay.test"
    assert settings.operator_credential == SecretStr("o" * 32)
    assert settings.operator_request_timeout == 5.0


def test_blank_bootstrap_token_in_env_file_disables_enrollment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("C2_RELAY_BOOTSTRAP_TOKEN=\n", encoding="utf-8")

    assert Settings().bootstrap_token is None


def test_blank_environment_token_overrides_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"C2_RELAY_BOOTSTRAP_TOKEN={'x' * 32}\n", encoding="utf-8")
    monkeypatch.setenv("C2_RELAY_BOOTSTRAP_TOKEN", "")

    assert Settings().bootstrap_token is None


def test_direct_blank_secret_disables_enrollment() -> None:
    assert Settings(bootstrap_token=SecretStr("")).bootstrap_token is None
    assert Settings(agent_enrollment_token=SecretStr("")).agent_enrollment_token is None
    assert Settings.model_validate({"agent_enrollment_token": ""}).agent_enrollment_token is None
    assert Settings(operator_credential=SecretStr("")).operator_credential is None


def test_nonblank_bootstrap_token_still_requires_minimum_length() -> None:
    with pytest.raises(ValidationError) as error:
        Settings(bootstrap_token=SecretStr("short"))
    assert error.value.errors()[0]["type"] == "too_short"

    configured = Settings(bootstrap_token=SecretStr("x" * 32)).bootstrap_token
    assert configured is not None
    assert configured.get_secret_value() == "x" * 32


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()

    assert get_settings() is get_settings()

    get_settings.cache_clear()
