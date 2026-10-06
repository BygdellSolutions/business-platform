"""BFF -> FastAPI internal authentication: refused before the application runs, constant-time, exempt only for the two
coarse health probes, and the source-address header is believed only from an authenticated BFF."""

import hmac
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from app.core import internal_auth, security_events
from app.core.config import settings
from app.main import app
from tests.auth_support import BFF_SECRET, OTHER_PASSWORD, as_bff, login, login_user
from tests.test_auth_throttle import request_with

HEADER = "x-bff-secret"


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(settings, "bff_internal_secret", SecretStr(BFF_SECRET))
    return BFF_SECRET


@pytest.fixture
def raw():
    return TestClient(app)


def refused(response) -> bool:
    return response.status_code == 403 and response.headers.get("x-internal-auth") == "rejected"


# --- accepted and refused ----------------------------------------------------------------------------------------------------------------------


def test_the_correct_secret_reaches_the_application(enforced, raw):
    response = raw.get("/api/me/user", headers={HEADER: enforced})
    assert response.status_code == 401 and not refused(response)  # the APPLICATION answered (no user), not the guard


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {HEADER: ""},
        {HEADER: "wrong-secret-value"},
        {HEADER: BFF_SECRET[:-1]},  # a prefix
        {HEADER: BFF_SECRET + "x"},  # a suffix
        {HEADER: BFF_SECRET.upper()},
        {HEADER: " " + BFF_SECRET},
        {"x-internal-secret": BFF_SECRET},  # the right value under another name
        {"authorization": f"Bearer {BFF_SECRET}"},
        {"cookie": f"x-bff-secret={BFF_SECRET}"},
    ],
)
def test_a_missing_or_wrong_secret_is_refused_before_the_application(enforced, raw, headers):
    response = raw.get("/api/me/user", headers=headers)
    assert refused(response)
    assert response.json() == {"detail": {"code": "internal_auth_failed", "message": "Forbidden"}}
    assert BFF_SECRET not in response.text + json.dumps(dict(response.headers))


def test_the_secret_in_the_query_string_is_not_a_secret_header(enforced, raw):
    assert refused(raw.get(f"/api/me/user?x-bff-secret={BFF_SECRET}"))


def test_the_header_name_is_case_insensitive_like_every_header(enforced, raw):
    assert not refused(raw.get("/api/me/user", headers={"X-BFF-Secret": enforced}))


def test_every_method_and_route_is_guarded_not_only_reads(enforced, raw):
    for method, path in (("post", "/api/customers"), ("patch", "/api/customers/x"), ("delete", "/api/customers/x"), ("get", "/openapi.json"), ("get", "/docs"), ("options", "/api/customers")):
        assert refused(getattr(raw, method)(path)), (method, path)


def test_a_refusal_never_reaches_routing_so_an_unknown_path_is_refused_not_404(enforced, raw):
    assert refused(raw.get("/api/no-such-route"))


def test_the_probes_are_exempt_and_only_those_exact_paths(enforced, raw):
    assert raw.get("/health").status_code == 200
    assert raw.get("/health/ready").status_code in (200, 503)
    for path in ("/health/", "/health/ready/", "/health/db", "/health/../api/me/user", "/healthz"):
        assert refused(raw.get(path)), path


def test_pre_auth_routes_work_for_the_bff_without_a_user_session(enforced, session_client, db_session):
    """Login, setup and invitation preview have no session by nature; the BFF still authenticates ITSELF with the secret."""
    user = login_user(db_session)
    assert login(session_client, user.email, OTHER_PASSWORD).status_code == 401  # a normal application answer, via as_bff()
    assert session_client.post("/api/invite/preview", json={"token": "x" * 43}).status_code == 404
    bare = TestClient(app)
    assert refused(bare.post("/api/auth/login", json={"email": user.email, "password": OTHER_PASSWORD}))
    assert refused(bare.post("/api/invite/preview", json={"token": "x" * 43}))
    assert refused(bare.post("/api/auth/setup", json={"token": "x" * 43, "password": "a long passphrase here"}))


def test_without_a_configured_secret_nothing_is_enforced_the_documented_development_mode(raw):
    assert settings.bff_internal_secret is None
    response = raw.get("/api/me/user")
    assert response.status_code == 401 and not refused(response)


def test_a_refused_request_still_carries_the_request_id_and_headers(enforced, raw):
    response = raw.get("/api/me/user")
    assert len(response.headers["x-request-id"]) == 32 and response.headers["x-content-type-options"] == "nosniff"


# --- constant time -----------------------------------------------------------------------------------------------------------------------------


def test_the_comparison_is_the_constant_time_one(enforced, raw, monkeypatch):
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(internal_auth.hmac, "compare_digest", lambda a, b: calls.append((a, b)) or real(a, b))
    raw.get("/api/me/user", headers={HEADER: "wrong"})
    raw.get("/api/me/user", headers={HEADER: enforced})
    raw.get("/api/me/user")  # a MISSING header is compared too: no early exit that tells it apart by time
    assert (b"wrong", enforced.encode()) in calls and (enforced.encode(), enforced.encode()) in calls and (b"", enforced.encode()) in calls


def test_the_guard_does_not_compare_with_a_plain_equality():
    import inspect

    source = inspect.getsource(internal_auth.InternalAuthMiddleware)
    assert "compare_digest" in source and "== expected" not in source and "expected ==" not in source and "startswith" not in source


# --- the source address is believed only from an authenticated BFF ---------------------------------------------------------------------


def source_of(headers, *, authenticated: bool, trust: bool, monkeypatch) -> str:
    monkeypatch.setattr(settings, "trust_client_ip_header", trust)
    return security_events.client_source(request_with(headers, peer="10.0.0.1", authenticated=authenticated))


def test_an_unauthenticated_requests_client_ip_header_is_never_believed(monkeypatch):
    assert source_of({"X-Client-Ip": "198.51.100.7"}, authenticated=False, trust=True, monkeypatch=monkeypatch) == "10.0.0.1"


def test_an_authenticated_requests_header_is_believed_only_when_trust_is_on(monkeypatch):
    assert source_of({"X-Client-Ip": "198.51.100.7"}, authenticated=True, trust=True, monkeypatch=monkeypatch) == "198.51.100.7"
    assert source_of({"X-Client-Ip": "198.51.100.7"}, authenticated=True, trust=False, monkeypatch=monkeypatch) == "10.0.0.1"


@pytest.mark.parametrize("value, expected", [("::ffff:203.0.113.7", "203.0.113.7"), ("2001:db8:1:2:3:4:5:6", "2001:db8:1:2::/64"), ("203.0.113.7", "203.0.113.7")])
def test_ipv4_mapped_addresses_key_by_the_ipv4_client_not_one_shared_prefix(value, expected, monkeypatch):
    assert source_of({"X-Client-Ip": value}, authenticated=True, trust=True, monkeypatch=monkeypatch) == expected
    assert source_of({}, authenticated=False, trust=False, monkeypatch=monkeypatch) == "10.0.0.1"


def test_a_forged_client_ip_with_a_failing_secret_is_refused_and_leaves_no_trace(monkeypatch, db_session):
    monkeypatch.setattr(settings, "trust_client_ip_header", True)
    monkeypatch.setattr(settings, "bff_internal_secret", SecretStr(BFF_SECRET))
    from app.core.db import get_db

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).post("/api/auth/login", json={"email": "someone@tests.invalid", "password": "whatever long enough"}, headers={"X-Client-Ip": "198.51.100.99", HEADER: "nope"})
    finally:
        app.dependency_overrides.clear()
    assert refused(response)
    assert db_session.scalar(text("select count(*) from security_events where source = '198.51.100.99'")) == 0


def test_an_authenticated_bff_request_is_throttled_by_the_forwarded_address(monkeypatch, session_client, db_session):
    monkeypatch.setattr(settings, "trust_client_ip_header", True)
    monkeypatch.setattr(settings, "bff_internal_secret", SecretStr(BFF_SECRET))
    client = TestClient(app, headers=as_bff())
    from app.core.db import get_db

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        user = login_user(db_session)
        assert client.post("/api/auth/login", json={"email": user.email, "password": OTHER_PASSWORD}, headers={"X-Client-Ip": "198.51.100.42"}).status_code == 401
    finally:
        app.dependency_overrides.clear()
    assert db_session.scalar(text("select count(*) from security_events where source = '198.51.100.42'")) == 1
