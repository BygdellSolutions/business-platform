"""Tenant isolation for the NESTED resource (transaction lines) and for references.

The flat Transactions resource runs through the shared contract. Lines live under
/api/transactions/{id}/lines/{line_id}, so the 404 matrix has two ids to get wrong.

Layout mirrors the dev seed: both organizations have an identical-looking "Horse massage"
item, an identical-looking billing customer and an identical-looking transaction.
"""

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Customer, Item, Organization, Role, User
from app.modules.sales.models import Transaction, TransactionLine
from tests.factories import add_member, make_customer, make_item, make_org, make_transaction, make_user

LINE_BODY = {"description": "Travel", "unit": "km", "quantity": "1", "unit_price_ex_vat": "2.50", "vat_rate": "25.00"}


@dataclass
class World:
    org_a: Organization
    org_b: Organization
    a_only: User
    b_only: User
    shared: User
    item_a: Item
    item_b: Item
    customer_a: Customer
    customer_b: Customer
    tx_a: Transaction
    tx_b: Transaction
    tx_a2: Transaction  # a second transaction in A, for mismatched (same org) pairs


@pytest.fixture
def world(db_session: Session) -> World:
    org_a, org_b = make_org(db_session, "A"), make_org(db_session, "B")
    a_only, b_only, shared = (make_user(db_session) for _ in range(3))
    add_member(db_session, org_a, a_only, Role.EMPLOYEE)
    add_member(db_session, org_b, b_only, Role.EMPLOYEE)
    add_member(db_session, org_a, shared, Role.OWNER)
    add_member(db_session, org_b, shared, Role.ADMIN)
    item_a = make_item(db_session, org_a)
    item_b = make_item(db_session, org_b)
    tx_a = make_transaction(db_session, org_a, lines=[{"item": item_a}])
    tx_b = make_transaction(db_session, org_b, lines=[{"item": item_b}])
    tx_a2 = make_transaction(db_session, org_a, lines=[{"item": item_a, "description": "Second"}])
    return World(
        org_a, org_b, a_only, b_only, shared, item_a, item_b,
        make_customer(db_session, org_a, "Anna Andersson"), make_customer(db_session, org_b, "Anna Andersson"),
        tx_a, tx_b, tx_a2,
    )


def h(user: User, org: Organization | None = None) -> dict[str, str]:
    headers = {"X-Dev-User-Email": user.email}
    if org is not None:
        headers["X-Organization-Id"] = str(org.id)
    return headers


def lines_of(db: Session, tx: Transaction) -> list[TransactionLine]:
    db.expire_all()
    return list(db.scalars(select(TransactionLine).where(TransactionLine.transaction_id == tx.id)))


def line_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(TransactionLine))


# --- reading ----------------------------------------------------------------------------------------


def test_each_organization_sees_only_its_own_lines_and_totals(client: TestClient, world: World):
    a = client.get(f"/api/transactions/{world.tx_a.id}", headers=h(world.a_only)).json()
    b = client.get(f"/api/transactions/{world.tx_b.id}", headers=h(world.b_only)).json()

    assert [l["item_id"] for l in a["lines"]] == [str(world.item_a.id)]
    assert [l["item_id"] for l in b["lines"]] == [str(world.item_b.id)]
    assert a["totals"] == b["totals"]  # identical-looking, but computed from each side's own lines
    assert {l["transaction_id"] for l in a["lines"]} == {str(world.tx_a.id)}


def test_the_list_shows_only_the_active_organizations_transactions_and_totals(
    client: TestClient, db_session: Session, world: World
):
    # Make B's transaction large so any leak into A's numbers would show.
    make_transaction(db_session, world.org_b, lines=[{"quantity": "1000", "unit_price_ex_vat": "999.00"}])

    summaries = client.get("/api/transactions", headers=h(world.a_only)).json()

    assert {s["id"] for s in summaries} == {str(world.tx_a.id), str(world.tx_a2.id)}
    assert {s["totals"]["net_amount"] for s in summaries} == {"850.00"}


def test_filters_never_cross_organizations(client: TestClient, world: World):
    foreign_customer = client.get(
        "/api/transactions",
        params={"billing_customer_id": str(world.tx_b.billing_customer_id)},
        headers=h(world.a_only),
    )
    missing = client.get(
        "/api/transactions", params={"billing_customer_id": str(uuid.uuid4())}, headers=h(world.a_only)
    )

    assert foreign_customer.status_code == missing.status_code == 200
    assert foreign_customer.json() == missing.json() == []


# --- adding lines ---------------------------------------------------------------------------------------


def test_cannot_add_a_line_to_another_organizations_transaction(
    client: TestClient, db_session: Session, world: World
):
    before = line_count(db_session)

    response = client.post(f"/api/transactions/{world.tx_b.id}/lines", json=LINE_BODY, headers=h(world.a_only))

    assert response.status_code == 404
    assert line_count(db_session) == before
    assert len(lines_of(db_session, world.tx_b)) == 1


def test_a_foreign_item_id_is_indistinguishable_from_a_nonexistent_one(
    client: TestClient, db_session: Session, world: World
):
    before = line_count(db_session)
    url = f"/api/transactions/{world.tx_a.id}/lines"

    foreign = client.post(url, json={"item_id": str(world.item_b.id), "quantity": "1"}, headers=h(world.a_only))
    missing = client.post(url, json={"item_id": str(uuid.uuid4()), "quantity": "1"}, headers=h(world.a_only))

    assert foreign.status_code == missing.status_code == 422
    assert foreign.json() == missing.json()
    assert foreign.json()["detail"][0]["type"] == "reference.not_found"
    assert str(world.item_b.id) not in foreign.text
    assert line_count(db_session) == before


def test_a_foreign_item_id_is_refused_inside_nested_creation_too(
    client: TestClient, db_session: Session, world: World
):
    before = db_session.scalar(select(func.count()).select_from(Transaction))
    body = {
        "billing_customer_id": str(world.customer_a.id),
        "lines": [{"item_id": str(world.item_a.id), "quantity": "1"}, {"item_id": str(world.item_b.id), "quantity": "1"}],
    }

    foreign = client.post("/api/transactions", json=body, headers=h(world.a_only))
    body["lines"][1]["item_id"] = str(uuid.uuid4())
    missing = client.post("/api/transactions", json=body, headers=h(world.a_only))

    assert foreign.status_code == missing.status_code == 422
    assert foreign.json() == missing.json()
    assert foreign.json()["detail"][0]["loc"] == ["body", "lines", 1, "item_id"]
    assert db_session.scalar(select(func.count()).select_from(Transaction)) == before


def test_changing_a_line_to_a_foreign_item_is_refused(client: TestClient, db_session: Session, world: World):
    line = lines_of(db_session, world.tx_a)[0]
    url = f"/api/transactions/{world.tx_a.id}/lines/{line.id}"

    foreign = client.patch(url, json={"item_id": str(world.item_b.id)}, headers=h(world.a_only))
    missing = client.patch(url, json={"item_id": str(uuid.uuid4())}, headers=h(world.a_only))

    assert foreign.status_code == missing.status_code == 422
    assert foreign.json() == missing.json()
    db_session.refresh(line)
    assert line.item_id == world.item_a.id


# --- billing customer references -----------------------------------------------------------------------------


def test_a_foreign_billing_customer_is_indistinguishable_from_a_nonexistent_one(
    client: TestClient, db_session: Session, world: World
):
    before = db_session.scalar(select(func.count()).select_from(Transaction))

    foreign = client.post("/api/transactions", json={"billing_customer_id": str(world.customer_b.id)}, headers=h(world.a_only))
    missing = client.post("/api/transactions", json={"billing_customer_id": str(uuid.uuid4())}, headers=h(world.a_only))

    assert foreign.status_code == missing.status_code == 422
    assert foreign.json() == missing.json()
    assert str(world.customer_b.id) not in foreign.text
    assert db_session.scalar(select(func.count()).select_from(Transaction)) == before


def test_moving_a_transaction_to_a_foreign_billing_customer_is_refused(
    client: TestClient, db_session: Session, world: World
):
    url = f"/api/transactions/{world.tx_a.id}"
    original = world.tx_a.billing_customer_id

    foreign = client.patch(url, json={"billing_customer_id": str(world.customer_b.id)}, headers=h(world.a_only))
    missing = client.patch(url, json={"billing_customer_id": str(uuid.uuid4())}, headers=h(world.a_only))

    assert foreign.status_code == missing.status_code == 422
    assert foreign.json() == missing.json()
    db_session.refresh(world.tx_a)
    assert world.tx_a.billing_customer_id == original


# --- the id matrix for line routes --------------------------------------------------------------------------------


def line_urls(world: World, db_session: Session) -> dict[str, str]:
    la = lines_of(db_session, world.tx_a)[0].id
    la2 = lines_of(db_session, world.tx_a2)[0].id
    lb = lines_of(db_session, world.tx_b)[0].id
    return {
        "own tx + own line": f"/api/transactions/{world.tx_a.id}/lines/{la}",
        "own tx + foreign line": f"/api/transactions/{world.tx_a.id}/lines/{lb}",
        "foreign tx + foreign line": f"/api/transactions/{world.tx_b.id}/lines/{lb}",
        "foreign tx + own line": f"/api/transactions/{world.tx_b.id}/lines/{la}",
        "own tx + line of my other tx": f"/api/transactions/{world.tx_a.id}/lines/{la2}",
        "own tx + random line": f"/api/transactions/{world.tx_a.id}/lines/{uuid.uuid4()}",
        "random tx + random line": f"/api/transactions/{uuid.uuid4()}/lines/{uuid.uuid4()}",
    }


@pytest.mark.parametrize(
    "case",
    [
        "own tx + foreign line",
        "foreign tx + foreign line",
        "foreign tx + own line",
        "own tx + line of my other tx",
        "own tx + random line",
        "random tx + random line",
    ],
)
def test_every_wrong_id_combination_is_the_same_404(client: TestClient, db_session: Session, world: World, case: str):
    urls = line_urls(world, db_session)
    before = [(l.id, l.quantity, l.description) for tx in (world.tx_a, world.tx_a2, world.tx_b) for l in lines_of(db_session, tx)]

    patched = client.patch(urls[case], json={"quantity": "9", "description": "Hijacked"}, headers=h(world.a_only))
    deleted = client.delete(urls[case], headers=h(world.a_only))
    reference = client.patch(urls["random tx + random line"], json={"quantity": "9"}, headers=h(world.a_only))

    assert patched.status_code == deleted.status_code == 404
    assert patched.json() == deleted.json() == reference.json()
    after = [(l.id, l.quantity, l.description) for tx in (world.tx_a, world.tx_a2, world.tx_b) for l in lines_of(db_session, tx)]
    assert after == before  # nothing anywhere was touched


def test_the_own_tx_own_line_case_works(client: TestClient, db_session: Session, world: World):
    url = line_urls(world, db_session)["own tx + own line"]

    assert client.patch(url, json={"quantity": "2"}, headers=h(world.a_only)).status_code == 200
    assert client.delete(url, headers=h(world.a_only)).status_code == 204


def test_changing_own_line_leaves_the_identical_twin_untouched(client: TestClient, db_session: Session, world: World):
    twin_before = [(l.quantity, l.net_amount) for l in lines_of(db_session, world.tx_b)]

    client.patch(line_urls(world, db_session)["own tx + own line"], json={"quantity": "7"}, headers=h(world.a_only))

    assert [(l.quantity, l.net_amount) for l in lines_of(db_session, world.tx_b)] == twin_before


# --- lifecycle actions on foreign transactions -------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["complete", "reopen", "cancel"])
def test_cannot_change_the_lifecycle_of_a_foreign_transaction(
    client: TestClient, db_session: Session, world: World, action: str
):
    foreign = client.post(f"/api/transactions/{world.tx_b.id}/{action}", headers=h(world.a_only))
    missing = client.post(f"/api/transactions/{uuid.uuid4()}/{action}", headers=h(world.a_only))

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    db_session.refresh(world.tx_b)
    assert world.tx_b.status == "draft"


def test_the_same_user_acts_only_on_the_selected_organizations_transactions(client: TestClient, world: World):
    completed_in_a = client.post(f"/api/transactions/{world.tx_a.id}/complete", headers=h(world.shared, world.org_a))
    other_org = client.post(f"/api/transactions/{world.tx_b.id}/complete", headers=h(world.shared, world.org_a))
    switched = client.post(f"/api/transactions/{world.tx_b.id}/complete", headers=h(world.shared, world.org_b))

    assert (completed_in_a.status_code, other_org.status_code, switched.status_code) == (200, 404, 200)


# --- selector and authentication guard the nested routes ---------------------------------------------------------------------


def test_selecting_an_organization_you_do_not_belong_to_blocks_every_sales_route(
    client: TestClient, db_session: Session, world: World
):
    foreign = h(world.a_only, world.org_b)  # a_only is not a member of B
    line = lines_of(db_session, world.tx_b)[0]
    base = f"/api/transactions/{world.tx_b.id}"

    responses = [
        client.get("/api/transactions", headers=foreign),
        client.post("/api/transactions", json={"billing_customer_id": str(world.customer_a.id)}, headers=foreign),
        client.get(base, headers=foreign),
        client.patch(base, json={"transaction_date": "2026-01-01"}, headers=foreign),
        client.delete(base, headers=foreign),
        client.post(f"{base}/lines", json=LINE_BODY, headers=foreign),
        client.patch(f"{base}/lines/{line.id}", json={"quantity": "2"}, headers=foreign),
        client.delete(f"{base}/lines/{line.id}", headers=foreign),
        client.post(f"{base}/complete", headers=foreign),
        client.post(f"{base}/reopen", headers=foreign),
        client.post(f"{base}/cancel", headers=foreign),
    ]

    assert [r.status_code for r in responses] == [404] * 11


def test_every_sales_route_requires_authentication(client: TestClient, db_session: Session, world: World):
    line = lines_of(db_session, world.tx_a)[0]
    base = f"/api/transactions/{world.tx_a.id}"

    responses = [
        client.get("/api/transactions"),
        client.post("/api/transactions", json={"billing_customer_id": str(world.customer_a.id)}),
        client.get(base),
        client.patch(base, json={"transaction_date": "2026-01-01"}),
        client.delete(base),
        client.post(f"{base}/lines", json=LINE_BODY),
        client.patch(f"{base}/lines/{line.id}", json={"quantity": "2"}),
        client.delete(f"{base}/lines/{line.id}"),
        client.post(f"{base}/complete"),
        client.post(f"{base}/reopen"),
        client.post(f"{base}/cancel"),
    ]

    assert [r.status_code for r in responses] == [401] * 11


# --- records of other resources do not resolve as lines or transactions ------------------------------------------------------


def test_ids_of_other_resources_are_not_transactions_or_lines(client: TestClient, db_session: Session, world: World):
    line = lines_of(db_session, world.tx_a)[0]
    headers = h(world.a_only)

    assert client.get(f"/api/transactions/{world.customer_a.id}", headers=headers).status_code == 404
    assert client.get(f"/api/transactions/{world.item_a.id}", headers=headers).status_code == 404
    assert client.get(f"/api/customers/{world.tx_a.id}", headers=headers).status_code == 404
    assert client.get(f"/api/items/{line.id}", headers=headers).status_code == 404
    assert client.patch(f"/api/transactions/{world.tx_a.id}/lines/{world.item_a.id}", json={"quantity": "2"}, headers=headers).status_code == 404
