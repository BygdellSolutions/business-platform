"""Discounts: the customer's permanent discount, temporary catalog discounts, and the layers on a line.

Layers apply in order (catalog, then customer), each rounded half-up to two decimals before the next, never added
together; every step is stored and visible; a typed price replaces them; invoices copy them verbatim.
"""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AuditEvent, Role
from app.modules.sales.pricing import discounted_unit_price
from tests.factories import add_member, make_customer, make_item, make_org, make_transaction, make_user

D = Decimal


@pytest.mark.parametrize(
    "price,catalog,customer,expected",
    [
        ("1000.00", "20", "10", "720.00"),  # 28 % in effect, never 30 %
        ("100.00", "15", "10", "76.50"),
        ("99.99", "15", "10", "76.49"),  # 84.9915 -> 84.99 first, then 76.491 -> 76.49
        ("10.05", "50", None, "5.03"),  # half up
        ("850.00", None, None, "850.00"),
        ("850.00", None, "10", "765.00"),
    ],
)
def test_layers_are_sequential_and_rounded_per_step(price, catalog, customer, expected):
    assert discounted_unit_price(D(price), D(catalog) if catalog else None, D(customer) if customer else None) == D(expected)


def _world(db: Session, role: Role = Role.OWNER):
    org = make_org(db)
    user = make_user(db)
    add_member(db, org, user, role)
    return org, {"X-Dev-User-Email": user.email}


def _member(db: Session, org, role: Role) -> dict[str, str]:
    user = make_user(db)
    add_member(db, org, user, role)
    return {"X-Dev-User-Email": user.email}


# --- the customer's permanent discount ----------------------------------------------------------------------------


def test_an_owner_sets_and_removes_a_customer_discount_and_history_records_it(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    customer = client.post("/api/customers", json={"name": "Customer A", "customer_type": "company", "default_discount_percent": "10"}, headers=owner).json()
    assert customer["default_discount_percent"] == "10.00"

    changed = client.patch(f"/api/customers/{customer['id']}", json={"default_discount_percent": "12.5"}, headers=owner)
    removed = client.patch(f"/api/customers/{customer['id']}", json={"default_discount_percent": None}, headers=owner)

    assert changed.json()["default_discount_percent"] == "12.50" and removed.json()["default_discount_percent"] is None
    events = client.get("/api/history", params={"entity_type": "customer", "entity_id": customer["id"]}, headers=owner).json()["events"]
    assert [e["changes"].get("default_discount_percent") for e in events[:2]] == [{"from": "12.50", "to": None}, {"from": "10.00", "to": "12.50"}]


@pytest.mark.parametrize("role", [Role.ACCOUNTANT, Role.EMPLOYEE])
def test_other_writers_may_edit_a_customer_but_not_its_discount(client: TestClient, db_session: Session, role: Role):
    org, _ = _world(db_session)
    headers = _member(db_session, org, role)
    customer = make_customer(db_session, org, default_discount_percent=D("10"))

    assert client.patch(f"/api/customers/{customer.id}", json={"default_discount_percent": "50"}, headers=headers).status_code == 403
    assert client.post("/api/customers", json={"name": "X", "customer_type": "person", "default_discount_percent": "5"}, headers=headers).status_code == 403
    # ...while the ordinary edit, and an untouched or empty discount, are fine.
    assert client.patch(f"/api/customers/{customer.id}", json={"name": "Renamed", "default_discount_percent": "10"}, headers=headers).status_code == 200
    assert client.post("/api/customers", json={"name": "Y", "customer_type": "person", "default_discount_percent": None}, headers=headers).status_code == 201
    db_session.refresh(customer)
    assert customer.default_discount_percent == D("10.00")


@pytest.mark.parametrize("bad", ["0", "100", "-5", "100.5", 10.5])
def test_a_discount_is_strictly_between_0_and_100(client: TestClient, db_session: Session, bad):
    org, owner = _world(db_session)
    customer = make_customer(db_session, org)
    assert client.patch(f"/api/customers/{customer.id}", json={"default_discount_percent": bad}, headers=owner).status_code == 422


# --- temporary catalog discounts ------------------------------------------------------------------------------------


def test_an_owner_adds_lists_and_removes_item_discounts(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    item = make_item(db_session, org)
    url = f"/api/items/{item.id}/discounts"

    made = client.post(url, json={"percent": "10", "starts_on": "2026-10-01", "ends_on": "2026-10-07", "note": "Week offer"}, headers=owner)
    later = client.post(url, json={"percent": "20", "starts_on": "2026-11-01"}, headers=owner)

    assert made.status_code == 201 and later.status_code == 201
    assert [d["percent"] for d in client.get(url, headers=owner).json()] == ["20.00", "10.00"]
    assert client.delete(f"{url}/{made.json()['id']}", headers=owner).status_code == 204
    assert [d["percent"] for d in client.get(url, headers=owner).json()] == ["20.00"]
    actions = db_session.scalars(select(AuditEvent.action).where(AuditEvent.context_id == item.id).order_by(AuditEvent.id)).all()
    assert actions == ["created", "created", "deleted"]
    assert db_session.get(type(item), item.id).price_ex_vat == D("850.00")  # the item's price never changes


@pytest.mark.parametrize(
    "period",
    [
        {"starts_on": "2026-10-05", "ends_on": "2026-10-10"},  # overlaps the end
        {"starts_on": "2026-09-01", "ends_on": "2026-10-01"},  # touches the first day
        {"starts_on": "2026-09-01"},  # open-ended, covers everything after
    ],
)
def test_periods_of_one_item_never_overlap(client: TestClient, db_session: Session, period):
    org, owner = _world(db_session)
    item = make_item(db_session, org)
    url = f"/api/items/{item.id}/discounts"
    client.post(url, json={"percent": "10", "starts_on": "2026-10-01", "ends_on": "2026-10-07"}, headers=owner)

    response = client.post(url, json={"percent": "5", **period}, headers=owner)

    assert response.status_code == 422 and response.json()["detail"][0]["type"] == "discount.overlap"


def test_a_period_must_be_in_order_and_writers_other_than_owners_and_admins_cannot_change_discounts(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    item = make_item(db_session, org)
    url = f"/api/items/{item.id}/discounts"
    assert client.post(url, json={"percent": "10", "starts_on": "2026-10-07", "ends_on": "2026-10-01"}, headers=owner).status_code == 422
    for role in (Role.ACCOUNTANT, Role.EMPLOYEE, Role.VIEWER):
        assert client.post(url, json={"percent": "10", "starts_on": "2026-10-01"}, headers=_member(db_session, org, role)).status_code == 403
    assert client.get(url, headers=_member(db_session, org, Role.VIEWER)).status_code == 200


def test_another_organizations_item_has_no_discounts_here(client: TestClient, db_session: Session):
    _, owner = _world(db_session)
    other = make_item(db_session, make_org(db_session, "Other"))
    assert client.get(f"/api/items/{other.id}/discounts", headers=owner).status_code == 404
    assert client.post(f"/api/items/{other.id}/discounts", json={"percent": "10", "starts_on": "2026-10-01"}, headers=owner).status_code == 404


# --- lines -------------------------------------------------------------------------------------------------------------


def _discounted_world(db: Session, client: TestClient):
    org, owner = _world(db)
    item = make_item(db, org, price_ex_vat="1000.00", vat_rate="25.00")
    client.post(f"/api/items/{item.id}/discounts", json={"percent": "20", "starts_on": "2026-10-01", "ends_on": "2026-10-07"}, headers=owner)
    customer = make_customer(db, org, "Customer A", default_discount_percent=D("10"))
    plain = make_customer(db, org, "No discount")
    return org, owner, item, customer, plain


def _add(client, headers, tx_id, **body):
    response = client.post(f"/api/transactions/{tx_id}/lines", json={"quantity": "1", **body}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_a_catalog_line_gets_both_layers_with_every_step_visible(client: TestClient, db_session: Session):
    org, owner, item, customer, _ = _discounted_world(db_session, client)
    tx = client.post("/api/transactions", json={"billing_customer_id": str(customer.id), "transaction_date": "2026-10-03"}, headers=owner).json()

    line = _add(client, owner, tx["id"], item_id=str(item.id), quantity="2")

    assert (line["list_unit_price"], line["catalog_discount_percent"], line["customer_discount_percent"]) == ("1000.00", "20.00", "10.00")
    assert (line["unit_price_ex_vat"], line["net_amount"], line["vat_amount"], line["gross_amount"]) == ("720.00", "1440.00", "360.00", "1800.00")


def test_the_catalog_discount_applies_only_inside_its_period(client: TestClient, db_session: Session):
    org, owner, item, _, plain = _discounted_world(db_session, client)
    for day, expected in (("2026-09-30", None), ("2026-10-01", "20.00"), ("2026-10-07", "20.00"), ("2026-10-08", None)):
        tx = client.post("/api/transactions", json={"billing_customer_id": str(plain.id), "transaction_date": day}, headers=owner).json()
        line = _add(client, owner, tx["id"], item_id=str(item.id))
        assert line["catalog_discount_percent"] == expected, day
        assert line["unit_price_ex_vat"] == ("800.00" if expected else "1000.00")


def test_ad_hoc_lines_carry_no_discounts_and_a_catalog_lines_price_cannot_be_typed(client: TestClient, db_session: Session):
    org, owner, item, customer, _ = _discounted_world(db_session, client)
    tx = client.post("/api/transactions", json={"billing_customer_id": str(customer.id), "transaction_date": "2026-10-03"}, headers=owner).json()

    adhoc = _add(client, owner, tx["id"], description="Special", unit="st", unit_price_ex_vat="100.00", vat_rate="25")
    typed = client.post(f"/api/transactions/{tx['id']}/lines", json={"quantity": "1", "item_id": str(item.id), "unit_price_ex_vat": "950.00"}, headers=owner)

    assert (adhoc["list_unit_price"], adhoc["catalog_discount_percent"], adhoc["customer_discount_percent"]) == (None, None, None)
    assert typed.status_code == 422 and typed.json()["detail"][0]["type"] == "line.catalog_value"


def test_a_discounted_catalog_lines_price_cannot_be_typed_over(client: TestClient, db_session: Session):
    org, owner, item, customer, _ = _discounted_world(db_session, client)
    tx = client.post("/api/transactions", json={"billing_customer_id": str(customer.id), "transaction_date": "2026-10-03"}, headers=owner).json()
    line = _add(client, owner, tx["id"], item_id=str(item.id))

    edited = client.patch(f"/api/transactions/{tx['id']}/lines/{line['id']}", json={"unit_price_ex_vat": "700.00"}, headers={**owner, "If-Match": f'"{line["version"]}"'})

    assert edited.status_code == 422 and edited.json()["detail"][0]["type"] == "line.catalog_value"


def test_a_new_billing_customer_or_date_reprices_catalog_lines_and_records_it(client: TestClient, db_session: Session):
    org, owner, item, customer, plain = _discounted_world(db_session, client)
    tx = client.post("/api/transactions", json={"billing_customer_id": str(plain.id), "transaction_date": "2026-10-03"}, headers=owner).json()
    line = _add(client, owner, tx["id"], item_id=str(item.id))
    adhoc = _add(client, owner, tx["id"], description="Special", unit="st", unit_price_ex_vat="100.00", vat_rate="25")
    assert line["unit_price_ex_vat"] == "800.00"

    current = client.get(f"/api/transactions/{tx['id']}", headers=owner).json()
    client.patch(f"/api/transactions/{tx['id']}", json={"billing_customer_id": str(customer.id)}, headers={**owner, "If-Match": f'"{current["header_version"]}"'})
    after_customer = {l["id"]: l for l in client.get(f"/api/transactions/{tx['id']}", headers=owner).json()["lines"]}
    current = client.get(f"/api/transactions/{tx['id']}", headers=owner).json()
    client.patch(f"/api/transactions/{tx['id']}", json={"transaction_date": "2026-10-20"}, headers={**owner, "If-Match": f'"{current["header_version"]}"'})
    after_date = {l["id"]: l for l in client.get(f"/api/transactions/{tx['id']}", headers=owner).json()["lines"]}

    assert after_customer[line["id"]]["unit_price_ex_vat"] == "720.00"  # + the customer's 10 %
    assert after_date[line["id"]]["unit_price_ex_vat"] == "900.00"  # the campaign is over; the customer's discount stays
    assert after_date[adhoc["id"]]["unit_price_ex_vat"] == "100.00"  # ad-hoc lines are never repriced
    events = client.get("/api/history", params={"entity_type": "transaction", "entity_id": tx["id"]}, headers=owner).json()["events"]
    assert any(e["entity_type"] == "transaction_line" and "customer_discount_percent" in e["changes"] for e in events)


def test_an_invoice_copies_the_layers_verbatim(client: TestClient, db_session: Session):
    org, owner, item, customer, _ = _discounted_world(db_session, client)
    tx = client.post("/api/transactions", json={"billing_customer_id": str(customer.id), "transaction_date": "2026-10-03"}, headers=owner).json()
    line = _add(client, owner, tx["id"], item_id=str(item.id))
    current = client.get(f"/api/transactions/{tx['id']}", headers=owner).json()
    client.post(f"/api/transactions/{tx['id']}/complete", headers={**owner, "If-Match": f'"{current["version"]}"'})

    invoice = client.post("/api/invoices", json={"transaction_ids": [tx["id"]]}, headers=owner).json()

    copied = invoice["lines"][0]
    assert (copied["list_unit_price"], copied["catalog_discount_percent"], copied["customer_discount_percent"], copied["unit_price_ex_vat"]) == (
        "1000.00", "20.00", "10.00", "720.00",
    )
    assert line["unit_price_ex_vat"] == "720.00"


def test_the_database_refuses_inconsistent_layers(db_session: Session):
    org = make_org(db_session)
    tx = make_transaction(db_session, org)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.execute(
                text("UPDATE transaction_lines SET list_unit_price = 1000, catalog_discount_percent = 20 WHERE transaction_id = :t"), {"t": tx.id}
            )


def test_the_items_current_discount_is_the_one_active_today(client: TestClient, db_session: Session, monkeypatch):
    from datetime import datetime, timezone

    from app.core import clock

    monkeypatch.setattr(clock, "utcnow", lambda: datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc))
    org, owner, item, _, _ = _discounted_world(db_session, client)
    read = client.get(f"/api/items/{item.id}", headers=owner).json()
    assert read["current_discount"]["percent"] == "20.00" and read["price_ex_vat"] == "1000.00"
    listed = [i for i in client.get("/api/items", headers=owner).json() if i["id"] == str(item.id)][0]
    assert listed["current_discount"]["ends_on"] == str(date(2026, 10, 7))


# --- the line's own discount (the last layer) --------------------------------------------------------------------------


def _patch_line(client, headers, tx_id, line, **body):
    response = client.patch(f"/api/transactions/{tx_id}/lines/{line['id']}", json=body, headers={**headers, "If-Match": f'"{line["version"]}"'})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_line_discount_is_the_last_layer_after_campaign_and_customer(client: TestClient, db_session: Session):
    org, owner, item, customer, _ = _discounted_world(db_session, client)
    tx = client.post("/api/transactions", json={"billing_customer_id": str(customer.id), "transaction_date": "2026-10-03"}, headers=owner).json()

    line = _add(client, owner, tx["id"], item_id=str(item.id), line_discount_percent="5")

    # 1000.00 -20 % = 800.00, -10 % = 720.00, -5 % = 684.00
    assert (line["list_unit_price"], line["catalog_discount_percent"], line["customer_discount_percent"], line["line_discount_percent"]) == (
        "1000.00", "20.00", "10.00", "5.00",
    )
    assert (line["unit_price_ex_vat"], line["priced_by_hand"]) == ("684.00", False)
    assert line["price_before_line_discount"] == "720.00"  # what the line's discount applies to


def test_an_ad_hoc_line_gets_its_line_discount_with_the_typed_price_as_list_price(client: TestClient, db_session: Session):
    org, owner, item, customer, _ = _discounted_world(db_session, client)
    tx = client.post("/api/transactions", json={"billing_customer_id": str(customer.id), "transaction_date": "2026-10-03"}, headers=owner).json()

    adhoc = _add(client, owner, tx["id"], description="Special", unit="st", unit_price_ex_vat="500.00", vat_rate="25", line_discount_percent="20")

    assert (adhoc["list_unit_price"], adhoc["line_discount_percent"], adhoc["unit_price_ex_vat"], adhoc["priced_by_hand"]) == ("500.00", "20.00", "400.00", True)
    assert adhoc["price_before_line_discount"] == "500.00"


def test_a_service_line_takes_a_line_discount_too(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    massage = make_item(db_session, org, name="Massage", price_ex_vat="850.00")
    anna = make_customer(db_session, org, "Anna Andersson")
    tx = client.post("/api/transactions", json={"billing_customer_id": str(anna.id)}, headers=owner).json()

    line = _add(client, owner, tx["id"], kind="service", item_id=str(massage.id), subject_type="customer", subject_id=str(anna.id), line_discount_percent="10")

    assert (line["unit_price_ex_vat"], line["line_discount_percent"]) == ("765.00", "10.00")


def test_a_line_discount_is_changed_and_removed_on_the_line_only(client: TestClient, db_session: Session):
    org, owner, item, customer, _ = _discounted_world(db_session, client)
    tx = client.post("/api/transactions", json={"billing_customer_id": str(customer.id), "transaction_date": "2026-10-03"}, headers=owner).json()
    catalog = _add(client, owner, tx["id"], item_id=str(item.id))
    other = _add(client, owner, tx["id"], item_id=str(item.id))
    adhoc = _add(client, owner, tx["id"], description="Special", unit="st", unit_price_ex_vat="500.00", vat_rate="25")

    catalog = _patch_line(client, owner, tx["id"], catalog, line_discount_percent="50")
    adhoc = _patch_line(client, owner, tx["id"], adhoc, line_discount_percent="10")
    assert (catalog["unit_price_ex_vat"], adhoc["unit_price_ex_vat"], adhoc["list_unit_price"]) == ("360.00", "450.00", "500.00")

    catalog = _patch_line(client, owner, tx["id"], catalog, line_discount_percent=None)
    adhoc = _patch_line(client, owner, tx["id"], adhoc, line_discount_percent=None)
    assert (catalog["unit_price_ex_vat"], catalog["line_discount_percent"]) == ("720.00", None)
    assert (adhoc["unit_price_ex_vat"], adhoc["list_unit_price"], adhoc["line_discount_percent"]) == ("500.00", None, None)
    lines = {l["id"]: l for l in client.get(f"/api/transactions/{tx['id']}", headers=owner).json()["lines"]}
    assert lines[other["id"]]["unit_price_ex_vat"] == "720.00"  # the other line of the same item is untouched


def test_a_new_customer_reprices_catalog_lines_with_their_line_discount_but_never_hand_priced_ones(client: TestClient, db_session: Session):
    org, owner, item, customer, plain = _discounted_world(db_session, client)
    tx = client.post("/api/transactions", json={"billing_customer_id": str(plain.id), "transaction_date": "2026-10-03"}, headers=owner).json()
    catalog = _add(client, owner, tx["id"], item_id=str(item.id), line_discount_percent="5")  # 1000 -20 % -5 % = 760.00
    hand = _add(client, owner, tx["id"], description="Special", unit="st", unit_price_ex_vat="500.00", vat_rate="25", line_discount_percent="10")  # 450.00

    current = client.get(f"/api/transactions/{tx['id']}", headers=owner).json()
    client.patch(f"/api/transactions/{tx['id']}", json={"billing_customer_id": str(customer.id)}, headers={**owner, "If-Match": f'"{current["header_version"]}"'})
    lines = {l["id"]: l for l in client.get(f"/api/transactions/{tx['id']}", headers=owner).json()["lines"]}

    assert (lines[catalog["id"]]["unit_price_ex_vat"], lines[catalog["id"]]["line_discount_percent"]) == ("684.00", "5.00")  # 800 -10 % -5 %
    assert (lines[hand["id"]]["unit_price_ex_vat"], lines[hand["id"]]["customer_discount_percent"]) == ("450.00", None)


def test_an_invoice_copies_the_line_discount(client: TestClient, db_session: Session):
    org, owner, item, customer, _ = _discounted_world(db_session, client)
    tx = client.post("/api/transactions", json={"billing_customer_id": str(customer.id), "transaction_date": "2026-10-03"}, headers=owner).json()
    _add(client, owner, tx["id"], item_id=str(item.id), line_discount_percent="5")
    current = client.get(f"/api/transactions/{tx['id']}", headers=owner).json()
    client.post(f"/api/transactions/{tx['id']}/complete", headers={**owner, "If-Match": f'"{current["version"]}"'})

    copied = client.post("/api/invoices", json={"transaction_ids": [tx["id"]]}, headers=owner).json()["lines"][0]

    assert (copied["line_discount_percent"], copied["unit_price_ex_vat"]) == ("5.00", "684.00")


def test_the_database_refuses_a_line_discount_that_does_not_match_the_price(db_session: Session):
    org = make_org(db_session)
    tx = make_transaction(db_session, org)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.execute(
                text("UPDATE transaction_lines SET list_unit_price = unit_price_ex_vat, line_discount_percent = 10 WHERE transaction_id = :t"), {"t": tx.id}
            )
