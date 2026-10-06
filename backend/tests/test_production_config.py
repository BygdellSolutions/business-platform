"""Production configuration fails closed. Every unsafe value below must stop the web process from starting; the one safe
configuration must start; development stays convenient on purpose."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, is_local_host, public_origin_problem
from tests.test_auth_config import BFF, KEY, ORIGIN, PRODUCTION, build, clean_environment  # noqa: F401  (the fixture applies here too)


def production(**changes) -> Settings:
    return build(**{**PRODUCTION, **changes})


def test_the_complete_safe_production_configuration_starts():
    settings = production()
    assert settings.app_env == "production" and settings.auth_mode == "session"
    assert settings.cors_origins == []  # production has no CORS origin at all
    assert settings.trust_client_ip_header is False  # source-IP trust stays off until the deployment topology is verified


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"auth_mode": "dev"}, "only allowed when APP_ENV=development"),
        ({"auth_mode": "disabled"}, "requires AUTH_MODE=session"),
        ({"dev_user_email": "someone@example.test"}, "DEV_USER_EMAIL"),
        ({"dev_user_email": ""}, "DEV_USER_EMAIL"),  # configured at all, even empty, is refused
        ({"test_database_url": "postgresql+psycopg://x/y_test"}, "TEST_DATABASE_URL"),
        ({"migration_database_url": "postgresql+psycopg://owner@x/y"}, "MIGRATION_DATABASE_URL"),
        ({"public_origin": None}, "PUBLIC_ORIGIN is required"),
        ({"public_origin": ""}, "PUBLIC_ORIGIN is required"),
        ({"public_origin": "http://app.example.test"}, "https"),
        ({"public_origin": "https://localhost"}, "localhost"),
        ({"public_origin": "https://localhost:3000"}, "localhost"),
        ({"public_origin": "https://127.0.0.1"}, "localhost"),
        ({"public_origin": "https://[::1]"}, "localhost"),
        ({"public_origin": "https://app.localhost"}, "localhost"),
        ({"public_origin": "https://app.example.test/path"}, "origin only"),
        ({"public_origin": "https://user:pass@app.example.test"}, "origin only"),
        ({"public_origin": "https://app.example.test?x=1"}, "origin only"),
        ({"public_origin": "not a url"}, "https"),
        ({"cors_origins": ["*"]}, "wildcard"),
        ({"cors_origins": ["http://localhost:3000"]}, "localhost"),
        ({"cors_origins": ["https://127.0.0.1:3000"]}, "localhost"),
        ({"cors_origins": ["https://app.example.test"]}, "CORS_ORIGINS must be \\[\\]"),
        ({"bff_internal_secret": None}, "BFF_INTERNAL_SECRET is required"),
        ({"bff_internal_secret": "short"}, "at least 32"),
        ({"bff_internal_secret": "a" * 48}, "too repetitive"),
        ({"bff_internal_secret": "ab" * 24}, "too repetitive"),
        ({"security_key": BFF}, "must differ from SECURITY_KEY"),
        ({"security_key": None}, "SECURITY_KEY"),
        ({"security_key": "short"}, "SECURITY_KEY"),
    ],
)
def test_every_unsafe_production_value_is_refused(changes, message):
    with pytest.raises(ValidationError, match=message):
        production(**changes)


def test_production_requires_an_explicit_environment_and_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(ValidationError, match="app_env"):
        Settings(_env_file=None, database_url="postgresql+psycopg://x", auth_mode="session")
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None, **PRODUCTION)


def test_the_environment_variables_are_what_production_reads(monkeypatch):
    """Not only keyword arguments: the real route into a container is the environment."""
    for name, value in {
        "APP_ENV": "production", "AUTH_MODE": "session", "SECURITY_KEY": KEY, "BFF_INTERNAL_SECRET": BFF,
        "PUBLIC_ORIGIN": ORIGIN, "DATABASE_URL": "postgresql+psycopg://app@db/x", "CORS_ORIGINS": "[]",
    }.items():
        monkeypatch.setenv(name, value)
    assert Settings(_env_file=None).cors_origins == []
    monkeypatch.setenv("DEV_USER_EMAIL", "dev@example.test")
    with pytest.raises(ValidationError, match="DEV_USER_EMAIL"):
        Settings(_env_file=None)
    monkeypatch.delenv("DEV_USER_EMAIL")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg://x/y_test")
    with pytest.raises(ValidationError, match="TEST_DATABASE_URL"):
        Settings(_env_file=None)
    monkeypatch.delenv("TEST_DATABASE_URL")
    monkeypatch.setenv("CORS_ORIGINS", '["*"]')
    with pytest.raises(ValidationError, match="wildcard"):
        Settings(_env_file=None)


# --- development stays convenient, and says so explicitly ---------------------------------------------------------------------------------------


def test_development_defaults_remain_convenient():
    settings = build(app_env="development", auth_mode="dev")
    assert settings.public_origin == "http://localhost:3000"
    assert settings.cors_origins == ["http://localhost:3000"]
    assert settings.bff_internal_secret is None  # the one documented unauthenticated mode (nothing is enforced)
    assert settings.dev_user_email is None


def test_development_may_configure_what_production_refuses():
    settings = build(app_env="development", auth_mode="dev", dev_user_email="d@dev.test", cors_origins=["http://localhost:3000"],
                     test_database_url="postgresql+psycopg://x/y_test", migration_database_url="postgresql+psycopg://o@x/y")
    assert settings.dev_user_email == "d@dev.test"


def test_a_configured_internal_secret_must_be_a_real_secret_everywhere():
    with pytest.raises(ValidationError, match="at least 32"):
        build(app_env="development", auth_mode="dev", bff_internal_secret="short")
    assert build(app_env="development", auth_mode="dev", bff_internal_secret=BFF).bff_internal_secret is not None


def test_trusting_the_client_address_header_requires_the_internal_secret():
    with pytest.raises(ValidationError, match="TRUST_CLIENT_IP_HEADER=true requires BFF_INTERNAL_SECRET"):
        build(app_env="development", auth_mode="dev", trust_client_ip_header=True)
    assert build(app_env="development", auth_mode="dev", trust_client_ip_header=True, bff_internal_secret=BFF).trust_client_ip_header


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "app.localhost", "127.0.0.1", "127.9.9.9", "::1", "[::1]", "0.0.0.0", "::", "localhost."])
def test_every_spelling_of_localhost_is_local(host):
    assert is_local_host(host)


@pytest.mark.parametrize("host", ["app.example.test", "203.0.113.5", "localhost.example.test", "notlocalhost"])
def test_ordinary_hosts_are_not_local(host):
    assert not is_local_host(host)


def test_a_normal_public_origin_is_accepted():
    for origin in (ORIGIN, "https://app.example.test:8443", "https://app.example.test/"):
        assert public_origin_problem(origin) is None, origin


def test_a_refused_configuration_never_prints_the_values_it_refused_or_the_other_secrets():
    """Pydantic would otherwise print `input_value=...` for the whole input: database URLs with passwords, every secret."""
    secrets = {"database_url": "postgresql+psycopg://appuser:SENTINEL-DB-PASSWORD@db/x", "security_key": "SENTINEL-SECURITY-KEY-" + "x" * 30}
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, **{**PRODUCTION, **secrets, "cors_origins": ["*"], "bff_internal_secret": "SENTINEL-SHORT"})
    rendered = str(caught.value)
    for sentinel in ("SENTINEL-DB-PASSWORD", "SENTINEL-SECURITY-KEY", "SENTINEL-SHORT", "appuser"):
        assert sentinel not in rendered, sentinel
    assert "BFF_INTERNAL_SECRET" in rendered  # the RULE that failed is still named


def test_the_migration_settings_do_not_print_their_input_either():
    from app.core.migration import MigrationSettings

    with pytest.raises(ValidationError) as caught:
        MigrationSettings(_env_file=None, migration_database_url=["SENTINEL-NOT-A-STRING-PASSWORD"])
    assert "SENTINEL-NOT-A-STRING-PASSWORD" not in str(caught.value)


def test_a_malformed_database_url_is_refused_without_echoing_it():
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, app_env="development", auth_mode="dev", database_url="postgresql+psycopg//user:SENTINEL-DB-PASSWORD@host/db")
    assert "SENTINEL-DB-PASSWORD" not in str(caught.value) and "DATABASE_URL is not a valid database URL" in str(caught.value)
