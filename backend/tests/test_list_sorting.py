"""Sorting the lists by a column (?sort=<key>&dir=asc|desc), on the server, so a sort covers every page.

Empty values sort last either way, ties keep the list's default order (stable pages), an unknown key is a 422, and a
sort never reaches another organization's rows. The catalog's stock columns are SQL from the Inventory module; they
must order exactly as the figures the Inventory module reports.
"""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import ItemType, Role
from tests.factories import make_customer, make_horse, make_item, make_line, make_org, make_transaction
from tests.invoicing_support import member_of


def _names(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [row["name"] for row in response.json()]


def test_customers_sort_by_any_column_both_ways_and_only_within_the_organization(client: TestClient, db_session: Session):
    org, other = make_org(db_session), make_org(db_session)
    owner = member_of(db_session, org, Role.OWNER)
    for name, email in (("bo", "z@example.test"), ("Anna", None), ("Cia", "a@example.test")):
        make_customer(db_session, org, name, email=email)
    make_customer(db_session, other, "Aaron", email="0@example.test")

    assert _names(client.get("/api/customers", params={"sort": "name"}, headers=owner)) == ["Anna", "bo", "Cia"]  # case-insensitive
    assert _names(client.get("/api/customers", params={"sort": "name", "dir": "desc"}, headers=owner)) == ["Cia", "bo", "Anna"]
    assert _names(client.get("/api/customers", params={"sort": "number", "dir": "desc"}, headers=owner)) == ["Cia", "Anna", "bo"]
    # No email: last whichever way.
    assert _names(client.get("/api/customers", params={"sort": "email"}, headers=owner)) == ["Cia", "bo", "Anna"]
    assert _names(client.get("/api/customers", params={"sort": "email", "dir": "desc"}, headers=owner)) == ["bo", "Cia", "Anna"]

    refused = client.get("/api/customers", params={"sort": "organization_id"}, headers=owner)
    assert refused.status_code == 422 and refused.json()["detail"][0]["type"] == "sort.unknown"
    assert client.get("/api/customers", params={"sort": "name; drop"}, headers=owner).status_code == 422
    assert client.get("/api/customers", params={"sort": "name", "dir": "up"}, headers=owner).status_code == 422


def test_ties_keep_the_default_order_so_pages_never_repeat_or_skip_a_row(client: TestClient, db_session: Session):
    org = make_org(db_session)
    owner = member_of(db_session, org, Role.OWNER)
    for index in range(7):
        make_customer(db_session, org, f"Same {index}")  # every row ties on the sorted column (status)
    pages = [client.get("/api/customers", params={"sort": "active", "limit": 3, "offset": offset}, headers=owner).json() for offset in (0, 3, 6)]
    seen = [row["name"] for page in pages for row in page]
    assert seen == [f"Same {index}" for index in range(7)]


def test_the_catalog_sorts_by_price_promotion_and_price_incl_vat_today(client: TestClient, db_session: Session):
    org = make_org(db_session)
    owner = member_of(db_session, org, Role.OWNER)
    make_item(db_session, org, name="Cheap", price_ex_vat="100.00", vat_rate="25.00")
    dear = make_item(db_session, org, name="Dear", price_ex_vat="1000.00", vat_rate="25.00")
    make_item(db_session, org, name="Middle", price_ex_vat="500.00", vat_rate="0.00")
    start = date.today().replace(day=1).isoformat()
    assert client.post(f"/api/items/{dear.id}/discounts", json={"percent": "60", "starts_on": start}, headers=owner).status_code == 201

    assert _names(client.get("/api/items", params={"sort": "price"}, headers=owner)) == ["Cheap", "Middle", "Dear"]
    # Today Dear costs 400.00 + 25 % = 500.00, Middle 500.00 + 0 %: a tie, kept in name order; Cheap 125.00.
    assert _names(client.get("/api/items", params={"sort": "price_inc_vat"}, headers=owner)) == ["Cheap", "Dear", "Middle"]
    assert _names(client.get("/api/items", params={"sort": "current_price"}, headers=owner)) == ["Cheap", "Dear", "Middle"]  # 100, 400, 500
    assert _names(client.get("/api/items", params={"sort": "promotion_ends"}, headers=owner))[0] == "Dear"  # the only promotion; the rest last
    assert _names(client.get("/api/items", params={"sort": "vat_rate", "dir": "desc"}, headers=owner)) == ["Cheap", "Dear", "Middle"]


@pytest.mark.parametrize("key,field", [("on_hand", "on_hand"), ("allocated", "allocated"), ("available", "available"), ("committed", "committed"), ("incoming", "incoming")])
def test_the_catalog_s_stock_columns_order_exactly_as_the_figures_inventory_reports(client: TestClient, db_session: Session, key, field):
    org = make_org(db_session)
    owner = member_of(db_session, org, Role.OWNER)
    products = [make_item(db_session, org, name=f"P{index}", type=ItemType.PRODUCT, unit="pcs", track_stock=True) for index in range(4)]
    make_item(db_session, org, name="A service")  # tracks nothing: last
    for product, count in zip(products, ("5", "0", "12", "3")):
        client.post(f"/api/items/{product.id}/stock", json={"kind": "count", "quantity": count, "note": "Opening stock"}, headers=owner)
    customer = make_customer(db_session, org)
    draft = make_transaction(db_session, org, billing_customer=customer)
    make_line(db_session, org, draft, item=products[0], description="P0", unit="pcs", quantity="4", unit_price_ex_vat="1.00")
    done = make_transaction(db_session, org, billing_customer=customer)
    make_line(db_session, org, done, item=products[1], description="P1", unit="pcs", quantity="2", unit_price_ex_vat="1.00")
    client.post(f"/api/transactions/{done.id}/complete", headers=owner)  # nothing on hand: 2 backordered
    client.post("/api/inventory/incoming", json={"item_id": str(products[3].id), "quantity": "9"}, headers=owner)

    figures = {row["item_id"]: Decimal(row[field]) for row in client.get(
        "/api/inventory/availability", params=[("item_id", str(p.id)) for p in products], headers=owner
    ).json()}
    for descending in (False, True):
        listed = client.get("/api/items", params={"sort": key, "dir": "desc" if descending else "asc"}, headers=owner).json()
        assert listed[-1]["name"] == "A service"
        values = [figures[row["id"]] for row in listed[:-1]]
        assert values == sorted(values, reverse=descending), (key, values)


def test_orders_sort_by_their_totals_and_customer(client: TestClient, db_session: Session):
    org = make_org(db_session)
    owner = member_of(db_session, org, Role.OWNER)
    anna, bo = make_customer(db_session, org, "Anna"), make_customer(db_session, org, "Bo")
    big = make_transaction(db_session, org, billing_customer=anna)
    make_line(db_session, org, big, quantity="3", unit_price_ex_vat="100.00")
    small = make_transaction(db_session, org, billing_customer=bo)
    make_line(db_session, org, small, quantity="1", unit_price_ex_vat="10.00")
    empty = make_transaction(db_session, org, billing_customer=bo, lines=[])

    by_gross = [row["id"] for row in client.get("/api/transactions", params={"sort": "gross", "dir": "desc"}, headers=owner).json()]
    assert by_gross == [str(big.id), str(small.id), str(empty.id)]
    by_customer = [row["billing_customer"]["name"] for row in client.get("/api/transactions", params={"sort": "customer"}, headers=owner).json()]
    assert by_customer == ["Anna", "Bo", "Bo"]
    by_lines = [row["line_count"] for row in client.get("/api/transactions", params={"sort": "lines"}, headers=owner).json()]
    assert by_lines == sorted(by_lines) and len(set(by_lines)) > 1


def test_horses_sort_by_their_owner(client: TestClient, db_session: Session):
    org = make_org(db_session)
    owner = member_of(db_session, org, Role.OWNER)
    make_horse(db_session, org, name="Kalle", owner=make_customer(db_session, org, "Zelda"))
    make_horse(db_session, org, name="Zorro", owner=make_customer(db_session, org, "Anna"))
    assert _names(client.get("/api/horses", params={"sort": "owner"}, headers=owner)) == ["Zorro", "Kalle"]


def test_the_inventory_list_sorts_its_computed_figures(client: TestClient, db_session: Session):
    org = make_org(db_session)
    owner = member_of(db_session, org, Role.OWNER)
    for name, count in (("A", "7"), ("B", "2"), ("C", "40")):
        item = make_item(db_session, org, name=name, type=ItemType.PRODUCT, unit="pcs", track_stock=True)
        client.post(f"/api/items/{item.id}/stock", json={"kind": "count", "quantity": count, "note": "Opening stock"}, headers=owner)
    assert _names(client.get("/api/inventory/items", params={"sort": "on_hand", "dir": "desc"}, headers=owner)) == ["C", "A", "B"]
    assert client.get("/api/inventory/items", params={"sort": "price"}, headers=owner).status_code == 422


def test_the_new_invoice_list_sorts_on_the_server_and_never_shows_another_organization(client: TestClient, db_session: Session):
    from tests.invoicing_support import completed

    org, other = make_org(db_session), make_org(db_session)
    owner = member_of(db_session, org, Role.OWNER)
    customer = make_customer(db_session, org, "Umeå HK")
    small = completed(db_session, org, customer, lines=[{"quantity": "1", "unit_price_ex_vat": "10.00"}])
    big = completed(db_session, org, customer, lines=[{"quantity": "5", "unit_price_ex_vat": "100.00"}])
    completed(db_session, other)
    listed = client.get("/api/invoiceable-transactions", params={"sort": "gross", "dir": "desc"}, headers=owner).json()
    assert [row["id"] for row in listed] == [str(big.id), str(small.id)]
    assert client.get("/api/invoiceable-transactions", params={"sort": "nope"}, headers=owner).status_code == 422
