"""Session resolution, lifetimes, CSRF, forged dev identity, and password change."""

from datetime import timedelta

import pytest
from sqlalchemy import text

from app.core import clock, passwords
from app.core.config import settings
from app.models import Role
from tests.auth_support import OTHER_PASSWORD, PASSWORD, active_sessions, events, login, login_user, make_session
from tests.factories import add_member, make_org, make_user

pytestmark = pytest.mark.usefixtures("session_mode")

ME = "/api/me/organizations"


def get(client, handle_or_headers, path=ME):
    headers = handle_or_headers.read_headers if hasattr(handle_or_headers, "read_headers") else handle_or_headers
    return client.get(path, headers=headers)


# --- the Authorization header -------------------------------------------------------------------------------------------------------------------


def test_a_valid_session_authenticates(session_client, db_session):
    handle = make_session(db_session, login_user(db_session))
    assert get(session_client, handle).status_code == 200


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
def test_the_scheme_is_case_insensitive(session_client, db_session, scheme):
    handle = make_session(db_session, login_user(db_session))
    assert get(session_client, {"Authorization": f"{scheme} {handle.token}"}).status_code == 200


@pytest.mark.parametrize(
    "build",
    [
        lambda t: {},
        lambda t: {"Authorization": ""},
        lambda t: {"Authorization": t},
        lambda t: {"Authorization": f"Basic {t}"},
        lambda t: {"Authorization": f"Token {t}"},
        lambda t: {"Authorization": f"Bearer {t[:-1]}"},
        lambda t: {"Authorization": f"Bearer {t}x"},
        lambda t: {"Authorization": f"Bearer {t[:-1]}!"},
        lambda t: {"Authorization": f"Bearer {t} extra"},
        lambda t: {"Authorization": "Bearer "},
        lambda t: {"Authorization": f"Bearer {t.upper() if t.upper() != t else t.lower()}"},
    ],
)
def test_anything_but_exactly_our_token_is_not_authenticated(session_client, db_session, build):
    handle = make_session(db_session, login_user(db_session))
    assert get(session_client, build(handle.token)).status_code == 401


@pytest.mark.parametrize("junk", ["é" * 43, "A" * 42 + "é", "Āā" * 20])
def test_a_non_ascii_authorization_header_is_a_plain_401_not_a_server_error(session_client, db_session, junk):
    assert session_client.get(ME, headers={"Authorization": ("Bearer " + junk).encode("latin-1", "ignore")}).status_code == 401


def test_an_unknown_token_of_the_right_shape_is_not_authenticated(session_client, db_session):
    assert get(session_client, {"Authorization": "Bearer " + "A" * 43}).status_code == 401


def test_a_cookie_header_is_never_an_authentication_method(session_client, db_session):
    handle = make_session(db_session, login_user(db_session))
    assert session_client.get(ME, headers={"Cookie": f"bp_session={handle.token}; session={handle.token}"}).status_code == 401


# --- lifetimes ---------------------------------------------------------------------------------------------------------------------------------


def test_an_absolutely_expired_session_is_refused(session_client, db_session, monkeypatch):
    handle = make_session(db_session, login_user(db_session), absolute_days=1)
    created = handle.session.created_at
    for hours in (0, 6, 12, 18, 23):  # in use all the time: only the absolute limit can end it
        monkeypatch.setattr(clock, "utcnow", lambda hours=hours: created + timedelta(hours=hours))
        assert get(session_client, handle).status_code == 200
    monkeypatch.setattr(clock, "utcnow", lambda: created + timedelta(days=1, seconds=1))
    assert get(session_client, handle).status_code == 401


def test_activity_does_not_extend_the_absolute_lifetime(session_client, db_session, monkeypatch):
    handle = make_session(db_session, login_user(db_session))
    created = clock.utcnow()
    for hours in range(0, settings.session_absolute_days * 24, 10):  # used every 10 hours: never idle
        monkeypatch.setattr(clock, "utcnow", lambda hours=hours: created + timedelta(hours=hours))
        assert get(session_client, handle).status_code == 200
    monkeypatch.setattr(clock, "utcnow", lambda: created + timedelta(days=settings.session_absolute_days, minutes=1))
    assert get(session_client, handle).status_code == 401


def test_an_idle_session_expires(session_client, db_session, monkeypatch):
    handle = make_session(db_session, login_user(db_session))
    created = clock.utcnow()
    idle = timedelta(hours=settings.session_idle_hours)
    monkeypatch.setattr(clock, "utcnow", lambda: created + idle - timedelta(minutes=1))
    assert get(session_client, handle).status_code == 200  # (this also refreshes it)
    monkeypatch.setattr(clock, "utcnow", lambda: created + idle - timedelta(minutes=1) + idle + timedelta(minutes=1))
    assert get(session_client, handle).status_code == 401


def test_use_keeps_a_session_from_going_idle(session_client, db_session, monkeypatch):
    handle = make_session(db_session, login_user(db_session))
    created = clock.utcnow()
    for hours in (6, 11, 17, 23, 29):  # never idle for 12 hours
        monkeypatch.setattr(clock, "utcnow", lambda hours=hours: created + timedelta(hours=hours))
        assert get(session_client, handle).status_code == 200


def test_last_use_is_refreshed_at_most_every_touch_interval(session_client, db_session, monkeypatch):
    handle = make_session(db_session, login_user(db_session))
    created = handle.session.created_at
    stamp = lambda: db_session.scalar(text("select last_used_at from auth_sessions where id = :i"), {"i": handle.session.id})  # noqa: E731
    monkeypatch.setattr(clock, "utcnow", lambda: created + timedelta(seconds=10))
    get(session_client, handle)
    assert stamp() == created  # too soon: no write for a read
    monkeypatch.setattr(clock, "utcnow", lambda: created + timedelta(seconds=settings.session_touch_seconds + 1))
    get(session_client, handle)
    assert stamp() == created + timedelta(seconds=settings.session_touch_seconds + 1)


def test_a_revoked_session_is_refused(session_client, db_session):
    handle = make_session(db_session, login_user(db_session))
    db_session.execute(text("update auth_sessions set revoked_at = now(), revoked_reason = 'logout' where id = :i"), {"i": handle.session.id})
    assert get(session_client, handle).status_code == 401


def test_deactivating_the_user_ends_their_sessions_at_once(session_client, db_session):
    user = login_user(db_session)
    handle = make_session(db_session, user)
    assert get(session_client, handle).status_code == 200
    user.is_active = False
    db_session.flush()
    assert get(session_client, handle).status_code == 401
    assert session_client.post("/api/auth/logout", headers=handle.headers).status_code == 401


# --- the development identity is not an option in session mode -----------------------------------------------------------------------------------


def test_a_forged_dev_header_is_ignored(session_client, db_session):
    user = login_user(db_session)
    assert session_client.get(ME, headers={"X-Dev-User-Email": user.email}).status_code == 401


def test_the_dev_user_default_is_ignored(session_client, db_session, monkeypatch):
    user = login_user(db_session)
    monkeypatch.setattr(settings, "dev_user_email", user.email)
    assert session_client.get(ME).status_code == 401
    assert session_client.get(ME, headers={"X-Dev-User-Email": user.email}).status_code == 401


def test_a_valid_session_wins_over_a_forged_dev_header_which_cannot_change_who_you_are(session_client, db_session):
    mine, other = login_user(db_session), login_user(db_session)
    org_mine, org_other = make_org(db_session, "Mine"), make_org(db_session, "Other")
    add_member(db_session, org_mine, mine), add_member(db_session, org_other, other)
    handle = make_session(db_session, mine)

    response = session_client.get(ME, headers={**handle.read_headers, "X-Dev-User-Email": other.email})

    assert [o["name"] for o in response.json()] == ["Mine"]


def test_a_failed_session_never_falls_back_to_the_dev_identity(session_client, db_session, monkeypatch):
    user = login_user(db_session)
    monkeypatch.setattr(settings, "dev_user_email", user.email)
    for headers in ({"Authorization": "Bearer " + "A" * 43, "X-Dev-User-Email": user.email}, {"Authorization": "garbage", "X-Dev-User-Email": user.email}):
        assert session_client.get(ME, headers=headers).status_code == 401


def test_the_dev_resolver_is_not_even_consulted_in_session_mode(session_client, db_session, monkeypatch):
    from app.core import dev_identity

    def forbidden(*args, **kwargs):
        raise AssertionError("the dev identity must not be consulted in session mode")

    monkeypatch.setattr(dev_identity, "resolve_dev_user", forbidden)
    handle = make_session(db_session, login_user(db_session))
    assert get(session_client, handle).status_code == 200
    assert session_client.get(ME, headers={"X-Dev-User-Email": "x@tests.invalid"}).status_code == 401


def test_the_session_never_says_anything_about_organizations_the_membership_does(session_client, db_session):
    user = login_user(db_session)
    mine, foreign = make_org(db_session, "Mine"), make_org(db_session, "Foreign")
    add_member(db_session, mine, user, Role.VIEWER)
    handle = make_session(db_session, user)

    assert session_client.get("/api/me", headers={**handle.read_headers, "X-Organization-Id": str(mine.id)}).json()["role"] == "viewer"
    assert session_client.get("/api/me", headers={**handle.read_headers, "X-Organization-Id": str(foreign.id)}).status_code == 404


# --- CSRF (the FastAPI half: the BFF validates Origin and the browser cookie) --------------------------------------------------------------------


def make_owner_session(db_session):
    user = login_user(db_session)
    org = make_org(db_session)
    add_member(db_session, org, user, Role.OWNER)
    return org, make_session(db_session, user)


def patch_org(client, org, headers):
    return client.patch("/api/organization", json={"name": "Renamed"}, headers={**headers, "X-Organization-Id": str(org.id)})


def test_a_mutating_request_needs_the_csrf_token_of_its_own_session(session_client, db_session):
    org, handle = make_owner_session(db_session)
    other = make_session(db_session, login_user(db_session))

    assert patch_org(session_client, org, handle.read_headers).status_code == 403
    assert patch_org(session_client, org, {**handle.read_headers, "X-CSRF-Token": "A" * 43}).status_code == 403
    assert patch_org(session_client, org, {**handle.read_headers, "X-CSRF-Token": other.csrf}).status_code == 403  # bound to THIS session
    assert patch_org(session_client, org, {**handle.read_headers, "X-CSRF-Token": handle.token}).status_code == 403  # the session token is not the CSRF token
    assert patch_org(session_client, org, {**handle.read_headers, "X-CSRF-Token": "short"}).status_code == 403
    assert patch_org(session_client, org, handle.headers).status_code == 200


def test_the_refusal_is_structured_and_changes_nothing(session_client, db_session):
    org, handle = make_owner_session(db_session)
    before = org.name
    response = patch_org(session_client, org, handle.read_headers)
    assert response.json()["detail"]["code"] == "csrf_failed"
    db_session.refresh(org)
    assert org.name == before


def test_reads_need_no_csrf_token_and_every_mutation_method_does(session_client, db_session):
    org, handle = make_owner_session(db_session)
    headers = {**handle.read_headers, "X-Organization-Id": str(org.id)}
    assert session_client.get("/api/customers", headers=headers).status_code == 200
    for method, path in (("POST", "/api/customers"), ("PATCH", "/api/organization"), ("DELETE", "/api/customers/00000000-0000-4000-8000-000000000001")):
        assert session_client.request(method, path, json={}, headers=headers).status_code == 403, method
    created = session_client.post("/api/customers", json={"customer_type": "person", "name": "Anna"}, headers={**handle.headers, "X-Organization-Id": str(org.id)})
    assert created.status_code == 201


def test_an_unauthenticated_mutation_is_401_not_a_csrf_hint(session_client):
    assert session_client.post("/api/customers", json={}, headers={"X-CSRF-Token": "A" * 43}).status_code == 401


# --- change password ---------------------------------------------------------------------------------------------------------------------------


def change(client, handle, current=PASSWORD, new="a brand new long passphrase"):
    return client.post("/api/auth/change-password", json={"current_password": current, "new_password": new}, headers=handle.headers)


def test_changing_the_password_ends_every_other_session_and_keeps_this_one(session_client, db_session):
    user = login_user(db_session)
    here, elsewhere, third = make_session(db_session, user), make_session(db_session, user), make_session(db_session, user)

    assert change(session_client, here).status_code == 204

    assert get(session_client, here).status_code == 200
    assert get(session_client, elsewhere).status_code == 401 and get(session_client, third).status_code == 401
    assert len(active_sessions(db_session, user)) == 1
    db_session.refresh(elsewhere.session)
    assert elsewhere.session.revoked_reason == "password_changed"
    assert [e.event_type for e in events(db_session)] == ["password_changed"]


def test_after_a_change_only_the_new_password_logs_in(session_client, db_session):
    user = login_user(db_session)
    handle = make_session(db_session, user)
    change(session_client, handle, new="a brand new long passphrase")
    assert login(session_client, user.email, PASSWORD).status_code == 401
    assert login(session_client, user.email, "a brand new long passphrase").status_code == 200


def test_the_wrong_current_password_changes_nothing_and_is_recorded(session_client, db_session):
    user = login_user(db_session)
    other = make_session(db_session, user)
    handle = make_session(db_session, user)

    response = change(session_client, handle, current=OTHER_PASSWORD)

    assert response.status_code == 400 and response.json()["detail"]["code"] == "wrong_current_password"
    assert get(session_client, other).status_code == 200  # no session was ended
    assert login(session_client, user.email, PASSWORD).status_code == 200
    assert [e.event_type for e in events(db_session)] == ["password_change_failure", "login_success"]


def test_guessing_the_current_password_through_a_stolen_session_is_throttled(session_client, db_session):
    user = login_user(db_session)
    handle = make_session(db_session, user)
    codes = [change(session_client, handle, current=OTHER_PASSWORD).status_code for _ in range(settings.password_change_max_failures + 2)]
    assert codes == [400] * settings.password_change_max_failures + [429, 429]
    assert change(session_client, handle).status_code == 429  # even the right one waits for the window


@pytest.mark.parametrize("new", ["short", "x" * 129, PASSWORD, "has\x00nul inside the password"])
def test_a_new_password_must_meet_the_policy_and_differ(session_client, db_session, new):
    handle = make_session(db_session, login_user(db_session))
    response = change(session_client, handle, new=new)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "password_policy"


def test_changing_the_password_needs_a_session_and_csrf(session_client, db_session):
    handle = make_session(db_session, login_user(db_session))
    body = {"current_password": PASSWORD, "new_password": "a brand new long passphrase"}
    assert session_client.post("/api/auth/change-password", json=body).status_code == 401
    assert session_client.post("/api/auth/change-password", json=body, headers=handle.read_headers).status_code == 403


def test_a_user_without_a_credential_cannot_change_a_password_they_do_not_have(session_client, db_session):
    user = make_user(db_session)
    handle = make_session(db_session, user)
    assert change(session_client, handle).status_code == 400
    assert db_session.scalar(text("select count(*) from user_credentials where user_id = :u"), {"u": user.id}) == 0


def test_the_change_hashes_with_the_current_parameters(session_client, db_session):
    user = login_user(db_session)
    handle = make_session(db_session, user)
    change(session_client, handle)
    stored = db_session.scalar(text("select password_hash from user_credentials where user_id = :u"), {"u": user.id})
    assert stored.startswith("$argon2id$") and passwords.verify(stored, "a brand new long passphrase")


# --- GET /api/me/user (S2: a client learns who it is from the backend, never from a browser-held email) ----------------------------------------------


def test_me_user_returns_the_session_user_and_nothing_about_organizations(session_client, db_session):
    user = login_user(db_session, name="Ada Owner")
    org = make_org(db_session)
    add_member(db_session, org, user, Role.OWNER)
    handle = make_session(db_session, user)

    response = session_client.get("/api/me/user", headers=handle.read_headers)

    assert response.status_code == 200
    assert response.json() == {"id": str(user.id), "email": user.email, "name": "Ada Owner", "can_create_organizations": False}


def test_me_user_needs_authentication_and_ignores_an_organization_selector(session_client, db_session):
    user = login_user(db_session)  # no membership at all
    handle = make_session(db_session, user)
    assert session_client.get("/api/me/user").status_code == 401
    assert session_client.get("/api/me/user", headers={"X-Dev-User-Email": user.email}).status_code == 401
    assert session_client.get("/api/me/user", headers={**handle.read_headers, "X-Organization-Id": "00000000-0000-4000-8000-0000000000ff"}).status_code == 200
