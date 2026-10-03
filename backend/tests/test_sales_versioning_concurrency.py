"""Optimistic concurrency with REAL, COMMITTED data and separate connections.

The rest of the suite runs inside one rolled-back transaction, which can show neither a stale
tab nor a race. Here every request opens its own session on committed rows, so "tab A saved,
tab B is stale" and "two requests at the same instant" are exactly what happens in production.
Each test builds its own organizations and always purges them.

What is proven (versioning rules: app/modules/sales/versioning.py):
  - a stale tab cannot overwrite a newer edit of a line or of the header;
  - a stale delete cannot delete a line that changed elsewhere;
  - a stale lifecycle step fails safely, and a refused change changes NOTHING;
  - a fresh version works normally;
  - of two simultaneous writers on the same version, exactly one wins;
  - none of it is reachable, or revealed, across organizations.
"""

import threading
import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.db import SessionLocal, engine
from app.main import app
from app.models import Customer, Organization, Role
from app.modules.sales.models import TransactionLine
from tests.factories import add_member, make_customer, make_org, make_transaction, make_user

LINE = {"description": "Travel", "unit": "km", "quantity": "1", "unit_price_ex_vat": "2.50", "vat_rate": "25.00"}


@dataclass
class World:
    org_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    customer_id: uuid.UUID
    other_customer_id: uuid.UUID

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Dev-User-Email": self.email}

    def tab(self) -> "Tab":
        return Tab(self)


class Tab:
    """One browser tab: its own client, and the transaction exactly as it was last loaded."""

    def __init__(self, world: World):
        self.world = world
        self.http = TestClient(app)

    def call(self, method: str, url: str, version: int | None = None, **kwargs):
        headers = {**self.world.headers, **({} if version is None else {"If-Match": f'"{version}"'})}
        return getattr(self.http, method)(url, headers=headers, **kwargs)

    def load(self, tx_id) -> dict:
        response = self.call("get", f"/api/transactions/{tx_id}")
        assert response.status_code == 200, response.text
        return response.json()


def build_world() -> World:
    with SessionLocal() as db:
        org = make_org(db, f"Versions {uuid.uuid4().hex[:8]}")
        user = make_user(db)
        add_member(db, org, user, Role.OWNER)
        customer = make_customer(db, org, "Billing")
        other = make_customer(db, org, "Other")
        world = World(org.id, user.id, user.email, customer.id, other.id)
        db.commit()
    return world


def new_transaction(world: World, lines: int = 2) -> uuid.UUID:
    with SessionLocal() as db:
        tx = make_transaction(
            db,
            db.get(Organization, world.org_id),
            billing_customer=db.get(Customer, world.customer_id),
            lines=[{"description": f"Line {number}"} for number in range(1, lines + 1)],
        )
        tx_id = tx.id
        db.commit()
    return tx_id


def purge(world: World) -> None:
    with engine.begin() as conn:
        for statement in (
            "delete from transaction_lines where organization_id = :o",
            "delete from transactions where organization_id = :o",
            "delete from customers where organization_id = :o",
            "delete from organization_users where organization_id = :o",
            "delete from organizations where id = :o",
        ):
            conn.execute(text(statement), {"o": world.org_id})
        conn.execute(text("delete from users where id = :u"), {"u": world.user_id})


@pytest.fixture
def worlds(dev_auth):
    made: list[World] = []

    def build() -> World:
        world = build_world()
        made.append(world)
        return world

    yield build
    for world in made:
        purge(world)


def row(tx_id) -> dict:
    """The transaction as committed in the database, read on a fresh connection."""
    with engine.connect() as conn:
        tx = conn.execute(
            text("select status, version, header_version, billing_customer_id, transaction_date::text as day, updated_at from transactions where id = :i"),
            {"i": tx_id},
        ).mappings().one()
        lines = conn.execute(
            text("select id, description, quantity::text as quantity, unit_price_ex_vat::text as price, net_amount::text as net, version, updated_at from transaction_lines where transaction_id = :i order by position"),
            {"i": tx_id},
        ).mappings().all()
    return {**tx, "lines": [dict(line) for line in lines]}


# --- a stale tab cannot overwrite -------------------------------------------------------------------------------


def test_tab_a_saves_a_line_then_stale_tab_b_cannot_overwrite_it(worlds):
    world = worlds()
    tx_id = new_transaction(world)
    tab_a, tab_b = world.tab(), world.tab()
    seen_a, seen_b = tab_a.load(tx_id), tab_b.load(tx_id)
    line = seen_a["lines"][0]
    assert line["version"] == seen_b["lines"][0]["version"] == 1

    saved = tab_a.call("patch", f"/api/transactions/{tx_id}/lines/{line['id']}", line["version"], json={"description": "From tab A", "quantity": "2"})
    assert saved.status_code == 200

    stale = tab_b.call("patch", f"/api/transactions/{tx_id}/lines/{line['id']}", line["version"], json={"description": "From tab B", "quantity": "9"})

    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_record"
    committed = row(tx_id)["lines"][0]
    assert (committed["description"], committed["quantity"], committed["version"]) == ("From tab A", "2.000", 2)


def test_a_stale_header_edit_cannot_overwrite_a_newer_header_edit(worlds):
    world = worlds()
    tx_id = new_transaction(world)
    tab_a, tab_b = world.tab(), world.tab()
    seen_a, seen_b = tab_a.load(tx_id), tab_b.load(tx_id)

    newer = tab_a.call("patch", f"/api/transactions/{tx_id}", seen_a["header_version"], json={"transaction_date": "2026-11-11", "billing_customer_id": str(world.other_customer_id)})
    assert newer.status_code == 200

    stale = tab_b.call("patch", f"/api/transactions/{tx_id}", seen_b["header_version"], json={"transaction_date": "2030-05-05"})

    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_record"
    committed = row(tx_id)
    assert (committed["day"], committed["billing_customer_id"], committed["header_version"]) == ("2026-11-11", world.other_customer_id, 2)


def test_a_stale_delete_cannot_delete_a_line_changed_elsewhere(worlds):
    world = worlds()
    tx_id = new_transaction(world)
    tab_a, tab_b = world.tab(), world.tab()
    line = tab_a.load(tx_id)["lines"][0]
    tab_b.load(tx_id)
    tab_a.call("patch", f"/api/transactions/{tx_id}/lines/{line['id']}", 1, json={"quantity": "7"})

    stale = tab_b.call("delete", f"/api/transactions/{tx_id}/lines/{line['id']}", 1)

    assert stale.status_code == 409
    after = row(tx_id)
    assert len(after["lines"]) == 2 and after["lines"][0]["quantity"] == "7.000"


def test_stale_lifecycle_actions_fail_safely(worlds):
    world = worlds()
    tx_id = new_transaction(world)
    tab_a, tab_b = world.tab(), world.tab()
    tab_a.load(tx_id)
    seen_b = tab_b.load(tx_id)

    # Tab A adds a line; tab B, which never saw it, tries to complete / cancel.
    assert tab_a.call("post", f"/api/transactions/{tx_id}/lines", json=LINE).status_code == 201
    for action in ("complete", "cancel"):
        stale = tab_b.call("post", f"/api/transactions/{tx_id}/{action}", seen_b["version"])
        assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_record", action
    after = row(tx_id)
    assert (after["status"], after["version"], len(after["lines"])) == ("draft", 2, 3)

    # Tab A completes. Tab B, still on the old version, is told about the status, not allowed through.
    assert tab_a.call("post", f"/api/transactions/{tx_id}/complete", 2).status_code == 200
    again = tab_b.call("post", f"/api/transactions/{tx_id}/complete", seen_b["version"])
    edit = tab_b.call("patch", f"/api/transactions/{tx_id}", seen_b["header_version"], json={"transaction_date": "2030-01-01"})
    assert again.status_code == edit.status_code == 409
    assert "completed transaction cannot be" in again.json()["detail"] and "completed transaction cannot be" in edit.json()["detail"]
    final = row(tx_id)
    assert (final["status"], final["version"]) == ("completed", 3)


def test_a_stale_reopen_fails_safely(worlds):
    world = worlds()
    tx_id = new_transaction(world)
    tab_a, tab_b = world.tab(), world.tab()
    assert tab_a.call("post", f"/api/transactions/{tx_id}/complete", 1).status_code == 200
    seen_b = tab_b.load(tx_id)  # completed, version 2
    assert tab_a.call("post", f"/api/transactions/{tx_id}/reopen", 2).status_code == 200  # reopened, version 3

    stale = tab_b.call("post", f"/api/transactions/{tx_id}/reopen", seen_b["version"])  # reopening a draft
    assert stale.status_code == 409 and "draft transaction cannot be reopened" in stale.json()["detail"]
    assert row(tx_id)["status"] == "draft"


# --- a refused change changes nothing ---------------------------------------------------------------------------


def test_a_refused_change_leaves_every_column_version_and_timestamp_untouched(worlds):
    world = worlds()
    tx_id = new_transaction(world)
    tab = world.tab()
    seen = tab.load(tx_id)
    assert tab.call("patch", f"/api/transactions/{tx_id}", seen["header_version"], json={"transaction_date": "2026-11-11"}).status_code == 200
    line = seen["lines"][0]
    assert tab.call("patch", f"/api/transactions/{tx_id}/lines/{line['id']}", 1, json={"quantity": "3"}).status_code == 200
    before = row(tx_id)

    refused = [
        tab.call("patch", f"/api/transactions/{tx_id}", 1, json={"transaction_date": "2040-01-01", "billing_customer_id": str(world.other_customer_id)}),
        tab.call("patch", f"/api/transactions/{tx_id}/lines/{line['id']}", 1, json={"description": "x", "quantity": "8", "unit_price_ex_vat": "50.00", "vat_rate": "6"}),
        tab.call("delete", f"/api/transactions/{tx_id}/lines/{line['id']}", 1),
        tab.call("post", f"/api/transactions/{tx_id}/complete", 1),
        tab.call("post", f"/api/transactions/{tx_id}/cancel", 1),
        tab.call("delete", f"/api/transactions/{tx_id}", 1),
        tab.call("patch", f"/api/transactions/{tx_id}", None, json={"transaction_date": "2040-01-01"}),  # no header at all
    ]

    assert [response.status_code for response in refused] == [409, 409, 409, 409, 409, 409, 428]
    assert row(tx_id) == before  # every column, every version, both updated_at stamps


# --- a fresh version continues normally --------------------------------------------------------------------------


def test_after_reloading_the_stale_tab_can_save_normally(worlds):
    world = worlds()
    tx_id = new_transaction(world)
    tab_a, tab_b = world.tab(), world.tab()
    line = tab_a.load(tx_id)["lines"][0]
    tab_b.load(tx_id)
    tab_a.call("patch", f"/api/transactions/{tx_id}/lines/{line['id']}", 1, json={"description": "A"})
    assert tab_b.call("patch", f"/api/transactions/{tx_id}/lines/{line['id']}", 1, json={"description": "B"}).status_code == 409

    reloaded = tab_b.load(tx_id)["lines"][0]
    assert reloaded["version"] == 2 and reloaded["description"] == "A"
    saved = tab_b.call("patch", f"/api/transactions/{tx_id}/lines/{line['id']}", reloaded["version"], json={"description": "B after reload"})

    assert saved.status_code == 200 and saved.json()["version"] == 3
    assert row(tx_id)["lines"][0]["description"] == "B after reload"


def test_a_long_run_of_fresh_edits_keeps_working(worlds):
    world = worlds()
    tx_id = new_transaction(world, lines=1)
    tab = world.tab()
    line = tab.load(tx_id)["lines"][0]
    version = line["version"]
    for quantity in ("2", "3", "4", "5", "6"):
        response = tab.call("patch", f"/api/transactions/{tx_id}/lines/{line['id']}", version, json={"quantity": quantity})
        assert response.status_code == 200
        version = response.json()["version"]
    assert version == 6
    assert tab.call("post", f"/api/transactions/{tx_id}/complete", tab.load(tx_id)["version"]).status_code == 200


# --- two requests at the same instant ----------------------------------------------------------------------------


def race(*calls):
    """Run the calls at the same moment on separate connections; return their responses in order."""
    barrier = threading.Barrier(len(calls))
    results: list = [None] * len(calls)

    def runner(index, call):
        barrier.wait(timeout=10)
        try:
            results[index] = call()
        except Exception as exc:  # reported by the assertions
            results[index] = exc

    threads = [threading.Thread(target=runner, args=(i, call), daemon=True) for i, call in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
        assert not thread.is_alive(), "a request never finished"
    for result in results:
        assert not isinstance(result, Exception), result
    return results


def test_two_simultaneous_edits_of_the_same_line_version_exactly_one_wins(worlds):
    world = worlds()
    tx_id = new_transaction(world, lines=6)
    seen = world.tab().load(tx_id)
    for line in seen["lines"]:  # six rounds, each a fresh race
        a, b = world.tab(), world.tab()
        url = f"/api/transactions/{tx_id}/lines/{line['id']}"

        first, second = race(
            lambda: a.call("patch", url, 1, json={"description": "from A", "quantity": "2"}),
            lambda: b.call("patch", url, 1, json={"description": "from B", "quantity": "3"}),
        )

        assert sorted([first.status_code, second.status_code]) == [200, 409]
        winner = "from A" if first.status_code == 200 else "from B"
        stored = next(item for item in row(tx_id)["lines"] if item["id"] == uuid.UUID(line["id"]))
        assert (stored["description"], stored["version"]) == (winner, 2)
        assert stored["quantity"] == ("2.000" if winner == "from A" else "3.000")  # one writer's fields, never a mix


def test_complete_and_cancel_at_the_same_instant_on_one_version_exactly_one_wins(worlds):
    world = worlds()
    for _ in range(4):
        tx_id = new_transaction(world)
        a, b = world.tab(), world.tab()

        done, cancelled = race(
            lambda: a.call("post", f"/api/transactions/{tx_id}/complete", 1),
            lambda: b.call("post", f"/api/transactions/{tx_id}/cancel", 1),
        )

        assert sorted([done.status_code, cancelled.status_code]) == [200, 409]
        final = row(tx_id)
        assert final["status"] == ("completed" if done.status_code == 200 else "cancelled")
        assert final["version"] == 2


def test_a_line_added_at_the_same_instant_as_a_completion_is_never_completed_unseen(worlds):
    """The completer decided on version 1. Either the line lands first (and the completion is
    refused as stale) or the completion lands first (and the line is refused as non-draft)."""
    world = worlds()
    for _ in range(6):
        tx_id = new_transaction(world)
        a, b = world.tab(), world.tab()

        added, completed = race(
            lambda: a.call("post", f"/api/transactions/{tx_id}/lines", json=LINE),
            lambda: b.call("post", f"/api/transactions/{tx_id}/complete", 1),
        )

        final = row(tx_id)
        if final["status"] == "completed":
            assert completed.status_code == 200 and added.status_code == 409
            assert len(final["lines"]) == 2  # exactly the lines the completer saw
        else:
            assert completed.status_code == 409 and added.status_code == 201
            assert len(final["lines"]) == 3


# --- other organizations --------------------------------------------------------------------------------------------


def test_another_organizations_user_gets_the_same_404_for_a_real_and_a_random_transaction(worlds):
    owner_world, outsider_world = worlds(), worlds()
    tx_id = new_transaction(owner_world)
    line_id = world_line_id = row(tx_id)["lines"][0]["id"]
    outsider = outsider_world.tab()
    before = row(tx_id)

    for token in (None, 1, 2, 999):  # the real version, a wrong one, nothing
        for tx, line in ((tx_id, line_id), (uuid.uuid4(), uuid.uuid4())):
            responses = [
                outsider.call("patch", f"/api/transactions/{tx}", token, json={"transaction_date": "2030-01-01"}),
                outsider.call("delete", f"/api/transactions/{tx}", token),
                outsider.call("post", f"/api/transactions/{tx}/complete", token),
                outsider.call("patch", f"/api/transactions/{tx}/lines/{line}", token, json={"quantity": "2"}),
                outsider.call("delete", f"/api/transactions/{tx}/lines/{line}", token),
            ]
            assert [r.status_code for r in responses] == [404] * 5
            assert all(r.json() == {"detail": "Not found"} for r in responses)

    assert row(tx_id) == before
    assert world_line_id == line_id
