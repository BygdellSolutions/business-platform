"""Transactions inside one organization: headers, lines, calculation, lifecycle.

Cross-tenant isolation: test_tenant_isolation_contract.py and
test_transaction_lines_isolation.py. Item snapshots: test_transaction_snapshots.py.
"""

import datetime

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app.core.db import engine
from app.modules.sales.models import Transaction, TransactionLine
from tests.factories import make_customer, make_item, make_line, make_transaction

JSON = {"Content-Type": "application/json"}


def create(client: TestClient, sales, **fields):
    body = {"billing_customer_id": str(sales.billing.id), **fields}
    return client.post("/api/transactions", json=body, headers=sales.headers)


def from_item(sales, quantity="1", **overrides):
    return {"item_id": str(sales.item.id), "quantity": quantity, **overrides}


def ad_hoc(quantity="1", **overrides):
    return {
        "description": "Travel",
        "unit": "km",
        "quantity": quantity,
        "unit_price_ex_vat": "2.50",
        "vat_rate": "25.00",
        **overrides,
    }


# --- the milestone example and the totals --------------------------------------------------


def test_milestone_example_billing_customer_item_quantity_price_vat(client: TestClient, sales):
    response = create(client, sales, lines=[from_item(sales)])

    assert response.status_code == 201, response.text
    tx = response.json()
    assert tx["status"] == "draft"
    assert tx["billing_customer"] == {"id": str(sales.billing.id), "name": "Umeå HK", "active": True, "walk_in": False}
    assert tx["line_count"] == 1
    [line] = tx["lines"]
    assert line["item_id"] == str(sales.item.id)
    assert (line["description"], line["unit"]) == ("Horse massage", "session")
    assert line["quantity"] == "1.000"
    assert line["unit_price_ex_vat"] == "850.00"
    assert line["vat_rate"] == "25.00"
    assert (line["net_amount"], line["vat_amount"], line["gross_amount"]) == ("850.00", "212.50", "1062.50")
    assert tx["totals"] == {
        "net_amount": "850.00",
        "vat_amount": "212.50",
        "gross_amount": "1062.50",
        "vat_breakdown": [{"vat_rate": "25.00", "net_amount": "850.00", "vat_amount": "212.50"}],
    }
    assert "organization_id" not in tx and "organization_id" not in line


def test_totals_and_vat_breakdown_sum_the_stored_line_amounts(client: TestClient, sales):
    # 3 lines of net 0.02 at 25 %: stored VAT is 0.01 each (0.005 rounds up), so the
    # breakdown must say 0.03. Regrouping net (0.06 * 25 % = 0.015 -> 0.02) would be wrong.
    lines = [ad_hoc(unit_price_ex_vat="0.02", description=f"Tiny {i}") for i in range(3)]

    tx = create(client, sales, lines=lines).json()

    assert [line["vat_amount"] for line in tx["lines"]] == ["0.01"] * 3
    assert tx["totals"]["vat_amount"] == "0.03"
    assert tx["totals"]["vat_breakdown"] == [
        {"vat_rate": "25.00", "net_amount": "0.06", "vat_amount": "0.03"}
    ]
    assert tx["totals"]["gross_amount"] == "0.09"


def test_breakdown_groups_by_rate_in_ascending_order(client: TestClient, sales):
    lines = [
        from_item(sales, "2"),  # 1700.00 @ 25
        ad_hoc("1", unit_price_ex_vat="100.00", vat_rate="12.00"),
        ad_hoc("3", unit_price_ex_vat="10.00", vat_rate="25"),
        ad_hoc("1", unit_price_ex_vat="5.00", vat_rate="0.00"),
    ]

    tx = create(client, sales, lines=lines).json()

    assert tx["totals"]["vat_breakdown"] == [
        {"vat_rate": "0.00", "net_amount": "5.00", "vat_amount": "0.00"},
        {"vat_rate": "12.00", "net_amount": "100.00", "vat_amount": "12.00"},
        {"vat_rate": "25.00", "net_amount": "1730.00", "vat_amount": "432.50"},
    ]
    assert tx["totals"]["net_amount"] == "1835.00"
    assert tx["totals"]["vat_amount"] == "444.50"
    assert tx["totals"]["gross_amount"] == "2279.50"


def test_a_transaction_has_many_lines_in_a_stable_order(client: TestClient, sales):
    lines = [ad_hoc(description=name) for name in ("c", "a", "b")]

    tx = create(client, sales, lines=lines).json()

    assert [(l["position"], l["description"]) for l in tx["lines"]] == [(1, "c"), (2, "a"), (3, "b")]
    assert client.get(f"/api/transactions/{tx['id']}", headers=sales.headers).json() == tx


def test_a_transaction_may_start_without_lines(client: TestClient, sales):
    tx = create(client, sales).json()

    assert tx["lines"] == [] and tx["line_count"] == 0
    assert tx["totals"] == {
        "net_amount": "0.00",
        "vat_amount": "0.00",
        "gross_amount": "0.00",
        "vat_breakdown": [],
    }


def test_stored_amounts_equal_what_the_api_reports(client: TestClient, db_session: Session, sales):
    tx = create(client, sales, lines=[from_item(sales, "2.375"), ad_hoc("3", unit_price_ex_vat="19.99", vat_rate="12.00")]).json()

    rows = db_session.execute(
        text(
            "select net_amount::text, vat_amount::text, gross_amount::text, quantity::text"
            " from transaction_lines where transaction_id = :id order by position"
        ),
        {"id": tx["id"]},
    ).all()

    assert rows == [
        (l["net_amount"], l["vat_amount"], l["gross_amount"], l["quantity"]) for l in tx["lines"]
    ]


# --- creating: header ------------------------------------------------------------------------------


def test_transaction_date_defaults_to_today_and_can_be_set(client: TestClient, sales):
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()

    assert create(client, sales).json()["transaction_date"] == today
    assert create(client, sales, transaction_date="2026-03-04").json()["transaction_date"] == "2026-03-04"


@pytest.mark.parametrize("bad", ["2026-13-45", "not-a-date", "04/03/2026", 20260304, True])
def test_invalid_dates_are_rejected(client: TestClient, sales, bad):
    assert create(client, sales, transaction_date=bad).status_code == 422


@pytest.mark.parametrize(
    "extra",
    [
        {"status": "completed"},
        {"note": "deferred"},  # the header has no note
        {"net_amount": "1.00"},
        {"totals": {}},
        {"organization_id": "00000000-0000-4000-8000-0000000000b2"},
        {"invoice_id": "00000000-0000-4000-8000-0000000000b2"},
        {"unexpected": 1},
    ],
)
def test_header_rejects_unknown_and_client_supplied_fields(client: TestClient, sales, extra):
    assert create(client, sales, **extra).status_code == 422


def test_billing_customer_is_required_and_must_be_active(client: TestClient, db_session: Session, sales):
    assert client.post("/api/transactions", json={}, headers=sales.headers).status_code == 422
    assert client.post("/api/transactions", json={"billing_customer_id": None}, headers=sales.headers).status_code == 422

    inactive = make_customer(db_session, sales.org, "Gone AB", active=False)
    response = create(client, sales, billing_customer_id=str(inactive.id))

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "reference.inactive"


def test_creation_is_all_or_nothing(client: TestClient, db_session: Session, sales):
    before = db_session.scalar(select(func.count()).select_from(Transaction))
    lines = [from_item(sales), from_item(sales, item_id="00000000-0000-4000-8000-00000000dead")]

    response = create(client, sales, lines=lines)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "lines", 1, "item_id"]
    assert db_session.scalar(select(func.count()).select_from(Transaction)) == before


def test_a_request_may_carry_at_most_200_lines(client: TestClient, sales):
    assert create(client, sales, lines=[ad_hoc()] * 201).status_code == 422


# --- creating: lines, defaults and overrides ---------------------------------------------------------


def test_item_values_are_copied_and_cannot_be_typed_on_a_catalog_line(client: TestClient, sales):
    tx = create(client, sales, lines=[from_item(sales)]).json()
    a = tx["lines"][0]
    assert (a["description"], a["unit"], a["unit_price_ex_vat"], a["vat_rate"]) == ("Horse massage", "session", "850.00", "25.00")
    assert a["item_id"] == str(sales.item.id)

    for override in ({"description": "Massage, long"}, {"unit": "hour"}, {"unit_price_ex_vat": "0.00"}, {"vat_rate": "6.00"}):
        response = create(client, sales, lines=[from_item(sales, **override)])
        assert response.status_code == 422 and response.json()["detail"][0]["type"] == "line.catalog_value", override


def test_ad_hoc_lines_have_no_item_and_need_every_value(client: TestClient, sales):
    ok = create(client, sales, lines=[ad_hoc("2")]).json()["lines"][0]

    assert ok["item_id"] is None
    assert (ok["net_amount"], ok["vat_amount"]) == ("5.00", "1.25")

    for missing in ("description", "unit", "unit_price_ex_vat", "vat_rate"):
        line = {k: v for k, v in ad_hoc().items() if k != missing}
        assert create(client, sales, lines=[line]).status_code == 422, missing


def test_an_inactive_item_cannot_be_added_to_a_new_line(client: TestClient, db_session: Session, sales):
    old = make_item(db_session, sales.org, "Discontinued", active=False)

    response = create(client, sales, lines=[{"item_id": str(old.id), "quantity": "1"}])

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "reference.inactive"


def test_existing_lines_survive_their_item_being_deactivated(client: TestClient, sales):
    tx = create(client, sales, lines=[from_item(sales)]).json()

    client.patch(f"/api/items/{sales.item.id}", json={"active": False}, headers=sales.headers)

    shown = client.get(f"/api/transactions/{tx['id']}", headers=sales.headers).json()
    assert shown["lines"] == tx["lines"]
    assert client.post(f"/api/transactions/{tx['id']}/complete", headers=sales.headers).status_code == 200


# --- quantity, price and VAT use the strict decimal contract ---------------------------------------------


@pytest.mark.parametrize(
    "sent,shown",
    [("1", "1.000"), ("0.001", "0.001"), ("2.375", "2.375"), ("0.25", "0.250"), (5, "5.000"), ("100", "100.000")],
)
def test_quantity_roundtrips_exactly(client: TestClient, db_session: Session, sales, sent, shown):
    tx = create(client, sales, lines=[from_item(sales, sent)]).json()

    assert tx["lines"][0]["quantity"] == shown
    stored = db_session.scalar(
        text("select quantity::text from transaction_lines where id = :id"), {"id": tx["lines"][0]["id"]}
    )
    assert stored == shown


@pytest.mark.parametrize(
    "bad",
    ["0", "0.000", "-1", "-0.001", "1.0001", "1e1", "1E1", "NaN", "Infinity", "", " ", " 1", "1 ", "+1", "1,5", "1.", ".5",
     "abc", "1000000000", True, False, None, [], {}, 0, -1],
    ids=repr,
)
def test_invalid_quantities_are_rejected(client: TestClient, sales, bad):
    assert create(client, sales, lines=[from_item(sales, bad)]).status_code == 422


@pytest.mark.parametrize("raw", ["1.5", "1.0", "0.25", "2.375", "1e0"])
def test_json_numbers_with_decimals_are_rejected_for_quantity(client: TestClient, sales, raw):
    body = '{"billing_customer_id":"%s","lines":[{"item_id":"%s","quantity":%s}]}' % (
        sales.billing.id, sales.item.id, raw,
    )

    assert client.post("/api/transactions", content=body, headers={**sales.headers, **JSON}).status_code == 422


@pytest.mark.parametrize("field,raw", [("unit_price_ex_vat", "850.5"), ("unit_price_ex_vat", "0.1"), ("vat_rate", "25.0"), ("vat_rate", "6.5")])
def test_json_numbers_with_decimals_are_rejected_for_overrides(client: TestClient, sales, field, raw):
    body = '{"billing_customer_id":"%s","lines":[{"item_id":"%s","quantity":"1","%s":%s}]}' % (
        sales.billing.id, sales.item.id, field, raw,
    )

    assert client.post("/api/transactions", content=body, headers={**sales.headers, **JSON}).status_code == 422


@pytest.mark.parametrize(
    "override",
    [
        {"unit_price_ex_vat": "-0.01"},
        {"unit_price_ex_vat": "1.001"},
        {"unit_price_ex_vat": "10000000000.00"},
        {"unit_price_ex_vat": "1e2"},
        {"unit_price_ex_vat": True},
        {"vat_rate": "100.01"},
        {"vat_rate": "-1"},
        {"vat_rate": "25.555"},
        {"description": ""},
        {"description": "d" * 256},
        {"unit": ""},
        {"unit": "u" * 33},
        {"net_amount": "1.00"},  # amounts are never accepted
        {"vat_amount": "1.00"},
        {"gross_amount": "1.00"},
        {"position": 5},
        {"transaction_id": "00000000-0000-4000-8000-0000000000b2"},
        {"organization_id": "00000000-0000-4000-8000-0000000000b2"},
        {"unexpected": 1},
    ],
)
def test_line_validation(client: TestClient, sales, override):
    assert create(client, sales, lines=[from_item(sales, **override)]).status_code == 422


def test_a_line_amount_above_the_maximum_is_rejected_not_truncated(
    client: TestClient, db_session: Session, sales
):
    before = db_session.scalar(select(func.count()).select_from(Transaction))
    line = ad_hoc("999999999", unit_price_ex_vat="9999999999.99")

    response = create(client, sales, lines=[line])

    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["type"] == "amount.too_large" and error["loc"] == ["body", "lines", 0, "quantity"]
    assert db_session.scalar(select(func.count()).select_from(Transaction)) == before


# --- header update -------------------------------------------------------------------------------------------


def test_header_can_be_updated_while_draft(client: TestClient, db_session: Session, sales):
    other = make_customer(db_session, sales.org, "Anna Andersson")
    tx = create(client, sales, lines=[from_item(sales)]).json()

    response = client.patch(
        f"/api/transactions/{tx['id']}",
        json={"billing_customer_id": str(other.id), "transaction_date": "2026-05-06"},
        headers=sales.headers,
    )

    assert response.status_code == 200
    assert response.json()["billing_customer"]["name"] == "Anna Andersson"
    assert response.json()["transaction_date"] == "2026-05-06"
    assert response.json()["lines"] == tx["lines"]  # lines untouched


@pytest.mark.parametrize(
    "patch",
    [
        {"billing_customer_id": None},
        {"transaction_date": None},
        {"status": "completed"},
        {"note": "x"},
        {"lines": []},
        {"organization_id": "00000000-0000-4000-8000-0000000000b2"},
        {"transaction_date": "nonsense"},
    ],
)
def test_header_update_validation(client: TestClient, sales, patch):
    tx = create(client, sales).json()
    assert client.patch(f"/api/transactions/{tx['id']}", json=patch, headers=sales.headers).status_code == 422


def test_header_cannot_be_moved_to_an_inactive_billing_customer(client: TestClient, db_session: Session, sales):
    inactive = make_customer(db_session, sales.org, "Gone AB", active=False)
    tx = create(client, sales).json()

    response = client.patch(
        f"/api/transactions/{tx['id']}", json={"billing_customer_id": str(inactive.id)}, headers=sales.headers
    )

    assert response.status_code == 422 and response.json()["detail"][0]["type"] == "reference.inactive"


# --- list ---------------------------------------------------------------------------------------------------


def test_list_returns_summaries_with_totals_and_no_lines(client: TestClient, sales):
    create(client, sales, lines=[from_item(sales), ad_hoc()])

    [summary] = client.get("/api/transactions", headers=sales.headers).json()

    assert "lines" not in summary
    assert summary["line_count"] == 2
    assert summary["totals"]["net_amount"] == "852.50"
    assert summary["totals"]["vat_breakdown"] == [{"vat_rate": "25.00", "net_amount": "852.50", "vat_amount": "213.13"}]
    assert summary["status"] == "draft"


def test_list_filters_ordering_and_pagination(client: TestClient, db_session: Session, sales):
    anna = make_customer(db_session, sales.org, "Anna Andersson")
    first = make_transaction(db_session, sales.org, billing_customer=sales.billing, transaction_date=datetime.date(2026, 1, 10))
    second = make_transaction(db_session, sales.org, billing_customer=anna, transaction_date=datetime.date(2026, 2, 10), status="completed")
    third = make_transaction(db_session, sales.org, billing_customer=sales.billing, transaction_date=datetime.date(2026, 3, 10), status="cancelled")

    def ids(**params):
        response = client.get("/api/transactions", params=params, headers=sales.headers)
        assert response.status_code == 200, response.text
        return [t["id"] for t in response.json()]

    assert ids() == [str(third.id), str(second.id), str(first.id)]  # newest first
    assert ids(status="completed") == [str(second.id)]
    assert ids(status="draft") == [str(first.id)]
    assert ids(billing_customer_id=str(sales.billing.id)) == [str(third.id), str(first.id)]
    assert ids(date_from="2026-02-01") == [str(third.id), str(second.id)]
    assert ids(date_to="2026-02-10") == [str(second.id), str(first.id)]
    assert ids(date_from="2026-02-01", date_to="2026-02-28") == [str(second.id)]
    assert ids(status="completed", billing_customer_id=str(sales.billing.id)) == []
    assert ids(limit=1, offset=1) == [str(second.id)]
    for bad in ({"status": "invoiced"}, {"date_from": "x"}, {"billing_customer_id": "x"}, {"limit": 201}, {"limit": 0}):
        assert client.get("/api/transactions", params=bad, headers=sales.headers).status_code == 422


def test_the_list_has_no_text_search(client: TestClient, db_session: Session, sales):
    make_transaction(db_session, sales.org, billing_customer=sales.billing)

    # `q` is not a parameter of this endpoint (the header has no searchable text); it is ignored.
    everything = client.get("/api/transactions", headers=sales.headers).json()
    with_q = client.get("/api/transactions", params={"q": "zzz"}, headers=sales.headers).json()

    assert with_q == everything


# --- lines: add, change, remove ----------------------------------------------------------------------------------


def test_add_update_and_delete_lines(client: TestClient, sales):
    tx = create(client, sales, lines=[from_item(sales)]).json()
    base = f"/api/transactions/{tx['id']}"

    added = client.post(f"{base}/lines", json=ad_hoc("4"), headers=sales.headers)
    assert added.status_code == 201
    line = added.json()
    assert line["position"] == 2 and line["net_amount"] == "10.00"

    updated = client.patch(
        f"{base}/lines/{line['id']}",
        json={"quantity": "10", "unit_price_ex_vat": "3.00", "vat_rate": "12.00"},
        headers=sales.headers,
    )
    assert updated.status_code == 200
    assert updated.json()["position"] == 2  # unchanged
    assert (updated.json()["net_amount"], updated.json()["vat_amount"], updated.json()["gross_amount"]) == ("30.00", "3.60", "33.60")
    assert updated.json()["description"] == "Travel"  # untouched

    shown = client.get(base, headers=sales.headers).json()
    assert shown["totals"]["net_amount"] == "880.00"
    assert shown["totals"]["vat_amount"] == "216.10"

    assert client.delete(f"{base}/lines/{line['id']}", headers=sales.headers).status_code == 204
    after = client.get(base, headers=sales.headers).json()
    assert [l["position"] for l in after["lines"]] == [1]
    assert after["totals"]["net_amount"] == "850.00"


def test_new_lines_continue_the_position_sequence(client: TestClient, sales):
    tx = create(client, sales, lines=[ad_hoc(), ad_hoc()]).json()
    base = f"/api/transactions/{tx['id']}"

    third = client.post(f"{base}/lines", json=ad_hoc(), headers=sales.headers).json()
    client.delete(f"{base}/lines/{tx['lines'][0]['id']}", headers=sales.headers)
    fourth = client.post(f"{base}/lines", json=ad_hoc(), headers=sales.headers).json()

    assert (third["position"], fourth["position"]) == (3, 4)  # positions are never reused


def test_changing_a_lines_item_recopies_the_new_items_values(client: TestClient, db_session: Session, sales):
    other = make_item(db_session, sales.org, "Saddle fitting", unit="hour", price_ex_vat="600.00", vat_rate="12.00")
    tx = create(client, sales, lines=[from_item(sales)]).json()
    line_url = f"/api/transactions/{tx['id']}/lines/{tx['lines'][0]['id']}"

    swapped = client.patch(line_url, json={"item_id": str(other.id)}, headers=sales.headers).json()

    assert swapped["item_id"] == str(other.id)
    assert (swapped["description"], swapped["unit"], swapped["unit_price_ex_vat"], swapped["vat_rate"]) == (
        "Saddle fitting", "hour", "600.00", "12.00")
    assert (swapped["net_amount"], swapped["vat_amount"]) == ("600.00", "72.00")

    # a typed value is refused with the item change, as it is when a catalog line is added
    again = client.patch(line_url, json={"item_id": str(sales.item.id), "unit_price_ex_vat": "1.00"}, headers=sales.headers)
    assert again.status_code == 422 and again.json()["detail"][0]["type"] == "line.catalog_value"


def test_a_line_never_changes_kind(client: TestClient, sales):
    tx = create(client, sales, lines=[from_item(sales), ad_hoc("1")]).json()
    catalog_url = f"/api/transactions/{tx['id']}/lines/{tx['lines'][0]['id']}"
    adhoc_url = f"/api/transactions/{tx['id']}/lines/{tx['lines'][1]['id']}"

    detached = client.patch(catalog_url, json={"item_id": None}, headers=sales.headers)
    attached = client.patch(adhoc_url, json={"item_id": str(sales.item.id)}, headers=sales.headers)

    assert detached.status_code == 422 and detached.json()["detail"][0]["type"] == "line.kind_change"
    assert attached.status_code == 422 and attached.json()["detail"][0]["type"] == "line.kind_change"
    assert client.patch(adhoc_url, json={"description": "Still ad hoc", "unit_price_ex_vat": "9.00"}, headers=sales.headers).status_code == 200


def test_changing_only_the_quantity_recalculates_the_amounts(client: TestClient, sales):
    tx = create(client, sales, lines=[from_item(sales, "1")]).json()
    line_url = f"/api/transactions/{tx['id']}/lines/{tx['lines'][0]['id']}"

    half = client.patch(line_url, json={"quantity": "0.5"}, headers=sales.headers).json()

    assert (half["net_amount"], half["vat_amount"], half["gross_amount"]) == ("425.00", "106.25", "531.25")


@pytest.mark.parametrize(
    "patch",
    [
        {"quantity": None},
        {"quantity": "0"},
        {"quantity": 1.5},
        {"description": None},
        {"description": ""},
        {"unit": None},
        {"unit_price_ex_vat": None},
        {"unit_price_ex_vat": "-1.00"},
        {"vat_rate": None},
        {"vat_rate": "100.01"},
        {"net_amount": "1.00"},
        {"position": 9},
        {"transaction_id": "00000000-0000-4000-8000-0000000000b2"},
        {"organization_id": "00000000-0000-4000-8000-0000000000b2"},
    ],
)
def test_line_update_validation(client: TestClient, sales, patch):
    tx = create(client, sales, lines=[from_item(sales)]).json()
    line_url = f"/api/transactions/{tx['id']}/lines/{tx['lines'][0]['id']}"

    assert client.patch(line_url, json=patch, headers=sales.headers).status_code == 422


def test_a_line_update_that_overflows_changes_nothing(client: TestClient, sales):
    tx = create(client, sales, lines=[from_item(sales)]).json()
    line_url = f"/api/transactions/{tx['id']}/lines/{tx['lines'][0]['id']}"

    response = client.patch(line_url, json={"quantity": "999999999"}, headers=sales.headers)

    assert response.status_code == 422
    assert client.get(f"/api/transactions/{tx['id']}", headers=sales.headers).json()["lines"] == tx["lines"]


def test_a_line_in_the_path_must_belong_to_that_transaction(client: TestClient, sales):
    one = create(client, sales, lines=[ad_hoc()]).json()
    two = create(client, sales, lines=[ad_hoc()]).json()

    mismatched = f"/api/transactions/{one['id']}/lines/{two['lines'][0]['id']}"

    assert client.patch(mismatched, json={"quantity": "2"}, headers=sales.headers).status_code == 404
    assert client.delete(mismatched, headers=sales.headers).status_code == 404


# --- lifecycle ----------------------------------------------------------------------------------------------------


def act(client: TestClient, sales, tx_id, action):
    return client.post(f"/api/transactions/{tx_id}/{action}", headers=sales.headers)


def test_complete_reopen_and_cancel(client: TestClient, sales):
    tx = create(client, sales, lines=[from_item(sales)]).json()

    completed = act(client, sales, tx["id"], "complete")
    assert completed.status_code == 200 and completed.json()["status"] == "completed"
    assert completed.json()["lines"] == tx["lines"]  # amounts frozen as they were

    reopened = act(client, sales, tx["id"], "reopen")
    assert reopened.status_code == 200 and reopened.json()["status"] == "draft"

    assert act(client, sales, tx["id"], "complete").json()["status"] == "completed"
    cancelled = act(client, sales, tx["id"], "cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"


def test_a_draft_can_be_cancelled_directly(client: TestClient, sales):
    tx = create(client, sales).json()
    assert act(client, sales, tx["id"], "cancel").json()["status"] == "cancelled"


def test_a_transaction_needs_a_line_to_be_completed(client: TestClient, sales):
    tx = create(client, sales).json()

    response = act(client, sales, tx["id"], "complete")

    assert response.status_code == 409
    assert client.get(f"/api/transactions/{tx['id']}", headers=sales.headers).json()["status"] == "draft"


@pytest.mark.parametrize(
    "start,action",
    [
        ("draft", "reopen"),
        ("completed", "complete"),
        ("cancelled", "complete"),
        ("cancelled", "reopen"),
        ("cancelled", "cancel"),
    ],
)
def test_invalid_transitions_are_409(client: TestClient, db_session: Session, sales, start, action):
    tx = make_transaction(db_session, sales.org, billing_customer=sales.billing, status=start)

    response = act(client, sales, tx.id, action)

    assert response.status_code == 409
    db_session.refresh(tx)
    assert tx.status == start


def test_status_values_are_lifecycle_states_only(client: TestClient, sales):
    # There is deliberately no "invoiced" state, and status cannot be set directly.
    tx = create(client, sales, lines=[ad_hoc()]).json()

    assert client.patch(f"/api/transactions/{tx['id']}", json={"status": "invoiced"}, headers=sales.headers).status_code == 422
    assert client.get("/api/transactions", params={"status": "invoiced"}, headers=sales.headers).status_code == 422
    assert act(client, sales, tx["id"], "invoice").status_code in (404, 405)


@pytest.mark.parametrize("status", ["completed", "cancelled"])
def test_only_drafts_can_be_changed(client: TestClient, db_session: Session, sales, status):
    tx = make_transaction(db_session, sales.org, billing_customer=sales.billing, status=status)
    line = tx_line = db_session.scalar(select(TransactionLine).where(TransactionLine.transaction_id == tx.id))
    base = f"/api/transactions/{tx.id}"
    before = client.get(base, headers=sales.headers).json()

    responses = [
        client.patch(base, json={"transaction_date": "2026-12-24"}, headers=sales.headers),
        client.post(f"{base}/lines", json=ad_hoc(), headers=sales.headers),
        client.patch(f"{base}/lines/{line.id}", json={"quantity": "2"}, headers=sales.headers),
        client.delete(f"{base}/lines/{line.id}", headers=sales.headers),
        client.delete(base, headers=sales.headers),
    ]

    assert [r.status_code for r in responses] == [409] * 5
    assert client.get(base, headers=sales.headers).json() == before  # nothing moved


def test_a_completed_transaction_must_be_reopened_to_be_edited(client: TestClient, sales):
    tx = create(client, sales, lines=[from_item(sales)]).json()
    act(client, sales, tx["id"], "complete")
    base = f"/api/transactions/{tx['id']}"

    blocked = client.post(f"{base}/lines", json=ad_hoc(), headers=sales.headers)
    assert blocked.status_code == 409 and "reopen" in blocked.json()["detail"]

    act(client, sales, tx["id"], "reopen")
    assert client.post(f"{base}/lines", json=ad_hoc(), headers=sales.headers).status_code == 201


def test_only_drafts_can_be_deleted_and_their_lines_go_with_them(
    client: TestClient, db_session: Session, sales
):
    tx = create(client, sales, lines=[from_item(sales), ad_hoc()]).json()

    assert client.delete(f"/api/transactions/{tx['id']}", headers=sales.headers).status_code == 204

    assert client.get(f"/api/transactions/{tx['id']}", headers=sales.headers).status_code == 404
    assert db_session.scalar(select(func.count()).select_from(TransactionLine).where(TransactionLine.transaction_id == tx["id"])) == 0


def test_a_completed_transaction_is_cancelled_not_deleted(client: TestClient, sales):
    tx = create(client, sales, lines=[ad_hoc()]).json()
    act(client, sales, tx["id"], "complete")

    response = client.delete(f"/api/transactions/{tx['id']}", headers=sales.headers)

    assert response.status_code == 409 and "cancel" in response.json()["detail"]


def test_lifecycle_actions_are_posts_with_no_body(client: TestClient, sales):
    tx = create(client, sales, lines=[ad_hoc()]).json()
    assert client.get(f"/api/transactions/{tx['id']}/complete", headers=sales.headers).status_code == 405


# --- concurrency -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda c, h, base, line: c.patch(base, json={"transaction_date": "2026-12-24"}, headers=h),
        lambda c, h, base, line: c.post(f"{base}/lines", json=ad_hoc(), headers=h),
        lambda c, h, base, line: c.patch(f"{base}/lines/{line}", json={"quantity": "2"}, headers=h),
        lambda c, h, base, line: c.delete(f"{base}/lines/{line}", headers=h),
        lambda c, h, base, line: c.post(f"{base}/complete", headers=h),
        lambda c, h, base, line: c.delete(base, headers=h),
    ],
    ids=["patch header", "add line", "patch line", "delete line", "complete", "delete"],
)
def test_every_mutation_locks_the_transaction_row_first(client: TestClient, sales, call):
    tx = create(client, sales, lines=[from_item(sales)]).json()
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        response = call(client, sales.headers, f"/api/transactions/{tx['id']}", tx["lines"][0]["id"])
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert response.status_code < 300
    locks = [i for i, s in enumerate(statements) if "FOR UPDATE" in s and "FROM transactions" in s]
    writes = [i for i, s in enumerate(statements) if s.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))]
    assert locks, "the transaction row was never locked"
    assert not writes or min(locks) < min(writes), "a write happened before the lock"
