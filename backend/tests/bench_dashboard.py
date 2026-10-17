"""A benchmark, not a test: how long the dashboard summary and the "ready to invoice" list take as the backlog grows.

Not collected by the normal run (the file name does not start with test_). Run it on its own, with nothing else using the test
database:

    uv run pytest tests/bench_dashboard.py -s -p no:cacheprovider

It builds, inside one rolled-back transaction, an organization with a backlog of completed orders (four lines each) plus a history of
issued invoices, then times the two endpoints' functions and counts the SQL statements they issue. The numbers belong to the machine
they run on: compare runs on the SAME machine, before and after a change.
"""

import statistics
import time
from datetime import date, timedelta

import pytest
from sqlalchemy import event, text

from app.core.tenant import TenantContext
from app.models import Role
from app.modules.invoicing import api as invoicing_api
from app.api.deps import Pagination, Sorting
from tests.factories import add_member, make_customer, make_org, make_transaction, make_user

pytestmark = pytest.mark.usefixtures("session_mode")

SIZES = (100, 250, 1000)
REPEATS = 9


def build(db, orders: int):
    org = make_org(db, f"Bench {orders}")
    user = make_user(db)
    add_member(db, org, user, Role.OWNER)
    customers = [make_customer(db, org, f"Customer {n}", email=f"c{n}@example.test") for n in range(25)]
    for n in range(orders):
        make_transaction(
            db,
            org,
            billing_customer=customers[n % len(customers)],
            transaction_date=date(2026, 9, 1) + timedelta(days=n % 40),
            status="completed",
            lines=[{"description": f"Line {k}", "quantity": str(1 + k), "unit_price_ex_vat": f"{100 + 10 * k}.00"} for k in range(4)],
        )
    db.flush()
    # A real database has statistics (autovacuum analyses a table as it grows); a table loaded a moment ago in this
    # transaction has none, and the planner then picks plans no production database would. Analyse like production does.
    for table in ("transactions", "transaction_lines", "customers", "invoice_lines", "invoice_transactions", "invoices", "line_fulfillments", "invoice_holds"):
        db.execute(text(f"ANALYZE {table}"))
    return TenantContext(user=user, organization_id=org.id, role=Role.OWNER)


def timed(db, call):
    statements = []

    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = db.get_bind().engine
    event.listen(engine, "before_cursor_execute", count)
    try:
        samples = []
        for _ in range(REPEATS):
            statements.clear()
            started = time.perf_counter()
            call()
            samples.append((time.perf_counter() - started) * 1000)
        return statistics.median(samples), min(samples), len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", count)


@pytest.mark.parametrize("orders", SIZES)
def test_bench(db_session, orders):
    ctx = build(db_session, orders)

    summary_ms, summary_min, summary_sql = timed(db_session, lambda: invoicing_api.invoicing_summary(month=None, ctx=ctx, db=db_session))
    page = Pagination(offset=0, limit=25)
    listing_ms, listing_min, listing_sql = timed(
        db_session,
        lambda: invoicing_api.list_invoiceable_transactions(customer_id=None, date_from=None, date_to=None, page=page, sort=Sorting(key=None, descending=False), ctx=ctx, db=db_session),
    )
    print(
        f"\nBENCH backlog={orders:5d} orders ({orders * 4} lines)  "
        f"summary: median {summary_ms:7.1f} ms (best {summary_min:6.1f}), {summary_sql:3d} statements  |  "
        f"ready list (page of 25): median {listing_ms:7.1f} ms (best {listing_min:6.1f}), {listing_sql:3d} statements"
    )
