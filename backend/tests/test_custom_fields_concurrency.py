"""Concurrency: a custom-value write cannot race completion, and completion cannot bypass
required-field validation.

These tests use real, separate database connections and COMMITTED data (the rest of the
suite runs inside one rolled-back transaction, which cannot show a race). Each test builds
its own organization and always purges it afterwards.

The mechanism under test: every mutation of a transaction (Sales lifecycle steps, and the
custom-fields write through the registered `is_editable` callback) first takes
`SELECT ... FOR UPDATE` on the transaction row, then checks the state, then acts. So
"someone else is in the middle of something" is simulated by holding that row lock from a
third connection and watching the real endpoints wait for it.
"""

import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text

from app.core.db import SessionLocal, engine
from app.main import app
from app.models import Role
from app.modules.sales.models import TransactionLine
from tests.versions import FreshVersionClient
from tests.factories import add_member, make_customer, make_definition, make_org, make_transaction, make_user, make_value

BASE = "/api/custom-fields"
BLOCKED_FOR = 0.7  # seconds a request must still be waiting while the lock is held
FINISH_WITHIN = 15


@dataclass
class Committed:
    org_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    tx_id: uuid.UUID
    line_id: uuid.UUID
    anna_id: uuid.UUID
    owner_def_id: uuid.UUID

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Dev-User-Email": self.email}


def create_world(*, with_value: bool) -> Committed:
    """A draft transaction with one line and a REQUIRED custom field "owner" on lines."""
    with SessionLocal() as db:
        org = make_org(db, f"Concurrency {uuid.uuid4().hex[:8]}")
        user = make_user(db)
        add_member(db, org, user, Role.OWNER)
        billing = make_customer(db, org, "Billing")
        anna = make_customer(db, org, "Anna")
        tx = make_transaction(db, org, billing_customer=billing, lines=[{}])
        line = db.scalar(select(TransactionLine).where(TransactionLine.transaction_id == tx.id))
        owner = make_definition(db, org, key="owner", label="Owner", field_type="reference", reference_source="customer", required=True)
        if with_value:
            make_value(db, org, owner, line.id, value_reference_id=anna.id)
        world = Committed(org.id, user.id, user.email, tx.id, line.id, anna.id, owner.id)
        db.commit()
    return world


def purge(world: Committed) -> None:
    statements = [
        "delete from custom_field_values where organization_id = :o",
        "delete from custom_field_options where organization_id = :o",
        "delete from custom_field_definitions where organization_id = :o",
        "delete from transaction_lines where organization_id = :o",
        "delete from transactions where organization_id = :o",
        "delete from customers where organization_id = :o",
        "delete from organization_users where organization_id = :o",
        "delete from organizations where id = :o",
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement), {"o": world.org_id})
        conn.execute(text("delete from users where id = :u"), {"u": world.user_id})


@pytest.fixture
def committed(dev_auth):
    worlds: list[Committed] = []

    def build(with_value: bool) -> Committed:
        world = create_world(with_value=with_value)
        worlds.append(world)
        return world

    yield build
    for world in worlds:
        purge(world)


@contextmanager
def holding_the_transaction_row(tx_id):
    """Another writer that is mid-way through changing this transaction (row locked, nothing
    committed yet). Yields (connection, db transaction); the caller commits or rolls back."""
    conn = engine.connect()
    trans = conn.begin()
    conn.execute(text("select id from transactions where id = :i for update"), {"i": tx_id})
    try:
        yield conn, trans
    finally:
        if trans.is_active:
            trans.rollback()
        conn.close()


def in_thread(call):
    result: dict = {}

    def run():
        try:
            result["response"] = call(FreshVersionClient(app))
        except Exception as exc:  # surfaced by the assertions below
            result["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, result


def assert_still_waiting(thread: threading.Thread) -> None:
    thread.join(BLOCKED_FOR)
    assert thread.is_alive(), "the request did not wait for the row lock"


def finish(thread: threading.Thread, result: dict):
    thread.join(FINISH_WITHIN)
    assert not thread.is_alive(), "the request never finished"
    assert "error" not in result, result.get("error")
    return result["response"]


def complete(world: Committed):
    return lambda client: client.post(f"/api/transactions/{world.tx_id}/complete", headers=world.headers)


def write_owner(world: Committed, value):
    return lambda client: client.patch(
        f"{BASE}/entities/transaction_line/{world.line_id}/values",
        json={"values": {"owner": value}},
        headers=world.headers,
    )


def status_in_db(world: Committed) -> str:
    with engine.connect() as conn:
        return conn.execute(text("select status from transactions where id = :i"), {"i": world.tx_id}).scalar_one()


def value_in_db(world: Committed):
    with engine.connect() as conn:
        return conn.execute(
            text("select value_reference_id from custom_field_values where entity_id = :e"), {"e": world.line_id}
        ).scalar()


# --- completion waits for a value write that is in flight --------------------------------------------------------


def test_completion_waits_for_an_in_flight_value_write_and_then_sees_it(committed):
    world = committed(with_value=False)
    with holding_the_transaction_row(world.tx_id) as (conn, trans):
        # The in-flight writer has locked the row and set the required value, uncommitted.
        conn.execute(
            text(
                "insert into custom_field_values (organization_id, definition_id, entity_type, field_type, entity_id, value_reference_id)"
                " values (:o, :d, 'transaction_line', 'reference', :e, :v)"
            ),
            {"o": world.org_id, "d": world.owner_def_id, "e": world.line_id, "v": world.anna_id},
        )
        thread, result = in_thread(complete(world))
        assert_still_waiting(thread)  # completion cannot validate against a half-finished state
        trans.commit()

    response = finish(thread, result)

    assert response.status_code == 200, response.text  # it validated AFTER the write committed
    assert status_in_db(world) == "completed"


def test_completion_cannot_bypass_validation_when_a_required_value_is_removed_concurrently(committed):
    world = committed(with_value=True)
    with holding_the_transaction_row(world.tx_id) as (conn, trans):
        conn.execute(text("delete from custom_field_values where entity_id = :e"), {"e": world.line_id})
        thread, result = in_thread(complete(world))
        assert_still_waiting(thread)
        trans.commit()

    response = finish(thread, result)

    assert response.status_code == 409, "completed on a stale read of the required value"
    detail = response.json()["detail"]
    assert detail["code"] == "validation_failed"
    assert [(p["entity_type"], p["entity_id"], p["field"]) for p in detail["problems"]] == [
        ("transaction_line", str(world.line_id), "owner")
    ]
    assert status_in_db(world) == "draft"


def test_completion_still_fails_if_the_in_flight_writer_rolls_back(committed):
    world = committed(with_value=False)
    with holding_the_transaction_row(world.tx_id) as (conn, trans):
        conn.execute(
            text(
                "insert into custom_field_values (organization_id, definition_id, entity_type, field_type, entity_id, value_reference_id)"
                " values (:o, :d, 'transaction_line', 'reference', :e, :v)"
            ),
            {"o": world.org_id, "d": world.owner_def_id, "e": world.line_id, "v": world.anna_id},
        )
        thread, result = in_thread(complete(world))
        assert_still_waiting(thread)
        trans.rollback()

    assert finish(thread, result).status_code == 409
    assert status_in_db(world) == "draft"


# --- a value write waits for a completion that is in flight -----------------------------------------------------


def test_a_value_write_waits_for_an_in_flight_completion_and_is_then_refused(committed):
    world = committed(with_value=True)
    with holding_the_transaction_row(world.tx_id) as (conn, trans):
        conn.execute(text("update transactions set status = 'completed' where id = :i"), {"i": world.tx_id})
        thread, result = in_thread(write_owner(world, str(uuid.uuid4())))  # would change the value
        assert_still_waiting(thread)  # it must not slip in while the transaction is being completed
        trans.commit()

    response = finish(thread, result)

    assert response.status_code == 409  # re-checked AFTER taking the lock: the record is now locked
    assert value_in_db(world) == world.anna_id  # untouched
    assert status_in_db(world) == "completed"


def test_a_value_write_proceeds_once_an_in_flight_change_is_rolled_back(committed):
    world = committed(with_value=True)
    with holding_the_transaction_row(world.tx_id) as (conn, trans):
        conn.execute(text("update transactions set status = 'completed' where id = :i"), {"i": world.tx_id})
        thread, result = in_thread(write_owner(world, str(world.anna_id)))
        assert_still_waiting(thread)
        trans.rollback()

    assert finish(thread, result).status_code == 200  # still a draft, so the write is allowed


def test_two_value_writes_to_the_same_record_are_serialized_not_lost(committed):
    world = committed(with_value=False)
    outcomes: list[int] = []

    def write(client: TestClient):
        return client.patch(
            f"{BASE}/entities/transaction_line/{world.line_id}/values",
            json={"values": {"owner": str(world.anna_id)}},
            headers=world.headers,
        )

    threads = [in_thread(write) for _ in range(6)]
    for thread, result in threads:
        outcomes.append(finish(thread, result).status_code)

    assert outcomes == [200] * 6  # no unique-violation 500s, no lost update
    with engine.connect() as conn:
        count = conn.execute(text("select count(*) from custom_field_values where entity_id = :e"), {"e": world.line_id}).scalar()
    assert count == 1


# --- the real endpoints racing each other, many times -------------------------------------------------------------


def test_completing_and_filling_the_required_field_at_the_same_time_is_always_consistent(committed):
    """Whatever the interleaving: a completed transaction always has the required value, a
    write after completion is always refused, and a refused completion changes nothing."""
    for _ in range(12):
        world = committed(with_value=False)
        start = threading.Barrier(2)

        def racing(call):
            def run(client):
                start.wait(10)
                return call(client)

            return run

        t_complete, r_complete = in_thread(racing(complete(world)))
        t_write, r_write = in_thread(racing(write_owner(world, str(world.anna_id))))
        completion = finish(t_complete, r_complete)
        write = finish(t_write, r_write)

        status, value = status_in_db(world), value_in_db(world)
        assert completion.status_code in (200, 409) and write.status_code in (200, 409), (completion.text, write.text)
        if status == "completed":
            assert completion.status_code == 200
            assert value == world.anna_id, "completed while the required value was missing"
        else:
            assert completion.status_code == 409 and status == "draft"
        if write.status_code == 409:
            assert status == "completed" and value is None  # refused only because it came too late
        else:
            assert value == world.anna_id


# --- the write path locks before it validates (and is the first thing that does) ---------------------------------------------------


def test_the_value_write_takes_the_row_lock_before_reading_or_writing_custom_fields(client, cf):
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        response = client.patch(
            f"{BASE}/entities/transaction_line/{cf.lines[0].id}/values",
            json={"values": {"owner": str(cf.anna.id)}},
            headers=cf.headers,
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert response.status_code == 200
    lock = next(i for i, s in enumerate(statements) if "FOR UPDATE" in s and "FROM transactions" in s)
    touches = [i for i, s in enumerate(statements) if "custom_field_" in s]
    assert touches and min(touches) > lock
