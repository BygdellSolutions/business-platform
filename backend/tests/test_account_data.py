"""A person's rights over their own ACCOUNT data: a copy of it, and the operator's erasure (anonymisation)."""

import json
from contextlib import nullcontext

import pytest
from sqlalchemy import select, text

from app.core import clock, mfa, mfa_service
from app.core.config import settings
from app.models import Customer, Role, User
from app.models.auth import AuthSession, UserCredential
from app.scripts import admin
from tests.auth_support import PASSWORD, events, login, login_user, make_session
from tests.factories import add_member, make_customer, make_org

pytestmark = pytest.mark.usefixtures("session_mode")

EXPORT = "/api/me/export"


# --- the copy ---------------------------------------------------------------------------------------------------------------------------


def test_a_person_gets_a_copy_of_their_own_account_data(session_client, db_session):
    user, other = login_user(db_session), login_user(db_session)
    org, other_org = make_org(db_session, "Mine"), make_org(db_session, "Not Mine")
    add_member(db_session, org, user, Role.OWNER)
    add_member(db_session, other_org, other, Role.OWNER)
    handle = make_session(db_session, user)
    session_client.post("/api/auth/login", json={"email": user.email, "password": "wrong password here"})  # an event with no actor
    login(session_client, user.email, PASSWORD)

    response = session_client.get(EXPORT, headers=handle.read_headers)

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="account-data.json"' and response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["profile"]["email"] == user.email and data["profile"]["password_set"] is True
    assert [m["organization_name"] for m in data["memberships"]] == ["Mine"]
    assert len(data["sessions"]) >= 1 and data["second_factor"] == {"enabled": False, "enabled_at": None, "recovery_codes_left": 0}
    assert {e["type"] for e in data["security_events"]} <= {"login_success", "login_failure"}


def test_nothing_secret_and_nothing_about_other_people_is_in_it(session_client, db_session):
    user, other = login_user(db_session), login_user(db_session)
    handle = make_session(db_session, user)
    other_handle = make_session(db_session, other)
    now = clock.utcnow()
    mfa_service.begin_enrollment(db_session, user, now)
    seed = db_session.get(__import__("app.models.auth", fromlist=["UserMfa"]).UserMfa, user.id).seed
    mfa_service.confirm_enrollment(db_session, user, mfa.hotp(mfa.derive_secret(seed), mfa.step_of(now)), now, "10.0.0.1")

    text_of_copy = json.dumps(session_client.get(EXPORT, headers=handle.read_headers).json())

    credential = db_session.get(UserCredential, user.id)
    assert credential.password_hash not in text_of_copy and "$argon2" not in text_of_copy
    assert seed not in text_of_copy and handle.token not in text_of_copy and other_handle.token not in text_of_copy
    assert other.email not in text_of_copy and str(other.id) not in text_of_copy
    assert "recovery_codes_left" in text_of_copy and json.loads(text_of_copy)["second_factor"]["enabled"] is True
    for row in db_session.scalars(select(AuthSession.token_hash)):
        assert row not in text_of_copy


def test_the_copy_needs_a_session_and_is_limited_per_hour(session_client, db_session):
    user = login_user(db_session)
    handle = make_session(db_session, user)

    assert session_client.get(EXPORT).status_code == 401
    statuses = [session_client.get(EXPORT, headers=handle.read_headers).status_code for _ in range(11)]
    assert statuses == [200] * 10 + [429]


# --- erasure ---------------------------------------------------------------------------------------------------------------------------


def test_an_account_that_still_belongs_to_an_organization_is_not_erased(db_session):
    user = login_user(db_session)
    add_member(db_session, make_org(db_session), user, Role.EMPLOYEE)

    with pytest.raises(admin.OperatorError, match="still a member of 1 organization"):
        admin.erase_account(db_session, email=user.email)

    db_session.expire_all()
    assert db_session.get(User, user.id).email == user.email and db_session.get(UserCredential, user.id) is not None  # untouched


def test_an_erased_account_has_no_name_no_email_no_credential_no_session_and_cannot_sign_in(session_client, db_session):
    user, bystander = login_user(db_session), login_user(db_session)
    make_session(db_session, user), make_session(db_session, bystander)
    handle = make_session(db_session, user)
    old_email = user.email

    admin.erase_account(db_session, email=old_email)
    db_session.expire_all()

    erased = db_session.get(User, user.id)
    assert erased.email.startswith("erased-") and erased.email.endswith("@erased.invalid") and erased.name == "Erased account" and not erased.is_active
    assert db_session.get(UserCredential, user.id) is None
    assert db_session.scalar(text("select count(*) from auth_sessions where user_id = :u"), {"u": user.id}) == 0
    assert session_client.get("/api/me/organizations", headers=handle.read_headers).status_code == 401
    assert login(session_client, old_email, PASSWORD).status_code == 401
    assert db_session.get(User, bystander.id).email == bystander.email  # nobody else is touched
    assert db_session.get(UserCredential, bystander.id) is not None


def test_erasing_removes_the_second_factor_too_and_records_one_event_without_the_email(db_session):
    user = login_user(db_session)
    now = clock.utcnow()
    mfa_service.begin_enrollment(db_session, user, now)
    seed = db_session.get(__import__("app.models.auth", fromlist=["UserMfa"]).UserMfa, user.id).seed
    mfa_service.confirm_enrollment(db_session, user, mfa.hotp(mfa.derive_secret(seed), mfa.step_of(now)), now, "10.0.0.1")
    old_email = user.email

    admin.erase_account(db_session, email=old_email)

    for table in ("user_mfa", "user_recovery_codes", "mfa_challenges", "user_setup_tokens"):
        assert db_session.scalar(text(f"select count(*) from {table} where user_id = :u"), {"u": user.id}) == 0, table
    [event] = events(db_session, "account_erased")
    assert event.actor_user_id == user.id and old_email not in str(event.detail) and (event.detail or "") == ""


def test_history_written_in_organizations_keeps_its_shape(db_session):
    user = login_user(db_session)
    org = make_org(db_session)
    customer = make_customer(db_session, org)
    db_session.execute(text("update customers set created_by = :u where id = :c"), {"u": user.id, "c": customer.id})

    admin.erase_account(db_session, email=user.email)
    db_session.expire_all()

    kept = db_session.get(Customer, customer.id)
    assert kept is not None and kept.created_by == user.id  # the reference stays; the person behind it no longer has a name


def test_invitations_addressed_to_the_erased_email_stop_naming_the_person(db_session):
    owner, invitee = login_user(db_session), login_user(db_session)
    org = make_org(db_session)
    add_member(db_session, org, owner, Role.OWNER)
    db_session.execute(
        text("INSERT INTO organization_invitations (organization_id, email, role, token_hash, expires_at, created_by) VALUES (:o, :e, 'viewer', :h, now() + interval '7 days', :u)"),
        {"o": org.id, "e": invitee.email, "h": "e" * 64, "u": owner.id},
    )

    old_email = invitee.email  # (the object is refreshed by the erasure)
    admin.erase_account(db_session, email=old_email)

    assert db_session.scalar(text("select count(*) from organization_invitations where email = :e"), {"e": old_email}) == 0
    assert db_session.scalar(text("select email from organization_invitations where organization_id = :o"), {"o": org.id}).startswith("erased-")


def test_the_command_needs_the_confirmation_flag_and_a_known_account(db_session, monkeypatch, capsys):
    user = login_user(db_session)
    monkeypatch.setattr(admin, "SessionLocal", lambda: nullcontext(db_session))

    assert admin.main(["erase-account", "--email", user.email]) == 1
    assert "--yes-erase" in capsys.readouterr().err
    assert db_session.get(User, user.id).email == user.email

    assert admin.main(["erase-account", "--email", "nobody@example.test", "--yes-erase"]) == 1  # a plain error, not a stack trace
    assert admin.main(["erase-account", "--email", user.email, "--yes-erase"]) == 0
    assert "erased" in capsys.readouterr().out
