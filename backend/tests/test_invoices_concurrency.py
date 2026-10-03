"""Invoicing under real concurrency: committed data, separate connections, genuine waiting.

The rollback-only fixtures of the rest of the suite cannot show a race. Here every actor has its
own connection and its own session. Two techniques are used:

  * natural races: several requests started at the same instant, repeated; whatever the interleaving,
    the outcome must be one of the consistent ones;
  * gated races: one side is paused while it holds its locks (an uncommitted session, or a request
    stopped at a gate), the other side must visibly WAIT, and decides only after the first commits.

Each test builds its own organization and always purges it.
"""

import threading
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.db import SessionLocal
from app.core.entity_registry import registry
from app.core.lifecycle import REOPEN
from app.modules.invoicing import numbering, service
from tests.invoicing_support import (
    INVOICES,
    Request,
    Statements,
    build_committed_world,
    if_match,
    purge_organization,
    race,
    scalar,
    tx_version,
)

ROUNDS = 12


@pytest.fixture
def world(dev_auth):
    made = build_committed_world()
    yield made
    purge_organization(made.org_id, [made.user_id])


def lock_transaction(session, transaction_id: uuid.UUID) -> None:
    """What every Sales writer does first."""
    session.execute(text("select 1 from transactions where id = :i for update"), {"i": transaction_id})


def post_invoice(world, *ids, **extra) -> Request:
    return Request(world.headers, "post", INVOICES, json={"transaction_ids": [str(i) for i in ids], **extra})


def invoice_count(world) -> int:
    return scalar("select count(*) from invoices where organization_id = :o", o=world.org_id)


def link_count(world) -> int:
    return scalar("select count(*) from invoice_transactions where organization_id = :o", o=world.org_id)


# --- two creators for the same transaction ---------------------------------------------------------------------------------------


def test_two_simultaneous_creators_for_one_transaction_exactly_one_succeeds(world):
    for _ in range(ROUNDS):
        tx = world.transaction()
        first, second = race(
            lambda: post_invoice(world, tx).result(),
            lambda: post_invoice(world, tx).result(),
        )
        assert sorted([first.status_code, second.status_code]) == [201, 409]
        loser = first if first.status_code == 409 else second
        assert loser.json()["detail"]["code"] == "already_invoiced"
        assert loser.json()["detail"]["transaction_ids"] == [str(tx)]
    assert (invoice_count(world), link_count(world)) == (ROUNDS, ROUNDS)


def test_creators_held_at_the_same_lock_are_serialized_and_one_wins(world):
    tx = world.transaction()
    holder = SessionLocal()
    try:
        lock_transaction(holder, tx)  # e.g. a Sales writer that is mid-change
        a, b = post_invoice(world, tx), post_invoice(world, tx)
        assert a.still_waiting() and b.still_waiting()
        holder.rollback()
    finally:
        holder.close()
    codes = sorted([a.result().status_code, b.result().status_code])
    assert codes == [201, 409]
    assert invoice_count(world) == 1


def test_overlapping_requests_for_the_same_transactions_in_opposite_order_do_not_deadlock(world):
    a, b = world.transaction(), world.transaction()
    for _ in range(ROUNDS // 2):
        first, second = race(lambda: post_invoice(world, a, b).result(), lambda: post_invoice(world, b, a).result())
        assert {first.status_code, second.status_code} <= {201, 409}
        assert 201 in (first.status_code, second.status_code)
        # They are the same two transactions: whoever won, the other cannot have them.
        a, b = world.transaction(), world.transaction()
    assert invoice_count(world) == ROUNDS // 2


def test_a_creator_for_a_set_and_a_creator_for_one_of_its_members_exactly_one_wins(world):
    for _ in range(ROUNDS // 2):
        a, b = world.transaction(), world.transaction()
        wide, narrow = race(lambda: post_invoice(world, a, b).result(), lambda: post_invoice(world, b).result())
        assert sorted([wide.status_code, narrow.status_code]) == [201, 409]
        taken = scalar("select count(*) from invoice_transactions where transaction_id in (:a, :b)", a=a, b=b)
        # Whoever won owns b; if the wide request won it owns a as well, otherwise a stays free.
        assert taken == (2 if wide.status_code == 201 else 1)
    # No transaction is on two invoices, whatever the interleaving.
    assert scalar("select count(distinct transaction_id) from invoice_transactions where organization_id = :o", o=world.org_id) == link_count(world)


def test_sources_are_locked_in_a_fixed_order_by_id(world):
    """Two requests that list the same transactions in opposite order must lock them in the same
    order, or they could deadlock. A real deadlock cannot be forced reliably (the planner's row
    order is stable), so the locking statement itself is checked: ORDER BY id, then FOR UPDATE."""
    a, b = world.transaction(), world.transaction()
    with Statements() as recorded:
        assert post_invoice(world, b, a).result().status_code == 201
    locking = [s.lower() for s in recorded.sql if "for update" in s.lower() and "from transactions" in s.lower()]
    assert len(locking) == 1
    statement = " ".join(locking[0].split())
    assert statement.index("order by transactions.id") < statement.index("for update")
    assert "organization_id" in statement  # and scoped to the organization


# --- creation racing Sales lifecycle ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("step", ["reopen", "cancel"])
def test_creation_racing_reopen_or_cancel_always_ends_consistently(world, step):
    outcomes = {"invoice": 0, "sales_step": 0}
    for _ in range(ROUNDS):
        tx = world.transaction()
        version = tx_version(tx)
        create, sales = race(
            lambda: post_invoice(world, tx).result(),
            lambda: Request({**world.headers, **if_match(version)}, "post", f"/api/transactions/{tx}/{step}").result(),
        )
        status = scalar("select status from transactions where id = :i", i=tx)
        linked = scalar("select count(*) from invoice_transactions where transaction_id = :i", i=tx)
        if create.status_code == 201:
            # The invoice got there first: the step must have been refused, and the transaction is still completed.
            assert sales.status_code == 409 and sales.json()["detail"]["problems"][0]["code"] == "invoice.reserved"
            assert (status, linked) == ("completed", 1)
            outcomes["invoice"] += 1
        else:
            # The step got there first: the invoice must have been refused, and nothing is reserved.
            assert sales.status_code == 200
            assert create.status_code == 409 and create.json()["detail"]["code"] == "transactions_not_completed"
            assert (status, linked) == ("draft" if step == "reopen" else "cancelled", 0)
            outcomes["sales_step"] += 1
    assert sum(outcomes.values()) == ROUNDS


@pytest.fixture
def gate():
    """An event that holds a request open until the test lets it go (and never forever)."""
    event = threading.Event()
    yield event
    event.set()


def test_a_reopen_that_is_underway_makes_the_creation_wait_and_then_refuse(world, gate):
    tx = world.transaction()
    entered = threading.Event()

    def paused_validator(db, ctx, event, entity_key, entity_id):
        if event == REOPEN and entity_id == tx:
            entered.set()  # the transaction row is locked; the status is not yet written
            gate.wait(15)
        return []

    with registry.isolated():
        registry.add_validator(paused_validator)
        reopen = Request({**world.headers, **if_match(tx_version(tx))}, "post", f"/api/transactions/{tx}/reopen")
        assert entered.wait(10)
        create = post_invoice(world, tx)
        assert create.still_waiting(), "the creation must wait for the reopen's lock"
        gate.set()
        assert reopen.result().status_code == 200
        response = create.result()
    assert response.status_code == 409 and response.json()["detail"]["code"] == "transactions_not_completed"
    assert invoice_count(world) == 0


@pytest.mark.parametrize("step", ["reopen", "cancel"])
def test_a_creation_that_is_underway_makes_a_reopen_or_cancel_wait_and_then_refuse(world, gate, step, monkeypatch):
    tx = world.transaction()
    inside = threading.Event()
    real = service._insert_children

    def paused(*args, **kwargs):
        real(*args, **kwargs)
        inside.set()  # the sources are locked and the rows are written, but nothing is committed
        gate.wait(15)

    monkeypatch.setattr(service, "_insert_children", paused)
    create = post_invoice(world, tx)
    assert inside.wait(10)
    sales = Request({**world.headers, **if_match(tx_version(tx))}, "post", f"/api/transactions/{tx}/{step}")
    assert sales.still_waiting(), "the step must wait for the creation's lock"
    gate.set()
    assert create.result().status_code == 201
    response = sales.result()
    assert response.status_code == 409 and response.json()["detail"]["problems"][0]["code"] == "invoice.reserved"
    assert scalar("select status from transactions where id = :i", i=tx) == "completed"


def test_deleting_the_draft_releases_the_reservation_for_sales(world):
    tx = world.transaction()
    invoice = world.create_invoice(tx)
    blocked = Request({**world.headers, **if_match(tx_version(tx))}, "post", f"/api/transactions/{tx}/reopen").result()
    assert blocked.status_code == 409
    assert Request({**world.headers, **if_match(1)}, "delete", f"{INVOICES}/{invoice['id']}").result().status_code == 204
    assert Request({**world.headers, **if_match(tx_version(tx))}, "post", f"/api/transactions/{tx}/reopen").result().status_code == 200


# --- creation racing Sales writes and custom-field writes -----------------------------------------------------------------------------


def test_creation_waits_for_a_sales_line_write_and_then_refuses_a_transaction_that_is_still_a_draft(world):
    tx = world.transaction(status="draft")
    writer = SessionLocal()
    try:
        lock_transaction(writer, tx)
        writer.execute(
            text(
                "insert into transaction_lines (organization_id, transaction_id, position, description, unit, quantity, unit_price_ex_vat, vat_rate, net_amount, vat_amount, gross_amount) "
                "select organization_id, id, 9, 'In flight', 'u', 1, 1.00, 25.00, 1.00, 0.25, 1.25 from transactions where id = :t"
            ),
            {"t": tx},
        )
        create = post_invoice(world, tx)
        assert create.still_waiting()
        writer.commit()
    finally:
        writer.close()
    response = create.result()
    assert response.status_code == 409 and response.json()["detail"]["code"] == "transactions_not_completed"
    assert invoice_count(world) == 0


def test_creation_waits_for_a_completion_in_flight_and_then_sees_all_of_it(world):
    """A line added, a custom value set and the completion all happen in one in-flight Sales
    transaction; the invoice made after it has exactly that committed content, never half of it."""
    tx = world.transaction(status="draft")
    with SessionLocal() as db:
        db.execute(
            text(
                "insert into custom_field_definitions (organization_id, entity_type, key, label, field_type, show_on_invoice, position) "
                "values (:o, 'transaction', 'po', 'PO', 'text', true, 10)"
            ),
            {"o": world.org_id},
        )
        db.commit()
    writer = SessionLocal()
    try:
        lock_transaction(writer, tx)
        writer.execute(
            text(
                "insert into transaction_lines (organization_id, transaction_id, position, description, unit, quantity, unit_price_ex_vat, vat_rate, net_amount, vat_amount, gross_amount) "
                "select organization_id, id, 9, 'Added late', 'u', 1, 1.00, 25.00, 1.00, 0.25, 1.25 from transactions where id = :t"
            ),
            {"t": tx},
        )
        writer.execute(
            text(
                "insert into custom_field_values (organization_id, definition_id, entity_type, field_type, entity_id, value_text) "
                "select d.organization_id, d.id, 'transaction', 'text', :t, 'PO-1' from custom_field_definitions d where d.organization_id = :o and d.key = 'po'"
            ),
            {"t": tx, "o": world.org_id},
        )
        writer.execute(text("update transactions set status = 'completed', version = version + 1 where id = :t"), {"t": tx})
        create = post_invoice(world, tx)
        assert create.still_waiting()
        writer.commit()
    finally:
        writer.close()

    body = create.result().json()
    assert [line["description"] for line in body["lines"]] == ["Horse massage", "Added late"]
    assert body["transactions"][0]["fields"][0]["value"] == "PO-1"
    assert body["transactions"][0]["source_version"] == 2
    assert Decimal(body["gross_amount"]) == Decimal("1062.50") + Decimal("1.25")


def test_a_custom_field_write_racing_creation_is_either_refused_or_in_the_invoice(world):
    """The field write needs a draft, the creation needs a completed transaction: so whichever
    holds the row first decides, and the other gets a clean refusal."""
    for _ in range(ROUNDS // 2):
        tx = world.transaction(status="completed")
        with SessionLocal() as db:
            line_id = db.execute(text("select id from transaction_lines where transaction_id = :t"), {"t": tx}).scalar_one()
        definition = scalar("select id from custom_field_definitions where organization_id = :o and key = 'note'", o=world.org_id)
        if definition is None:
            with SessionLocal() as db:
                db.execute(
                    text(
                        "insert into custom_field_definitions (organization_id, entity_type, key, label, field_type, show_on_invoice, position) "
                        "values (:o, 'transaction_line', 'note', 'Note', 'text', true, 10)"
                    ),
                    {"o": world.org_id},
                )
                db.commit()
        create, write = race(
            lambda: post_invoice(world, tx).result(),
            lambda: Request(world.headers, "patch", f"/api/custom-fields/entities/transaction_line/{line_id}/values", json={"values": {"note": "late"}}).result(),
        )
        assert create.status_code == 201  # the transaction is completed either way
        assert write.status_code == 409  # a completed transaction's custom values are locked
        assert create.json()["lines"][0]["fields"] == []
        assert scalar("select count(*) from custom_field_values where entity_id = :i", i=line_id) == 0


# --- party snapshots ------------------------------------------------------------------------------------------------------------------


def set_customer(world, state: str) -> None:
    with SessionLocal() as db:
        db.execute(
            text("update customers set name = :n, city = :c, vat_number = :v, email = :e where id = :i"),
            {"n": f"Name {state}", "c": f"City {state}", "v": f"VAT {state}", "e": f"{state}@example.test", "i": world.customer_id},
        )
        db.commit()


def snapshot_state(body: dict) -> str:
    snapshot = body["customer_snapshot"]
    states = {snapshot["name"][-1], snapshot["city"][-1], snapshot["vat_number"][-1], snapshot["email"][0].upper()}
    assert len(states) == 1, f"torn snapshot: {snapshot}"  # every column from the same version
    return states.pop()


def test_a_customer_change_in_flight_is_not_in_the_snapshot_and_a_committed_one_is_entirely(world):
    set_customer(world, "A")
    tx = world.transaction()
    changing = SessionLocal()
    try:
        changing.execute(
            text("update customers set name = 'Name B', city = 'City B', vat_number = 'VAT B', email = 'b@example.test' where id = :i"),
            {"i": world.customer_id},
        )  # uncommitted: no lock the creation would wait for
        before = world_create(world, tx)
        changing.commit()
    finally:
        changing.close()
    assert snapshot_state(before) == "A"

    after = world_create(world, world.transaction())
    assert snapshot_state(after) == "B"


def world_create(world, *tx_ids) -> dict:
    response = post_invoice(world, *tx_ids).result()
    assert response.status_code == 201, response.text
    return response.json()


def test_snapshots_taken_while_the_customer_flips_between_two_versions_are_never_torn(world):
    set_customer(world, "A")
    stop = threading.Event()

    def flip():
        state = "A"
        while not stop.is_set():
            state = "B" if state == "A" else "A"
            set_customer(world, state)

    flipper = threading.Thread(target=flip, daemon=True)
    flipper.start()
    try:
        seen = {snapshot_state(world_create(world, world.transaction())) for _ in range(ROUNDS)}
    finally:
        stop.set()
        flipper.join(10)
    assert seen <= {"A", "B"}


def test_an_issue_time_snapshot_is_also_deterministic_before_or_after(world):
    set_customer(world, "A")
    invoice = world.create_invoice(world.transaction())
    assert snapshot_state(invoice) == "A"
    changing = SessionLocal()
    try:
        changing.execute(
            text("update customers set name = 'Name B', city = 'City B', vat_number = 'VAT B', email = 'b@example.test' where id = :i"),
            {"i": world.customer_id},
        )
        issued_before = Request({**world.headers, **if_match(1)}, "post", f"{INVOICES}/{invoice['id']}/issue").result()
        changing.commit()
    finally:
        changing.close()
    assert issued_before.status_code == 200 and snapshot_state(issued_before.json()) == "A"

    second = world.create_invoice(world.transaction())
    set_customer(world, "A")
    set_customer(world, "B")
    issued_after = Request({**world.headers, **if_match(1)}, "post", f"{INVOICES}/{second['id']}/issue").result()
    assert snapshot_state(issued_after.json()) == "B"


# --- numbering ---------------------------------------------------------------------------------------------------------------------------


def test_concurrent_issuances_get_distinct_sequential_numbers(world):
    count = 8
    invoices = [world.create_invoice(world.transaction()) for _ in range(count)]
    responses = race(*[
        (lambda inv=inv: Request({**world.headers, **if_match(inv["version"])}, "post", f"{INVOICES}/{inv['id']}/issue").result())
        for inv in invoices
    ])
    assert [r.status_code for r in responses] == [200] * count
    numbers = sorted(r.json()["number"] for r in responses)
    assert numbers == list(range(1, count + 1))  # distinct, and exactly 1..N
    assert scalar("select next_number from invoice_counters where organization_id = :o", o=world.org_id) == count + 1
    assert scalar("select count(distinct number) from invoices where organization_id = :o", o=world.org_id) == count


def test_each_organization_numbers_independently_under_concurrency(world):
    other = build_committed_world("Other")
    try:
        mine = [world.create_invoice(world.transaction()) for _ in range(3)]
        theirs = [other.create_invoice(other.transaction()) for _ in range(3)]
        calls = [
            (lambda w=w, inv=inv: Request({**w.headers, **if_match(1)}, "post", f"{INVOICES}/{inv['id']}/issue").result())
            for w, group in ((world, mine), (other, theirs))
            for inv in group
        ]
        responses = race(*calls)
        assert sorted(r.json()["number"] for r in responses[:3]) == [1, 2, 3]
        assert sorted(r.json()["number"] for r in responses[3:]) == [1, 2, 3]
    finally:
        purge_organization(other.org_id, [other.user_id])


def test_a_failed_issuance_gives_its_number_back_and_a_waiting_one_takes_it(world, gate, monkeypatch):
    first, second = world.create_invoice(world.transaction()), world.create_invoice(world.transaction())
    real = numbering.allocate_number
    allocated = threading.Event()
    calls: list[int] = []

    def allocate_then_fail_once(*args, **kwargs):
        calls.append(1)
        number = real(*args, **kwargs)
        if len(calls) == 1:
            allocated.set()  # the counter row is locked by this (failing) issuance
            gate.wait(15)
            raise RuntimeError("boom after allocating")
        return number

    monkeypatch.setattr(numbering, "allocate_number", allocate_then_fail_once)
    failing = Request({**world.headers, **if_match(1)}, "post", f"{INVOICES}/{first['id']}/issue")
    assert allocated.wait(10)
    waiting = Request({**world.headers, **if_match(1)}, "post", f"{INVOICES}/{second['id']}/issue")
    assert waiting.still_waiting(), "the second issuance must wait for the counter row"
    gate.set()

    assert failing.result().status_code == 500
    response = waiting.result()
    assert response.status_code == 200 and response.json()["number"] == 1  # the failed number was never consumed
    assert scalar("select status from invoices where id = :i", i=uuid.UUID(first["id"])) == "draft"
    assert scalar("select number from invoices where id = :i", i=uuid.UUID(first["id"])) is None
    assert scalar("select next_number from invoice_counters where organization_id = :o", o=world.org_id) == 2


def test_an_issued_number_is_never_handed_out_again(world):
    first = world.create_invoice(world.transaction())
    issued = Request({**world.headers, **if_match(1)}, "post", f"{INVOICES}/{first['id']}/issue").result().json()
    draft = world.create_invoice(world.transaction())
    assert Request({**world.headers, **if_match(1)}, "delete", f"{INVOICES}/{draft['id']}").result().status_code == 204  # a deleted draft takes no number
    third = world.create_invoice(world.transaction())
    numbers = [issued["number"], Request({**world.headers, **if_match(1)}, "post", f"{INVOICES}/{third['id']}/issue").result().json()["number"]]
    assert numbers == [1, 2]
    # The issued invoice itself cannot be removed, so its number can never come back.
    assert Request({**world.headers, **if_match(2)}, "delete", f"{INVOICES}/{first['id']}").result().status_code == 409


# --- issue / delete / edit races ---------------------------------------------------------------------------------------------------------------


def test_issue_racing_delete_of_the_same_draft_has_one_consistent_winner(world):
    wins = {"issued": 0, "deleted": 0}
    for _ in range(ROUNDS):
        invoice = world.create_invoice(world.transaction())
        issue, delete = race(
            lambda: Request({**world.headers, **if_match(1)}, "post", f"{INVOICES}/{invoice['id']}/issue").result(),
            lambda: Request({**world.headers, **if_match(1)}, "delete", f"{INVOICES}/{invoice['id']}").result(),
        )
        exists = scalar("select count(*) from invoices where id = :i", i=uuid.UUID(invoice["id"]))
        if issue.status_code == 200:
            assert delete.status_code == 409 and delete.json()["detail"]["code"] == "invoice_issued"
            assert exists == 1
            wins["issued"] += 1
        else:
            assert issue.status_code == 404 and delete.status_code == 204 and exists == 0
            wins["deleted"] += 1
    assert sum(wins.values()) == ROUNDS


def test_two_simultaneous_issuances_of_one_draft_issue_it_once(world):
    for _ in range(ROUNDS // 2):
        invoice = world.create_invoice(world.transaction())
        first, second = race(
            lambda: Request({**world.headers, **if_match(1)}, "post", f"{INVOICES}/{invoice['id']}/issue").result(),
            lambda: Request({**world.headers, **if_match(1)}, "post", f"{INVOICES}/{invoice['id']}/issue").result(),
        )
        assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert scalar("select next_number from invoice_counters where organization_id = :o", o=world.org_id) == ROUNDS // 2 + 1


def test_an_edit_racing_an_issue_has_one_consistent_winner(world):
    for _ in range(ROUNDS // 2):
        invoice = world.create_invoice(world.transaction())
        issue, edit = race(
            lambda: Request({**world.headers, **if_match(1)}, "post", f"{INVOICES}/{invoice['id']}/issue").result(),
            lambda: Request({**world.headers, **if_match(1)}, "patch", f"{INVOICES}/{invoice['id']}", json={"description": "late edit"}).result(),
        )
        stored = scalar("select description from invoices where id = :i", i=uuid.UUID(invoice["id"]))
        status = scalar("select status from invoices where id = :i", i=uuid.UUID(invoice["id"]))
        if issue.status_code == 200:
            # Issuance took the lock first: the late edit is refused and the document is untouched.
            assert edit.status_code == 409 and edit.json()["detail"]["code"] == "invoice_issued"
            assert (stored, status) == (None, "issued")
        else:
            # The edit took the lock first and moved the version, so the issuance (based on version 1) is stale.
            assert edit.status_code == 200 and issue.status_code == 409
            assert issue.json()["detail"]["code"] == "stale_record"
            assert (stored, status) == ("late edit", "draft")


def test_two_simultaneous_edits_on_one_version_exactly_one_wins(world):
    invoice = world.create_invoice(world.transaction())
    first, second = race(
        lambda: Request({**world.headers, **if_match(1)}, "patch", f"{INVOICES}/{invoice['id']}", json={"description": "one"}).result(),
        lambda: Request({**world.headers, **if_match(1)}, "patch", f"{INVOICES}/{invoice['id']}", json={"description": "two"}).result(),
    )
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert scalar("select version from invoices where id = :i", i=uuid.UUID(invoice["id"])) == 2


# --- failures leave nothing behind (committed) ----------------------------------------------------------------------------------------------


def test_a_failed_creation_leaves_no_committed_trace_and_the_transaction_stays_free(world, monkeypatch):
    tx = world.transaction()
    real = service._insert_children

    def fail_late(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "_insert_children", fail_late)
    assert post_invoice(world, tx).result().status_code == 500
    monkeypatch.undo()
    for table in ("invoices", "invoice_transactions", "invoice_lines", "invoice_vat_rows"):
        assert scalar(f"select count(*) from {table} where organization_id = :o", o=world.org_id) == 0, table
    assert post_invoice(world, tx).result().status_code == 201


def test_a_failed_issuance_leaves_the_draft_and_the_counter_untouched(world, monkeypatch):
    invoice = world.create_invoice(world.transaction())
    real = numbering.allocate_number

    def fail_after(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("boom")

    monkeypatch.setattr(numbering, "allocate_number", fail_after)
    assert Request({**world.headers, **if_match(1)}, "post", f"{INVOICES}/{invoice['id']}/issue").result().status_code == 500
    monkeypatch.undo()
    assert scalar("select count(*) from invoice_counters where organization_id = :o", o=world.org_id) == 0
    row = scalar("select status || '|' || version || '|' || coalesce(number::text, '-') from invoices where id = :i", i=uuid.UUID(invoice["id"]))
    assert row == "draft|1|-"


def test_issuing_waits_for_a_lock_held_on_a_source_transaction(world):
    """Issuing re-verifies the sources under their row locks, so it cannot read them while a
    Sales writer is mid-change (here: an uncommitted session holding the row)."""
    invoice = world.create_invoice(world.transaction())
    tx = uuid.UUID(invoice["transactions"][0]["transaction_id"])
    holder = SessionLocal()
    try:
        lock_transaction(holder, tx)
        issuing = Request({**world.headers, **if_match(1)}, "post", f"{INVOICES}/{invoice['id']}/issue")
        assert issuing.still_waiting(), "issuing must wait for the source transaction's lock"
        holder.rollback()
    finally:
        holder.close()
    assert issuing.result().status_code == 200


def test_issuing_after_a_committed_change_of_a_source_refuses(world):
    """If a writer did get a change in (here by raw SQL, which the API would never allow), the
    issuance that waited for it sees it and refuses instead of issuing different content."""
    invoice = world.create_invoice(world.transaction())
    tx = uuid.UUID(invoice["transactions"][0]["transaction_id"])
    holder = SessionLocal()
    try:
        lock_transaction(holder, tx)
        holder.execute(text("update transactions set version = version + 1 where id = :t"), {"t": tx})
        issuing = Request({**world.headers, **if_match(1)}, "post", f"{INVOICES}/{invoice['id']}/issue")
        assert issuing.still_waiting()
        holder.commit()
    finally:
        holder.close()
    response = issuing.result()
    assert response.status_code == 409 and response.json()["detail"]["code"] == "source_changed"
    assert scalar("select count(*) from invoice_counters where organization_id = :o", o=world.org_id) == 0
