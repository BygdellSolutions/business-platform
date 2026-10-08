"""Slice 13: the dashboard's figures, each from the module that owns them.

Every figure counts the active organization's records only, amounts are summed per currency (never across), and
"this month" is the organization's month in its time zone.
"""

from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.org_time import today_in
from app.models import ItemType, Role
from tests.factories import add_member, make_customer, make_item, make_line, make_org, make_transaction, make_user
from tests.invoicing_support import completed, draft_invoice, issue


def _world(db: Session):
    org = make_org(db)
    user = make_user(db)
    add_member(db, org, user, Role.OWNER)
    return org, {"X-Dev-User-Email": user.email}


def _sale(db: Session, org, *, status="completed", on=None, currency="org", gross="850.00", kind="standard"):
    tx = completed(db, org, status=status, transaction_date=on or today_in(None), currency=currency, lines=[])
    extra = {}
    if kind == "service":
        customer = make_customer(db, org, "Anna Andersson")
        extra = dict(kind="service", performed_at=tx.created_at, subject_type="customer", subject_id=customer.id, item=make_item(db, org, "Massage"))
    make_line(db, org, tx, unit_price_ex_vat=Decimal(gross) / Decimal("1.25"), **extra)
    return tx


def test_sales_counts_drafts_and_this_months_completed_sales_per_currency(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    other, _ = _world(db_session)
    today = today_in(None)
    _sale(db_session, org, status="draft")
    _sale(db_session, org)
    _sale(db_session, org, currency="EUR")
    _sale(db_session, org, on=today.replace(day=1) - timedelta(days=1))  # last month
    _sale(db_session, org, status="draft", kind="service")
    _sale(db_session, other)  # another organization

    summary = client.get("/api/transactions/summary", headers=owner).json()

    assert summary["drafts"] == 2 and summary["services_this_month"] == 1
    assert summary["completed_this_month"]["count"] == 2
    assert summary["completed_this_month"]["amounts"] == [{"currency": "EUR", "amount": "850.00"}, {"currency": "SEK", "amount": "850.00"}]
    assert summary["month_start"] == str(today.replace(day=1))


def test_invoicing_shows_what_is_ready_drafts_issued_and_past_due(client: TestClient, db_session: Session, sales):
    today = today_in(None)
    ready = completed(db_session, sales.org, sales.billing)
    drafted = completed(db_session, sales.org, sales.billing)
    issued_now = completed(db_session, sales.org, sales.billing)
    overdue = completed(db_session, sales.org, sales.billing)
    draft_invoice(client, sales.headers, drafted)
    issue(client, sales.headers, draft_invoice(client, sales.headers, issued_now, invoice_date=str(today)))
    long_ago = today.replace(day=1) - timedelta(days=40)
    issue(client, sales.headers, draft_invoice(client, sales.headers, overdue, invoice_date=str(long_ago), due_date=str(long_ago + timedelta(days=10))))

    summary = client.get("/api/invoices/summary", headers=sales.headers).json()

    assert summary["ready_to_invoice"]["count"] == 1 and len(summary["ready_to_invoice"]["amounts"]) == 1
    assert summary["draft_invoices"] == 1
    assert summary["issued_this_month"]["count"] == 1
    assert summary["past_due"]["count"] == 1
    assert ready.id  # the one completed transaction that is on no invoice


def test_inventory_counts_states_backorders_and_incoming(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    empty = make_item(db_session, org, name="Empty", type=ItemType.PRODUCT, unit="pcs", track_stock=True)
    low = make_item(db_session, org, name="Low", type=ItemType.PRODUCT, unit="pcs", track_stock=True, low_stock_threshold=Decimal("5"))
    make_item(db_session, org, name="Untracked", type=ItemType.PRODUCT)
    client.post(f"/api/items/{low.id}/stock", json={"kind": "count", "quantity": "2"}, headers=owner)
    client.post("/api/inventory/incoming", json={"item_id": str(empty.id), "quantity": "10"}, headers=owner)
    tx = make_transaction(db_session, org, billing_customer=make_customer(db_session, org))
    make_line(db_session, org, tx, item=empty, description="Empty", unit="pcs", quantity="3", unit_price_ex_vat="10.00")
    client.post(f"/api/transactions/{tx.id}/complete", headers=owner)

    summary = client.get("/api/inventory/summary", headers=owner).json()

    assert summary == {"tracked_items": 2, "out_of_stock": 1, "low_stock": 1, "open_backorders": 1, "backordered_items": 1, "incoming_deliveries": 1}


def test_another_organizations_records_never_count(client: TestClient, db_session: Session):
    _, owner = _world(db_session)
    other, other_owner = _world(db_session)
    _sale(db_session, other)
    _sale(db_session, other, status="draft")
    make_item(db_session, other, name="Theirs", type=ItemType.PRODUCT, track_stock=True)

    sales = client.get("/api/transactions/summary", headers=owner).json()
    invoicing = client.get("/api/invoices/summary", headers=owner).json()
    inventory = client.get("/api/inventory/summary", headers=owner).json()

    assert (sales["drafts"], sales["completed_this_month"]["count"]) == (0, 0)
    assert invoicing["ready_to_invoice"] == {"count": 0, "amounts": []}
    assert inventory["tracked_items"] == 0
    assert client.get("/api/transactions/summary", headers=other_owner).json()["drafts"] == 1


# --- choosing a month, and pending money -----------------------------------------------------------------------------


def test_a_past_month_covers_all_its_days_and_a_future_month_is_refused(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    today = today_in(None)
    last_month_end = today.replace(day=1) - timedelta(days=1)
    _sale(db_session, org, on=last_month_end)  # the last day of last month
    _sale(db_session, org, on=last_month_end.replace(day=1))  # its first day
    _sale(db_session, org)  # this month
    month = last_month_end.strftime("%Y-%m")

    chosen = client.get("/api/transactions/summary", params={"month": month}, headers=owner).json()
    current = client.get("/api/transactions/summary", headers=owner).json()
    next_month = (today.replace(day=28) + timedelta(days=5)).strftime("%Y-%m")

    assert (chosen["month_start"], chosen["month_end"]) == (str(last_month_end.replace(day=1)), str(last_month_end))
    assert chosen["completed_this_month"]["count"] == 2 and current["completed_this_month"]["count"] == 1
    assert current["month_end"] == str(today)
    assert (chosen["month"], chosen["next_month"], current["next_month"]) == (month, today.strftime("%Y-%m"), None)
    assert current["previous_month"] == month
    for bad in (next_month, "2026-13", "26-01", "nonsense"):
        assert client.get("/api/transactions/summary", params={"month": bad}, headers=owner).status_code == 422
        assert client.get("/api/invoices/summary", params={"month": bad}, headers=owner).status_code == 422


def test_pending_shows_unpaid_not_yet_due_and_partially_paid_with_what_is_outstanding(client: TestClient, db_session: Session, sales):
    today = today_in(None)
    overdue = issue(client, sales.headers, draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing),
                                                         invoice_date=str(today - timedelta(days=40)), due_date=str(today - timedelta(days=10))))
    upcoming = issue(client, sales.headers, draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing),
                                                          invoice_date=str(today), due_date=str(today + timedelta(days=20))))
    paid = issue(client, sales.headers, draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing), invoice_date=str(today)))
    pay = lambda invoice, amount: client.post(f"/api/invoices/{invoice['id']}/payments", json={"amount": amount, "paid_on": str(today), "method": "swish"}, headers=sales.headers)  # noqa: E731
    pay(upcoming, "62.50")
    pay(paid, "1062.50")

    summary = client.get("/api/invoices/summary", headers=sales.headers).json()

    assert summary["unpaid"] == {"count": 2, "amounts": [{"currency": "SEK", "amount": "2062.50"}]}  # 1062.50 + 1000.00
    assert summary["not_yet_due"] == {"count": 1, "amounts": [{"currency": "SEK", "amount": "1000.00"}]}
    assert summary["partially_paid"] == {"count": 1, "amounts": [{"currency": "SEK", "amount": "1000.00"}]}
    assert summary["past_due"]["count"] == 1 and overdue["id"]
