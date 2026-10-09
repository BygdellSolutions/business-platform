"""Return cases on issued invoices: opened with lines, a reason and a follow-up date; goods received (into stock where
ticked); approved and closed by the credit note made for it, or rejected with a reason; a log of every step and note.
The dashboard counts open cases and those to follow up; the list finds invoices with an open case.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.org_time import today_in
from app.models import ItemType, Role
from tests.factories import make_item
from tests.invoicing_support import Two, completed, draft_invoice, issue, member_of

TEN = [{"description": "Liniment", "unit": "pcs", "quantity": "10", "unit_price_ex_vat": "100.00", "vat_rate": "25.00"}]


def _issued(client: TestClient, db_session: Session, sales, lines=TEN):
    return issue(client, sales.headers, draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing, lines=lines)))


def _open(client: TestClient, headers, invoice, quantity="2", follow_up=None, reason="Wrong size"):
    body = {
        "reason": reason,
        "follow_up_on": str(follow_up or today_in(None) + timedelta(days=7)),
        "lines": [{"invoice_line_id": invoice["lines"][0]["id"], "quantity": quantity}],
    }
    return client.post(f"/api/invoices/{invoice['id']}/returns", json=body, headers=headers)


def _step(client: TestClient, headers, invoice, case_id, step, body=None):
    return client.post(f"/api/invoices/{invoice['id']}/returns/{case_id}/{step}", json=body or {}, headers=headers)


def test_a_return_goes_from_requested_to_received_to_approved_and_its_credit_note_closes_it(client: TestClient, db_session: Session, sales):
    invoice = _issued(client, db_session, sales)

    opened = _open(client, sales.headers, invoice)
    assert opened.status_code == 201, opened.text
    case = opened.json()["returns"][0]
    assert (case["state"], case["reason"], opened.json()["open_returns"]) == ("requested", "Wrong size", 1)
    assert [(l["description"], l["quantity"], l["returned_to_stock"]) for l in case["lines"]] == [("Liniment", "2.000", False)]

    assert _step(client, sales.headers, invoice, case["id"], "notes", {"note": "Customer posts them Monday"}).status_code == 201
    received = _step(client, sales.headers, invoice, case["id"], "goods-received", {"note": "Two boxes, unopened"})
    approved = _step(client, sales.headers, invoice, case["id"], "approve")
    assert received.json()["returns"][0]["state"] == "goods_received" and approved.json()["returns"][0]["state"] == "approved"
    assert _step(client, sales.headers, invoice, case["id"], "goods-received").json()["detail"]["code"] == "return_state"

    body = {"reason": "Return: wrong size", "return_id": case["id"], "lines": [{"invoice_line_id": invoice["lines"][0]["id"], "quantity": "2"}]}
    note = client.post(f"/api/invoices/{invoice['id']}/credit-notes", json=body, headers=sales.headers)
    assert note.status_code == 201, note.text

    after = client.get(f"/api/invoices/{invoice['id']}", headers=sales.headers).json()
    closed = after["returns"][0]
    assert (closed["state"], closed["credit_note_id"], after["open_returns"]) == ("credited", note.json()["id"], 0)
    assert [e["kind"] for e in closed["events"]] == ["opened", "note", "goods_received", "approved", "credited"]
    assert all(e["created_by_name"] for e in closed["events"])
    again = client.post(f"/api/invoices/{invoice['id']}/credit-notes", json=body, headers=sales.headers)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "return_state"


def test_a_credit_note_for_a_case_that_is_not_approved_is_refused_and_nothing_is_written(client: TestClient, db_session: Session, sales):
    invoice = _issued(client, db_session, sales)
    case = _open(client, sales.headers, invoice).json()["returns"][0]
    body = {"reason": "x", "return_id": case["id"], "lines": [{"invoice_line_id": invoice["lines"][0]["id"], "quantity": "1"}]}

    response = client.post(f"/api/invoices/{invoice['id']}/credit-notes", json=body, headers=sales.headers)

    assert response.status_code == 409
    assert client.get(f"/api/invoices/{invoice['id']}", headers=sales.headers).json()["credit_notes"] == []


def test_a_rejection_needs_a_reason_and_closes_the_case(client: TestClient, db_session: Session, sales):
    invoice = _issued(client, db_session, sales)
    case = _open(client, sales.headers, invoice).json()["returns"][0]

    assert _step(client, sales.headers, invoice, case["id"], "reject", {"reason": " "}).status_code == 422
    rejected = _step(client, sales.headers, invoice, case["id"], "reject", {"reason": "Used, not resellable"}).json()

    assert (rejected["returns"][0]["state"], rejected["returns"][0]["rejection_reason"], rejected["open_returns"]) == ("rejected", "Used, not resellable", 0)
    assert _step(client, sales.headers, invoice, case["id"], "approve").status_code == 409
    follow = client.patch(f"/api/invoices/{invoice['id']}/returns/{case['id']}", json={"follow_up_on": str(today_in(None))}, headers=sales.headers)
    assert follow.status_code == 409


def test_what_a_return_may_ask_for(client: TestClient, db_session: Session, sales):
    invoice = _issued(client, db_session, sales)
    other = _issued(client, db_session, sales)
    client.post(
        f"/api/invoices/{invoice['id']}/credit-notes",
        json={"reason": "x", "lines": [{"invoice_line_id": invoice["lines"][0]["id"], "quantity": "9"}]},
        headers=sales.headers,
    )
    draft = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing))

    too_much = _open(client, sales.headers, invoice, quantity="2")
    foreign_line = client.post(
        f"/api/invoices/{invoice['id']}/returns",
        json={"reason": "x", "follow_up_on": str(today_in(None)), "lines": [{"invoice_line_id": other["lines"][0]["id"], "quantity": "1"}]},
        headers=sales.headers,
    )
    on_draft = _open(client, sales.headers, draft)

    assert too_much.status_code == 422 and "Only 1 of this line" in too_much.json()["detail"][0]["msg"]
    assert foreign_line.json()["detail"][0]["type"] == "return.line_not_on_invoice"
    assert on_draft.status_code == 409
    assert _open(client, sales.headers, invoice, quantity="1").status_code == 201


def test_goods_received_returns_ticked_lines_to_stock_at_most_what_was_delivered(client: TestClient, db_session: Session, sales):
    product = make_item(db_session, sales.org, name="Liniment", type=ItemType.PRODUCT, unit="pcs", price_ex_vat="120.00", track_stock=True)
    assert client.post(f"/api/items/{product.id}/stock", json={"kind": "count", "quantity": "5", "note": "Count"}, headers=sales.headers).status_code == 201
    line = {"item": product, "description": "Liniment", "unit": "pcs", "quantity": "3", "unit_price_ex_vat": "120.00", "vat_rate": "25.00"}
    tx = completed(db_session, sales.org, sales.billing, lines=[line], status="draft")
    assert client.post(f"/api/transactions/{tx.id}/complete", headers=sales.headers).status_code == 200
    invoice = issue(client, sales.headers, draft_invoice(client, sales.headers, tx))
    case = _open(client, sales.headers, invoice, quantity="2").json()["returns"][0]

    received = _step(client, sales.headers, invoice, case["id"], "goods-received", {"to_stock": [invoice["lines"][0]["id"]]})

    assert received.status_code == 200, received.text
    assert received.json()["returns"][0]["lines"][0]["returned_to_stock"] is True
    assert received.json()["lines"][0]["stock_returnable"] == "1.000"
    assert client.get(f"/api/items/{product.id}/stock", headers=sales.headers).json()["on_hand"] == "4.000"


def test_an_untracked_line_cannot_go_back_into_stock(client: TestClient, db_session: Session, sales):
    invoice = _issued(client, db_session, sales)
    case = _open(client, sales.headers, invoice).json()["returns"][0]
    response = _step(client, sales.headers, invoice, case["id"], "goods-received", {"to_stock": [invoice["lines"][0]["id"]]})
    assert response.status_code == 422 and response.json()["detail"][0]["type"] == "stock.not_tracked"
    assert client.get(f"/api/invoices/{invoice['id']}", headers=sales.headers).json()["returns"][0]["state"] == "requested"


def test_the_dashboard_and_the_list_find_open_returns_and_those_to_follow_up(client: TestClient, db_session: Session, sales):
    today = today_in(None)
    due = _issued(client, db_session, sales)
    later = _issued(client, db_session, sales)
    closed = _issued(client, db_session, sales)
    _open(client, sales.headers, due, follow_up=today - timedelta(days=1))
    case = _open(client, sales.headers, later, follow_up=today + timedelta(days=5)).json()["returns"][0]
    rejected = _open(client, sales.headers, closed, follow_up=today - timedelta(days=3)).json()["returns"][0]
    _step(client, sales.headers, closed, rejected["id"], "reject", {"reason": "No"})

    def ids(**params):
        return {row["id"] for row in client.get("/api/invoices", params=params, headers=sales.headers).json()}

    assert ids(returns="open") == {due["id"], later["id"]}
    assert ids(returns="follow_up_due") == {due["id"]}
    summary = client.get("/api/invoices/summary", headers=sales.headers).json()
    assert (summary["returns_open"], summary["returns_follow_up_due"]) == (2, 1)

    moved = client.patch(f"/api/invoices/{later['id']}/returns/{case['id']}", json={"follow_up_on": str(today)}, headers=sales.headers).json()
    assert moved["returns"][0]["events"][-1]["kind"] == "follow_up"
    assert ids(returns="follow_up_due") == {due["id"], later["id"]}
    listed = next(row for row in client.get("/api/invoices", headers=sales.headers).json() if row["id"] == due["id"])
    assert listed["open_returns"] == 1


@pytest.mark.parametrize("role,allowed", [(Role.ACCOUNTANT, True), (Role.EMPLOYEE, False), (Role.VIEWER, False)])
def test_who_may_handle_returns(client: TestClient, db_session: Session, sales, role, allowed):
    invoice = _issued(client, db_session, sales)
    headers = member_of(db_session, sales.org, role)
    assert _open(client, headers, invoice).status_code == (201 if allowed else 403)


def test_another_organizations_return_is_out_of_reach(client: TestClient, db_session: Session):
    two = Two(db_session)
    b_invoice = issue(client, two.b, draft_invoice(client, two.b, completed(db_session, two.b_org, two.b_customer)))
    case = _open(client, two.b, b_invoice, quantity="1").json()["returns"][0]

    assert _open(client, two.a, b_invoice, quantity="1").status_code == 404
    for step in ("goods-received", "approve", "notes"):
        assert _step(client, two.a, b_invoice, case["id"], step, {"note": "x"}).status_code == 404
    assert _step(client, two.a, b_invoice, case["id"], "reject", {"reason": "x"}).status_code == 404
    assert client.get(f"/api/invoices/{b_invoice['id']}", headers=two.b).json()["returns"][0]["state"] == "requested"
