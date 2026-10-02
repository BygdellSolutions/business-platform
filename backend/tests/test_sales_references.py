"""Customers and Items are referenced by transactions: deletion, deactivation, independence."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Customer, Item
from tests.factories import make_customer, make_item


def make_tx(client: TestClient, sales, lines=None, customer=None):
    response = client.post(
        "/api/transactions",
        json={
            "billing_customer_id": str((customer or sales.billing).id),
            "lines": lines if lines is not None else [{"item_id": str(sales.item.id), "quantity": "1"}],
        },
        headers=sales.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- the billing customer ---------------------------------------------------------------------------


def test_a_billing_customer_cannot_be_deleted(client: TestClient, db_session: Session, sales):
    make_tx(client, sales)

    response = client.delete(f"/api/customers/{sales.billing.id}", headers=sales.headers)

    assert response.status_code == 409
    assert "transaction" not in response.text.lower()  # Customers does not know about Sales
    assert db_session.get(Customer, sales.billing.id) is not None


def test_a_cancelled_transaction_still_protects_its_billing_customer(client: TestClient, sales):
    tx = make_tx(client, sales)
    client.post(f"/api/transactions/{tx['id']}/cancel", headers=sales.headers)

    assert client.delete(f"/api/customers/{sales.billing.id}", headers=sales.headers).status_code == 409


def test_the_billing_customer_can_be_deleted_once_no_transaction_uses_it(client: TestClient, sales):
    tx = make_tx(client, sales)
    assert client.delete(f"/api/customers/{sales.billing.id}", headers=sales.headers).status_code == 409

    client.delete(f"/api/transactions/{tx['id']}", headers=sales.headers)

    assert client.delete(f"/api/customers/{sales.billing.id}", headers=sales.headers).status_code == 204


def test_deactivating_the_billing_customer_does_not_affect_existing_transactions(client: TestClient, sales):
    tx = make_tx(client, sales)
    client.post(f"/api/transactions/{tx['id']}/complete", headers=sales.headers)

    client.patch(f"/api/customers/{sales.billing.id}", json={"active": False}, headers=sales.headers)

    shown = client.get(f"/api/transactions/{tx['id']}", headers=sales.headers).json()
    assert shown["status"] == "completed"
    assert shown["billing_customer"]["active"] is False
    assert shown["lines"] == tx["lines"]
    # ...but a NEW transaction cannot use the deactivated customer.
    new = client.post("/api/transactions", json={"billing_customer_id": str(sales.billing.id)}, headers=sales.headers)
    assert new.status_code == 422 and new.json()["detail"][0]["type"] == "reference.inactive"


def test_the_billing_customer_is_independent_of_any_other_customer(
    client: TestClient, db_session: Session, sales
):
    # Billing is its own relationship: any customer may be billed, and nothing implies
    # that the billed party is "the" customer of anything else.
    anna = make_customer(db_session, sales.org, "Anna Andersson")

    one = make_tx(client, sales, customer=anna)
    two = make_tx(client, sales, customer=sales.billing)

    assert one["billing_customer"]["name"] == "Anna Andersson"
    assert two["billing_customer"]["name"] == "Umeå HK"
    assert set(one) == set(two) and not [k for k in one if k in ("owner", "owner_customer_id", "customer_id")]


# --- the item ---------------------------------------------------------------------------------------------


def test_an_item_used_by_a_line_cannot_be_deleted(client: TestClient, db_session: Session, sales):
    make_tx(client, sales)

    response = client.delete(f"/api/items/{sales.item.id}", headers=sales.headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "Item is referenced by other records"
    assert "transaction" not in response.text.lower()
    assert db_session.get(Item, sales.item.id) is not None


def test_an_item_can_be_deactivated_instead(client: TestClient, sales):
    tx = make_tx(client, sales)

    deactivated = client.patch(f"/api/items/{sales.item.id}", json={"active": False}, headers=sales.headers)

    assert deactivated.status_code == 200
    assert client.get(f"/api/transactions/{tx['id']}", headers=sales.headers).json()["lines"] == tx["lines"]


def test_an_item_is_deletable_when_no_line_uses_it(client: TestClient, db_session: Session, sales):
    unused = make_item(db_session, sales.org, "Unused")
    make_tx(client, sales)  # uses sales.item, not `unused`

    assert client.delete(f"/api/items/{unused.id}", headers=sales.headers).status_code == 204


def test_an_item_is_released_when_its_lines_are_removed(client: TestClient, sales):
    tx = make_tx(client, sales)
    assert client.delete(f"/api/items/{sales.item.id}", headers=sales.headers).status_code == 409

    client.delete(f"/api/transactions/{tx['id']}/lines/{tx['lines'][0]['id']}", headers=sales.headers)

    assert client.delete(f"/api/items/{sales.item.id}", headers=sales.headers).status_code == 204


def test_ad_hoc_lines_do_not_protect_any_item(client: TestClient, sales):
    make_tx(client, sales, lines=[{"description": "Travel", "unit": "km", "quantity": "1", "unit_price_ex_vat": "2.50", "vat_rate": "25.00"}])

    assert client.delete(f"/api/items/{sales.item.id}", headers=sales.headers).status_code == 204


def test_a_refused_item_delete_does_not_break_the_session(client: TestClient, sales):
    make_tx(client, sales)

    client.delete(f"/api/items/{sales.item.id}", headers=sales.headers)

    assert client.get("/api/items", headers=sales.headers).status_code == 200
