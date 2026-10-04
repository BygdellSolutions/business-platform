"""First downloads of an invoice PDF under real concurrency: committed data, separate connections.

Several requests may ask for the PDF of the same issued invoice at the same instant, before any artifact
exists. Each may render (duplicate rendering is allowed), but exactly ONE artifact becomes canonical and
every caller receives that artifact's bytes. No lock or transaction is held while ReportLab renders.

The renderer is deterministic, so two renderings of one invoice are identical and could not be told apart.
These tests therefore replace `render_pdf` with a wrapper that makes every rendering UNIQUE (it appends a
comment after the end of the file), and uses gates to hold requests inside the render step.
"""

import itertools
import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import engine
from app.main import app
from app.modules.invoicing.pdf import render
from tests.invoicing_support import INVOICES, Request, build_committed_world, if_match, purge_organization, race, scalar

ROUNDS = 5
WAIT_FOR_GATE = 15


@pytest.fixture
def world(dev_auth):
    made = build_committed_world("PdfRace")
    extra: list[tuple[uuid.UUID, uuid.UUID]] = []
    made.extra = extra  # other committed worlds a test made, purged too
    yield made
    purge_organization(made.org_id, [made.user_id])
    for org_id, user_id in extra:
        purge_organization(org_id, [user_id])


def issued(world) -> str:
    """A new issued invoice of the world, committed."""
    draft = world.create_invoice(world.transaction())
    response = TestClient(app).post(f"{INVOICES}/{draft['id']}/issue", headers={**world.headers, **if_match(draft["version"])})
    assert response.status_code == 200, response.text
    return draft["id"]


def pdf_request(world, invoice_id: str) -> Request:
    return Request(world.headers, "get", f"{INVOICES}/{invoice_id}/pdf")


def rows(invoice_id: str) -> list[tuple[bytes, str]]:
    with engine.connect() as connection:
        return [(bytes(c), h) for c, h in connection.execute(text("select content, sha256 from invoice_pdfs where invoice_id = :i"), {"i": invoice_id})]


class UniqueRenders:
    """Make every rendering different, count them, and optionally stop them at a gate."""

    def __init__(self, monkeypatch, *, gate: threading.Barrier | None = None, hold_first: threading.Event | None = None):
        self.real = render.render_pdf
        self.count = itertools.count(1)
        self.rendered: list[bytes] = []
        self.gate, self.hold_first, self.entered_first = gate, hold_first, threading.Event()
        self._lock = threading.Lock()
        monkeypatch.setattr(render, "render_pdf", self)

    def __call__(self, document):
        with self._lock:
            number = next(self.count)
        data = self.real(document) + b"\n% rendering " + str(number).encode() + b"\n"
        with self._lock:
            self.rendered.append(data)
        if self.gate is not None:
            self.gate.wait(WAIT_FOR_GATE)  # every request has finished rendering before any may store
        if number == 1 and self.hold_first is not None:
            self.entered_first.set()
            assert self.hold_first.wait(WAIT_FOR_GATE), "the held first rendering was never released"
        return data


def test_simultaneous_first_downloads_store_one_artifact_and_every_caller_gets_its_bytes(world, monkeypatch):
    for _ in range(ROUNDS):
        invoice = issued(world)
        renders = UniqueRenders(monkeypatch, gate=threading.Barrier(3))  # three callers, all rendered before any stores
        responses = race(*[lambda: pdf_request(world, invoice).result() for _ in range(3)])
        assert [r.status_code for r in responses] == [200, 200, 200]
        ((stored_bytes, stored_sha),) = rows(invoice)
        assert len(renders.rendered) == 3 and len(set(renders.rendered)) == 3  # genuinely three different renderings
        assert stored_bytes in renders.rendered
        for response in responses:
            assert response.content == stored_bytes  # not their own rendering: THE artifact
            assert response.headers["etag"] == f'"{stored_sha}"'
        monkeypatch.undo()
        monkeypatch.setattr(render, "render_pdf", lambda d: pytest.fail("rendered again"))
        assert TestClient(app).get(f"{INVOICES}/{invoice}/pdf", headers=world.headers).content == stored_bytes
        monkeypatch.undo()


def test_a_late_loser_discards_its_rendering_and_returns_the_winners_bytes(world, monkeypatch):
    invoice = issued(world)
    release = threading.Event()
    renders = UniqueRenders(monkeypatch, hold_first=release)

    slow = pdf_request(world, invoice)  # rendering #1: held inside the render step
    assert renders.entered_first.wait(WAIT_FOR_GATE)
    assert slow.still_waiting()
    quick = pdf_request(world, invoice).result()  # rendering #2: stores first and wins
    assert quick.status_code == 200
    ((stored_bytes, _),) = rows(invoice)
    assert stored_bytes == quick.content and stored_bytes == renders.rendered[1]

    release.set()  # the held request now finishes rendering #1 and tries to store it
    late = slow.result()
    assert late.status_code == 200
    assert late.content == quick.content and late.content != renders.rendered[0]  # rendering #1 was discarded
    assert len(rows(invoice)) == 1 and rows(invoice)[0][0] == quick.content


def test_many_callers_at_once_still_one_artifact(world):
    for _ in range(ROUNDS):
        invoice = issued(world)
        responses = race(*[lambda: pdf_request(world, invoice).result() for _ in range(8)])
        assert {r.status_code for r in responses} == {200}
        ((stored_bytes, _),) = rows(invoice)
        assert {r.content for r in responses} == {stored_bytes}


def test_no_transaction_and_no_row_lock_is_held_while_the_pdf_renders(world, monkeypatch):
    invoice = issued(world)
    seen: dict[str, object] = {}
    real = render.render_pdf

    def spy(document):
        with engine.connect() as other:
            seen["idle_in_transaction"] = other.execute(
                text("select count(*) from pg_stat_activity where datname = current_database() and state like 'idle in transaction%' and pid <> pg_backend_pid()")
            ).scalar_one()
            other.execute(text("set local lock_timeout = '1s'"))
            seen["invoice_lockable"] = other.execute(text("select id from invoices where id = :i for update nowait"), {"i": invoice}).scalar_one() is not None
            seen["org_lockable"] = other.execute(text("select id from organizations where id = :o for update nowait"), {"o": world.org_id}).scalar_one() is not None
            other.rollback()
        return real(document)

    monkeypatch.setattr(render, "render_pdf", spy)
    assert pdf_request(world, invoice).result().status_code == 200
    assert seen == {"idle_in_transaction": 0, "invoice_lockable": True, "org_lockable": True}


def test_two_organizations_downloading_at_once_each_get_their_own_artifact(world):
    other = build_committed_world("PdfRaceB")
    world.extra.append((other.org_id, other.user_id))
    mine, theirs = issued(world), issued(other)
    mine_response, theirs_response = race(
        lambda: pdf_request(world, mine).result(),
        lambda: pdf_request(other, theirs).result(),
    )
    assert mine_response.status_code == theirs_response.status_code == 200
    assert rows(mine)[0][0] == mine_response.content and rows(theirs)[0][0] == theirs_response.content
    assert scalar("select count(*) from invoice_pdfs where organization_id = :o", o=world.org_id) == 1
    assert scalar("select count(*) from invoice_pdfs where organization_id = :o", o=other.org_id) == 1
    # and neither can fetch the other's through its own identity
    crossed = Request(world.headers, "get", f"{INVOICES}/{theirs}/pdf").result()
    assert crossed.status_code == 404 and b"%PDF" not in crossed.content


def test_a_failed_first_download_leaves_nothing_behind_and_a_retry_succeeds(world, monkeypatch):
    invoice = issued(world)

    def broken(document):
        raise RuntimeError("renderer crashed")

    monkeypatch.setattr(render, "render_pdf", broken)
    crashed = Request(world.headers, "get", f"{INVOICES}/{invoice}/pdf").result()
    assert crashed.status_code == 500 and rows(invoice) == []
    monkeypatch.undo()
    retry = pdf_request(world, invoice).result()
    assert retry.status_code == 200 and rows(invoice)[0][0] == retry.content
