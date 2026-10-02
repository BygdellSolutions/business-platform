"""Writing and reading custom-field values on a record (here: transaction lines).

The workflow under test: Owner (reference to a customer) and Horse (reference to a horse,
dependent on Owner, choices filtered by owner_customer_id = the selected Owner).
"""

import datetime
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.modules.custom_fields.models import CustomFieldValue
from app.scripts.seed_dev import ORG_HORSE_THERAPY_ID, ORG_STABLE_SERVICES_ID
from tests.factories import make_customer, make_definition, make_horse, make_value

JSON = {"Content-Type": "application/json"}
SEEDED_ORGANIZATIONS = [ORG_HORSE_THERAPY_ID, ORG_STABLE_SERVICES_ID]


def url(line, suffix="values"):
    return f"/api/custom-fields/entities/transaction_line/{line.id}/{suffix}"


def put(client: TestClient, cf, line, **values):
    return client.patch(url(line), json={"values": values}, headers=cf.headers)


def read(client: TestClient, cf, line):
    return client.get(url(line), headers=cf.headers).json()


def by_key(body):
    return {v["key"]: v for v in body["values"]}


def value_count(db: Session) -> int:
    """Value rows outside the dev-seed organizations (the seed carries a few of its own)."""
    return db.scalar(
        select(func.count())
        .select_from(CustomFieldValue)
        .where(CustomFieldValue.organization_id.not_in(SEEDED_ORGANIZATIONS))
    )


# --- the Owner -> Horse workflow --------------------------------------------------------------------


def test_set_owner_and_the_horse_that_belongs_to_that_owner(client: TestClient, cf):
    line = cf.lines[0]

    response = put(client, cf, line, owner=str(cf.anna.id), horse=str(cf.kalle.id))

    assert response.status_code == 200, response.text
    values = by_key(response.json())
    assert values["owner"]["value"] == str(cf.anna.id)
    assert values["owner"]["display"] == "Anna Andersson"
    assert values["owner"]["active"] is True and values["owner"]["missing"] is False
    assert values["horse"]["display"] == "Kalle"
    assert response.json()["missing_required"] == []
    assert read(client, cf, line) == response.json()


def test_values_are_listed_in_field_order(client: TestClient, db_session: Session, cf):
    make_definition(db_session, cf.org, key="aaa_first_by_name", label="Zed", position=5)
    put(client, cf, cf.lines[0], horse=None, owner=str(cf.anna.id), aaa_first_by_name="x")

    assert [v["key"] for v in read(client, cf, cf.lines[0])["values"]] == ["aaa_first_by_name", "owner"]


def test_a_horse_that_belongs_to_another_owner_is_refused(client: TestClient, db_session: Session, cf):
    line = cf.lines[0]

    response = put(client, cf, line, owner=str(cf.anna.id), horse=str(cf.storm.id))  # Storm is Erik's

    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["loc"] == ["body", "values", "horse"] and error["type"] == "custom_field.dependency"
    assert value_count(db_session) == 0  # nothing at all was written


def test_a_horse_needs_its_owner_to_be_set_first(client: TestClient, cf):
    response = put(client, cf, cf.lines[0], horse=str(cf.kalle.id))

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "custom_field.dependency"


def test_changing_the_owner_without_the_horse_is_refused(client: TestClient, cf):
    put(client, cf, cf.lines[0], owner=str(cf.anna.id), horse=str(cf.kalle.id))

    response = put(client, cf, cf.lines[0], owner=str(cf.erik.id))  # Kalle is not Erik's

    assert response.status_code == 422
    assert by_key(read(client, cf, cf.lines[0]))["owner"]["display"] == "Anna Andersson"


def test_owner_and_horse_can_change_together(client: TestClient, cf):
    put(client, cf, cf.lines[0], owner=str(cf.anna.id), horse=str(cf.kalle.id))

    response = put(client, cf, cf.lines[0], owner=str(cf.erik.id), horse=str(cf.storm.id))

    assert response.status_code == 200
    values = by_key(response.json())
    assert (values["owner"]["display"], values["horse"]["display"]) == ("Erik Svensson", "Storm")


def test_clearing_the_owner_while_the_horse_stays_is_refused_but_clearing_both_works(client: TestClient, cf):
    put(client, cf, cf.lines[0], owner=str(cf.anna.id), horse=str(cf.kalle.id))

    refused = put(client, cf, cf.lines[0], owner=None)
    cleared = put(client, cf, cf.lines[0], owner=None, horse=None)

    assert refused.status_code == 422
    assert cleared.status_code == 200 and cleared.json()["values"] == []


def test_a_horse_that_is_sold_later_does_not_break_old_values(client: TestClient, db_session: Session, cf):
    put(client, cf, cf.lines[0], owner=str(cf.anna.id), horse=str(cf.kalle.id))
    cf.kalle.owner_customer_id = cf.erik.id  # Kalle is sold to Erik afterwards
    db_session.flush()
    make_definition(db_session, cf.org, key="note", label="Note", position=30)

    # The recorded pair is history: an unrelated write must not re-validate it.
    response = put(client, cf, cf.lines[0], note="invoiced as Anna's horse")

    assert response.status_code == 200
    assert by_key(response.json())["horse"]["display"] == "Kalle"


def test_values_of_two_lines_are_independent(client: TestClient, cf):
    put(client, cf, cf.lines[0], owner=str(cf.anna.id), horse=str(cf.kalle.id))
    put(client, cf, cf.lines[1], owner=str(cf.erik.id), horse=str(cf.storm.id))

    assert by_key(read(client, cf, cf.lines[0]))["horse"]["display"] == "Kalle"
    assert by_key(read(client, cf, cf.lines[1]))["horse"]["display"] == "Storm"


def test_the_same_value_written_again_changes_nothing(client: TestClient, db_session: Session, cf):
    put(client, cf, cf.lines[0], owner=str(cf.anna.id))
    before = value_count(db_session)

    assert put(client, cf, cf.lines[0], owner=str(cf.anna.id)).status_code == 200
    assert value_count(db_session) == before


def test_choices_for_the_dependent_field_are_filtered_by_the_selected_owner(client: TestClient, db_session: Session, cf):
    make_horse(db_session, cf.org, "Kalle's foal", owner=cf.anna)
    make_horse(db_session, cf.org, "Retired", owner=cf.anna, active=False)
    base = f"/api/custom-fields/definitions/{cf.horse.id}/choices"

    anna = client.get(base, params={"depends_on_value": str(cf.anna.id)}, headers=cf.headers).json()
    erik = client.get(base, params={"depends_on_value": str(cf.erik.id)}, headers=cf.headers).json()
    with_inactive = client.get(
        base, params={"depends_on_value": str(cf.anna.id), "include_inactive": "true"}, headers=cf.headers
    ).json()
    searched = client.get(
        base, params={"depends_on_value": str(cf.anna.id), "q": "foal"}, headers=cf.headers
    ).json()

    assert [c["label"] for c in anna] == ["Kalle", "Kalle's foal"]
    assert [c["label"] for c in erik] == ["Storm"]
    assert [c["label"] for c in with_inactive] == ["Kalle", "Kalle's foal", "Retired"]
    assert [c["label"] for c in searched] == ["Kalle's foal"]
    assert all(set(c) == {"id", "label", "active"} for c in anna)


def test_a_dependent_field_needs_the_parents_value_to_list_choices(client: TestClient, cf):
    response = client.get(f"/api/custom-fields/definitions/{cf.horse.id}/choices", headers=cf.headers)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "depends_on_value"]


def test_the_independent_reference_field_lists_active_customers(client: TestClient, db_session: Session, cf):
    make_customer(db_session, cf.org, "Gone AB", active=False)

    labels = [c["label"] for c in client.get(f"/api/custom-fields/definitions/{cf.owner.id}/choices", headers=cf.headers).json()]

    assert "Anna Andersson" in labels and "Gone AB" not in labels


# --- the six field types ----------------------------------------------------------------------------------


@pytest.fixture
def typed(db_session: Session, cf):
    """One field of each plain type on transaction lines."""
    org = cf.org
    cf.text = make_definition(db_session, org, key="comment", field_type="text", position=30)
    cf.number = make_definition(db_session, org, key="weight", field_type="number", position=40)
    cf.date = make_definition(db_session, org, key="seen_on", field_type="date", position=50)
    cf.flag = make_definition(db_session, org, key="urgent", field_type="boolean", position=60)
    cf.select = make_definition(db_session, org, key="grade", field_type="select", position=70, options=["Low", "High"])
    return cf


@pytest.mark.parametrize(
    "key,sent,shown",
    [
        ("comment", "  Needs follow-up  ", "Needs follow-up"),
        ("comment", "Åke söker häst", "Åke söker häst"),
        ("comment", "x" * 2000, "x" * 2000),
        ("weight", "12.5", "12.5"),
        ("weight", "12.5000", "12.5"),
        ("weight", "-3", "-3"),
        ("weight", "0", "0"),
        ("weight", 7, "7"),
        ("weight", "99999999999999.9999", "99999999999999.9999"),
        ("weight", "0.0001", "0.0001"),
        ("seen_on", "2026-10-03", "2026-10-03"),
        ("seen_on", "2024-02-29", "2024-02-29"),
        ("urgent", True, "true"),
        ("urgent", False, "false"),
    ],
)
def test_valid_values_per_type(client: TestClient, typed, key, sent, shown):
    response = put(client, typed, typed.lines[0], **{key: sent})

    assert response.status_code == 200, response.text
    assert by_key(response.json())[key]["display"] == shown
    if key == "urgent":
        assert by_key(response.json())[key]["value"] is sent  # booleans stay booleans


@pytest.mark.parametrize(
    "key,bad",
    [
        ("comment", ""),
        ("comment", "   "),
        ("comment", "x" * 2001),
        ("comment", 5),
        ("comment", True),
        ("comment", ["a"]),
        ("weight", 1.5),  # a JSON float
        ("weight", 0.1),
        ("weight", True),
        ("weight", "1e2"),
        ("weight", "NaN"),
        ("weight", "Infinity"),
        ("weight", "1.00001"),
        ("weight", "100000000000000"),
        ("weight", " 1"),
        ("weight", "1,5"),
        ("weight", ""),
        ("weight", "abc"),
        ("seen_on", "2026-13-01"),
        ("seen_on", "2026-02-30"),
        ("seen_on", "03/10/2026"),
        ("seen_on", "2026-1-5"),
        ("seen_on", 20261003),
        ("seen_on", True),
        ("urgent", "true"),
        ("urgent", 1),
        ("urgent", "yes"),
        ("grade", "High"),  # a label, not an option id
        ("grade", 5),
        ("grade", str(uuid.uuid4())),  # not an option of this field
        ("owner", "not-a-uuid"),
        ("owner", 5),
    ],
    ids=repr,
)
def test_invalid_values_per_type_are_rejected_and_write_nothing(client: TestClient, db_session: Session, typed, key, bad):
    response = put(client, typed, typed.lines[0], **{key: bad})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "values", key]
    assert value_count(db_session) == 0


@pytest.mark.parametrize("raw", ["1.5", "0.1", "12.0", "1e2"])
def test_json_numbers_with_decimals_are_rejected_for_number_fields(client: TestClient, typed, raw):
    body = '{"values": {"weight": %s}}' % raw
    assert client.patch(url(typed.lines[0]), content=body, headers={**typed.headers, **JSON}).status_code == 422


def test_number_values_round_trip_exactly_in_postgres(client: TestClient, db_session: Session, typed):
    put(client, typed, typed.lines[0], weight="0.1235")

    stored = db_session.scalar(text("select value_number::text from custom_field_values where entity_id = :id"), {"id": str(typed.lines[0].id)})

    assert stored == "0.1235"


def test_a_select_value_stores_the_stable_option_id(client: TestClient, db_session: Session, typed):
    options = {o["label"]: o["id"] for o in client.get(f"/api/custom-fields/definitions/{typed.select.id}", headers=typed.headers).json()["options"]}

    response = put(client, typed, typed.lines[0], grade=options["High"])

    value = by_key(response.json())["grade"]
    assert (value["value"], value["display"], value["active"]) == (options["High"], "High", True)
    stored = db_session.scalar(select(CustomFieldValue.value_option_id).where(CustomFieldValue.entity_id == typed.lines[0].id))
    assert str(stored) == options["High"]


def test_null_clears_a_value_and_removes_its_row(client: TestClient, db_session: Session, typed):
    put(client, typed, typed.lines[0], comment="x", urgent=True)

    response = put(client, typed, typed.lines[0], comment=None)

    assert [v["key"] for v in response.json()["values"]] == ["urgent"]
    assert value_count(db_session) == 1


def test_clearing_a_value_that_was_never_set_is_fine(client: TestClient, typed):
    assert put(client, typed, typed.lines[0], comment=None).status_code == 200


def test_several_fields_are_written_atomically(client: TestClient, db_session: Session, typed):
    response = put(client, typed, typed.lines[0], comment="fine", weight="not a number")

    assert response.status_code == 422
    assert value_count(db_session) == 0  # the valid one was not written either


def test_all_errors_are_reported_together(client: TestClient, typed):
    response = put(client, typed, typed.lines[0], comment="", weight="x", seen_on="y")

    assert {e["loc"][-1] for e in response.json()["detail"]} == {"comment", "weight", "seen_on"}


@pytest.mark.parametrize("body", [{}, {"values": []}, {"values": "x"}, {"values": {}, "extra": 1}, {"other": {}}])
def test_malformed_requests_are_422(client: TestClient, cf, body):
    assert client.patch(url(cf.lines[0]), json=body, headers=cf.headers).status_code == 422


def test_an_empty_write_changes_nothing(client: TestClient, cf):
    assert put(client, cf, cf.lines[0]).status_code == 200


# --- unknown and disabled fields ------------------------------------------------------------------------------


def test_unknown_field_keys_are_rejected(client: TestClient, cf):
    response = put(client, cf, cf.lines[0], nonsense="x")

    assert response.status_code == 422 and response.json()["detail"][0]["type"] == "custom_field.unknown"


def test_a_disabled_field_cannot_be_written_but_keeps_its_value(client: TestClient, db_session: Session, typed):
    put(client, typed, typed.lines[0], comment="kept")
    typed.text.enabled = False
    db_session.flush()

    assert put(client, typed, typed.lines[0], comment="new").status_code == 422
    assert "comment" not in by_key(read(client, typed, typed.lines[0]))  # hidden by default
    shown = client.get(url(typed.lines[0]), params={"include_disabled": "true"}, headers=typed.headers).json()
    assert by_key(shown)["comment"]["display"] == "kept"  # not lost

    typed.text.enabled = True
    db_session.flush()
    assert by_key(read(client, typed, typed.lines[0]))["comment"]["display"] == "kept"  # restored


# --- required fields ------------------------------------------------------------------------------------------------


def test_required_fields_are_enforced_when_values_are_written(client: TestClient, db_session: Session, cf):
    make_definition(db_session, cf.org, key="reason", label="Reason", required=True, position=5)

    refused = put(client, cf, cf.lines[0], owner=str(cf.anna.id))
    accepted = put(client, cf, cf.lines[0], owner=str(cf.anna.id), reason="checkup")

    assert refused.status_code == 422
    assert refused.json()["detail"][0]["type"] == "custom_field.required"
    assert refused.json()["detail"][0]["loc"] == ["body", "values", "reason"]
    assert accepted.status_code == 200 and accepted.json()["missing_required"] == []


def test_a_required_field_cannot_be_cleared(client: TestClient, db_session: Session, cf):
    make_definition(db_session, cf.org, key="reason", label="Reason", required=True, position=5)
    put(client, cf, cf.lines[0], reason="checkup")

    assert put(client, cf, cf.lines[0], reason=None).status_code == 422


def test_making_a_field_required_later_does_not_invalidate_existing_records(client: TestClient, db_session: Session, cf):
    put(client, cf, cf.lines[0], owner=str(cf.anna.id))
    make_definition(db_session, cf.org, key="reason", label="Reason", required=True, position=5)

    body = read(client, cf, cf.lines[0])

    assert body["missing_required"] == ["reason"]  # reported, not an error
    assert client.get(url(cf.lines[0]), headers=cf.headers).status_code == 200
    # the next write to that record must supply it
    assert put(client, cf, cf.lines[0], owner=str(cf.erik.id), horse=None).status_code == 422
    assert put(client, cf, cf.lines[0], owner=str(cf.erik.id), reason="now given").status_code == 200


def test_disabled_required_fields_are_not_required(client: TestClient, db_session: Session, cf):
    make_definition(db_session, cf.org, key="reason", label="Reason", required=True, enabled=False, position=5)

    assert put(client, cf, cf.lines[0], owner=str(cf.anna.id)).status_code == 200
    assert read(client, cf, cf.lines[0])["missing_required"] == []


def test_a_required_boolean_is_satisfied_by_false(client: TestClient, db_session: Session, cf):
    make_definition(db_session, cf.org, key="urgent", field_type="boolean", required=True, position=5)

    assert put(client, cf, cf.lines[0], urgent=False).status_code == 200


# --- bulk read -------------------------------------------------------------------------------------------------------------


def test_bulk_read_returns_values_for_each_requested_record(client: TestClient, cf):
    put(client, cf, cf.lines[0], owner=str(cf.anna.id), horse=str(cf.kalle.id))
    put(client, cf, cf.lines[1], owner=str(cf.erik.id))
    ids = f"{cf.lines[0].id},{cf.lines[1].id}"

    body = client.get("/api/custom-fields/values", params={"entity_type": "transaction_line", "entity_ids": ids}, headers=cf.headers).json()

    assert body["entity_type"] == "transaction_line"
    first, second = body["entities"][str(cf.lines[0].id)], body["entities"][str(cf.lines[1].id)]
    assert [v["display"] for v in first] == ["Anna Andersson", "Kalle"]
    assert [v["display"] for v in second] == ["Erik Svensson"]


def test_bulk_read_validation(client: TestClient, cf):
    def call(**params):
        return client.get("/api/custom-fields/values", params={"entity_type": "transaction_line", **params}, headers=cf.headers)

    assert call(entity_ids="not-a-uuid").status_code == 422
    assert call(entity_ids=",".join(str(uuid.uuid4()) for _ in range(201))).status_code == 422
    assert call(entity_ids="").json()["entities"] == {}
    assert client.get("/api/custom-fields/values", params={"entity_type": "customer", "entity_ids": str(cf.anna.id)}, headers=cf.headers).status_code == 404  # not a custom-field type


# --- entity types ---------------------------------------------------------------------------------------------------------------


def test_only_registered_custom_field_types_accept_values(client: TestClient, cf):
    for entity_type, entity_id in (("customer", cf.anna.id), ("nonsense", cf.anna.id), ("horse", cf.kalle.id)):
        response = client.get(f"/api/custom-fields/entities/{entity_type}/{entity_id}/values", headers=cf.headers)
        assert response.status_code == 404, entity_type


def test_values_of_a_nonexistent_record_are_404(client: TestClient, cf):
    assert client.get(f"/api/custom-fields/entities/transaction_line/{uuid.uuid4()}/values", headers=cf.headers).status_code == 404
    assert client.patch(f"/api/custom-fields/entities/transaction_line/{uuid.uuid4()}/values", json={"values": {}}, headers=cf.headers).status_code == 404


def test_header_level_fields_work_the_same_way(client: TestClient, db_session: Session, cf):
    make_definition(db_session, cf.org, entity_type="transaction", key="project_ref", label="Project", field_type="text")

    response = client.patch(f"/api/custom-fields/entities/transaction/{cf.tx.id}/values", json={"values": {"project_ref": "P-17"}}, headers=cf.headers)

    assert response.status_code == 200 and response.json()["values"][0]["display"] == "P-17"
    # and the line-level definitions are not offered on the header
    assert client.patch(f"/api/custom-fields/entities/transaction/{cf.tx.id}/values", json={"values": {"owner": str(cf.anna.id)}}, headers=cf.headers).status_code == 422
