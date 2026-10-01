"""Tenant resolution through GET /api/me.

The central rule: X-Organization-Id only *selects* among organizations the
current user already belongs to. It never grants access.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.models import Role
from tests.factories import add_member, make_org, make_user

DEV_USER = "X-Dev-User-Email"
ORG = "X-Organization-Id"


def me(client: TestClient, email: str | None, org_id=None):
    headers = {}
    if email:
        headers[DEV_USER] = email
    if org_id is not None:
        headers[ORG] = str(org_id)
    return client.get("/api/me", headers=headers)


# --- resolving the organization -------------------------------------------------


def test_single_membership_is_used_without_selector(client: TestClient, db_session: Session):
    org, user = make_org(db_session, "Solo Org"), make_user(db_session)
    add_member(db_session, org, user, Role.ACCOUNTANT)

    response = me(client, user.email)

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == user.email
    assert body["organization"] == {"id": str(org.id), "name": "Solo Org"}
    assert body["role"] == "accountant"


def test_valid_selection_among_several_memberships(client: TestClient, db_session: Session):
    org_a, org_b = make_org(db_session, "A"), make_org(db_session, "B")
    user = make_user(db_session)
    add_member(db_session, org_a, user, Role.OWNER)
    add_member(db_session, org_b, user, Role.VIEWER)

    response = me(client, user.email, org_b.id)

    assert response.status_code == 200
    assert response.json()["organization"]["id"] == str(org_b.id)
    assert response.json()["role"] == "viewer"  # the role in the *selected* org


def test_switching_between_memberships(client: TestClient, db_session: Session):
    org_a, org_b = make_org(db_session, "A"), make_org(db_session, "B")
    user = make_user(db_session)
    add_member(db_session, org_a, user, Role.OWNER)
    add_member(db_session, org_b, user, Role.VIEWER)

    first = me(client, user.email, org_a.id).json()
    second = me(client, user.email, org_b.id).json()
    back = me(client, user.email, org_a.id).json()

    assert (first["organization"]["id"], first["role"]) == (str(org_a.id), "owner")
    assert (second["organization"]["id"], second["role"]) == (str(org_b.id), "viewer")
    assert back == first


def test_several_memberships_without_selector_is_400(client: TestClient, db_session: Session):
    user = make_user(db_session)
    add_member(db_session, make_org(db_session, "A"), user)
    add_member(db_session, make_org(db_session, "B"), user)

    assert me(client, user.email).status_code == 400


def test_malformed_selector_is_400(client: TestClient, db_session: Session):
    user = make_user(db_session)
    add_member(db_session, make_org(db_session), user)

    response = client.get("/api/me", headers={DEV_USER: user.email, ORG: "not-a-uuid"})

    assert response.status_code == 400


def test_user_without_memberships_is_403(client: TestClient, db_session: Session):
    user = make_user(db_session)
    assert me(client, user.email).status_code == 403


# --- the important negative cases: selecting an organization you do not belong to ---


def test_selecting_another_tenants_organization_is_404(client: TestClient, db_session: Session):
    mine, theirs = make_org(db_session, "Mine"), make_org(db_session, "Theirs")
    user, other = make_user(db_session), make_user(db_session)
    add_member(db_session, mine, user)
    add_member(db_session, theirs, other, Role.OWNER)

    response = me(client, user.email, theirs.id)

    assert response.status_code == 404
    assert "Theirs" not in response.text and str(theirs.id) not in response.text


def test_failed_selection_does_not_fall_back_to_own_organization(
    client: TestClient, db_session: Session
):
    # A user with exactly one membership must still be refused when naming a foreign
    # org, not silently handed their own.
    mine, theirs = make_org(db_session, "Mine"), make_org(db_session, "Theirs")
    user = make_user(db_session)
    add_member(db_session, mine, user)

    assert me(client, user.email, theirs.id).status_code == 404


def test_foreign_organization_looks_identical_to_nonexistent_one(
    client: TestClient, db_session: Session
):
    # Must not reveal whether a UUID belongs to another tenant (CLAUDE.md sections 6, 21).
    mine, theirs = make_org(db_session, "Mine"), make_org(db_session, "Theirs")
    user = make_user(db_session)
    add_member(db_session, mine, user)
    add_member(db_session, theirs, make_user(db_session))

    foreign = me(client, user.email, theirs.id)
    nonexistent = me(client, user.email, uuid.uuid4())

    assert foreign.status_code == nonexistent.status_code == 404
    assert foreign.json() == nonexistent.json()


def test_membership_in_one_org_does_not_carry_over_to_another_user(
    client: TestClient, db_session: Session
):
    # Seeded-style layout: shared user in A and B, second user only in B.
    org_a, org_b = make_org(db_session, "A"), make_org(db_session, "B")
    shared, only_b = make_user(db_session), make_user(db_session)
    add_member(db_session, org_a, shared, Role.OWNER)
    add_member(db_session, org_b, shared, Role.ADMIN)
    add_member(db_session, org_b, only_b, Role.EMPLOYEE)

    assert me(client, shared.email, org_a.id).status_code == 200
    assert me(client, only_b.email, org_b.id).status_code == 200
    assert me(client, only_b.email, org_a.id).status_code == 404


# --- identity -------------------------------------------------------------------


def test_no_identity_is_401(client: TestClient):
    assert me(client, None).status_code == 401


def test_unknown_user_is_401(client: TestClient):
    assert me(client, "nobody@tests.invalid").status_code == 401


def test_inactive_user_is_401(client: TestClient, db_session: Session):
    user = make_user(db_session, is_active=False)
    add_member(db_session, make_org(db_session), user)
    assert me(client, user.email).status_code == 401


def test_dev_email_header_is_case_insensitive(client: TestClient, db_session: Session):
    user = make_user(db_session)
    add_member(db_session, make_org(db_session), user)
    assert me(client, user.email.upper()).status_code == 200


def test_dev_user_env_default_is_used_without_header(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    user = make_user(db_session)
    add_member(db_session, make_org(db_session), user)
    monkeypatch.setattr(settings, "dev_user_email", user.email)

    assert me(client, None).status_code == 200


def test_dev_identity_is_ignored_when_auth_mode_is_not_dev(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    user = make_user(db_session)
    add_member(db_session, make_org(db_session), user)
    monkeypatch.setattr(settings, "auth_mode", "disabled")
    monkeypatch.setattr(settings, "dev_user_email", user.email)

    assert me(client, user.email).status_code == 401
    assert me(client, None).status_code == 401


def test_dev_auth_refuses_to_load_outside_development():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="x", auth_mode="dev", app_env="production")
