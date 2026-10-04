"""Organization onboarding: an authenticated user creates an organization and becomes its owner (S3).

Rollback-only tests (one shared session). The behaviour that depends on separate transactions (atomicity as
seen from another connection, races, a revoke in flight) is in test_organization_creation_concurrency.py.
"""

import secrets
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.core import organizations as service
from app.models import Organization, OrganizationUser, Role, SecurityEvent, User
from app.models.organization_request import OrganizationCreationRequest
from app.scripts import admin
from tests.auth_support import events, make_session
from tests.factories import add_member, make_org, make_user

URL = "/api/organizations"


def key() -> str:
    return secrets.token_urlsafe(32)  # 43 URL-safe characters, like a browser's


def body(name: str | None = None, **extra) -> dict:
    return {"name": name or f"Org {uuid.uuid4().hex[:8]}", "default_currency": "EUR", **extra}


def creator(db, **fields):
    return make_user(db, can_create_organizations=True, **fields)


def as_user(user) -> dict[str, str]:
    return {"X-Dev-User-Email": user.email}


def count(db, model, *conditions) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


def snapshot(db) -> tuple[int, int, int, int]:
    return tuple(count(db, m) for m in (Organization, OrganizationUser, OrganizationCreationRequest, SecurityEvent))


# --- the happy path, through the ordinary membership reads ------------------------------------------------------------------


def test_an_allowed_user_creates_an_organization_and_becomes_its_owner(client, db_session):
    user = creator(db_session)

    response = client.post(URL, json=body("Fredrik Horse Therapy", legal_name="Fredrik AB", city="Umeå", country_code="se"), headers=as_user(user))

    assert response.status_code == 201, response.text
    created = response.json()
    assert created["name"] == "Fredrik Horse Therapy" and created["default_currency"] == "EUR"
    assert created["legal_name"] == "Fredrik AB" and created["city"] == "Umeå" and created["country_code"] == "SE"
    memberships = db_session.scalars(select(OrganizationUser).where(OrganizationUser.organization_id == uuid.UUID(created["id"]))).all()
    assert [(m.user_id, m.role) for m in memberships] == [(user.id, "owner")]


def test_the_new_organization_appears_in_the_ordinary_membership_reads_and_works_as_a_tenant(client, db_session):
    user = creator(db_session)
    created = client.post(URL, json=body("Fresh Org"), headers=as_user(user)).json()

    mine = client.get("/api/me/organizations", headers=as_user(user)).json()
    assert mine == [{"id": created["id"], "name": "Fresh Org", "role": "owner"}]

    scoped = {**as_user(user), "X-Organization-Id": created["id"]}
    me = client.get("/api/me", headers=scoped).json()
    assert me["organization"] == {"id": created["id"], "name": "Fresh Org"} and me["role"] == "owner"
    assert client.get("/api/organization", headers=scoped).json()["default_currency"] == "EUR"
    assert client.post("/api/customers", json={"name": "Anna Andersson", "customer_type": "person"}, headers=scoped).status_code == 201
    assert len(client.get("/api/customers", headers=scoped).json()) == 1


def test_a_user_who_already_belongs_to_organizations_can_create_another_without_naming_one(client, db_session):
    user = creator(db_session)
    add_member(db_session, make_org(db_session, "First"), user, Role.EMPLOYEE)
    add_member(db_session, make_org(db_session, "Second"), user, Role.VIEWER)

    # Two memberships and no X-Organization-Id: an ordinary tenant request would be ambiguous (400); creation is not tenant-scoped.
    assert client.post(URL, json=body(), headers=as_user(user)).status_code == 201
    assert len(client.get("/api/me/organizations", headers=as_user(user)).json()) == 3


def test_each_new_organization_starts_with_exactly_one_owner_and_no_other_member(client, db_session):
    first, second = creator(db_session), creator(db_session)
    a = client.post(URL, json=body("Same name"), headers=as_user(first)).json()
    b = client.post(URL, json=body("Same name"), headers=as_user(second)).json()

    assert a["id"] != b["id"]  # no uniqueness on names, across users
    for created, owner in ((a, first), (b, second)):
        rows = db_session.execute(select(OrganizationUser.user_id, OrganizationUser.role).where(OrganizationUser.organization_id == uuid.UUID(created["id"]))).all()
        assert rows == [(owner.id, "owner")]


def test_one_user_may_create_two_organizations_with_the_same_name(client, db_session):
    user = creator(db_session)
    assert client.post(URL, json=body("Twin"), headers=as_user(user)).status_code == 201
    assert client.post(URL, json=body("Twin"), headers=as_user(user)).status_code == 201
    assert count(db_session, Organization, Organization.name == "Twin") == 2


# --- authority ------------------------------------------------------------------------------------------------------------


def test_without_authentication_it_is_a_401_and_nothing_is_written(client, db_session):
    before = snapshot(db_session)
    assert client.post(URL, json=body()).status_code == 401
    assert client.post(URL, json=body(), headers={"X-Dev-User-Email": "nobody@tests.invalid"}).status_code == 401
    assert snapshot(db_session) == before


def test_a_user_without_the_flag_is_refused_with_a_403_and_nothing_is_written(client, db_session):
    user = make_user(db_session)  # can_create_organizations defaults to false
    before = snapshot(db_session)

    response = client.post(URL, json=body(), headers=as_user(user))

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "organization_creation_not_allowed"
    assert snapshot(db_session) == before


def test_being_an_owner_of_an_organization_does_not_allow_creating_another(client, db_session):
    user = make_user(db_session)
    add_member(db_session, make_org(db_session, "Mine"), user, Role.OWNER)

    assert client.post(URL, json=body(), headers=as_user(user)).status_code == 403


def test_creating_an_organization_does_not_grant_the_flag_and_the_flag_is_not_changed_by_it(client, db_session):
    user = creator(db_session)
    client.post(URL, json=body(), headers=as_user(user))
    db_session.refresh(user)
    assert user.can_create_organizations is True

    other = make_user(db_session)
    add_member(db_session, make_org(db_session, "X"), other, Role.OWNER)
    db_session.refresh(other)
    assert other.can_create_organizations is False


def test_a_disabled_user_is_unauthenticated(client, db_session):
    user = creator(db_session, is_active=False)
    assert client.post(URL, json=body(), headers=as_user(user)).status_code == 401


def test_the_flag_is_judged_from_the_database_not_from_a_stale_loaded_user(client, db_session):
    user = creator(db_session)
    assert client.get("/api/me/user", headers=as_user(user)).json()["can_create_organizations"] is True  # the user is now loaded in the session
    db_session.execute(text("update users set can_create_organizations = false where id = :u"), {"u": user.id})  # the ORM object still says true
    assert user.can_create_organizations is True

    assert client.post(URL, json=body(), headers=as_user(user)).status_code == 403


@pytest.mark.parametrize(
    "extra",
    [
        {"owner_user_id": str(uuid.uuid4())},
        {"owner_email": "someone@tests.invalid"},
        {"owner": "someone"},
        {"role": "owner"},
        {"user_id": str(uuid.uuid4())},
        {"created_by": str(uuid.uuid4())},
        {"organization_id": str(uuid.uuid4())},
        {"id": str(uuid.uuid4())},
        {"can_create_organizations": True},
    ],
)
def test_a_request_cannot_name_an_owner_a_role_or_an_id(client, db_session, extra):
    user = creator(db_session)
    before = snapshot(db_session)

    response = client.post(URL, json={**body(), **extra}, headers=as_user(user))

    assert response.status_code == 422
    assert snapshot(db_session) == before


def test_organization_and_role_headers_are_irrelevant(client, db_session):
    user, victim_org = creator(db_session), make_org(db_session, "Not yours")
    forged = {**as_user(user), "X-Organization-Id": str(victim_org.id), "X-Role": "owner", "X-User-Id": str(uuid.uuid4()), "X-Owner-Email": "x@tests.invalid"}

    response = client.post(URL, json=body(), headers=forged)

    assert response.status_code == 201
    assert response.json()["id"] != str(victim_org.id)
    assert count(db_session, OrganizationUser, OrganizationUser.organization_id == victim_org.id) == 0
    assert db_session.scalar(select(OrganizationUser.role).where(OrganizationUser.organization_id == uuid.UUID(response.json()["id"]))) == "owner"


# --- validation and the currency rule -------------------------------------------------------------------------------------------


def test_the_currency_must_be_chosen_explicitly_there_is_no_default(client, db_session):
    user = creator(db_session)
    before = snapshot(db_session)
    missing = {"name": "No currency"}
    for payload in (missing, {**missing, "default_currency": None}, {**missing, "default_currency": ""}, {**missing, "default_currency": "SEKK"}, {**missing, "default_currency": "S3K"}):
        assert client.post(URL, json=payload, headers=as_user(user)).status_code == 422, payload
    assert snapshot(db_session) == before
    assert count(db_session, Organization, Organization.default_currency == "SEK", Organization.name == "No currency") == 0


def test_a_chosen_currency_is_stored_as_given_without_inference_from_the_country(client, db_session):
    user = creator(db_session)
    created = client.post(URL, json=body(default_currency="usd", country_code="SE"), headers=as_user(user)).json()
    assert created["default_currency"] == "USD" and created["country_code"] == "SE"  # shape-checked and upper-cased, nothing more


@pytest.mark.parametrize("name", [None, "", "   ", "x" * 256])
def test_the_name_is_required_and_bounded(client, db_session, name):
    user = creator(db_session)
    payload = {"default_currency": "EUR"} if name is None else {"name": name, "default_currency": "EUR"}
    assert client.post(URL, json=payload, headers=as_user(user)).status_code == 422


# --- the security event ----------------------------------------------------------------------------------------------------------


def test_a_successful_creation_records_one_security_event_without_the_form_contents(client, db_session):
    user = creator(db_session)
    created = client.post(URL, json=body("Very Private Name", legal_name="Secret Legal AB", city="Hidden City", registration_number="556677-8899"), headers=as_user(user), ).json()

    recorded = events(db_session, "organization_created")
    assert len(recorded) == 1
    event = recorded[0]
    assert event.actor_user_id == user.id and str(event.organization_id) == created["id"] and event.occurred_at is not None
    for field in ("source", "identifier_hash", "detail"):
        value = getattr(event, field) or ""
        assert not any(secret in value for secret in ("Very Private", "Secret Legal", "Hidden City", "556677", user.email, "EUR"))
    assert event.detail == "unkeyed"


def test_a_refused_or_invalid_request_records_no_event(client, db_session):
    plain, allowed = make_user(db_session), creator(db_session)
    client.post(URL, json=body(), headers=as_user(plain))
    client.post(URL, json={"name": "x"}, headers=as_user(allowed))
    client.post(URL, json=body())
    assert events(db_session, "organization_created") == []


# --- retries: the idempotency key -----------------------------------------------------------------------------------------------


def test_repeating_a_request_key_returns_the_same_organization_and_creates_nothing_more(client, db_session):
    user, request_key, payload = creator(db_session), key(), body("Once")
    first = client.post(URL, json=payload, headers={**as_user(user), "Idempotency-Key": request_key})
    before = snapshot(db_session)

    second = client.post(URL, json=payload, headers={**as_user(user), "Idempotency-Key": request_key})

    assert first.status_code == 201 and second.status_code == 200
    assert second.json()["id"] == first.json()["id"] and second.json()["name"] == "Once"
    assert snapshot(db_session) == before
    assert [e.detail for e in events(db_session, "organization_created")] == ["keyed"]


def test_the_same_key_with_a_different_body_is_a_conflict_and_changes_nothing(client, db_session):
    user, request_key = creator(db_session), key()
    first = client.post(URL, json=body("Original"), headers={**as_user(user), "Idempotency-Key": request_key})
    before = snapshot(db_session)

    response = client.post(URL, json=body("Different"), headers={**as_user(user), "Idempotency-Key": request_key})

    assert first.status_code == 201 and response.status_code == 409
    assert response.json()["detail"]["code"] == "request_key_conflict"
    assert snapshot(db_session) == before


def test_different_keys_or_no_key_create_different_organizations(client, db_session):
    user, payload = creator(db_session), body("Repeat")
    for headers in ({"Idempotency-Key": key()}, {"Idempotency-Key": key()}, {}, {}):
        assert client.post(URL, json=payload, headers={**as_user(user), **headers}).status_code == 201
    assert count(db_session, Organization, Organization.name == "Repeat") == 4


def test_a_request_key_belongs_to_its_creator_and_cannot_replay_for_someone_else(client, db_session):
    first, second, request_key, payload = creator(db_session), creator(db_session), key(), body("Shared key")
    a = client.post(URL, json=payload, headers={**as_user(first), "Idempotency-Key": request_key}).json()
    b = client.post(URL, json=payload, headers={**as_user(second), "Idempotency-Key": request_key})

    assert b.status_code == 201 and b.json()["id"] != a["id"]  # the second user's own organization, not the first user's
    owners = db_session.scalars(select(OrganizationUser.user_id).where(OrganizationUser.organization_id == uuid.UUID(b.json()["id"]))).all()
    assert owners == [second.id]


def test_a_replay_is_only_returned_to_a_user_who_is_still_a_member(client, db_session):
    user, request_key, payload = creator(db_session), key(), body("Left")
    created = client.post(URL, json=payload, headers={**as_user(user), "Idempotency-Key": request_key}).json()
    db_session.execute(text("delete from organization_users where organization_id = :o"), {"o": created["id"]})

    response = client.post(URL, json=payload, headers={**as_user(user), "Idempotency-Key": request_key})

    assert response.status_code == 409 and created["id"] not in response.text


def test_a_replay_does_not_bypass_a_revoked_capability(client, db_session):
    user, request_key, payload = creator(db_session), key(), body("Revoked later")
    client.post(URL, json=payload, headers={**as_user(user), "Idempotency-Key": request_key})
    admin.set_org_creation(db_session, email=user.email, allowed=False)

    assert client.post(URL, json=payload, headers={**as_user(user), "Idempotency-Key": request_key}).status_code == 403


@pytest.mark.parametrize("bad", ["short", "x" * 44, "a" * 42 + "!", "a" * 42 + " ", ""])
def test_a_malformed_key_is_a_422(client, db_session, bad):
    user = creator(db_session)
    before = snapshot(db_session)
    assert client.post(URL, json=body(), headers={**as_user(user), "Idempotency-Key": bad}).status_code == 422
    assert snapshot(db_session) == before


def test_the_database_enforces_one_request_per_creator_and_key(db_session):
    user = creator(db_session)
    first, second = make_org(db_session, "A"), make_org(db_session, "B")
    row = lambda org, request_key, digest="0" * 64: OrganizationCreationRequest(user_id=user.id, request_key=request_key, request_hash=digest, organization_id=org.id)
    request_key = key()
    db_session.add(row(first, request_key))
    db_session.flush()

    for bad in (row(second, request_key), row(first, key()), row(second, "short"), row(second, key(), "xyz")):
        with pytest.raises(IntegrityError):
            with db_session.begin_nested():
                db_session.add(bad)
                db_session.flush()


# --- atomicity (the failure paths; the committed-data proof is in the concurrency file) ------------------------------------------


def test_a_failure_between_the_two_inserts_leaves_nothing_behind(client, db_session, monkeypatch):
    user = creator(db_session)
    before = snapshot(db_session)

    def boom():
        raise RuntimeError("injected between the organization and its owner membership")

    monkeypatch.setattr(service, "_between_inserts", boom)
    with pytest.raises(RuntimeError):
        client.post(URL, json=body(), headers={**as_user(user), "Idempotency-Key": key()})

    assert snapshot(db_session) == before


def test_a_failure_recording_the_event_leaves_nothing_behind(client, db_session, monkeypatch):
    user = creator(db_session)
    before = snapshot(db_session)
    monkeypatch.setattr(service, "SecurityEvent", lambda **k: (_ for _ in ()).throw(RuntimeError("injected")))

    with pytest.raises(RuntimeError):
        client.post(URL, json=body(), headers={**as_user(user), "Idempotency-Key": key()})

    assert snapshot(db_session) == before


# --- the legacy ownerless organization -------------------------------------------------------------------------------------------


def test_an_existing_organization_without_an_owner_is_left_exactly_as_it_is(client, db_session):
    legacy = make_org(db_session, "Legacy ownerless")
    # A fixed past timestamp: inside this one transaction now() never moves, so an untouched-or-touched check on the
    # default value would see nothing either way.
    db_session.execute(text("update organizations set updated_at = '2020-01-01 00:00:00+00' where id = :o"), {"o": legacy.id})
    member = make_user(db_session, can_create_organizations=True)
    add_member(db_session, legacy, member, Role.EMPLOYEE)
    before = db_session.execute(text("select name, default_currency, updated_at from organizations where id = :o"), {"o": legacy.id}).one()

    created = client.post(URL, json=body(), headers=as_user(member)).json()

    assert db_session.execute(text("select name, default_currency, updated_at from organizations where id = :o"), {"o": legacy.id}).one() == before
    assert db_session.execute(select(OrganizationUser.user_id, OrganizationUser.role).where(OrganizationUser.organization_id == legacy.id)).all() == [(member.id, "employee")]
    assert db_session.scalar(select(OrganizationUser.role).where(OrganizationUser.organization_id == uuid.UUID(created["id"]))) == "owner"


# --- real authentication: sessions and CSRF ----------------------------------------------------------------------------------


def test_session_mode_creates_for_the_session_user_and_requires_the_csrf_token(session_client, db_session):
    user = creator(db_session)
    handle = make_session(db_session, user)
    before = snapshot(db_session)

    assert session_client.post(URL, json=body(), headers=handle.read_headers).status_code == 403  # no CSRF token
    assert session_client.post(URL, json=body(), headers={**handle.read_headers, "X-CSRF-Token": "wrong"}).status_code == 403
    assert snapshot(db_session) == before

    response = session_client.post(URL, json=body("Via session"), headers=handle.headers)
    assert response.status_code == 201
    assert db_session.scalar(select(OrganizationUser.user_id).where(OrganizationUser.organization_id == uuid.UUID(response.json()["id"]))) == user.id


def test_session_mode_ignores_the_development_identity_and_headers_naming_someone_else(session_client, db_session):
    user, other = creator(db_session), creator(db_session)
    handle = make_session(db_session, user)

    assert session_client.post(URL, json=body(), headers={"X-Dev-User-Email": other.email}).status_code == 401
    response = session_client.post(URL, json=body(), headers={**handle.headers, "X-Dev-User-Email": other.email, "X-Owner-Email": other.email})
    assert response.status_code == 201
    assert db_session.scalar(select(OrganizationUser.user_id).where(OrganizationUser.organization_id == uuid.UUID(response.json()["id"]))) == user.id
    assert count(db_session, OrganizationUser, OrganizationUser.user_id == other.id) == 0


def test_session_mode_still_judges_the_flag(session_client, db_session):
    user = make_user(db_session)
    assert session_client.post(URL, json=body(), headers=make_session(db_session, user).headers).status_code == 403


# --- the operator command -------------------------------------------------------------------------------------------------


def test_the_operator_grants_and_revokes_the_account_right_without_touching_memberships(client, db_session):
    user = make_user(db_session)
    org = make_org(db_session, "Held")
    add_member(db_session, org, user, Role.ADMIN)
    assert client.post(URL, json=body(), headers=as_user(user)).status_code == 403

    assert admin.set_org_creation(db_session, email=user.email.upper(), allowed=True) is True
    assert client.post(URL, json=body(), headers=as_user(user)).status_code == 201
    assert admin.set_org_creation(db_session, email=user.email, allowed=True) is False  # already granted
    assert admin.set_org_creation(db_session, email=user.email, allowed=False) is True
    assert client.post(URL, json=body(), headers=as_user(user)).status_code == 403

    roles = db_session.execute(select(OrganizationUser.organization_id, OrganizationUser.role).where(OrganizationUser.user_id == user.id, OrganizationUser.organization_id == org.id)).all()
    assert roles == [(org.id, "admin")]  # the existing role is untouched; the organization it created before the revoke stays

    changes = events(db_session, "capability_changed")
    assert [(e.actor_user_id, e.detail) for e in changes] == [
        (user.id, "org_creation_granted:cli"),
        (user.id, "org_creation_granted:cli:unchanged"),
        (user.id, "org_creation_revoked:cli"),
    ]
    assert all(user.email not in (e.detail or "") for e in changes)


def test_the_operator_command_refuses_an_unknown_user(db_session):
    with pytest.raises(admin.OperatorError):
        admin.set_org_creation(db_session, email="nobody@tests.invalid", allowed=True)


def test_there_is_no_endpoint_that_changes_the_flag_for_a_user(client, db_session):
    user = make_user(db_session)
    for method, path in (("patch", "/api/me/user"), ("put", "/api/me/user"), ("post", "/api/me/user"), ("patch", f"/api/users/{user.id}")):
        response = getattr(client, method)(path, json={"can_create_organizations": True}, headers=as_user(user))
        assert response.status_code in (404, 405), (method, path)
    db_session.refresh(user)
    assert user.can_create_organizations is False


def test_the_development_seed_gives_the_flag_to_the_owner_only(db_session):
    from app.scripts import seed_dev

    seed_dev.seed(db_session)
    flags = dict(db_session.execute(select(User.email, User.can_create_organizations).where(User.email.in_(["fredrik@dev.test", "maria@dev.test"]))).all())
    assert flags == {"fredrik@dev.test": True, "maria@dev.test": False}
