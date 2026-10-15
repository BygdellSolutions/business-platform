"""Where the time goes: every SQL statement of the two dashboard calls with its own duration (run like bench_dashboard.py)."""

import time

import pytest
from sqlalchemy import event

from app.api.deps import Pagination, Sorting
from app.modules.invoicing import api as invoicing_api
from tests.bench_dashboard import build

pytestmark = pytest.mark.usefixtures("session_mode")


def test_statements(db_session):
    ctx = build(db_session, 1000)
    rows = []
    engine = db_session.get_bind().engine
    started = {}

    def before(conn, cursor, statement, parameters, context, executemany):
        started["t"] = time.perf_counter()

    def after(conn, cursor, statement, parameters, context, executemany):
        rows.append(((time.perf_counter() - started["t"]) * 1000, " ".join(statement.split())[:90] + " ... " + " ".join(statement.split())[-90:] + f"  [{len(parameters) if parameters else 0} params, {len(statement)} chars]"))

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    try:
        for label, call in (
            ("SUMMARY", lambda: invoicing_api.invoicing_summary(month=None, ctx=ctx, db=db_session)),
            ("LIST", lambda: invoicing_api.list_invoiceable_transactions(customer_id=None, date_from=None, date_to=None, page=Pagination(offset=0, limit=25), sort=Sorting(key=None, descending=False), ctx=ctx, db=db_session)),
        ):
            call()  # warm up
            rows.clear()
            call()
            total = sum(ms for ms, _ in rows)
            print(f"\nSTMT {label}: {len(rows)} statements, {total:.0f} ms in the database")
            for ms, sql in sorted(rows, reverse=True)[:6]:
                print(f"STMT   {ms:7.1f} ms  {sql}")
    finally:
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)
