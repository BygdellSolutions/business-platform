"""Races around the default currency and the lifecycle seam, with REAL committed data.

The rollback-only fixtures of the rest of the suite cannot show a race. Here every actor has its
own connection. An "in-flight" actor is a session that has done its work but not committed yet;
the request under test must then WAIT for it, and decide only after it commits:

  * changing the currency waits for an in-flight item or transaction, then sees it and refuses;
  * creating a transaction waits for an in-flight currency change, then snapshots the NEW currency;
  * a reopen or cancel and a "claim" on a transaction (what invoicing will be) serialize on the
    transaction row, whichever comes first, because the seam runs under Sales' row lock.

Each test builds its own organization and always purges it.
"""

import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.currency import default_currency_for_new_record, share_lock_organization
from app.core.db import SessionLocal, engine
from app.core.entity_registry import registry
from app.core.lifecycle import CANCEL, REOPEN, Problem
from app.main import app
from app.models import Customer, Item, ItemType, Organization, Role
from app.modules.sales.models import Transaction
from tests.factories import add_member, make_customer, make_org, make_transaction, make_user

WAIT = 0.7  # how long a request must still be blocked to count as "waiting"


@dataclass
class World:
    org_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    customer_id: uuid.UUID

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Dev-User-Email": self.email}


def build_world() -> World:
    with SessionLocal() as db:
        org = make_org(db, f"Currency race {uuid.uuid4().hex[:8]}")
        user = make_user(db)
        add_member(db, org, user, Role.OWNER)
        customer = make_customer(db, org, "Billing")
        world = World(org.id, user.id, user.email, customer.id)
        db.commit()
    return world


def purge(world: World) -> None:
    with engine.begin() as conn:
        conn.execute(text("drop table if exists test_claims"))
        for statement in (
            "delete from transaction_lines where organization_id = :o",
            "delete from transactions where organization_id = :o",
            "delete from items where organization_id = :o",
            "delete from customers where organization_id = :o",
            "delete from organization_users where organization_id = :o",
            "delete from organizations where id = :o",
        ):
            conn.execute(text(statement), {"o": world.org_id})
        conn.execute(text("delete from users where id = :u"), {"u": world.user_id})


@pytest.fixture
def world(dev_auth):
    made = build_world()
    yield made
    purge(made)


def committed_transaction(world: World, status: str = "completed") -> uuid.UUID:
    with SessionLocal() as db:
        tx = make_transaction(
            db, db.get(Organization, world.org_id), billing_customer=db.get(Customer, world.customer_id), status=status
        )
        tx_id = tx.id
        db.commit()
    return tx_id


def scalar(sql: str, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


class Request:
    """A request running in its own thread, so the test can see whether it is still waiting."""

    def __init__(self, world: World, method: str, url: str, **kwargs):
        self.response = None
        self.finished = threading.Event()

        def run():
            try:
                self.response = getattr(TestClient(app), method)(url, headers=world.headers, **kwargs)
            finally:
                self.finished.set()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def still_waiting(self) -> bool:
        return not self.finished.wait(WAIT)

    def result(self):
        assert self.finished.wait(15), "the request never finished"
        return self.response


# --- changing the currency vs prices being created ----------------------------------------------------------------


def test_a_currency_change_waits_for_an_item_being_created_and_then_refuses(world: World):
    inflight = SessionLocal()  # an item creation that has not committed yet
    try:
        share_lock_organization(inflight, world.org_id)
        inflight.add(Item(organization_id=world.org_id, name="Massage", type=ItemType.SERVICE, unit="s", price_ex_vat=850, vat_rate=25))
        inflight.flush()

        change = Request(world, "patch", "/api/organization", json={"default_currency": "EUR"})
        assert change.still_waiting(), "the change must wait for the in-flight item"
        inflight.commit()
    finally:
        inflight.close()

    response = change.result()
    assert response.status_code == 409 and response.json()["detail"]["code"] == "currency_locked"
    assert scalar("select default_currency from organizations where id = :o", o=world.org_id) == "SEK"


def test_a_currency_change_waits_for_a_transaction_being_created_and_then_refuses(world: World):
    inflight = SessionLocal()
    try:
        currency = default_currency_for_new_record(inflight, world.org_id)
        inflight.add(
            Transaction(
                organization_id=world.org_id, billing_customer_id=world.customer_id,
                transaction_date=date(2026, 10, 1), currency=currency,
            )
        )
        inflight.flush()

        change = Request(world, "patch", "/api/organization", json={"default_currency": "EUR"})
        assert change.still_waiting(), "the change must wait for the in-flight transaction"
        inflight.commit()
    finally:
        inflight.close()

    assert change.result().status_code == 409
    assert scalar("select default_currency from organizations where id = :o", o=world.org_id) == "SEK"
    assert scalar("select currency from transactions where organization_id = :o", o=world.org_id) == "SEK"


def test_a_transaction_created_during_a_currency_change_gets_the_new_currency(world: World):
    changing = SessionLocal()  # a currency change that has not committed yet
    try:
        changing.execute(text("select 1 from organizations where id = :o for update"), {"o": world.org_id})
        changing.execute(text("update organizations set default_currency = 'EUR' where id = :o"), {"o": world.org_id})

        create = Request(world, "post", "/api/transactions", json={"billing_customer_id": str(world.customer_id)})
        assert create.still_waiting(), "the creation must wait for the in-flight change"
        changing.commit()
    finally:
        changing.close()

    response = create.result()
    assert response.status_code == 201, response.text
    # Not the SEK it could have read a moment earlier: the creation saw the committed change.
    assert response.json()["currency"] == "EUR"


def test_an_item_created_during_a_currency_change_waits_too(world: World):
    changing = SessionLocal()
    try:
        changing.execute(text("select 1 from organizations where id = :o for update"), {"o": world.org_id})
        create = Request(
            world, "post", "/api/items",
            json={"type": "service", "name": "Massage", "unit": "session", "price_ex_vat": "850.00", "vat_rate": "25.00"},
        )
        assert create.still_waiting()
        changing.commit()
    finally:
        changing.close()
    assert create.result().status_code == 201


def test_many_creators_do_not_block_each_other(world: World):
    """Creation takes only a SHARED lock on the organization: two in-flight creators coexist."""
    first, second = SessionLocal(), SessionLocal()
    try:
        share_lock_organization(first, world.org_id)
        started = time.monotonic()
        share_lock_organization(second, world.org_id)  # would hang for the lock timeout if it were exclusive
        assert time.monotonic() - started < WAIT
    finally:
        first.rollback(), second.rollback()
        first.close(), second.close()


def test_a_currency_change_without_any_prices_still_works_after_the_dust_settles(world: World):
    response = TestClient(app).patch("/api/organization", json={"default_currency": "EUR"}, headers=world.headers)
    assert response.status_code == 200
    assert scalar("select default_currency from organizations where id = :o", o=world.org_id) == "EUR"


# --- assigning a currency vs an edit in flight -------------------------------------------------------------------------


def test_assigning_a_currency_waits_for_a_transaction_being_edited(world: World):
    with SessionLocal() as db:
        old = make_transaction(db, db.get(Organization, world.org_id), billing_customer=db.get(Customer, world.customer_id), currency=None)
        old_id = old.id
        db.commit()
    editing = SessionLocal()
    try:
        editing.execute(text("select 1 from transactions where id = :i for update"), {"i": old_id})  # what every Sales mutation does
        assign = Request(world, "post", "/api/transactions/assign-currency", json={"currency": "SEK"})
        assert assign.still_waiting()
        editing.execute(text("update transactions set version = version + 1 where id = :i"), {"i": old_id})
        editing.commit()
    finally:
        editing.close()

    assert assign.result().json() == {"currency": "SEK", "assigned": 1}
    # Both the edit's bump and the assignment's bump are there: neither was lost.
    assert scalar("select version from transactions where id = :i", i=old_id) == 3


# --- the lifecycle seam vs a claim (what invoicing will do) -----------------------------------------------------------------


@pytest.fixture
def claims(world: World):
    """A stand-in for the future invoicing tables: a claim row on a transaction, and a validator
    (registered through the registry only, as a module would) that vetoes reopen and cancel."""
    with engine.begin() as conn:
        conn.execute(text("create table test_claims (transaction_id uuid primary key)"))

    def validator(db, ctx, event, entity_key, entity_id):
        if event in (REOPEN, CANCEL) and entity_key == "transaction":
            held = db.execute(text("select 1 from test_claims where transaction_id = :t"), {"t": entity_id}).first()
            if held:
                return [Problem(code="test.claimed", message="Claimed.", entity_type="transaction", entity_id=str(entity_id))]
        return []

    with registry.isolated():
        registry.add_validator(validator)
        yield


@pytest.mark.parametrize("step", ["reopen", "cancel"])
def test_a_claim_in_flight_blocks_a_reopen_or_cancel_that_arrives_meanwhile(world: World, claims, step: str):
    tx_id = committed_transaction(world)
    claimer = SessionLocal()
    try:
        # A claimer first locks the transaction row (shared), then records its claim.
        claimer.execute(text("select 1 from transactions where id = :i for share"), {"i": tx_id})
        claimer.execute(text("insert into test_claims values (:i)"), {"i": tx_id})

        client_headers = {**world.headers, "If-Match": '"1"'}
        result: dict = {}
        done = threading.Event()

        def run():
            result["r"] = TestClient(app).post(f"/api/transactions/{tx_id}/{step}", headers=client_headers)
            done.set()

        threading.Thread(target=run, daemon=True).start()
        assert not done.wait(WAIT), "the step must wait for the claimer's lock"
        claimer.commit()
    finally:
        claimer.close()

    assert done.wait(15)
    response = result["r"]
    assert response.status_code == 409 and response.json()["detail"]["event"] == step
    assert response.json()["detail"]["problems"][0]["code"] == "test.claimed"
    assert scalar("select status from transactions where id = :i", i=tx_id) == "completed"


def test_a_claim_attempted_while_a_reopen_is_in_flight_sees_the_reopened_transaction(world: World, claims):
    """The other order: the reopen got the row first. The claimer, which must lock the row
    before claiming, then sees a draft and must not claim it."""
    tx_id = committed_transaction(world)
    reopening = SessionLocal()
    seen: dict = {}
    done = threading.Event()
    try:
        reopening.execute(text("select 1 from transactions where id = :i for update"), {"i": tx_id})  # Sales' lock
        reopening.execute(text("update transactions set status = 'draft', version = version + 1 where id = :i"), {"i": tx_id})

        def claim():
            with SessionLocal() as db:
                status = db.execute(text("select status from transactions where id = :i for share"), {"i": tx_id}).scalar()
                seen["status"] = status
                if status == "completed":  # only a completed transaction may be claimed
                    db.execute(text("insert into test_claims values (:i)"), {"i": tx_id})
                db.commit()
            done.set()

        threading.Thread(target=claim, daemon=True).start()
        assert not done.wait(WAIT), "the claimer must wait for the reopen"
        reopening.commit()
    finally:
        reopening.close()

    assert done.wait(15)
    assert seen["status"] == "draft"
    assert scalar("select count(*) from test_claims") == 0


def test_without_a_claim_the_steps_go_through_normally(world: World, claims):
    tx_id = committed_transaction(world)
    headers = {**world.headers, "If-Match": '"1"'}
    assert TestClient(app).post(f"/api/transactions/{tx_id}/reopen", headers=headers).status_code == 200
