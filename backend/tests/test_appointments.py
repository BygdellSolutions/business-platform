"""Scheduling: appointments. Tenant isolation first, then who may do what, validation, concurrency and what protects a booked record."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Role
from tests.factories import add_member, make_customer, make_horse, make_org, make_user

MORNING = {"starts_at": "2026-10-19T09:00:00+02:00", "ends_at": "2026-10-19T10:00:00+02:00"}
WINDOW = {"from": "2026-10-19T00:00:00+02:00", "to": "2026-10-26T00:00:00+02:00"}


def _person(db: Session, org, role: Role, name: str):
    user = make_user(db, name=name)
    add_member(db, org, user, role)
    return user, {"X-Dev-User-Email": user.email}


@pytest.fixture
def world(db_session: Session):
    org = make_org(db_session)
    anna = make_customer(db_session, org, "Anna Andersson")
    kalle = make_horse(db_session, org, "Kalle", owner=anna)
    people = {name: _person(db_session, org, role, name.title()) for name, role in (("owner", Role.OWNER), ("employee", Role.EMPLOYEE), ("accountant", Role.ACCOUNTANT), ("viewer", Role.VIEWER))}
    return org, anna, kalle, people


def _book(client: TestClient, headers, **body) -> dict:
    response = client.post("/api/appointments", json={"title": "Massage", **MORNING, **body}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _listed(client: TestClient, headers, **params) -> list[dict]:
    response = client.get("/api/appointments", params={**WINDOW, **params}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _etag(appointment: dict) -> dict:
    return {"If-Match": f'"{appointment["version"]}"'}


def test_book_read_change_and_remove(client: TestClient, world):
    _, anna, kalle, people = world
    employee, headers = people["employee"]
    booked = _book(client, headers, practitioner_user_id=str(employee.id), customer_id=str(anna.id), subject_type="horse", subject_id=str(kalle.id), location="Stable 3")
    assert (booked["number"], booked["status"], booked["version"]) == (1, "scheduled", 1)
    assert (booked["practitioner_name"], booked["customer_name"], booked["subject_label"]) == ("Employee", "Anna Andersson", "Kalle")
    assert _book(client, headers)["number"] == 2

    assert client.get(f"/api/appointments/{booked['id']}", headers=headers).json()["location"] == "Stable 3"
    moved = client.patch(f"/api/appointments/{booked['id']}", json={"starts_at": "2026-10-19T11:00:00+02:00", "ends_at": "2026-10-19T12:00:00+02:00", "location": None}, headers={**headers, **_etag(booked)})
    assert moved.status_code == 200, moved.text
    assert (moved.json()["version"], moved.json()["location"]) == (2, None)

    assert client.delete(f"/api/appointments/{booked['id']}", headers={**headers, **_etag(moved.json())}).status_code == 204
    assert client.get(f"/api/appointments/{booked['id']}", headers=headers).status_code == 404


def test_the_list_shows_what_overlaps_the_window_and_hides_cancelled(client: TestClient, world):
    _, _, _, people = world
    _, headers = people["owner"]
    inside = _book(client, headers, title="Inside")
    _book(client, headers, title="Before", starts_at="2026-10-18T09:00:00+02:00", ends_at="2026-10-18T10:00:00+02:00")
    _book(client, headers, title="After", starts_at="2026-10-26T00:00:00+02:00", ends_at="2026-10-26T01:00:00+02:00")  # starts exactly when the window ends
    _book(client, headers, title="Overlaps the start", starts_at="2026-10-18T23:00:00+02:00", ends_at="2026-10-19T01:00:00+02:00")
    cancelled = _book(client, headers, title="Cancelled")
    client.patch(f"/api/appointments/{cancelled['id']}", json={"status": "cancelled"}, headers={**headers, **_etag(cancelled)})

    assert [a["title"] for a in _listed(client, headers)] == ["Overlaps the start", "Inside"]
    assert {a["title"] for a in _listed(client, headers, include_cancelled=True)} == {"Overlaps the start", "Inside", "Cancelled"}
    assert inside["id"] in {a["id"] for a in _listed(client, headers)}


def test_the_list_can_be_narrowed_to_one_practitioner_or_customer(client: TestClient, world):
    _, anna, _, people = world
    employee, employee_headers = people["employee"]
    owner, owner_headers = people["owner"]
    _book(client, owner_headers, title="Owner's", practitioner_user_id=str(owner.id))
    _book(client, owner_headers, title="Employee's", practitioner_user_id=str(employee.id), customer_id=str(anna.id))
    _book(client, owner_headers, title="Nobody's")
    assert [a["title"] for a in _listed(client, owner_headers, practitioner_user_id=str(employee.id))] == ["Employee's"]
    assert [a["title"] for a in _listed(client, owner_headers, customer_id=str(anna.id))] == ["Employee's"]
    assert len(_listed(client, owner_headers)) == 3


# --- tenant isolation: Organization A and Organization B both have an "Anna" and a "Kalle" ---------------------------------------------


@pytest.fixture
def two_organizations(client: TestClient, db_session: Session, world):
    org, anna, kalle, people = world
    other = make_org(db_session, "Other")
    their_anna = make_customer(db_session, other, "Anna Andersson")
    their_kalle = make_horse(db_session, other, "Kalle", owner=their_anna)
    their_owner, their_headers = _person(db_session, other, Role.OWNER, "Their Owner")
    theirs = _book(client, their_headers, title="Theirs", practitioner_user_id=str(their_owner.id), customer_id=str(their_anna.id), subject_type="horse", subject_id=str(their_kalle.id))
    return people, theirs, their_anna, their_kalle, their_owner, their_headers


def test_another_organizations_appointments_are_invisible(client: TestClient, two_organizations):
    people, theirs, *_ = two_organizations
    _, headers = people["owner"]
    assert _listed(client, headers) == []
    assert client.get(f"/api/appointments/{theirs['id']}", headers=headers).status_code == 404
    assert client.patch(f"/api/appointments/{theirs['id']}", json={"title": "Mine now"}, headers={**headers, **_etag(theirs)}).status_code == 404
    assert client.delete(f"/api/appointments/{theirs['id']}", headers={**headers, **_etag(theirs)}).status_code == 404
    assert client.get(f"/api/appointments/{theirs['id']}", headers=two_organizations[5]).json()["title"] == "Theirs"  # untouched


def test_an_appointment_cannot_point_at_another_organizations_records(client: TestClient, two_organizations):
    people, _, their_anna, their_kalle, their_owner, _ = two_organizations
    _, headers = people["owner"]
    for body in (
        {"customer_id": str(their_anna.id)},
        {"practitioner_user_id": str(their_owner.id)},
        {"subject_type": "horse", "subject_id": str(their_kalle.id)},
    ):
        response = client.post("/api/appointments", json={"title": "x", **MORNING, **body}, headers=headers)
        assert response.status_code == 422, (body, response.text)
        assert _listed(client, headers) == []  # nothing was saved
    mine = _book(client, headers)
    for body in ({"customer_id": str(their_anna.id)}, {"practitioner_user_id": str(their_owner.id)}, {"subject_type": "horse", "subject_id": str(their_kalle.id)}):
        assert client.patch(f"/api/appointments/{mine['id']}", json=body, headers={**headers, **_etag(mine)}).status_code == 422


# --- who may do what -----------------------------------------------------------------------------------------------------------------


def test_everyone_may_look_but_a_viewer_may_not_book(client: TestClient, world):
    _, _, _, people = world
    booked = _book(client, people["owner"][1])
    for name in ("owner", "employee", "accountant", "viewer"):
        assert [a["id"] for a in _listed(client, people[name][1])] == [booked["id"]]
        assert client.get(f"/api/appointments/{booked['id']}", headers=people[name][1]).status_code == 200
    viewer = people["viewer"][1]
    assert client.post("/api/appointments", json={"title": "x", **MORNING}, headers=viewer).status_code == 403
    assert client.patch(f"/api/appointments/{booked['id']}", json={"title": "x"}, headers={**viewer, **_etag(booked)}).status_code == 403
    assert client.delete(f"/api/appointments/{booked['id']}", headers={**viewer, **_etag(booked)}).status_code == 403


@pytest.mark.parametrize("role", ["accountant", "viewer"])
def test_only_those_who_do_the_work_can_be_the_practitioner(client: TestClient, world, role):
    _, _, _, people = world
    response = client.post("/api/appointments", json={"title": "x", **MORNING, "practitioner_user_id": str(people[role][0].id)}, headers=people["owner"][1])
    assert response.status_code == 422


# --- validation -----------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"starts_at": "2026-10-19T09:00:00", "ends_at": "2026-10-19T10:00:00"},  # no time zone: which moment would that be?
        {"starts_at": "2026-10-19T10:00:00+02:00", "ends_at": "2026-10-19T09:00:00+02:00"},
        {"starts_at": "2026-10-19T09:00:00+02:00", "ends_at": "2026-10-19T09:00:00+02:00"},
        {"starts_at": "2026-10-19T09:00:00+02:00", "ends_at": "2026-10-27T09:00:00+02:00"},  # longer than a week
        {"title": "   "},
        {"subject_type": "horse"},  # a subject is a type and an id together
        {"organization_id": "00000000-0000-0000-0000-000000000000"},
    ],
)
def test_bad_bookings_are_refused(client: TestClient, world, body):
    assert client.post("/api/appointments", json={"title": "x", **MORNING, **body}, headers=world[3]["owner"][1]).status_code == 422


@pytest.mark.parametrize("params", [{"from": WINDOW["from"]}, {"from": "2026-10-19T00:00:00", "to": "2026-10-20T00:00:00"}, {"from": WINDOW["to"], "to": WINDOW["from"]}, {"from": "2026-01-01T00:00:00+00:00", "to": "2026-12-31T00:00:00+00:00"}])
def test_the_list_needs_a_sensible_window(client: TestClient, world, params):
    assert client.get("/api/appointments", params=params, headers=world[3]["owner"][1]).status_code == 422


def test_changing_the_end_before_the_start_is_refused_and_leaves_the_booking(client: TestClient, world):
    headers = world[3]["owner"][1]
    booked = _book(client, headers)
    response = client.patch(f"/api/appointments/{booked['id']}", json={"ends_at": "2026-10-19T08:00:00+02:00"}, headers={**headers, **_etag(booked)})
    assert response.status_code == 422
    assert client.get(f"/api/appointments/{booked['id']}", headers=headers).json()["ends_at"] == booked["ends_at"]


# --- concurrency ----------------------------------------------------------------------------------------------------------------------


def test_a_change_needs_the_version_the_caller_saw(client: TestClient, world):
    headers = world[3]["owner"][1]
    booked = _book(client, headers)
    assert client.patch(f"/api/appointments/{booked['id']}", json={"title": "x"}, headers=headers).status_code == 428
    assert client.patch(f"/api/appointments/{booked['id']}", json={"title": "x"}, headers={**headers, **_etag(booked)}).status_code == 200
    stale = client.patch(f"/api/appointments/{booked['id']}", json={"title": "y"}, headers={**headers, **_etag(booked)})  # the old version again
    assert (stale.status_code, stale.json()["detail"]["code"]) == (409, "stale_record")
    assert client.delete(f"/api/appointments/{booked['id']}", headers={**headers, **_etag(booked)}).status_code == 409
    assert client.get(f"/api/appointments/{booked['id']}", headers=headers).json()["title"] == "x"


def test_a_save_that_changes_nothing_moves_no_version(client: TestClient, world):
    headers = world[3]["owner"][1]
    booked = _book(client, headers)
    same = client.patch(f"/api/appointments/{booked['id']}", json={"title": booked["title"]}, headers={**headers, **_etag(booked)})
    assert same.json()["version"] == booked["version"]


# --- what a booking protects ----------------------------------------------------------------------------------------------------------


def test_a_customer_or_horse_with_a_booking_cannot_be_deleted(client: TestClient, db_session: Session, world):
    from app.core.entity_registry import registry

    _, anna, kalle, people = world
    headers = people["owner"][1]
    booked = _book(client, headers, customer_id=str(anna.id), subject_type="horse", subject_id=str(kalle.id))
    assert client.delete(f"/api/customers/{anna.id}", headers=headers).status_code == 409
    assert registry.is_referenced(db_session, kalle)  # horses are never deleted through the API; the guard is what any deletion path asks
    client.delete(f"/api/appointments/{booked['id']}", headers={**headers, **_etag(booked)})
    assert not registry.is_referenced(db_session, kalle)
    assert client.delete(f"/api/customers/{anna.id}", headers=headers).status_code in (204, 409)  # the horse's owner link still holds it


def test_the_history_records_booking_changes_and_removal(client: TestClient, db_session: Session, world):
    from sqlalchemy import select

    from app.models import AuditEvent

    headers = world[3]["owner"][1]
    booked = _book(client, headers)
    changed = client.patch(f"/api/appointments/{booked['id']}", json={"title": "Moved"}, headers={**headers, **_etag(booked)}).json()
    client.delete(f"/api/appointments/{booked['id']}", headers={**headers, **_etag(changed)})
    actions = [e.action for e in db_session.scalars(select(AuditEvent).where(AuditEvent.entity_type == "appointment").order_by(AuditEvent.id))]
    assert actions == ["created", "updated", "deleted"]
