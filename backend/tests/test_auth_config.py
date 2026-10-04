"""AUTH_MODE and APP_ENV: production fails closed, development can choose, nothing falls back."""

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings

KEY = "k" * 40


def build(**values) -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x", **values)


def test_production_refuses_the_dev_identity():
    with pytest.raises(ValidationError, match="only allowed when APP_ENV=development"):
        build(app_env="production", auth_mode="dev")


def test_production_refuses_disabled_and_the_default_mode(monkeypatch):
    with pytest.raises(ValidationError, match="requires AUTH_MODE=session"):
        build(app_env="production", auth_mode="disabled")
    for name in ("APP_ENV", "AUTH_MODE", "SECURITY_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError, match="requires AUTH_MODE=session"):
        build()  # the defaults are production + disabled: nothing starts by accident


def test_production_session_mode_needs_a_real_security_key():
    with pytest.raises(ValidationError, match="SECURITY_KEY"):
        build(app_env="production", auth_mode="session")
    with pytest.raises(ValidationError, match="SECURITY_KEY"):
        build(app_env="production", auth_mode="session", security_key="short")
    assert build(app_env="production", auth_mode="session", security_key=KEY).auth_mode == "session"


def test_development_can_explicitly_choose_dev_or_session_or_disabled():
    assert build(app_env="development", auth_mode="dev").auth_mode == "dev"
    assert build(app_env="development", auth_mode="session").auth_mode == "session"
    assert build(app_env="development", auth_mode="disabled").auth_mode == "disabled"


def test_development_session_mode_gets_a_process_key_when_none_is_set():
    settings = build(app_env="development", auth_mode="session")
    assert isinstance(settings.security_key, SecretStr) and len(settings.security_key.get_secret_value()) >= 32


@pytest.mark.parametrize("mode", ["", "none", "SESSION", "jwt", "dev,session"])
def test_an_unknown_mode_is_refused(mode):
    with pytest.raises(ValidationError):
        build(app_env="development", auth_mode=mode)


@pytest.mark.parametrize("env", ["", "staging", "Production", "test"])
def test_an_unknown_environment_is_refused(env):
    with pytest.raises(ValidationError):
        build(app_env=env, auth_mode="dev")


@pytest.mark.parametrize(
    "name",
    ["session_idle_hours", "session_absolute_days", "session_max_per_user", "argon2_max_concurrent", "throttle_pair_max_failures",
     "security_event_retention_days", "auth_record_retention_days", "setup_token_ttl_hours"],
)
def test_limits_cannot_be_set_to_nonsense(name):
    with pytest.raises(ValidationError, match=name.upper()):
        build(app_env="development", auth_mode="dev", **{name: 0})


def test_password_limits_keep_the_approved_floor():
    with pytest.raises(ValidationError):
        build(app_env="development", auth_mode="dev", password_min_length=8)
    with pytest.raises(ValidationError):
        build(app_env="development", auth_mode="dev", password_min_length=40, password_max_length=20)
    ok = build(app_env="development", auth_mode="dev")
    assert (ok.password_min_length, ok.password_max_length) == (12, 128)


def test_the_approved_defaults():
    s = build(app_env="development", auth_mode="dev")
    assert (s.session_idle_hours, s.session_absolute_days, s.session_max_per_user) == (12, 7, 20)
    assert (s.argon2_memory_kib, s.argon2_time_cost, s.argon2_parallelism) == (65536, 3, 1)
    assert s.security_event_retention_days == 30 and s.trust_client_ip_header is False
