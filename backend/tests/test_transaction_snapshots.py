"""A line is a SNAPSHOT of the Item at the time it was added.

Required guarantees (approved with the Sales architecture):
1. Changing an Item's name, unit, price and VAT afterwards changes none of the four
   snapshotted values, nor any stored amount, on a line that already exists.
2. Two lines using the same Item may carry different manual description/unit/price/VAT
   overrides, and none of that modifies the Item itself.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

SNAPSHOT_FIELDS = ("description", "unit", "unit_price_ex_vat", "vat_rate")
AMOUNT_FIELDS = ("net_amount", "vat_amount", "gross_amount")
ALL_LINE_FIELDS = (*SNAPSHOT_FIELDS, *AMOUNT_FIELDS, "quantity", "item_id", "position", "updated_at")

ITEM_CHANGES = {
    "name": {"name": "Renamed massage"},
    "unit": {"unit": "hour"},
    "price": {"price_ex_vat": "999.99"},
    "vat": {"vat_rate": "6.00"},
    "all four": {"name": "Renamed massage", "unit": "hour", "price_ex_vat": "999.99", "vat_rate": "6.00"},
}


def create_tx(client: TestClient, sales, lines):
    response = client.post(
        "/api/transactions",
        json={"billing_customer_id": str(sales.billing.id), "lines": lines},
        headers=sales.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def stored_line(db: Session, line_id: str) -> tuple:
    """The line exactly as PostgreSQL holds it (numerics as text, so no Python rounding)."""
    return db.execute(
        text(
            "select description, unit, unit_price_ex_vat::text, vat_rate::text, quantity::text,"
            " net_amount::text, vat_amount::text, gross_amount::text, item_id::text, updated_at::text"
            " from transaction_lines where id = :id"
        ),
        {"id": line_id},
    ).one()


@pytest.mark.parametrize("change", ITEM_CHANGES.values(), ids=ITEM_CHANGES.keys())
@pytest.mark.parametrize("finalize", [False, True], ids=["draft", "completed"])
def test_editing_an_item_changes_nothing_on_existing_lines(
    client: TestClient, db_session: Session, sales, change, finalize
):
    tx = create_tx(client, sales, [{"item_id": str(sales.item.id), "quantity": "2.5"}])
    line = tx["lines"][0]
    assert (line["description"], line["unit"], line["unit_price_ex_vat"], line["vat_rate"]) == (
        "Horse massage", "session", "850.00", "25.00")
    assert (line["net_amount"], line["vat_amount"], line["gross_amount"]) == ("2125.00", "531.25", "2656.25")
    if finalize:
        client.post(f"/api/transactions/{tx['id']}/complete", headers=sales.headers)
    row_before = stored_line(db_session, line["id"])

    edit = client.patch(f"/api/items/{sales.item.id}", json=change, headers=sales.headers)
    assert edit.status_code == 200, edit.text

    # The Item really did change...
    item = client.get(f"/api/items/{sales.item.id}", headers=sales.headers).json()
    for field, value in change.items():
        assert item[field] == value
    # ...and the line did not: not through the API, and not in the database row.
    shown = client.get(f"/api/transactions/{tx['id']}", headers=sales.headers).json()
    after = shown["lines"][0]
    for field in ALL_LINE_FIELDS:
        assert after[field] == line[field], field
    assert stored_line(db_session, line["id"]) == row_before
    assert shown["totals"] == tx["totals"]  # computed before the item was edited
    assert shown["totals"]["gross_amount"] == "2656.25"


def test_all_four_values_and_stored_amounts_are_untouched_after_every_change_in_turn(
    client: TestClient, db_session: Session, sales
):
    tx = create_tx(client, sales, [{"item_id": str(sales.item.id), "quantity": "3"}])
    line = tx["lines"][0]
    row_before = stored_line(db_session, line["id"])

    for change in ITEM_CHANGES.values():
        client.patch(f"/api/items/{sales.item.id}", json=change, headers=sales.headers)
        assert stored_line(db_session, line["id"]) == row_before
    client.patch(f"/api/items/{sales.item.id}", json={"active": False}, headers=sales.headers)
    assert stored_line(db_session, line["id"]) == row_before


def test_lines_added_after_an_item_edit_get_the_new_values_while_old_lines_keep_the_old(
    client: TestClient, sales
):
    tx = create_tx(client, sales, [{"item_id": str(sales.item.id), "quantity": "1"}])

    client.patch(f"/api/items/{sales.item.id}", json=ITEM_CHANGES["all four"], headers=sales.headers)
    added = client.post(
        f"/api/transactions/{tx['id']}/lines",
        json={"item_id": str(sales.item.id), "quantity": "1"},
        headers=sales.headers,
    ).json()

    old, new = client.get(f"/api/transactions/{tx['id']}", headers=sales.headers).json()["lines"]
    assert (old["description"], old["unit"], old["unit_price_ex_vat"], old["vat_rate"]) == (
        "Horse massage", "session", "850.00", "25.00")
    assert (new["description"], new["unit"], new["unit_price_ex_vat"], new["vat_rate"]) == (
        "Renamed massage", "hour", "999.99", "6.00")
    assert new["id"] == added["id"]
    assert (new["net_amount"], new["vat_amount"], new["gross_amount"]) == ("999.99", "60.00", "1059.99")


def test_editing_a_line_never_edits_the_item(client: TestClient, sales):
    tx = create_tx(client, sales, [{"item_id": str(sales.item.id), "quantity": "1"}])
    before = client.get(f"/api/items/{sales.item.id}", headers=sales.headers).json()

    client.patch(
        f"/api/transactions/{tx['id']}/lines/{tx['lines'][0]['id']}",
        json={"description": "Changed", "unit": "x", "unit_price_ex_vat": "1.00", "vat_rate": "0.00"},
        headers=sales.headers,
    )

    assert client.get(f"/api/items/{sales.item.id}", headers=sales.headers).json() == before


def test_two_lines_using_the_same_item_may_carry_different_overrides(
    client: TestClient, db_session: Session, sales
):
    item_url = f"/api/items/{sales.item.id}"
    item_before = client.get(item_url, headers=sales.headers).json()
    item_row_before = db_session.execute(
        text("select name, unit, price_ex_vat::text, vat_rate::text, active, updated_at::text from items where id = :id"),
        {"id": str(sales.item.id)},
    ).one()
    item_id = str(sales.item.id)

    tx = create_tx(
        client,
        sales,
        [
            {"item_id": item_id, "quantity": "1"},  # pure defaults
            {  # overrides description, price and VAT
                "item_id": item_id,
                "quantity": "2",
                "description": "Massage, injured horse",
                "unit_price_ex_vat": "1000.00",
                "vat_rate": "12.00",
            },
            {  # overrides unit and price only
                "item_id": item_id,
                "quantity": "1.5",
                "unit": "hour",
                "unit_price_ex_vat": "600.00",
            },
            {  # overrides everything
                "item_id": item_id,
                "quantity": "1",
                "description": "Goodwill",
                "unit": "visit",
                "unit_price_ex_vat": "0.00",
                "vat_rate": "0.00",
            },
        ],
    )

    default, injured, hourly, goodwill = tx["lines"]
    assert [l["item_id"] for l in tx["lines"]] == [item_id] * 4

    assert (default["description"], default["unit"], default["unit_price_ex_vat"], default["vat_rate"]) == (
        "Horse massage", "session", "850.00", "25.00")
    assert (default["net_amount"], default["vat_amount"], default["gross_amount"]) == ("850.00", "212.50", "1062.50")

    assert (injured["description"], injured["unit"], injured["unit_price_ex_vat"], injured["vat_rate"]) == (
        "Massage, injured horse", "session", "1000.00", "12.00")
    assert (injured["net_amount"], injured["vat_amount"], injured["gross_amount"]) == ("2000.00", "240.00", "2240.00")

    assert (hourly["description"], hourly["unit"], hourly["unit_price_ex_vat"], hourly["vat_rate"]) == (
        "Horse massage", "hour", "600.00", "25.00")
    assert (hourly["net_amount"], hourly["vat_amount"], hourly["gross_amount"]) == ("900.00", "225.00", "1125.00")

    assert (goodwill["description"], goodwill["unit"], goodwill["unit_price_ex_vat"], goodwill["vat_rate"]) == (
        "Goodwill", "visit", "0.00", "0.00")
    assert (goodwill["net_amount"], goodwill["vat_amount"], goodwill["gross_amount"]) == ("0.00", "0.00", "0.00")

    # The Item is exactly as it was: through the API and in the database row (updated_at too).
    assert client.get(item_url, headers=sales.headers).json() == item_before
    db_session.expire_all()
    assert db_session.execute(
        text("select name, unit, price_ex_vat::text, vat_rate::text, active, updated_at::text from items where id = :id"),
        {"id": item_id},
    ).one() == item_row_before

    # The totals are the sums of those four different lines.
    assert tx["totals"]["net_amount"] == "3750.00"
    assert tx["totals"]["vat_amount"] == "677.50"
    assert tx["totals"]["vat_breakdown"] == [
        {"vat_rate": "0.00", "net_amount": "0.00", "vat_amount": "0.00"},
        {"vat_rate": "12.00", "net_amount": "2000.00", "vat_amount": "240.00"},
        {"vat_rate": "25.00", "net_amount": "1750.00", "vat_amount": "437.50"},
    ]


def test_overriding_one_line_later_leaves_the_other_lines_and_the_item_alone(
    client: TestClient, sales
):
    tx = create_tx(client, sales, [{"item_id": str(sales.item.id), "quantity": "1"}] * 2)
    item_before = client.get(f"/api/items/{sales.item.id}", headers=sales.headers).json()
    first, second = tx["lines"]

    client.patch(
        f"/api/transactions/{tx['id']}/lines/{first['id']}",
        json={"unit_price_ex_vat": "1.00", "vat_rate": "6.00", "description": "Override"},
        headers=sales.headers,
    )

    lines = client.get(f"/api/transactions/{tx['id']}", headers=sales.headers).json()["lines"]
    assert (lines[0]["description"], lines[0]["unit_price_ex_vat"], lines[0]["vat_rate"]) == ("Override", "1.00", "6.00")
    assert lines[1] == second
    assert client.get(f"/api/items/{sales.item.id}", headers=sales.headers).json() == item_before
