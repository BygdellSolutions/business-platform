"""The core lifecycle seam now covers reopen and cancel, not only complete.

Sales asks the seam before every status change. Whatever registered a validator may veto it;
Sales never learns who. No real validator vetoes a reopen or cancel yet (the invoicing module,
which will, does not exist), so these tests use a synthetic one registered on an isolated
registry, exactly as a future module would.
"""

import uuid

import pytest

from app.core.entity_registry import registry
from app.core.lifecycle import CANCEL, COMPLETE, REOPEN, Problem
from tests.factories import make_transaction

TX = "/api/transactions"
STEPS = {"complete": COMPLETE, "reopen": REOPEN, "cancel": CANCEL}


def state(client, sales, tx_id) -> dict:
    body = client.get(f"{TX}/{tx_id}", headers=sales.headers).json()
    return {"status": body["status"], "version": body["version"]}


class Recorder:
    """A validator that records every question and vetoes the events it is told to."""

    def __init__(self, veto: set[str] = frozenset()):
        self.veto = set(veto)
        self.calls: list[tuple[str, str, uuid.UUID, uuid.UUID]] = []

    def __call__(self, db, ctx, event, entity_key, entity_id):
        self.calls.append((event, entity_key, entity_id, ctx.organization_id))
        if event in self.veto:
            return [Problem(code="test.veto", message=f"No {event}.", entity_type=entity_key, entity_id=str(entity_id))]
        return []


def test_the_event_names_are_stable_strings():
    assert (COMPLETE, REOPEN, CANCEL) == ("complete", "reopen", "cancel")


@pytest.mark.parametrize("step", ["complete", "reopen", "cancel"])
def test_every_status_step_asks_the_seam_with_its_own_event(client, db_session, sales, step):
    start = "draft" if step in ("complete", "cancel") else "completed"
    tx = make_transaction(db_session, sales.org, billing_customer=sales.billing, status=start)
    recorder = Recorder()
    with registry.isolated():
        registry.add_validator(recorder)
        response = client.post(f"{TX}/{tx.id}/{step}", headers=sales.headers)

    assert response.status_code == 200, response.text
    # Asked exactly once, about THIS transaction in THIS organization, with the step's event.
    assert recorder.calls == [(STEPS[step], "transaction", tx.id, sales.org.id)]


@pytest.mark.parametrize("step, start", [("complete", "draft"), ("reopen", "completed"), ("cancel", "draft"), ("cancel", "completed")])
def test_a_veto_blocks_the_step_and_changes_nothing(client, db_session, sales, step, start):
    tx = make_transaction(db_session, sales.org, billing_customer=sales.billing, status=start)
    before = state(client, sales, tx.id)
    with registry.isolated():
        registry.add_validator(Recorder(veto={STEPS[step]}))
        response = client.post(f"{TX}/{tx.id}/{step}", headers=sales.headers)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert (detail["code"], detail["event"], detail["total"]) == ("validation_failed", STEPS[step], 1)
    assert detail["problems"][0]["code"] == "test.veto"
    assert detail["problems"][0]["entity_id"] == str(tx.id)
    assert state(client, sales, tx.id) == before  # status AND version unchanged


def test_a_veto_of_one_event_does_not_block_the_others(client, db_session, sales):
    tx = make_transaction(db_session, sales.org, billing_customer=sales.billing)
    with registry.isolated():
        registry.add_validator(Recorder(veto={REOPEN}))
        assert client.post(f"{TX}/{tx.id}/complete", headers=sales.headers).status_code == 200
        assert client.post(f"{TX}/{tx.id}/reopen", headers=sales.headers).status_code == 409
        assert client.post(f"{TX}/{tx.id}/cancel", headers=sales.headers).status_code == 200  # cancel was not vetoed
    assert state(client, sales, tx.id)["status"] == "cancelled"


def test_every_registered_validator_gets_to_speak_and_problems_add_up(client, db_session, sales):
    tx = make_transaction(db_session, sales.org, billing_customer=sales.billing, status="completed")
    with registry.isolated():
        registry.add_validator(Recorder(veto={REOPEN}))
        registry.add_validator(Recorder(veto={REOPEN}))
        registry.add_validator(Recorder())
        response = client.post(f"{TX}/{tx.id}/reopen", headers=sales.headers)

    assert response.json()["detail"]["total"] == 2


def test_once_the_validator_is_gone_the_step_works_again(client, db_session, sales):
    tx = make_transaction(db_session, sales.org, billing_customer=sales.billing, status="completed")
    with registry.isolated():
        registry.add_validator(Recorder(veto={REOPEN}))
        assert client.post(f"{TX}/{tx.id}/reopen", headers=sales.headers).status_code == 409
    assert client.post(f"{TX}/{tx.id}/reopen", headers=sales.headers).status_code == 200


def test_a_wrong_state_or_a_stale_version_is_refused_before_any_validator_is_asked(raw_client, db_session, sales):
    done = make_transaction(db_session, sales.org, billing_customer=sales.billing, status="completed")
    draft = make_transaction(db_session, sales.org, billing_customer=sales.billing)
    recorder = Recorder()
    with registry.isolated():
        registry.add_validator(recorder)
        # Reopening a draft: the state rules answer first.
        assert raw_client.post(f"{TX}/{draft.id}/reopen", headers={**sales.headers, "If-Match": '"1"'}).status_code == 409
        # A stale If-Match on a legitimate step: the precondition answers first.
        stale = raw_client.post(f"{TX}/{done.id}/reopen", headers={**sales.headers, "If-Match": '"99"'})
        assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_record"
        # No precondition at all.
        assert raw_client.post(f"{TX}/{done.id}/cancel", headers=sales.headers).status_code == 428
    assert recorder.calls == []


def test_an_unknown_or_foreign_transaction_never_reaches_the_seam(client, db_session, sales):
    from tests.factories import make_org, make_transaction as make_tx

    foreign = make_tx(db_session, make_org(db_session, "Foreign"), status="completed")
    recorder = Recorder()
    with registry.isolated():
        registry.add_validator(recorder)
        for tx_id in (foreign.id, uuid.uuid4()):
            for step in STEPS:
                assert client.post(f"{TX}/{tx_id}/{step}", headers=sales.headers).status_code == 404
    assert recorder.calls == []


def test_validators_that_do_not_know_an_event_ignore_it(client, db_session, sales):
    """The real validator registered today (required custom fields) must not react to reopen or cancel."""
    done = make_transaction(db_session, sales.org, billing_customer=sales.billing, status="completed")
    draft = make_transaction(db_session, sales.org, billing_customer=sales.billing)
    from tests.factories import make_definition

    make_definition(db_session, sales.org, entity_type="transaction", key="needed", required=True)
    assert client.post(f"{TX}/{done.id}/reopen", headers=sales.headers).status_code == 200
    assert client.post(f"{TX}/{draft.id}/cancel", headers=sales.headers).status_code == 200  # a required field does not block a cancel
    assert client.post(f"{TX}/{done.id}/complete", headers=sales.headers).status_code == 409  # but it does block completion


def test_no_validator_of_a_missing_module_is_registered_yet():
    """Invoicing will register its own validator when it exists. Until then nothing from it is
    registered (the module boundary test separately forbids anything importing an invoicing package)."""
    names = {getattr(v, "__name__", type(v).__name__) for v in registry.validators}
    assert not any("invoic" in name.lower() for name in names)
