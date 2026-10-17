"""The journal's free-text search, and the optional link from an entry to the order the visit was billed on."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Role
from tests.factories import add_member, make_customer, make_horse, make_org, make_transaction, make_user


def _person(db: Session, org, role: Role, name: str):
    user = make_user(db, name=name)
    add_member(db, org, user, role)
    return {"X-Dev-User-Email": user.email}


@pytest.fixture
def world(db_session: Session):
    org = make_org(db_session)
    customer = make_customer(db_session, org, "Anna Andersson")
    horse = make_horse(db_session, org, "Kalle", owner=customer)
    owner = _person(db_session, org, Role.OWNER, "Olle Owner")
    employee = _person(db_session, org, Role.EMPLOYEE, "Tina Therapist")
    return org, customer, horse, owner, employee


def _url(horse, entry=None):
    return f"/api/horses/{horse.id}/journal" + (f"/{entry['id']}" if entry else "")


def _create(client, horse, headers, **body):
    response = client.post(_url(horse), json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _numbers(client, horse, headers, **params) -> list[int]:
    return sorted(row["number"] for row in client.get(_url(horse), params=params, headers=headers).json())


# --- search ----------------------------------------------------------------------------------------------------------------------


def test_the_search_finds_text_and_who_did_it_and_the_number(client: TestClient, world):
    _, _, horse, owner, employee = world
    a = _create(client, horse, owner, body="Stiff left shoulder after the competition")
    b = _create(client, horse, employee, body="Back relaxed, 100% better")
    c = _create(client, horse, owner, body="Hind legs checked")
    assert _numbers(client, horse, owner, q="shoulder") == [a["number"]]
    assert _numbers(client, horse, owner, q="SHOULDER") == [a["number"]]  # any case
    assert _numbers(client, horse, owner, q="therapist") == [b["number"]]  # who did it
    assert _numbers(client, horse, owner, q="olle") == [a["number"], c["number"]]
    assert _numbers(client, horse, owner, q=str(c["number"])) == [c["number"]]  # the entry number
    assert _numbers(client, horse, owner, q="100%") == [b["number"]]  # a percent sign is text, not a wildcard
    assert _numbers(client, horse, owner, q="%") == [b["number"]]
    assert _numbers(client, horse, owner, q="nothing like this") == []
    assert _numbers(client, horse, owner) == [a["number"], b["number"], c["number"]]


def test_the_search_stays_inside_the_horse_and_the_organization(client: TestClient, db_session: Session, world):
    org, customer, kalle, owner, _ = world
    storm = make_horse(db_session, org, "Storm", owner=customer)
    other_org = make_org(db_session, "Other")
    their_horse = make_horse(db_session, other_org, "Kalle", owner=make_customer(db_session, other_org, "Anna Andersson"))
    their_owner = _person(db_session, other_org, Role.OWNER, "Their Owner")
    _create(client, storm, owner, body="secret word on another horse")
    _create(client, their_horse, their_owner, body="secret word in another organization")
    assert _numbers(client, kalle, owner, q="secret") == []
    assert [r["preview"] for r in client.get("/api/journal/drafts", params={"q": "secret"}, headers=owner).json()] == ["secret word on another horse"]


# --- the link to an order --------------------------------------------------------------------------------------------------------


def test_an_entry_can_be_linked_to_an_order_of_the_organization_and_unlinked(client: TestClient, db_session: Session, world):
    org, customer, horse, owner, _ = world
    order = make_transaction(db_session, org, billing_customer=customer)
    entry = _create(client, horse, owner, body="Treatment", order_id=str(order.id))
    assert (entry["order_id"], entry["order_number"]) == (str(order.id), order.number)
    assert client.get(_url(horse, entry), headers=owner).json()["order_number"] == order.number

    cleared = client.patch(_url(horse, entry), json={"order_id": None}, headers={**owner, "If-Match": f'"{entry["version"]}"'})
    assert cleared.status_code == 200 and (cleared.json()["order_id"], cleared.json()["order_number"]) == (None, None)
    relinked = client.patch(_url(horse, entry), json={"order_id": str(order.id)}, headers={**owner, "If-Match": f'"{cleared.json()["version"]}"'})
    assert relinked.json()["order_number"] == order.number


def test_a_foreign_or_made_up_order_is_refused_alike(client: TestClient, db_session: Session, world):
    org, customer, horse, owner, _ = world
    other_org = make_org(db_session, "Other")
    foreign = make_transaction(db_session, other_org, billing_customer=make_customer(db_session, other_org, "Their Customer"))
    answers = {client.post(_url(horse), json={"order_id": str(order_id)}, headers=owner).text for order_id in (foreign.id, uuid.uuid4())}
    assert len(answers) == 1 and "Choose an order of this organization" in answers.pop()
    entry = _create(client, horse, owner)
    assert client.patch(_url(horse, entry), json={"order_id": str(foreign.id)}, headers={**owner, "If-Match": f'"{entry["version"]}"'}).status_code == 422
    assert client.get(_url(horse, entry), headers=owner).json()["order_id"] is None


def test_a_deleted_order_leaves_the_entry_intact_without_a_number(client: TestClient, db_session: Session, world):
    org, customer, horse, owner, _ = world
    order = make_transaction(db_session, org, billing_customer=customer)
    entry = _create(client, horse, owner, body="Kept", order_id=str(order.id))
    db_session.execute(text("DELETE FROM transactions WHERE id = :i"), {"i": str(order.id)})
    read = client.get(_url(horse, entry), headers=owner).json()
    assert read["body"] == "Kept" and read["order_number"] is None


def test_the_link_is_frozen_with_a_finalized_entry(client: TestClient, db_session: Session, world):
    org, customer, horse, owner, _ = world
    order = make_transaction(db_session, org, billing_customer=customer)
    entry = _create(client, horse, owner, body="Done", order_id=str(order.id))
    final = client.post(f"{_url(horse, entry)}/finalize", headers={**owner, "If-Match": f'"{entry["version"]}"'}).json()
    refused = client.patch(_url(horse, entry), json={"order_id": None}, headers={**owner, "If-Match": f'"{final["version"]}"'})
    assert refused.status_code == 409 and client.get(_url(horse, entry), headers=owner).json()["order_number"] == order.number


def test_the_journal_text_never_reaches_an_invoice(client: TestClient, db_session: Session, world):
    from tests.invoicing_support import draft_invoice

    org, customer, horse, owner, _ = world
    order = make_transaction(db_session, org, billing_customer=customer, status="completed")
    _create(client, horse, owner, body="A very private clinical observation", order_id=str(order.id))
    invoice = draft_invoice(client, owner, order)
    assert "very private clinical observation" not in str(invoice)
