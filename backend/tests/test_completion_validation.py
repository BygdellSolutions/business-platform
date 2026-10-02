"""Required custom fields must be filled before a transaction is completed (and locked).

Sales calls the generic core lifecycle seam; custom fields registered a validator on it.
Neither knows the other. These tests also exercise the seam itself with a synthetic
validator, so other modules can add checks the same way.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.db import engine
from app.core.entity_registry import registry
from app.core.lifecycle import COMPLETE, Problem
from tests.factories import make_definition, make_transaction

BASE = "/api/custom-fields"


def complete(client: TestClient, cf, tx=None):
    return client.post(f"/api/transactions/{(tx or cf.tx).id}/complete", headers=cf.headers)


def put(client: TestClient, cf, entity_id, entity_type="transaction_line", **values):
    return client.patch(f"{BASE}/entities/{entity_type}/{entity_id}/values", json={"values": values}, headers=cf.headers)


def status_of(client: TestClient, cf, tx=None) -> str:
    return client.get(f"/api/transactions/{(tx or cf.tx).id}", headers=cf.headers).json()["status"]


@pytest.fixture
def required(db_session: Session, cf):
    """Owner is required on every line; a text field is required on the header."""
    cf.owner.required = True
    cf.header_field = make_definition(db_session, cf.org, entity_type="transaction", key="project_ref", label="Project", required=True)
    db_session.flush()
    return cf


# --- blocking completion ---------------------------------------------------------------------------


def test_completion_is_blocked_while_required_fields_are_missing(client: TestClient, required):
    response = complete(client, required)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert (detail["code"], detail["event"]) == ("validation_failed", "complete")
    assert detail["total"] == 3 and len(detail["problems"]) == 3  # owner on 2 lines + the header field
    assert status_of(client, required) == "draft"  # nothing moved


def test_problems_identify_the_record_and_the_field_so_a_client_can_locate_them(client: TestClient, required):
    problems = complete(client, required).json()["detail"]["problems"]

    by_record = {(p["entity_type"], p["entity_id"]): p for p in problems}
    line_a, line_b = (str(line.id) for line in required.lines)
    assert set(by_record) == {("transaction_line", line_a), ("transaction_line", line_b), ("transaction", str(required.tx.id))}
    for (entity_type, entity_id), problem in by_record.items():
        assert problem["code"] == "custom_field.required"
        assert problem["field"] == ("project_ref" if entity_type == "transaction" else "owner")
        assert problem["label"] == ("Project" if entity_type == "transaction" else "Owner")
        assert problem["message"] == f"{problem['label']} is required"


def test_filling_everything_lets_the_transaction_complete_and_locks_the_values(client: TestClient, required):
    assert complete(client, required).status_code == 409
    put(client, required, required.tx.id, "transaction", project_ref="P-17")
    put(client, required, required.lines[0].id, owner=str(required.anna.id))
    assert complete(client, required).status_code == 409  # one line still missing
    problems = complete(client, required).json()["detail"]["problems"]
    assert [(p["entity_type"], p["entity_id"]) for p in problems] == [("transaction_line", str(required.lines[1].id))]

    put(client, required, required.lines[1].id, owner=str(required.erik.id))

    done = complete(client, required)
    assert done.status_code == 200 and done.json()["status"] == "completed"
    assert put(client, required, required.lines[0].id, owner=str(required.erik.id)).status_code == 409  # now locked


def test_only_enabled_required_fields_count(client: TestClient, db_session: Session, required):
    put(client, required, required.tx.id, "transaction", project_ref="P-17")
    put(client, required, required.lines[0].id, owner=str(required.anna.id))
    put(client, required, required.lines[1].id, owner=str(required.anna.id))
    make_definition(db_session, required.org, key="off", required=True, enabled=False)  # disabled: ignored
    make_definition(db_session, required.org, key="optional", required=False)  # not required: ignored

    assert complete(client, required).status_code == 200


def test_required_fields_of_other_entity_types_are_not_asked_for(client: TestClient, db_session: Session, cf):
    make_definition(db_session, cf.org, entity_type="transaction_line", key="owner_note", required=False)
    # no definition is required, so completion is unaffected by custom fields
    assert complete(client, cf).status_code == 200


def test_a_failed_attempt_can_simply_be_retried_after_fixing(client: TestClient, required):
    for _ in range(2):
        assert complete(client, required).status_code == 409
    put(client, required, required.tx.id, "transaction", project_ref="P")
    for line in required.lines:
        put(client, required, line.id, owner=str(required.anna.id))

    assert complete(client, required).status_code == 200


def test_the_other_lifecycle_steps_do_not_ask_for_required_fields(client: TestClient, required):
    assert client.post(f"/api/transactions/{required.tx.id}/cancel", headers=required.headers).status_code == 200


def test_reopening_and_completing_again_asks_again(client: TestClient, db_session: Session, cf):
    complete(client, cf)  # nothing is required yet
    assert status_of(client, cf) == "completed"
    client.post(f"/api/transactions/{cf.tx.id}/reopen", headers=cf.headers)
    make_definition(db_session, cf.org, key="reason", label="Reason", required=True, position=50)

    again = complete(client, cf)

    assert again.status_code == 409
    assert {p["field"] for p in again.json()["detail"]["problems"]} == {"reason"}


def test_a_field_made_required_after_completion_does_not_unlock_or_invalidate_it(client: TestClient, db_session: Session, cf):
    assert complete(client, cf).status_code == 200

    make_definition(db_session, cf.org, key="late", required=True, position=50)

    assert status_of(client, cf) == "completed"


def test_the_amount_of_problems_is_capped_but_the_total_is_exact(client: TestClient, db_session: Session, cf):
    big = make_transaction(db_session, cf.org, billing_customer=cf.billing, lines=[{} for _ in range(120)])
    cf.owner.required = True
    db_session.flush()

    detail = complete(client, cf, big).json()["detail"]

    assert detail["total"] == 120 and len(detail["problems"]) == 100


def test_completion_without_lines_is_refused_before_any_custom_field_check(client: TestClient, db_session: Session, cf):
    empty = make_transaction(db_session, cf.org, billing_customer=cf.billing, lines=[])
    cf.owner.required = True
    db_session.flush()

    response = complete(client, cf, empty)

    assert response.status_code == 409 and isinstance(response.json()["detail"], str)  # the simple message


# --- the validators run under the row lock ------------------------------------------------------------------


def test_validation_happens_after_the_transaction_row_is_locked(client: TestClient, required):
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        complete(client, required)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    lock = next(i for i, s in enumerate(statements) if "FOR UPDATE" in s and "FROM transactions" in s)
    reads = [i for i, s in enumerate(statements) if "custom_field_values" in s or "custom_field_definitions" in s]
    assert reads and min(reads) > lock


# --- tenant isolation ---------------------------------------------------------------------------------------


def test_another_organizations_required_fields_and_lines_never_appear(client: TestClient, db_session: Session, required):
    from tests.factories import make_org

    other = make_org(db_session, "Other")
    other_tx = make_transaction(db_session, other, lines=[{}, {}, {}])
    make_definition(db_session, other, key="owner", label="Owner", field_type="reference", reference_source="customer", required=True)
    make_definition(db_session, other, entity_type="transaction", key="project_ref", required=True)

    problems = complete(client, required).json()["detail"]["problems"]

    other_ids = {str(other_tx.id)}
    assert len(problems) == 3  # only this organization's two lines and header
    assert not other_ids & {p["entity_id"] for p in problems}


def test_a_foreign_transaction_is_404_not_a_list_of_problems(client: TestClient, db_session: Session, required):
    from tests.factories import make_org

    other = make_org(db_session, "Other")
    foreign = make_transaction(db_session, other, lines=[{}])
    make_definition(db_session, other, key="owner", label="Owner", field_type="reference", reference_source="customer", required=True)

    foreign_response = complete(client, required, foreign)
    missing_response = client.post(f"/api/transactions/{uuid.uuid4()}/complete", headers=required.headers)

    assert foreign_response.status_code == missing_response.status_code == 404
    assert foreign_response.json() == missing_response.json()


# --- the generic seam, with a synthetic validator ------------------------------------------------------------


def test_any_registered_validator_can_block_completion(client: TestClient, cf):
    seen = []

    def veto(db, ctx, event_name, entity_key, entity_id):
        seen.append((event_name, entity_key, entity_id, ctx.organization_id))
        return [Problem(code="demo.veto", message="Not today", entity_type=entity_key, entity_id=str(entity_id))]

    with registry.isolated():
        registry.add_validator(veto)
        response = complete(client, cf)

    assert response.status_code == 409
    assert response.json()["detail"]["problems"][0]["code"] == "demo.veto"
    assert seen == [(COMPLETE, "transaction", cf.tx.id, cf.org.id)]
    assert status_of(client, cf) == "draft"
    assert complete(client, cf).status_code == 200  # the validator is gone again


def test_problems_from_several_validators_are_combined(client: TestClient, required):
    def second(db, ctx, event_name, entity_key, entity_id):
        return [Problem(code="demo.second", message="Second", entity_type=entity_key, entity_id=str(entity_id))]

    with registry.isolated():
        registry.add_validator(second)
        detail = complete(client, required).json()["detail"]

    assert detail["total"] == 4
    assert {p["code"] for p in detail["problems"]} == {"custom_field.required", "demo.second"}


def test_a_validator_for_another_event_is_not_consulted_on_completion(client: TestClient, cf):
    def other_event(db, ctx, event_name, entity_key, entity_id):
        return [] if event_name == COMPLETE else [Problem("demo.x", "x", entity_key, str(entity_id))]

    with registry.isolated():
        registry.add_validator(other_event)
        assert complete(client, cf).status_code == 200


def test_the_validators_receive_the_active_organization_and_nothing_else(client: TestClient, db_session: Session, cf):
    from tests.factories import make_org

    other = make_org(db_session, "Other")
    foreign = make_transaction(db_session, other, lines=[{}])
    calls = []

    def spy(db, ctx, event_name, entity_key, entity_id):
        calls.append(ctx.organization_id)
        return []

    with registry.isolated():
        registry.add_validator(spy)
        complete(client, cf)
        complete(client, cf, foreign)  # 404 before any validator is asked

    assert calls == [cf.org.id]
