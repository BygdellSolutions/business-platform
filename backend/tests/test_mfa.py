"""The second factor end to end: enrol, sign in with it, recovery codes, disable, the policy for owners and admins, the operator reset.

Time is moved through the clock seam: a TOTP code works once, so a test that signs in twice moves on by a step in between."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core import clock, mfa, mfa_service
from app.core.config import settings
from app.models import Role
from app.models.auth import UserMfa
from app.scripts import admin
from tests.auth_support import PASSWORD, active_sessions, events, login, login_user, make_session
from tests.factories import add_member, make_org

pytestmark = pytest.mark.usefixtures("session_mode")

BASE = "/api/auth/mfa"
CUSTOMERS = "/api/customers"


@pytest.fixture
def now(monkeypatch):
    """A movable clock: `now[0]` is the current time; call `tick()` to move one TOTP step on."""
    moment = [datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(clock, "utcnow", lambda: moment[0])
    return moment


def tick(moment, steps: int = 1) -> None:
    moment[0] += timedelta(seconds=mfa.STEP_SECONDS * steps)


def code_for(db, user, moment) -> str:
    row = db.get(UserMfa, user.id)
    return mfa.hotp(mfa.derive_secret(row.seed), mfa.step_of(moment[0]))


def enrol(db, user, moment) -> list[str]:
    """Turn the second factor on for `user` through the service; returns the recovery codes."""
    mfa_service.begin_enrollment(db, user, moment[0])
    codes = mfa_service.confirm_enrollment(db, user, code_for(db, user, moment), moment[0], "10.0.0.1")
    tick(moment)  # the confirming code is spent: the next sign-in needs a newer step
    return codes


def first_step(client, email):
    return login(client, email, PASSWORD)


def second_step(client, challenge, code):
    return client.post("/api/auth/login/mfa", json={"challenge": challenge, "code": code})


def get(client, handle, path):
    return client.get(path, headers=handle.read_headers)


# --- enrolling ---------------------------------------------------------------------------------------------------------------------------


def test_enrolling_needs_the_password_then_a_code_and_only_then_is_it_on(session_client, db_session, now):
    user = login_user(db_session)
    handle = make_session(db_session, user)

    wrong = session_client.post(f"{BASE}/enroll", json={"password": "not the password at all"}, headers=handle.headers)
    assert wrong.status_code == 400 and wrong.json()["detail"]["code"] == "wrong_password"
    assert db_session.get(UserMfa, user.id) is None  # nothing was started

    started = session_client.post(f"{BASE}/enroll", json={"password": PASSWORD}, headers=handle.headers)
    assert started.status_code == 200
    body = started.json()
    assert body["uri"].startswith("otpauth://totp/") and body["secret"] in body["uri"]
    assert session_client.get(BASE, headers=handle.read_headers).json() == {"enabled": False, "required": False, "recovery_codes_left": 0}

    bad = session_client.post(f"{BASE}/confirm", json={"code": "000000"}, headers=handle.headers)
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "invalid_code"
    assert not mfa_service.is_enabled(db_session, user.id)

    good = session_client.post(f"{BASE}/confirm", json={"code": code_for(db_session, user, now)}, headers=handle.headers)
    assert good.status_code == 200
    codes = good.json()["recovery_codes"]
    assert len(codes) == mfa.RECOVERY_CODE_COUNT == len(set(codes))
    assert session_client.get(BASE, headers=handle.read_headers).json() == {"enabled": True, "required": False, "recovery_codes_left": 10}
    assert [e.event_type for e in events(db_session) if e.event_type.startswith("mfa")] == ["mfa_failure", "mfa_enabled"]


def test_nothing_usable_is_stored(session_client, db_session, now):
    user = login_user(db_session)
    handle = make_session(db_session, user)
    secret = session_client.post(f"{BASE}/enroll", json={"password": PASSWORD}, headers=handle.headers).json()["secret"]
    codes = session_client.post(f"{BASE}/confirm", json={"code": code_for(db_session, user, now)}, headers=handle.headers).json()["recovery_codes"]

    dump = " ".join(str(v) for table in ("user_mfa", "user_recovery_codes", "security_events") for row in db_session.execute(text(f"select * from {table}")) for v in row)

    assert secret not in dump  # the key an authenticator holds is derived, never stored
    assert not any(code in dump or code.replace("-", "") in dump for code in codes)  # recovery codes only as hashes


def test_a_second_enrolment_while_one_is_on_is_refused(session_client, db_session, now):
    user = login_user(db_session)
    enrol(db_session, user, now)
    handle = make_session(db_session, user)

    again = session_client.post(f"{BASE}/enroll", json={"password": PASSWORD}, headers=handle.headers)

    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_enabled"


def test_the_mfa_endpoints_need_a_session_and_the_csrf_token(session_client, db_session):
    user = login_user(db_session)
    handle = make_session(db_session, user)

    assert session_client.get(BASE).status_code == 401
    assert session_client.post(f"{BASE}/enroll", json={"password": PASSWORD}).status_code == 401
    assert session_client.post(f"{BASE}/enroll", json={"password": PASSWORD}, headers=handle.read_headers).status_code == 403  # no CSRF token


# --- signing in with it ------------------------------------------------------------------------------------------------------------------


def test_a_password_alone_gives_no_session_when_a_second_factor_is_on(session_client, db_session, now):
    user = login_user(db_session)
    enrol(db_session, user, now)

    response = first_step(session_client, user.email)

    assert response.status_code == 200
    body = response.json()
    assert body["mfa_required"] is True and "token" not in body and "csrf_token" not in body
    assert active_sessions(db_session, user) == []
    assert not events(db_session, "login_success")  # not a sign-in yet
    stored = db_session.execute(text("select token_hash from mfa_challenges")).scalar()
    assert stored != body["challenge"] and len(stored) == 64  # only the hash is kept


def test_the_right_code_completes_the_sign_in_and_the_session_works(session_client, db_session, now):
    user = login_user(db_session)
    enrol(db_session, user, now)
    challenge = first_step(session_client, user.email).json()["challenge"]

    done = second_step(session_client, challenge, code_for(db_session, user, now))

    assert done.status_code == 200 and done.json()["token"]
    assert session_client.get("/api/me/organizations", headers={"Authorization": f"Bearer {done.json()['token']}"}).status_code == 200
    assert len(events(db_session, "login_success")) == 1 and len(active_sessions(db_session, user)) == 1


def test_a_code_cannot_be_used_twice_and_a_challenge_cannot_be_spent_twice(session_client, db_session, now):
    user = login_user(db_session)
    enrol(db_session, user, now)
    code = code_for(db_session, user, now)
    first = first_step(session_client, user.email).json()["challenge"]
    assert second_step(session_client, first, code).status_code == 200

    replay_code = second_step(session_client, first_step(session_client, user.email).json()["challenge"], code)  # same code, new challenge
    replay_challenge = second_step(session_client, first, code_for(db_session, user, now))  # same challenge

    assert replay_code.status_code == 401 and replay_challenge.status_code == 401
    assert len(active_sessions(db_session, user)) == 1


def test_every_wrong_way_looks_the_same_and_five_wrong_codes_kill_the_challenge(session_client, db_session, now):
    user = login_user(db_session)
    enrol(db_session, user, now)
    challenge = first_step(session_client, user.email).json()["challenge"]

    wrong = [second_step(session_client, challenge, "000000") for _ in range(settings.mfa_max_attempts_per_challenge)]
    right_now_too_late = second_step(session_client, challenge, code_for(db_session, user, now))
    unknown = second_step(session_client, "A" * 43, "000000")
    garbage = second_step(session_client, "not a token", "000000")

    assert {r.status_code for r in wrong} == {401} and {r.text for r in wrong} == {wrong[0].text}
    assert right_now_too_late.status_code == 401 and unknown.status_code == 401 and garbage.status_code == 401
    assert right_now_too_late.text == unknown.text == garbage.text == wrong[0].text  # nothing says which part was wrong
    assert active_sessions(db_session, user) == []


def test_an_expired_challenge_is_refused(session_client, db_session, now):
    user = login_user(db_session)
    enrol(db_session, user, now)
    challenge = first_step(session_client, user.email).json()["challenge"]

    now[0] += timedelta(seconds=settings.mfa_challenge_ttl_seconds + 1)

    assert second_step(session_client, challenge, code_for(db_session, user, now)).status_code == 401


def test_too_many_wrong_codes_from_one_person_are_throttled_across_challenges(session_client, db_session, now):
    user = login_user(db_session)
    enrol(db_session, user, now)
    statuses = []
    for _ in range(settings.mfa_max_failures + 2):
        challenge = first_step(session_client, user.email).json()["challenge"]
        statuses.append(second_step(session_client, challenge, "000000").status_code)

    assert statuses[: settings.mfa_max_failures] == [401] * settings.mfa_max_failures
    assert statuses[settings.mfa_max_failures :] == [429, 429]
    challenge = first_step(session_client, user.email).json()["challenge"]
    assert second_step(session_client, challenge, code_for(db_session, user, now)).status_code == 429  # even the right code waits


def test_a_disabled_person_cannot_finish_a_sign_in(session_client, db_session, now):
    user = login_user(db_session)
    enrol(db_session, user, now)
    challenge = first_step(session_client, user.email).json()["challenge"]
    admin.disable_user(db_session, email=user.email)

    assert second_step(session_client, challenge, code_for(db_session, user, now)).status_code == 401


# --- recovery codes ----------------------------------------------------------------------------------------------------------------------


def test_a_recovery_code_signs_in_once_and_is_counted(session_client, db_session, now):
    user = login_user(db_session)
    codes = enrol(db_session, user, now)
    challenge = first_step(session_client, user.email).json()["challenge"]

    assert second_step(session_client, challenge, codes[0].upper()).status_code == 200  # case does not matter
    second = second_step(session_client, first_step(session_client, user.email).json()["challenge"], codes[0])

    assert second.status_code == 401
    assert mfa_service.recovery_codes_left(db_session, user.id) == mfa.RECOVERY_CODE_COUNT - 1
    assert len(events(db_session, "recovery_code_used")) == 1


def test_regenerating_codes_needs_a_code_and_kills_the_old_ones(session_client, db_session, now):
    user = login_user(db_session)
    old = enrol(db_session, user, now)
    handle = make_session(db_session, user)

    refused = session_client.post(f"{BASE}/recovery-codes", json={"code": "000000"}, headers=handle.headers)
    assert refused.status_code == 400
    fresh = session_client.post(f"{BASE}/recovery-codes", json={"code": code_for(db_session, user, now)}, headers=handle.headers).json()["recovery_codes"]

    assert len(fresh) == mfa.RECOVERY_CODE_COUNT and not set(fresh) & set(old)
    challenge = first_step(session_client, user.email).json()["challenge"]
    assert second_step(session_client, challenge, old[0]).status_code == 401
    assert second_step(session_client, first_step(session_client, user.email).json()["challenge"], fresh[0]).status_code == 200


# --- turning it off, the policy, the operator --------------------------------------------------------------------------------------------


def test_disabling_needs_a_current_code_and_removes_everything(session_client, db_session, now):
    user = login_user(db_session)
    enrol(db_session, user, now)
    handle = make_session(db_session, user)

    assert session_client.post(f"{BASE}/disable", json={"code": "000000"}, headers=handle.headers).status_code == 400
    assert mfa_service.is_enabled(db_session, user.id)
    assert session_client.post(f"{BASE}/disable", json={"code": code_for(db_session, user, now)}, headers=handle.headers).status_code == 204

    assert not mfa_service.is_enabled(db_session, user.id) and mfa_service.recovery_codes_left(db_session, user.id) == 0
    assert first_step(session_client, user.email).json()["token"]  # a password alone signs in again


def test_with_the_policy_on_an_owner_cannot_turn_it_off(session_client, db_session, now, monkeypatch):
    monkeypatch.setattr(settings, "mfa_required_for_admins", True)
    owner = login_user(db_session)
    add_member(db_session, make_org(db_session), owner, Role.OWNER)
    enrol(db_session, owner, now)
    handle = make_session(db_session, owner)

    refused = session_client.post(f"{BASE}/disable", json={"code": code_for(db_session, owner, now)}, headers=handle.headers)

    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "required"
    assert mfa_service.is_enabled(db_session, owner.id)


def test_with_the_policy_on_an_owner_or_admin_without_a_second_factor_reaches_nothing_of_an_organization(session_client, db_session, now, monkeypatch):
    monkeypatch.setattr(settings, "mfa_required_for_admins", True)
    org = make_org(db_session)
    owner, admin_user, employee = login_user(db_session), login_user(db_session), login_user(db_session)
    for person, role in ((owner, Role.OWNER), (admin_user, Role.ADMIN), (employee, Role.EMPLOYEE)):
        add_member(db_session, org, person, role)
    handles = {name: make_session(db_session, person) for name, person in (("owner", owner), ("admin", admin_user), ("employee", employee))}
    headers = lambda name: {**handles[name].read_headers, "X-Organization-Id": str(org.id)}  # noqa: E731

    refused = session_client.get(CUSTOMERS, headers=headers("owner"))
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "mfa_required"
    assert session_client.get(CUSTOMERS, headers=headers("admin")).status_code == 403
    assert session_client.get(CUSTOMERS, headers=headers("employee")).status_code == 200  # only owners and admins are required
    me = session_client.get("/api/me/user", headers=handles["owner"].read_headers).json()  # the page that explains it still works
    assert me["mfa_required"] is True and me["mfa_enabled"] is False
    assert session_client.get(BASE, headers=handles["owner"].read_headers).status_code == 200
    assert session_client.get("/api/me/user", headers=handles["employee"].read_headers).json()["mfa_required"] is False  # not for an employee

    enrol(db_session, owner, now)
    assert session_client.get(CUSTOMERS, headers=headers("owner")).status_code == 200  # enrolling lifts it at once
    assert session_client.get("/api/me/user", headers=handles["owner"].read_headers).json()["mfa_enabled"] is True


def test_with_the_policy_on_a_person_who_is_not_an_owner_or_admin_may_still_turn_it_off(session_client, db_session, now, monkeypatch):
    monkeypatch.setattr(settings, "mfa_required_for_admins", True)
    employee = login_user(db_session)
    add_member(db_session, make_org(db_session), employee, Role.EMPLOYEE)
    enrol(db_session, employee, now)
    handle = make_session(db_session, employee)

    assert session_client.get(BASE, headers=handle.read_headers).json()["required"] is False
    assert session_client.post(f"{BASE}/disable", json={"code": code_for(db_session, employee, now)}, headers=handle.headers).status_code == 204


def test_the_policy_is_off_by_default_outside_production(session_client, db_session):
    org = make_org(db_session)
    owner = login_user(db_session)
    add_member(db_session, org, owner, Role.OWNER)
    handle = make_session(db_session, owner)

    assert settings.mfa_required_for_admins is None and settings.app_env == "development"
    assert session_client.get(CUSTOMERS, headers={**handle.read_headers, "X-Organization-Id": str(org.id)}).status_code == 200


def test_the_operator_can_reset_a_person_who_lost_everything(session_client, db_session, now):
    user = login_user(db_session)
    enrol(db_session, user, now)
    handle = make_session(db_session, user)

    admin.reset_mfa(db_session, email=user.email)

    assert not mfa_service.is_enabled(db_session, user.id) and mfa_service.recovery_codes_left(db_session, user.id) == 0
    assert session_client.get("/api/me/organizations", headers=handle.read_headers).status_code == 401  # their sessions ended
    assert len(events(db_session, "mfa_reset_by_operator")) == 1
    assert first_step(session_client, user.email).json()["token"]  # password only, until they enrol again
