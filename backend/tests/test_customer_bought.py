"""What a customer bought besides services (GET /api/transactions/bought): catalog items and ad-hoc lines of the
customer's orders, newest first, cancelled orders left out, nothing of another customer or organization.
"""

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import ItemType, Role
from tests.factories import add_member, make_customer, make_item, make_line, make_org, make_transaction, make_user


def _owner(db: Session, org) -> dict[str, str]:
    user = make_user(db)
    add_member(db, org, user, Role.OWNER)
    return {"X-Dev-User-Email": user.email, "X-Organization-Id": str(org.id)}


def _order(db: Session, org, customer, day: int, status="completed", lines=()):
    tx = make_transaction(db, org, billing_customer=customer, transaction_date=date(2026, 10, day), status=status, lines=[])
    for position, fields in enumerate(lines, start=1):
        make_line(db, org, tx, position=position, **fields)
    return tx


def _bought(client: TestClient, headers, customer):
    return client.get("/api/transactions/bought", params={"billing_customer_id": str(customer.id)}, headers=headers)


def test_a_customers_products_and_ad_hoc_lines_newest_first_without_cancelled_orders(client: TestClient, db_session: Session):
    org = make_org(db_session)
    owner = _owner(db_session, org)
    anna, other = make_customer(db_session, org, "Anna"), make_customer(db_session, org, "Other")
    spray = make_item(db_session, org, name="Fly spray", type=ItemType.PRODUCT, unit="pcs", price_ex_vat="150.00")
    old = _order(db_session, org, anna, 1, lines=[{"item": spray, "description": "Fly spray", "unit": "pcs", "quantity": "2", "unit_price_ex_vat": "150.00"}])
    new = _order(db_session, org, anna, 5, status="draft", lines=[{"description": "Delivery", "unit": "trip", "quantity": "1", "unit_price_ex_vat": "90.00"}])
    _order(db_session, org, anna, 7, status="cancelled", lines=[{"description": "Cancelled thing"}])
    _order(db_session, org, other, 6, lines=[{"description": "Not Anna's"}])

    response = _bought(client, owner, anna)

    assert response.status_code == 200
    rows = response.json()
    assert [(r["transaction_id"], r["description"], r["unit"], r["quantity"], r["status"]) for r in rows] == [
        (str(new.id), "Delivery", "trip", "1.000", "draft"),
        (str(old.id), "Fly spray", "pcs", "2.000", "completed"),
    ]
    assert rows[1]["item_id"] == str(spray.id) and rows[0]["item_id"] is None
    assert rows[1]["unit_price_ex_vat"] == "150.00" and rows[1]["gross_amount"] == "375.00"


def test_another_organizations_customer_matches_nothing(client: TestClient, db_session: Session):
    org_a, org_b = make_org(db_session, "Org A"), make_org(db_session, "Org B")
    a = _owner(db_session, org_a)
    b_customer = make_customer(db_session, org_b, "Anna")
    _order(db_session, org_b, b_customer, 1, lines=[{"description": "B's secret"}])

    assert _bought(client, a, b_customer).json() == []
    assert client.get("/api/transactions/bought", headers=a).status_code == 422  # a customer is required
