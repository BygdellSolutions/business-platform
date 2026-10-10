"""Login: success, the indistinguishable failure contract, sessions, rotation, cap, and what is recorded."""

import hashlib
import hmac
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.core import clock, passwords, security_events
from app.core.config import settings
from app.core.tokens import hash_token
from tests.auth_support import OTHER_PASSWORD, PASSWORD, active_sessions, events, login, login_user, make_credential, make_session
from tests.factories import add_member, make_org, make_user

pytestmark = pytest.mark.usefixtures("session_mode")


@pytest.fixture
def verifies(monkeypatch):
    """Counts Argon2 verifications."""
    calls: list[str] = []
    real = passwords._argon_verify
    monkeypatch.setattr(passwords, "_argon_verify", lambda stored, password: calls.append(stored) or real(stored, password))
    return calls


def at(monkeypatch, moment):
    monkeypatch.setattr(clock, "utcnow", lambda: moment)


# --- success ---------------------------------------------------------------------------------------------------------------------------------


def test_a_valid_login_returns_a_fresh_session_and_the_user(session_client, db_session):
    user = login_user(db_session, name="Ada Owner")

    response = login(session_client, user.email)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"token", "csrf_token", "expires_at", "user"}
    assert len(body["token"]) == 43 and len(body["csrf_token"]) == 43 and body["token"] != body["csrf_token"]
    assert body["user"] == {"id": str(user.id), "email": user.email, "name": "Ada Owner", "can_create_organizations": True}
    (session,) = active_sessions(db_session, user)
    assert session.token_hash == hash_token(body["token"]) and session.csrf_hash == hash_token(body["csrf_token"])
    assert session.absolute_expires_at - session.created_at == timedelta(days=settings.session_absolute_days)
    assert [e.event_type for e in events(db_session)] == ["login_success"]


def test_the_issued_token_authenticates_the_next_request(session_client, db_session):
    user = login_user(db_session)
    org = make_org(db_session)
    add_member(db_session, org, user)
    token = login(session_client, user.email).json()["token"]

    me = session_client.get("/api/me", headers={"Authorization": f"Bearer {token}", "X-Organization-Id": str(org.id)})

    assert me.status_code == 200 and me.json()["user"]["email"] == user.email


def test_the_email_is_normalized_like_everywhere_else(session_client, db_session):
    user = login_user(db_session)
    assert login(session_client, f"  {user.email.upper()}  ").status_code == 200


def test_session_tokens_and_passwords_are_never_stored_in_plain_text(session_client, db_session):
    user = login_user(db_session)
    body = login(session_client, user.email).json()

    stored = " ".join(
        str(value)
        for table in ("auth_sessions", "user_credentials", "security_events", "users")
        for row in db_session.execute(text(f"select * from {table}"))
        for value in row
    )
    for secret in (body["token"], body["csrf_token"], PASSWORD):
        assert secret not in stored


def test_two_logins_give_two_independent_sessions(session_client, db_session):
    user = login_user(db_session)
    first = login(session_client, user.email).json()
    second = login(session_client, user.email).json()

    assert first["token"] != second["token"] and first["csrf_token"] != second["csrf_token"]
    assert len(active_sessions(db_session, user)) == 2
    for body in (first, second):
        assert session_client.get("/api/me/organizations", headers={"Authorization": f"Bearer {body['token']}"}).status_code == 200


def test_login_is_not_available_outside_session_mode(session_client, db_session, monkeypatch):
    user = login_user(db_session)
    for mode in ("dev", "disabled"):
        monkeypatch.setattr(settings, "auth_mode", mode)
        assert login(session_client, user.email).status_code == 404


# --- the failure contract --------------------------------------------------------------------------------------------------------------------------


def failure_cases(db_session):
    known = login_user(db_session)
    no_credential = make_user(db_session)
    inactive = login_user(db_session, is_active=False)
    return {
        "unknown email": ("nobody@tests.invalid", PASSWORD),
        "wrong password": (known.email, OTHER_PASSWORD),
        "empty password": (known.email, ""),
        "no credential": (no_credential.email, PASSWORD),
        "inactive user, right password": (inactive.email, PASSWORD),
        "malformed email": ("not an email", PASSWORD),
        "empty email": ("", PASSWORD),
    }


def test_every_kind_of_failure_looks_exactly_the_same(session_client, db_session):
    seen = {}
    for name, (email, password) in failure_cases(db_session).items():
        response = login(session_client, email, password)
        seen[name] = (response.status_code, response.json(), {k: v for k, v in response.headers.items() if k not in ("date", "x-request-id")})  # (the request id differs per request on purpose)
    assert {value[0] for value in seen.values()} == {401}
    reference = seen["wrong password"]
    for name, outcome in seen.items():
        assert outcome == reference, name
    assert reference[1] == {"detail": "Invalid email or password"}
    assert not any("set-cookie" in headers for _, _, headers in seen.values())


def test_every_failure_costs_exactly_one_argon2_verification(session_client, db_session, verifies):
    cases = failure_cases(db_session)
    for name in ("unknown email", "wrong password", "no credential", "inactive user, right password", "malformed email", "empty email", "empty password"):
        verifies.clear()
        email, password = cases[name]
        assert login(session_client, email, password).status_code == 401
        assert len(verifies) == 1, name


def test_a_dummy_hash_is_used_when_there_is_no_credential(session_client, db_session, verifies):
    user = make_user(db_session)
    login(session_client, user.email)
    login(session_client, "ghost@tests.invalid")
    assert len(verifies) == 2 and verifies[0] == verifies[1]  # the same dummy hash, never a real one
    assert verifies[0].startswith("$argon2id$") and "m=1024,t=1,p=1" in verifies[0]


def test_a_very_long_password_is_refused_without_hashing(session_client, db_session, verifies):
    user = login_user(db_session)
    assert login(session_client, user.email, "x" * 1025).status_code == 401
    assert verifies == []


def test_malformed_bodies_are_validation_errors_not_logins(session_client, db_session):
    assert session_client.post("/api/auth/login", json={"email": "a@b.c"}).status_code == 422
    assert session_client.post("/api/auth/login", json={"email": "a@b.c", "password": "x", "admin": True}).status_code == 422
    assert session_client.post("/api/auth/login", json={"email": "x" * 400, "password": "x"}).status_code == 422
    assert session_client.post("/api/auth/login", json={"email": "a@b.c", "password": "x" * 5000}).status_code == 422
    assert events(db_session) == []


def test_a_failed_login_makes_no_session_and_sets_nothing(session_client, db_session):
    user = login_user(db_session)
    login(session_client, user.email, OTHER_PASSWORD)
    assert active_sessions(db_session, user) == []


# --- rehash ------------------------------------------------------------------------------------------------------------------------------------


def test_a_login_upgrades_an_outdated_hash_and_the_password_still_works(session_client, db_session, monkeypatch):
    user = make_user(db_session)
    monkeypatch.setattr(settings, "argon2_memory_kib", 512)
    old = passwords.hash_password(PASSWORD)
    make_credential(db_session, user, hash_value=old)
    monkeypatch.setattr(settings, "argon2_memory_kib", 1024)

    assert login(session_client, user.email).status_code == 200

    stored = db_session.scalar(text("select password_hash from user_credentials where user_id = :u"), {"u": user.id})
    assert stored != old and "m=1024," in stored and "m=512," in old
    assert passwords.verify(stored, PASSWORD)
    assert login(session_client, user.email).status_code == 200


def test_a_failed_login_never_rewrites_the_hash(session_client, db_session, monkeypatch):
    user = make_user(db_session)
    monkeypatch.setattr(settings, "argon2_memory_kib", 512)
    old = passwords.hash_password(PASSWORD)
    make_credential(db_session, user, hash_value=old)
    monkeypatch.setattr(settings, "argon2_memory_kib", 1024)

    login(session_client, user.email, OTHER_PASSWORD)

    assert db_session.scalar(text("select password_hash from user_credentials where user_id = :u"), {"u": user.id}) == old


# --- rotation, fixation, cap, logout -------------------------------------------------------------------------------------------------------------


def test_logging_in_while_presenting_a_session_ends_that_session(session_client, db_session):
    user = login_user(db_session)
    old = make_session(db_session, user)

    new = login(session_client, user.email, headers=old.read_headers).json()

    assert new["token"] != old.token
    db_session.refresh(old.session)
    assert old.session.revoked_reason == "rotated"
    assert session_client.get("/api/me/organizations", headers=old.read_headers).status_code == 401
    assert session_client.get("/api/me/organizations", headers={"Authorization": f"Bearer {new['token']}"}).status_code == 200


def test_a_token_chosen_by_the_caller_never_becomes_the_session(session_client, db_session):
    """Fixation: whatever token is presented at login, the session issued is a new random one."""
    user = login_user(db_session)
    chosen = "A" * 43
    body = login(session_client, user.email, headers={"Authorization": f"Bearer {chosen}"}).json()
    assert body["token"] != chosen
    assert session_client.get("/api/me/organizations", headers={"Authorization": f"Bearer {chosen}"}).status_code == 401


def test_a_presented_session_of_ANOTHER_user_is_not_a_login(session_client, db_session):
    user, other = login_user(db_session), login_user(db_session)
    theirs = make_session(db_session, other)
    login(session_client, user.email, headers=theirs.read_headers)
    # Presenting a token only ever ends THAT token (the browser's own); it never signs anyone in as its owner.
    assert session_client.get("/api/me/organizations", headers=theirs.read_headers).status_code == 401


def test_the_session_cap_ends_the_oldest_sessions(session_client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "session_max_per_user", 3)
    user = login_user(db_session)
    base = clock.utcnow()
    tokens = []
    for minute in range(5):
        at(monkeypatch, base + timedelta(minutes=minute))
        tokens.append(login(session_client, user.email).json()["token"])
    at(monkeypatch, base + timedelta(minutes=6))

    assert len(active_sessions(db_session, user)) == 3
    codes = [session_client.get("/api/me/organizations", headers={"Authorization": f"Bearer {t}"}).status_code for t in tokens]
    assert codes == [401, 401, 200, 200, 200]  # the two oldest were ended
    reasons = db_session.execute(text("select revoked_reason from auth_sessions where user_id = :u and revoked_at is not null order by created_at"), {"u": user.id}).scalars().all()
    assert reasons == ["cap", "cap"]


def test_the_cap_is_per_user(session_client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "session_max_per_user", 1)
    a, b = login_user(db_session), login_user(db_session)
    login(session_client, a.email), login(session_client, b.email), login(session_client, b.email)
    assert len(active_sessions(db_session, a)) == 1 and len(active_sessions(db_session, b)) == 1


def test_logout_ends_only_that_session(session_client, db_session):
    user = login_user(db_session)
    first, second = make_session(db_session, user), make_session(db_session, user)

    response = session_client.post("/api/auth/logout", headers=first.headers)

    assert response.status_code == 204
    assert session_client.get("/api/me/organizations", headers=first.read_headers).status_code == 401
    assert session_client.get("/api/me/organizations", headers=second.read_headers).status_code == 200
    db_session.refresh(first.session)
    assert first.session.revoked_reason == "logout"
    assert [e.event_type for e in events(db_session)] == ["logout"]


def test_logout_needs_authentication_and_the_csrf_token(session_client, db_session):
    user = login_user(db_session)
    handle = make_session(db_session, user)
    assert session_client.post("/api/auth/logout").status_code == 401
    assert session_client.post("/api/auth/logout", headers=handle.read_headers).status_code == 403
    assert session_client.post("/api/auth/logout", headers={**handle.read_headers, "X-CSRF-Token": "B" * 43}).status_code == 403
    assert session_client.get("/api/me/organizations", headers=handle.read_headers).status_code == 200  # still signed in


# --- housekeeping -------------------------------------------------------------------------------------------------------------------------------


def test_a_login_removes_a_batch_of_expired_records(session_client, db_session):
    user = login_user(db_session)
    old = clock.utcnow() - timedelta(days=settings.security_event_retention_days + 5)
    db_session.execute(
        text("insert into security_events (occurred_at, event_type, source) values (:t, 'login_failure', '9.9.9.9')"), {"t": old}
    )
    ended = make_session(db_session, user, now=old, absolute_days=1)

    assert login(session_client, user.email).status_code == 200

    assert db_session.scalar(text("select count(*) from security_events where source = '9.9.9.9'")) == 0
    assert db_session.scalar(text("select count(*) from auth_sessions where id = :i"), {"i": ended.session.id}) == 0


# --- what is recorded ---------------------------------------------------------------------------------------------------------------------------


def test_security_events_hold_no_secret_email_or_password(session_client, db_session):
    user = login_user(db_session)
    login(session_client, user.email, OTHER_PASSWORD)
    login(session_client, "ghost@tests.invalid", "ghost password value")
    body = login(session_client, user.email).json()
    session_client.post("/api/auth/logout", headers={"Authorization": f"Bearer {body['token']}", "X-CSRF-Token": body["csrf_token"]})

    rows = db_session.execute(text("select * from security_events")).all()
    assert [r.event_type for r in rows] == ["login_failure", "login_failure", "login_success", "logout"]
    dump = " ".join(str(v) for row in rows for v in row)
    for secret in (OTHER_PASSWORD, PASSWORD, "ghost password value", body["token"], body["csrf_token"], hash_token(body["token"]), user.email, "ghost@tests.invalid", "tests.invalid"):
        assert secret not in dump, secret
    expected = hmac.new(settings.security_key.get_secret_value().encode(), f"login-identifier:{user.email}".encode(), hashlib.sha256).hexdigest()
    assert rows[0].identifier_hash == expected == security_events.identifier_hash(user.email)
    assert rows[1].identifier_hash != expected and len(rows[1].identifier_hash) == 64
    assert rows[0].actor_user_id is None and rows[2].actor_user_id == user.id  # a failure never says which account it was


def test_the_identifier_hash_depends_on_the_key_and_the_normalized_email(monkeypatch):
    first = security_events.identifier_hash("someone@example.com")
    assert first == security_events.identifier_hash("someone@example.com") and len(first) == 64
    assert security_events.identifier_hash("other@example.com") != first
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "security_key", SecretStr("a-completely-different-key-" + "y" * 20))
    assert security_events.identifier_hash("someone@example.com") != first


def test_security_events_cannot_be_updated_and_recent_ones_cannot_be_deleted(session_client, db_session):
    user = login_user(db_session)
    login(session_client, user.email, OTHER_PASSWORD)
    for statement in ("update security_events set detail = 'x'", "delete from security_events"):
        savepoint = db_session.begin_nested()
        with pytest.raises(Exception, match="security events"):
            db_session.execute(text(statement))
        savepoint.rollback()
