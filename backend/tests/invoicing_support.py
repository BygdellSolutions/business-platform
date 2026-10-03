"""Helpers shared by the Invoicing tests."""

import re
import threading
import uuid
from dataclasses import dataclass
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, engine
from app.main import app
from app.models import Customer, CustomerType, Organization, Role
from tests.factories import add_member, make_customer, make_org, make_transaction, make_user

INVOICES = "/api/invoices"
INVOICEABLE = "/api/invoiceable-transactions"
MUTATORS = {Role.OWNER, Role.ADMIN, Role.ACCOUNTANT}


def if_match(version: int) -> dict[str, str]:
    return {"If-Match": f'"{version}"'}


def member_of(db: Session, org, role: Role) -> dict[str, str]:
    user = make_user(db)
    add_member(db, org, user, role)
    return {"X-Dev-User-Email": user.email, "X-Organization-Id": str(org.id)}


def completed(db: Session, org, customer=None, *, lines=None, currency="org", transaction_date=date(2026, 10, 1), status="completed"):
    """A completed transaction with a currency (the organization's unless given; None for a
    transaction that predates currencies)."""
    customer = customer or make_customer(db, org, "Umeå HK", CustomerType.COMPANY, "hk@example.test", None)
    kwargs = {} if currency == "org" else {"currency": currency}
    return make_transaction(db, org, billing_customer=customer, status=status, lines=lines, transaction_date=transaction_date, **kwargs)


def draft_invoice(client, headers, *transactions, **extra) -> dict:
    response = client.post(INVOICES, json={"transaction_ids": [str(t.id) for t in transactions], **extra}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def issue(client, headers, invoice: dict) -> dict:
    response = client.post(f"{INVOICES}/{invoice['id']}/issue", headers={**headers, **if_match(invoice["version"])})
    assert response.status_code == 200, response.text
    return response.json()


class Two:
    """Two organizations whose records look identical (same names, same amounts)."""

    def __init__(self, db: Session):
        self.a_org, self.b_org = make_org(db, "Org A"), make_org(db, "Org B")
        self.a, self.b = member_of(db, self.a_org, Role.OWNER), member_of(db, self.b_org, Role.OWNER)
        self.a_customer = make_customer(db, self.a_org, "Anna Andersson")
        self.b_customer = make_customer(db, self.b_org, "Anna Andersson")
        self.a_tx = completed(db, self.a_org, self.a_customer)
        self.b_tx = completed(db, self.b_org, self.b_customer)


# --- committed-data tests ----------------------------------------------------------------------------------------------


def purge_organization(org_id: uuid.UUID, user_ids: list[uuid.UUID]) -> None:
    """Remove everything a committed-data test built. Issued invoices are protected by triggers
    (and RESTRICT foreign keys) on purpose, so this session switches both off; the test database
    is disposable and this is the only place that does it."""
    with engine.begin() as connection:
        connection.execute(text("set local session_replication_role = replica"))
        for table in (
            "invoice_vat_rows",
            "invoice_lines",
            "invoice_transactions",
            "invoices",
            "invoice_counters",
            "custom_field_values",
            "custom_field_options",
            "custom_field_definitions",
            "transaction_lines",
            "transactions",
            "items",
            "customers",
            "organization_users",
        ):
            connection.execute(text(f"delete from {table} where organization_id = :o"), {"o": org_id})
        connection.execute(text("delete from organizations where id = :o"), {"o": org_id})
        for user_id in user_ids:
            connection.execute(text("delete from users where id = :u"), {"u": user_id})


# --- threads and committed worlds ---------------------------------------------------------------------------------------


# Tables that supply LIVE content. A document read must not touch any of them.
LIVE_TABLES = re.compile(
    r"\b(customers|organizations|items|horses|transactions|transaction_lines|custom_field_definitions|custom_field_options|custom_field_values)\b"
)


class Statements:
    """Records every SQL statement sent while it is active."""

    def __init__(self):
        self.sql: list[str] = []

    def __enter__(self):
        event.listen(engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc):
        event.remove(engine, "before_cursor_execute", self._record)

    def _record(self, conn, cursor, statement, parameters, context, executemany):
        self.sql.append(statement)

    def touching_live_tables(self) -> list[str]:
        return [s for s in self.sql if LIVE_TABLES.search(s.lower())]


WAIT = 0.7  # how long a request must still be blocked to count as "waiting"


class Request:
    """An HTTP request running in its own thread, so a test can see whether it is still waiting.
    A server error becomes a 500 response instead of an exception in the thread."""

    def __init__(self, headers: dict[str, str], method: str, url: str, **kwargs):
        self.response = None
        self.finished = threading.Event()

        def run():
            try:
                self.response = getattr(TestClient(app, raise_server_exceptions=False), method)(url, headers=headers, **kwargs)
            finally:
                self.finished.set()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def still_waiting(self) -> bool:
        return not self.finished.wait(WAIT)

    def result(self):
        assert self.finished.wait(20), "the request never finished"
        return self.response


def race(*calls) -> list:
    """Start the calls at (nearly) the same instant and return their responses."""
    barrier = threading.Barrier(len(calls))
    results: list = [None] * len(calls)

    def run(index, call):
        barrier.wait()
        results[index] = call()

    threads = [threading.Thread(target=run, args=(i, c), daemon=True) for i, c in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
        assert not thread.is_alive(), "a racing request never finished"
    return results


@dataclass
class CommittedWorld:
    org_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    customer_id: uuid.UUID

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Dev-User-Email": self.email}

    def transaction(self, status: str = "completed", currency: str = "org", lines=None, date_=date(2026, 10, 1)) -> uuid.UUID:
        with SessionLocal() as db:
            tx = completed(
                db, db.get(Organization, self.org_id), db.get(Customer, self.customer_id),
                status=status, currency=currency, lines=lines, transaction_date=date_,
            )
            tx_id = tx.id
            db.commit()
        return tx_id

    def create_invoice(self, *transaction_ids) -> dict:
        response = TestClient(app).post(INVOICES, json={"transaction_ids": [str(t) for t in transaction_ids]}, headers=self.headers)
        assert response.status_code == 201, response.text
        return response.json()


def build_committed_world(label: str = "Race") -> CommittedWorld:
    with SessionLocal() as db:
        org = make_org(db, f"{label} {uuid.uuid4().hex[:8]}")
        user = make_user(db)
        add_member(db, org, user, Role.OWNER)
        customer = make_customer(db, org, "Billing Co", CustomerType.COMPANY, "billing@example.test", None)
        world = CommittedWorld(org.id, user.id, user.email, customer.id)
        db.commit()
    return world


def tx_version(transaction_id: uuid.UUID) -> int:
    with engine.connect() as connection:
        return connection.execute(text("select version from transactions where id = :i"), {"i": transaction_id}).scalar_one()


def scalar(sql: str, **params):
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar()
