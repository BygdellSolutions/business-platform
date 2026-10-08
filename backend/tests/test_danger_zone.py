"""Leaving, transferring ownership and deleting an organization: recent authentication and the owner rules.

Session mode is the real mechanism (the password IS the proof today); the development identity has no passwords
and is exempt, which one test pins down. Deleting an organization removes everything it owns, issued invoices and
their frozen PDFs included, and nothing of any other organization.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.organization_deletion import KEPT, TABLES_IN_DELETE_ORDER
from app.models import Organization, OrganizationUser, Role, SecurityEvent, User
from tests.auth_support import OTHER_PASSWORD, PASSWORD, make_credential, make_session
from tests.factories import add_member, make_customer, make_definition, make_horse, make_item, make_org, make_transaction, make_user, make_value

LEAVE = "/api/members/leave"
TRANSFER = "/api/organization/transfer-ownership"
DELETE = "/api/organization/delete"


def _person(db: Session, org: Organization, role: Role, name: str) -> SimpleNamespace:
    user = make_user(db, name=name)
    make_credential(db, user)
    membership = add_member(db, org, user, role)
    return SimpleNamespace(user=user, membership=membership, h={**make_session(db, user).headers, "X-Organization-Id": str(org.id)})


@pytest.fixture
def team(session_client, db_session: Session) -> SimpleNamespace:
    org = make_org(db_session, "Umeå Häst & Rehab")
    return SimpleNamespace(
        client=session_client,
        org=org,
        owner=_person(db_session, org, Role.OWNER, "Olle Owner"),
        admin=_person(db_session, org, Role.ADMIN, "Ada Admin"),
        employee=_person(db_session, org, Role.EMPLOYEE, "Erik Employee"),
    )


def _role(db: Session, membership: OrganizationUser) -> str | None:
    db.expire_all()
    return db.scalar(select(OrganizationUser.role).where(OrganizationUser.id == membership.id))


def _code(response) -> str:
    return response.json()["detail"]["code"]


# --- recent authentication ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("password", [None, "", "wrong password entirely"])
def test_leaving_without_the_right_password_is_refused_and_recorded(team, db_session: Session, password):
    body = {} if password is None else {"password": password}

    response = team.client.post(LEAVE, json=body, headers=team.employee.h)

    assert response.status_code == 403 and _code(response) == "reauthentication_failed"
    assert _role(db_session, team.employee.membership) == "employee"
    assert db_session.scalar(select(func.count()).select_from(SecurityEvent).where(SecurityEvent.event_type == "reauth_failure", SecurityEvent.actor_user_id == team.employee.user.id)) == 1


def test_leaving_with_the_password_works(team, db_session: Session):
    assert team.client.post(LEAVE, json={"password": PASSWORD}, headers=team.employee.h).status_code == 204
    assert _role(db_session, team.employee.membership) is None
    assert db_session.get(User, team.employee.user.id) is not None  # the account stays


def test_too_many_wrong_passwords_are_throttled(team):
    for _ in range(5):
        team.client.post(LEAVE, json={"password": OTHER_PASSWORD}, headers=team.employee.h)
    response = team.client.post(LEAVE, json={"password": PASSWORD}, headers=team.employee.h)
    assert response.status_code == 429


def test_the_development_identity_has_no_passwords_and_is_exempt(client: TestClient, db_session: Session):
    org = make_org(db_session)
    user = make_user(db_session)
    add_member(db_session, org, user, Role.EMPLOYEE)
    response = client.post(LEAVE, headers={"X-Dev-User-Email": user.email, "X-Organization-Id": str(org.id)})
    assert response.status_code == 204


# --- the sole owner and transfer -----------------------------------------------------------------------------------


def test_the_sole_owner_cannot_leave(team, db_session: Session):
    response = team.client.post(LEAVE, json={"password": PASSWORD}, headers=team.owner.h)
    assert response.status_code == 409 and _code(response) == "last_owner"
    assert _role(db_session, team.owner.membership) == "owner"


def test_transfer_makes_the_target_owner_and_the_previous_owner_admin_who_may_then_leave(team, db_session: Session):
    response = team.client.post(TRANSFER, json={"membership_id": str(team.employee.membership.id), "password": PASSWORD}, headers=team.owner.h)

    assert response.status_code == 204
    assert _role(db_session, team.employee.membership) == "owner"
    assert _role(db_session, team.owner.membership) == "admin"
    assert team.client.post(LEAVE, json={"password": PASSWORD}, headers=team.owner.h).status_code == 204


@pytest.mark.parametrize("who", ["admin", "employee"])
def test_only_an_owner_can_transfer(team, db_session: Session, who):
    actor = getattr(team, who)
    response = team.client.post(TRANSFER, json={"membership_id": str(actor.membership.id), "password": PASSWORD}, headers=actor.h)
    assert response.status_code == 403 and _code(response) == "not_owner"
    assert _role(db_session, team.owner.membership) == "owner"


def test_transfer_needs_the_password_and_a_real_member_of_this_organization(team, db_session: Session):
    other_org = make_org(db_session, "Elsewhere")
    stranger = add_member(db_session, other_org, make_user(db_session), Role.EMPLOYEE)

    wrong = team.client.post(TRANSFER, json={"membership_id": str(team.employee.membership.id), "password": OTHER_PASSWORD}, headers=team.owner.h)
    foreign = team.client.post(TRANSFER, json={"membership_id": str(stranger.id), "password": PASSWORD}, headers=team.owner.h)
    yourself = team.client.post(TRANSFER, json={"membership_id": str(team.owner.membership.id), "password": PASSWORD}, headers=team.owner.h)

    assert wrong.status_code == 403 and _code(wrong) == "reauthentication_failed"
    assert foreign.status_code == 404  # another organization's membership looks like no membership at all
    assert yourself.status_code == 403 and _code(yourself) == "self_transfer"
    assert _role(db_session, team.owner.membership) == "owner" and _role(db_session, team.employee.membership) == "employee"


# --- deleting the organization ---------------------------------------------------------------------------------------


def _fill(db: Session, org: Organization) -> None:
    """Something of every kind, including an issued invoice with a frozen PDF and history."""
    customer = make_customer(db, org)
    make_item(db, org)
    make_horse(db, org, owner=customer)
    parent = make_definition(db, org, entity_type="transaction", key="owner_ref", field_type="reference", reference_source="customer")
    make_definition(db, org, entity_type="transaction", key="memo")
    tx = make_transaction(db, org, billing_customer=customer, status="completed")
    make_value(db, org, parent, tx.id, value_reference_id=customer.id)
    db.execute(text("INSERT INTO audit_events (organization_id, entity_type, entity_id, action) VALUES (:o, 'customer', :c, 'created')"), {"o": org.id, "c": customer.id})


def _issued_invoice_with_pdf(client: TestClient, db: Session, org: Organization, headers) -> str:
    tx = make_transaction(db, org, status="completed")
    invoice = client.post("/api/invoices", json={"transaction_ids": [str(tx.id)]}, headers=headers).json()
    assert client.post(f"/api/invoices/{invoice['id']}/issue", headers={**headers, "If-Match": f'"{invoice["version"]}"'}).status_code == 200
    assert client.get(f"/api/invoices/{invoice['id']}/pdf", headers=headers).status_code == 200
    return invoice["id"]


def _rows_of(db: Session, org_id) -> dict[str, int]:
    return {
        table: db.scalar(text(f"SELECT count(*) FROM {table} WHERE organization_id = :o"), {"o": org_id})
        for table in TABLES_IN_DELETE_ORDER
    }


def test_an_owner_deletes_the_organization_and_everything_it_owns_and_nothing_else(team, db_session: Session):
    other = make_org(db_session, "Umeå Häst & Rehab")  # the same name in another organization must not matter
    _fill(db_session, team.org)
    _fill(db_session, other)
    _issued_invoice_with_pdf(team.client, db_session, team.org, team.owner.h)
    other_owner = _person(db_session, other, Role.OWNER, "Other Owner")
    _issued_invoice_with_pdf(team.client, db_session, other, other_owner.h)
    other_before = _rows_of(db_session, other.id)
    assert _rows_of(db_session, team.org.id)["invoice_pdfs"] == 1  # control: there is something to delete

    response = team.client.post(DELETE, json={"confirm_name": "Umeå Häst & Rehab", "password": PASSWORD}, headers=team.owner.h)

    assert response.status_code == 204, response.text
    db_session.expire_all()
    assert db_session.get(Organization, team.org.id) is None
    assert all(count == 0 for count in _rows_of(db_session, team.org.id).values()), _rows_of(db_session, team.org.id)
    assert _rows_of(db_session, other.id) == other_before
    assert db_session.get(User, team.owner.user.id) is not None  # accounts stay
    event = db_session.scalar(select(SecurityEvent).where(SecurityEvent.event_type == "organization_deleted"))
    assert event.organization_id == team.org.id and event.actor_user_id == team.owner.user.id


@pytest.mark.parametrize("name", ["umeå häst & rehab", "Umeå Häst", "", "Something else"])
def test_the_name_must_be_typed_exactly(team, db_session: Session, name):
    response = team.client.post(DELETE, json={"confirm_name": name, "password": PASSWORD}, headers=team.owner.h)
    assert response.status_code == 422
    assert db_session.get(Organization, team.org.id) is not None


def test_deletion_needs_the_password(team, db_session: Session):
    response = team.client.post(DELETE, json={"confirm_name": "Umeå Häst & Rehab", "password": OTHER_PASSWORD}, headers=team.owner.h)
    assert response.status_code == 403 and _code(response) == "reauthentication_failed"
    assert db_session.get(Organization, team.org.id) is not None


@pytest.mark.parametrize("who", ["admin", "employee"])
def test_only_an_owner_can_delete(team, db_session: Session, who):
    response = team.client.post(DELETE, json={"confirm_name": "Umeå Häst & Rehab", "password": PASSWORD}, headers=getattr(team, who).h)
    assert response.status_code == 403 and _code(response) == "not_owner"
    assert db_session.get(Organization, team.org.id) is not None


def test_the_protections_still_hold_outside_a_deletion(team, db_session: Session):
    invoice_id = _issued_invoice_with_pdf(team.client, db_session, team.org, team.owner.h)
    for statement in ("DELETE FROM invoice_pdfs WHERE invoice_id = :i", "DELETE FROM invoice_lines WHERE invoice_id = :i", "DELETE FROM invoices WHERE id = :i"):
        with pytest.raises(Exception, match="cannot be"):
            with db_session.begin_nested():
                db_session.execute(text(statement), {"i": invoice_id})


def test_the_escape_opens_only_the_organization_being_deleted(team, db_session: Session):
    invoice_id = _issued_invoice_with_pdf(team.client, db_session, team.org, team.owner.h)
    other = make_org(db_session, "Other")
    with pytest.raises(Exception, match="cannot be"):
        with db_session.begin_nested():
            db_session.execute(text("SELECT set_config('app.deleting_organization', :o, true)"), {"o": str(other.id)})
            db_session.execute(text("DELETE FROM invoice_pdfs WHERE invoice_id = :i"), {"i": invoice_id})


def test_every_tenant_table_is_either_deleted_or_deliberately_kept(db_session: Session):
    with_org = set(
        db_session.scalars(
            text("SELECT table_name FROM information_schema.columns WHERE column_name = 'organization_id' AND table_schema = 'public'")
        )
    )
    assert with_org == set(TABLES_IN_DELETE_ORDER) | set(KEPT)
