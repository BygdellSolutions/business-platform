"""Service lines: a catalog service performed for a subject (a person, a horse...), by a member, at a time.

Sales stores the subject generically (registry key and id) and never knows what it is; the registry resolves and
labels it. A service line is priced like any catalog line (discounts included), lists under its subject, protects
its subject from deletion, and an invoice keeps a snapshot of what, when, by whom and for whom.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ItemType, Role
from tests.factories import add_member, make_customer, make_horse, make_item, make_org, make_transaction, make_user


@pytest.fixture
def world(db_session: Session):
    org = make_org(db_session, "Umeå Häst & Rehab", timezone="Europe/Stockholm")
    owner = make_user(db_session, name="Olle Owner")
    therapist = make_user(db_session, name="Tina Therapist")
    add_member(db_session, org, owner, Role.OWNER)
    add_member(db_session, org, therapist, Role.EMPLOYEE)
    anna = make_customer(db_session, org, "Anna Andersson", default_discount_percent=Decimal("10"))
    horse = make_horse(db_session, org, "Kalle", owner=anna)
    massage = make_item(db_session, org, "Massage", type=ItemType.SERVICE, price_ex_vat="850.00")
    liniment = make_item(db_session, org, "Liniment", type=ItemType.PRODUCT, price_ex_vat="120.00")
    tx = make_transaction(db_session, org, billing_customer=anna, lines=[])

    class W:
        pass

    w = W()
    w.org, w.owner, w.therapist, w.anna, w.horse, w.massage, w.liniment, w.tx = org, owner, therapist, anna, horse, massage, liniment, tx
    w.h = {"X-Dev-User-Email": owner.email}
    return w


def _service(client: TestClient, w, **overrides):
    body = {
        "kind": "service", "item_id": str(w.massage.id), "quantity": "1", "subject_type": "horse", "subject_id": str(w.horse.id),
        "performed_by_user_id": str(w.therapist.id), "performed_at": "2026-10-03T14:00", "notes": "Stiff left shoulder", **overrides,
    }
    return client.post(f"/api/transactions/{w.tx.id}/lines", json=body, headers=w.h)


def test_a_service_line_records_what_when_by_whom_and_for_whom(client: TestClient, world):
    response = _service(client, world)

    assert response.status_code == 201, response.text
    line = response.json()
    assert (line["kind"], line["subject_type"], line["subject_label"], line["performed_by_name"]) == ("service", "horse", "Kalle", "Tina Therapist")
    assert line["performed_at"] == "2026-10-03T12:00:00Z"  # 14:00 in Stockholm (CEST) is 12:00 UTC
    assert line["notes"] == "Stiff left shoulder"
    assert (line["unit_price_ex_vat"], line["customer_discount_percent"]) == ("765.00", "10.00")  # priced like any catalog line


def test_a_person_can_be_the_subject_too(client: TestClient, world):
    line = _service(client, world, subject_type="customer", subject_id=str(world.anna.id)).json()
    assert (line["subject_type"], line["subject_label"]) == ("customer", "Anna Andersson")


def test_performed_at_defaults_to_now(client: TestClient, world, monkeypatch):
    from app.core import clock

    monkeypatch.setattr(clock, "utcnow", lambda: datetime(2026, 10, 8, 9, 30, tzinfo=timezone.utc))
    line = _service(client, world, performed_at=None).json()
    assert line["performed_at"] == "2026-10-08T09:30:00Z"


@pytest.mark.parametrize(
    "overrides,field,error_type",
    [
        ({"item_id": "LINIMENT"}, "item_id", "service.not_a_service"),
        ({"subject_type": "transaction"}, "subject_id", "reference.not_found"),  # not a service subject
        ({"subject_type": "spaceship"}, "subject_id", "reference.not_found"),
        ({"subject_id": "00000000-0000-4000-8000-000000000000"}, "subject_id", "reference.not_found"),
        ({"performed_by_user_id": "STRANGER"}, "performed_by_user_id", "reference.not_found"),
    ],
)
def test_a_service_needs_a_catalog_service_a_real_subject_and_a_member(client: TestClient, db_session: Session, world, overrides, field, error_type):
    stranger = make_user(db_session)
    replace = {"LINIMENT": str(world.liniment.id), "STRANGER": str(stranger.id)}
    response = _service(client, world, **{k: replace.get(v, v) for k, v in overrides.items()})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == field and response.json()["detail"][0]["type"] == error_type


def test_another_organizations_horse_is_not_found_here(client: TestClient, db_session: Session, world):
    other = make_org(db_session, "Other")
    foreign = make_horse(db_session, other, "Kalle")
    response = _service(client, world, subject_id=str(foreign.id))
    assert response.status_code == 422 and response.json()["detail"][0]["type"] == "reference.not_found"


def test_an_inactive_subject_cannot_be_chosen(client: TestClient, db_session: Session, world):
    world.horse.active = False
    db_session.flush()
    assert _service(client, world).json()["detail"][0]["type"] == "reference.inactive"


def test_only_a_service_line_has_service_details(client: TestClient, world):
    body = {"item_id": str(world.liniment.id), "quantity": "1", "subject_type": "horse", "subject_id": str(world.horse.id)}
    assert client.post(f"/api/transactions/{world.tx.id}/lines", json=body, headers=world.h).status_code == 422
    missing = {"kind": "service", "item_id": str(world.massage.id), "quantity": "1"}
    assert client.post(f"/api/transactions/{world.tx.id}/lines", json=missing, headers=world.h).status_code == 422


def test_service_details_can_be_edited_and_a_service_keeps_its_catalog_service(client: TestClient, world):
    line = _service(client, world).json()
    url, h = f"/api/transactions/{world.tx.id}/lines/{line['id']}", {**world.h, "If-Match": f'"{line["version"]}"'}

    edited = client.patch(url, json={"notes": "Better", "performed_at": "2026-10-03T15:30", "subject_type": "customer", "subject_id": str(world.anna.id)}, headers=h)
    assert edited.status_code == 200, edited.text
    assert (edited.json()["notes"], edited.json()["performed_at"], edited.json()["subject_label"]) == ("Better", "2026-10-03T13:30:00Z", "Anna Andersson")

    h2 = {**world.h, "If-Match": f'"{edited.json()["version"]}"'}
    assert client.patch(url, json={"item_id": None}, headers=h2).json()["detail"][0]["type"] == "line.kind_change"
    assert client.patch(url, json={"item_id": str(world.liniment.id)}, headers=h2).json()["detail"][0]["type"] == "service.not_a_service"


def test_services_list_under_their_subject_and_their_billing_customer(client: TestClient, db_session: Session, world):
    _service(client, world)
    _service(client, world, subject_type="customer", subject_id=str(world.anna.id), performed_at="2026-10-04T09:00")

    for_horse = client.get("/api/transactions/services", params={"subject_type": "horse", "subject_id": str(world.horse.id)}, headers=world.h).json()
    billed = client.get("/api/transactions/services", params={"billing_customer_id": str(world.anna.id)}, headers=world.h).json()

    assert [s["subject_label"] for s in for_horse] == ["Kalle"]
    assert for_horse[0]["performed_by_name"] == "Tina Therapist" and for_horse[0]["description"] == "Massage"
    assert [s["performed_at"] for s in billed] == ["2026-10-04T07:00:00Z", "2026-10-03T12:00:00Z"]  # newest first

    other = make_org(db_session, "Other")
    viewer = make_user(db_session)
    add_member(db_session, other, viewer, Role.VIEWER)
    assert client.get("/api/transactions/services", params={"subject_type": "horse", "subject_id": str(world.horse.id)}, headers={"X-Dev-User-Email": viewer.email}).json() == []


def test_a_subject_of_a_service_cannot_be_deleted(client: TestClient, world):
    _service(client, world)
    assert client.delete(f"/api/horses/{world.horse.id}", headers=world.h).status_code == 409


def test_an_invoice_keeps_a_snapshot_of_the_service(client: TestClient, db_session: Session, world):
    _service(client, world)
    current = client.get(f"/api/transactions/{world.tx.id}", headers=world.h).json()
    client.post(f"/api/transactions/{world.tx.id}/complete", headers={**world.h, "If-Match": f'"{current["version"]}"'})

    invoice = client.post("/api/invoices", json={"transaction_ids": [str(world.tx.id)]}, headers=world.h).json()

    service = invoice["lines"][0]["service"]
    assert {k: service[k] for k in ("performed_at", "performed_at_local", "performed_by", "subject_type", "subject_label", "notes")} == {
        "performed_at": "2026-10-03T12:00:00+00:00", "performed_at_local": "2026-10-03 14:00", "performed_by": "Tina Therapist", "subject_type": "horse", "subject_label": "Kalle", "notes": "Stiff left shoulder",
    }
    world.horse.name = "Renamed later"
    db_session.flush()
    again = client.get(f"/api/invoices/{invoice['id']}", headers=world.h).json()
    assert again["lines"][0]["service"]["subject_label"] == "Kalle"  # a snapshot, never resolved again


def test_every_member_can_read_the_names_of_colleagues(client: TestClient, db_session: Session, world):
    viewer = make_user(db_session, name="Vera Viewer")
    add_member(db_session, world.org, viewer, Role.VIEWER)
    names = [p["name"] for p in client.get("/api/members/people", headers={"X-Dev-User-Email": viewer.email}).json()]
    assert names == ["Olle Owner", "Tina Therapist", "Vera Viewer"]


def test_the_database_refuses_a_service_without_its_details(db_session: Session, world):
    tx = make_transaction(db_session, world.org)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.execute(text("UPDATE transaction_lines SET kind = 'service' WHERE transaction_id = :t"), {"t": tx.id})
