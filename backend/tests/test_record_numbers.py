"""Record numbers (decided 2026-10-10): orders count from 1001, customers, suppliers, horses and catalog items from 1,
each series per organization. The database hands them out on INSERT (whatever path inserts) and refuses changing one.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models import ItemType, Role
from tests.factories import make_customer, make_horse, make_item, make_org
from tests.invoicing_support import completed, draft_invoice, issue, member_of


def test_each_series_starts_where_decided_and_counts_per_organization(client, db_session: Session):
    a, b = make_org(db_session, "Org A"), make_org(db_session, "Org B")
    a_owner, b_owner = member_of(db_session, a, Role.OWNER), member_of(db_session, b, Role.OWNER)

    first = client.post("/api/customers", json={"customer_type": "person", "name": "Anna"}, headers=a_owner).json()
    second = client.post("/api/customers", json={"customer_type": "person", "name": "Bo"}, headers=a_owner).json()
    other = client.post("/api/customers", json={"customer_type": "person", "name": "Anna"}, headers=b_owner).json()
    assert (first["number"], second["number"], other["number"]) == (1, 2, 1)  # B's series is its own

    supplier = client.post("/api/suppliers", json={"name": "Horse Supplies AB"}, headers=a_owner).json()
    item = client.post("/api/items", json={"type": "service", "name": "Massage", "unit": "h", "price_ex_vat": "850", "vat_rate": "25"}, headers=a_owner).json()
    horse = make_horse(db_session, a, name="Kalle", owner=make_customer(db_session, a, "Owner"))
    order = client.post("/api/transactions", json={"billing_customer_id": first["id"], "transaction_date": "2026-10-10"}, headers=a_owner).json()
    next_order = client.post("/api/transactions", json={"billing_customer_id": first["id"], "transaction_date": "2026-10-10"}, headers=a_owner).json()
    assert (supplier["number"], item["number"], horse.number) == (1, 1, 1)
    assert (order["number"], next_order["number"]) == (1001, 1002)
    assert client.get(f"/api/horses/{horse.id}", headers=a_owner).json()["number"] == 1


def test_a_number_cannot_be_sent_or_changed(client, db_session: Session):
    org = make_org(db_session)
    owner = member_of(db_session, org, Role.OWNER)
    customer = client.post("/api/customers", json={"customer_type": "person", "name": "Anna"}, headers=owner).json()

    assert client.post("/api/customers", json={"customer_type": "person", "name": "X", "number": 99}, headers=owner).status_code == 422
    assert client.patch(f"/api/customers/{customer['id']}", json={"number": 99}, headers=owner).status_code == 422
    with pytest.raises(DBAPIError, match="a record number never changes"):
        with db_session.begin_nested():
            db_session.execute(text("update customers set number = 99 where id = :id"), {"id": customer["id"]})


def test_a_list_search_finds_a_record_by_its_number_but_never_another_organization_s(client, db_session: Session):
    a, b = make_org(db_session, "Org A"), make_org(db_session, "Org B")
    a_owner, b_owner = member_of(db_session, a, Role.OWNER), member_of(db_session, b, Role.OWNER)
    for name in ("Anna", "Bo", "Cia"):
        make_customer(db_session, a, name)
    make_customer(db_session, b, "Secret")  # number 1 in B
    make_item(db_session, a, name="Liniment", type=ItemType.PRODUCT)

    assert [c["name"] for c in client.get("/api/customers", params={"q": "2"}, headers=a_owner).json()] == ["Bo"]
    assert [c["name"] for c in client.get("/api/customers", params={"q": " 1 "}, headers=a_owner).json()] == ["Anna"]
    assert [c["name"] for c in client.get("/api/customers", params={"q": "1"}, headers=b_owner).json()] == ["Secret"]
    assert [i["name"] for i in client.get("/api/items", params={"q": "1"}, headers=a_owner).json()] == ["Liniment"]
    assert client.get("/api/customers", params={"q": "99999999999999999999"}, headers=a_owner).json() == []  # too big: no match, no error


def test_orders_are_found_by_number_and_an_invoice_records_the_number(client, db_session: Session):
    org = make_org(db_session)
    owner = member_of(db_session, org, Role.OWNER)
    customer = make_customer(db_session, org, "Umeå HK")
    first, second = completed(db_session, org, customer), completed(db_session, org, customer)
    assert (first.number, second.number) == (1001, 1002)

    found = client.get("/api/transactions", params={"number": "1002"}, headers=owner).json()
    assert [t["id"] for t in found] == [str(second.id)]
    other = make_org(db_session)
    assert client.get("/api/transactions", params={"number": "1002"}, headers=member_of(db_session, other, Role.OWNER)).json() == []

    invoice = issue(client, owner, draft_invoice(client, owner, first, second))
    assert sorted(s["transaction_number"] for s in invoice["transactions"]) == [1001, 1002]
