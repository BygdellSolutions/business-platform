"""Defining custom fields: shapes, validation against the registry, immutability,
disabling instead of deleting, options, and the metadata a frontend needs."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.factories import make_definition

BASE = "/api/custom-fields"


def define(client: TestClient, cf, **fields):
    body = {"entity_type": "transaction_line", "key": "comment", "label": "Comment", "field_type": "text", **fields}
    return client.post(f"{BASE}/definitions", json=body, headers=cf.headers)


def patch(client: TestClient, cf, definition_id, **fields):
    return client.patch(f"{BASE}/definitions/{definition_id}", json=fields, headers=cf.headers)


# --- creating, per type ---------------------------------------------------------------------------------


@pytest.mark.parametrize("field_type", ["text", "number", "date", "boolean"])
def test_plain_types(client: TestClient, member, field_type):
    _, headers = member
    cf = type("Ctx", (), {"headers": headers})

    response = define(client, cf, field_type=field_type, key=f"f_{field_type}")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["field_type"] == field_type
    assert body["reference"] is None and body["options"] is None
    assert (body["required"], body["enabled"], body["show_in_form"], body["show_in_table"], body["show_on_invoice"]) == (
        False, True, True, False, False)


def test_select_with_options_created_inline(client: TestClient, cf):
    response = define(client, cf, key="grade", field_type="select", options=[{"label": "Low"}, {"label": "High"}])

    body = response.json()
    assert response.status_code == 201
    assert [(o["label"], o["position"], o["enabled"]) for o in body["options"]] == [("Low", 10, True), ("High", 20, True)]
    assert all(uuid.UUID(o["id"]) for o in body["options"])  # stable identifiers


def test_a_select_may_start_without_options(client: TestClient, cf):
    assert define(client, cf, key="grade", field_type="select").json()["options"] == []


def test_independent_reference_field(client: TestClient, cf):
    response = define(client, cf, key="payer", field_type="reference", reference={"source": "customer"})

    assert response.status_code == 201
    assert response.json()["reference"] == {"source": "customer", "depends_on": None, "filter": None}


def test_dependent_reference_field(client: TestClient, db_session: Session, member):
    org, headers = member
    cf = type("Ctx", (), {"headers": headers})
    owner = define(client, cf, key="owner", field_type="reference", reference={"source": "customer"}).json()

    response = define(
        client, cf, key="horse", field_type="reference",
        reference={"source": "horse", "depends_on": "owner", "filter": "owner_customer_id"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["reference"] == {"source": "horse", "depends_on": "owner", "filter": "owner_customer_id"}
    assert owner["id"] != response.json()["id"]


def test_positions_default_in_steps_of_ten_and_can_be_given(client: TestClient, cf):
    # on the transaction header, where `cf` has defined nothing yet
    first = define(client, cf, key="one", entity_type="transaction").json()["position"]
    second = define(client, cf, key="two", entity_type="transaction").json()["position"]
    explicit = define(client, cf, key="three", entity_type="transaction", position=5).json()["position"]

    assert (first, second, explicit) == (10, 20, 5)


def test_display_flags_including_show_on_invoice(client: TestClient, cf):
    body = define(client, cf, show_in_form=False, show_in_table=True, show_on_invoice=True, required=True).json()

    assert (body["show_in_form"], body["show_in_table"], body["show_on_invoice"], body["required"]) == (False, True, True, True)


# --- create validation ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "override",
    [
        {"key": ""},
        {"key": "Owner"},
        {"key": "1owner"},
        {"key": "has space"},
        {"key": "x" * 41},
        {"key": "ok-dash"},
        {"label": ""},
        {"label": "   "},
        {"label": "l" * 101},
        {"field_type": "money"},  # deferred types are not accepted
        {"field_type": "percent"},
        {"field_type": "reference"},  # needs a reference
        {"reference": {"source": "customer"}},  # a reference on a text field
        {"options": [{"label": "A"}]},  # options on a text field
        {"unexpected": 1},
        {"organization_id": "00000000-0000-4000-8000-0000000000b2"},
        {"enabled": False},  # not accepted at creation
        {"position": "first"},
        {"entity_type": ""},
    ],
)
def test_create_validation(client: TestClient, cf, override):
    assert define(client, cf, **override).status_code == 422


@pytest.mark.parametrize("entity_type", ["customer", "horse", "nonsense"])
def test_only_registered_custom_field_entity_types_can_be_targeted(client: TestClient, cf, entity_type):
    # "customer" and "horse" are registered, but only as reference sources.
    response = define(client, cf, entity_type=entity_type)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "entity_type"]


def test_keys_are_unique_per_entity_type_but_not_across_them(client: TestClient, cf):
    assert define(client, cf, key="note").status_code == 201

    assert define(client, cf, key="note").status_code == 409
    assert define(client, cf, key="note", entity_type="transaction").status_code == 201


@pytest.mark.parametrize(
    "reference",
    [
        {"source": "nonsense"},
        {"source": "transaction"},  # registered, but not referenceable
        {"source": "horse", "depends_on": "owner"},  # depends_on without filter
        {"source": "horse", "filter": "owner_customer_id"},  # filter without depends_on
        {"source": "horse", "depends_on": "owner", "filter": "no_such_filter"},
        {"source": "horse", "depends_on": "missing", "filter": "owner_customer_id"},
        {"source": "horse", "depends_on": "Owner", "filter": "owner_customer_id"},
        {"source": "customer", "depends_on": "owner", "filter": "owner_customer_id"},  # customers register no filters
        {"source": "horse", "unexpected": 1},
    ],
)
def test_reference_configuration_is_validated_against_the_registry(client: TestClient, cf, reference):
    # `cf` already has the reference field "owner" (customers) to depend on
    response = define(client, cf, key="horse2", field_type="reference", reference=reference)

    assert response.status_code == 422, response.text


def test_the_parent_must_be_a_reference_field(client: TestClient, cf):
    define(client, cf, key="plain")  # a TEXT field

    response = define(client, cf, key="horse2", field_type="reference", reference={"source": "horse", "depends_on": "plain", "filter": "owner_customer_id"})

    assert response.status_code == 422 and response.json()["detail"][0]["type"] == "custom_field.depends_on"


def test_the_parent_must_point_at_the_kind_of_record_the_filter_expects(client: TestClient, cf):
    # "stable_customer_id" expects a customer; a field that points at horses does not fit.
    define(client, cf, key="parent", field_type="reference", reference={"source": "horse"})

    response = define(client, cf, key="child", field_type="reference", reference={"source": "horse", "depends_on": "parent", "filter": "owner_customer_id"})

    assert response.status_code == 422 and response.json()["detail"][0]["type"] == "custom_field.depends_on_type"


def test_the_parent_must_belong_to_the_same_entity_type(client: TestClient, cf):
    define(client, cf, key="header_owner", entity_type="transaction", field_type="reference", reference={"source": "customer"})

    response = define(client, cf, key="horse2", entity_type="transaction_line", field_type="reference", reference={"source": "horse", "depends_on": "header_owner", "filter": "owner_customer_id"})

    assert response.status_code == 422


def test_the_parent_must_be_enabled(client: TestClient, db_session: Session, cf):
    make_definition(db_session, cf.org, key="owner2", field_type="reference", reference_source="customer", enabled=False)

    response = define(client, cf, key="horse2", field_type="reference", reference={"source": "horse", "depends_on": "owner2", "filter": "owner_customer_id"})

    assert response.status_code == 422


def test_several_dependent_fields_can_coexist(client: TestClient, cf):
    # customer -> horse (filter owner_customer_id): a second level would need another source
    # whose filter references "horse"; here we only check a field may itself be a parent.
    # `cf` has owner -> horse; a second independent pair works alongside it
    assert define(client, cf, key="stable", field_type="reference", reference={"source": "customer"}).status_code == 201
    assert define(client, cf, key="stabled_horse", field_type="reference", reference={"source": "horse", "depends_on": "stable", "filter": "stable_customer_id"}).status_code == 201
    assert define(client, cf, key="second_horse", field_type="reference", reference={"source": "horse", "depends_on": "owner", "filter": "owner_customer_id"}).status_code == 201


def test_option_labels_must_be_unique_at_creation(client: TestClient, cf):
    assert define(client, cf, key="grade", field_type="select", options=[{"label": "High"}, {"label": "high"}]).status_code == 422


# --- immutability ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "structural",
    [
        {"key": "renamed"},
        {"entity_type": "transaction"},
        {"field_type": "number"},
        {"reference": {"source": "horse"}},
        {"reference_source": "horse"},
        {"depends_on": "x"},
        {"options": []},
        {"organization_id": "00000000-0000-4000-8000-0000000000b2"},
    ],
)
def test_structural_properties_cannot_be_changed(client: TestClient, cf, structural):
    definition = define(client, cf).json()

    response = patch(client, cf, definition["id"], **structural)

    assert response.status_code == 422
    assert client.get(f"{BASE}/definitions/{definition['id']}", headers=cf.headers).json() == definition


def test_label_required_position_and_flags_can_change(client: TestClient, cf):
    definition = define(client, cf).json()

    response = patch(client, cf, definition["id"], label="Kommentar", required=True, position=3, show_in_form=False, show_in_table=True, show_on_invoice=True)

    body = response.json()
    assert response.status_code == 200
    assert (body["label"], body["required"], body["position"]) == ("Kommentar", True, 3)
    assert (body["show_in_form"], body["show_in_table"], body["show_on_invoice"]) == (False, True, True)
    assert (body["key"], body["field_type"], body["entity_type"]) == ("comment", "text", "transaction_line")  # untouched


@pytest.mark.parametrize("bad", [{"label": None}, {"required": None}, {"enabled": None}, {"position": None}, {"label": ""}, {"position": "x"}, {"show_in_form": None}])
def test_update_validation(client: TestClient, cf, bad):
    definition = define(client, cf).json()
    assert patch(client, cf, definition["id"], **bad).status_code == 422


def test_there_is_no_way_to_delete_a_definition(client: TestClient, cf):
    definition = define(client, cf).json()

    assert client.delete(f"{BASE}/definitions/{definition['id']}", headers=cf.headers).status_code == 405


# --- disabling ---------------------------------------------------------------------------------------------------------


def test_disabled_definitions_are_hidden_by_default_and_listed_on_request(client: TestClient, cf):
    definition = define(client, cf).json()
    patch(client, cf, definition["id"], enabled=False)

    default = client.get(f"{BASE}/definitions", params={"entity_type": "transaction_line"}, headers=cf.headers).json()
    everything = client.get(f"{BASE}/definitions", params={"entity_type": "transaction_line", "include_disabled": "true"}, headers=cf.headers).json()

    assert [d["key"] for d in default if d["key"] == "comment"] == []
    assert [d["key"] for d in everything if d["key"] == "comment"] == ["comment"]
    assert client.get(f"{BASE}/definitions/{definition['id']}", headers=cf.headers).status_code == 200  # still readable by id


def test_a_field_with_enabled_dependents_cannot_be_disabled(client: TestClient, cf):
    # `cf` already has owner -> horse
    assert patch(client, cf, str(cf.owner.id), enabled=False).status_code == 409

    assert patch(client, cf, str(cf.horse.id), enabled=False).status_code == 200  # the child first
    assert patch(client, cf, str(cf.owner.id), enabled=False).status_code == 200  # then the parent


def test_a_dependent_field_cannot_be_enabled_while_its_parent_is_disabled(client: TestClient, cf):
    patch(client, cf, str(cf.horse.id), enabled=False)
    patch(client, cf, str(cf.owner.id), enabled=False)

    assert patch(client, cf, str(cf.horse.id), enabled=True).status_code == 409
    assert patch(client, cf, str(cf.owner.id), enabled=True).status_code == 200
    assert patch(client, cf, str(cf.horse.id), enabled=True).status_code == 200


def test_choices_of_a_disabled_field_are_unavailable(client: TestClient, cf):
    patch(client, cf, str(cf.horse.id), enabled=False)

    assert client.get(f"{BASE}/definitions/{cf.horse.id}/choices", params={"depends_on_value": str(cf.anna.id)}, headers=cf.headers).status_code == 409


# --- listing and metadata --------------------------------------------------------------------------------------------------


def test_definitions_are_listed_in_form_order_with_options_inline(client: TestClient, cf):
    define(client, cf, key="late", position=99)
    define(client, cf, key="grade", field_type="select", position=15, options=[{"label": "Low"}, {"label": "High"}])

    listed = client.get(f"{BASE}/definitions", params={"entity_type": "transaction_line"}, headers=cf.headers).json()

    assert [d["key"] for d in listed] == ["owner", "grade", "horse", "late"]
    assert [o["label"] for o in listed[1]["options"]] == ["Low", "High"]
    assert listed[2]["reference"] == {"source": "horse", "depends_on": "owner", "filter": "owner_customer_id"}


def test_the_list_can_be_filtered_by_entity_type_and_paged(client: TestClient, cf):
    define(client, cf, key="header_field", entity_type="transaction")

    lines = client.get(f"{BASE}/definitions", params={"entity_type": "transaction_line"}, headers=cf.headers).json()
    headers = client.get(f"{BASE}/definitions", params={"entity_type": "transaction"}, headers=cf.headers).json()
    paged = client.get(f"{BASE}/definitions", params={"entity_type": "transaction_line", "limit": 1, "offset": 1}, headers=cf.headers).json()

    assert {d["entity_type"] for d in lines} == {"transaction_line"}
    assert [d["key"] for d in headers] == ["header_field"]
    assert [d["key"] for d in paged] == ["horse"]
    assert client.get(f"{BASE}/definitions", params={"limit": 501}, headers=cf.headers).status_code == 422


def test_entity_types_describe_what_modules_registered(client: TestClient, cf):
    types = {t["key"]: t for t in client.get(f"{BASE}/entity-types", headers=cf.headers).json()}

    assert types["transaction_line"]["custom_fields"] is True and types["transaction_line"]["referenceable"] is False
    assert types["transaction"]["custom_fields"] is True
    assert types["customer"]["referenceable"] is True and types["customer"]["custom_fields"] is False
    assert types["customer"]["filters"] == []
    assert {f["key"]: (f["label"], f["references"]) for f in types["horse"]["filters"]} == {
        "owner_customer_id": ("Owner", "customer"),
        "stable_customer_id": ("Stable", "customer"),
    }


def test_the_definition_metadata_is_enough_to_render_a_form(client: TestClient, cf):
    define(client, cf, key="grade", field_type="select", options=[{"label": "Low"}])

    listed = client.get(f"{BASE}/definitions", params={"entity_type": "transaction_line"}, headers=cf.headers).json()

    for definition in listed:
        assert set(definition) == {
            "id", "entity_type", "key", "label", "field_type", "required", "position", "enabled",
            "show_in_form", "show_in_table", "show_on_invoice", "reference", "options", "created_at", "updated_at",
        }
    horse = next(d for d in listed if d["key"] == "horse")
    # A form needs: the control (field_type), where choices come from (id -> /choices), and what
    # it depends on (the parent's key and that the choices take the parent's current value).
    assert horse["field_type"] == "reference" and horse["reference"]["depends_on"] == "owner"


# --- select options ----------------------------------------------------------------------------------------------------------


@pytest.fixture
def grade(client: TestClient, cf):
    cf.grade = define(client, cf, key="grade", field_type="select", options=[{"label": "Low"}, {"label": "High"}]).json()
    return cf


def test_options_can_be_added_relabelled_and_disabled(client: TestClient, grade):
    base = f"{BASE}/definitions/{grade.grade['id']}/options"

    added = client.post(base, json={"label": "Medium"}, headers=grade.headers)
    option = added.json()
    relabelled = client.patch(f"{base}/{option['id']}", json={"label": "Mid", "position": 5}, headers=grade.headers)
    disabled = client.patch(f"{base}/{option['id']}", json={"enabled": False}, headers=grade.headers)

    assert added.status_code == 201 and option["position"] == 30
    assert (relabelled.json()["id"], relabelled.json()["label"], relabelled.json()["position"]) == (option["id"], "Mid", 5)
    assert disabled.json()["enabled"] is False and disabled.json()["id"] == option["id"]  # the id never changes


def test_option_labels_are_unique_case_insensitively(client: TestClient, grade):
    base = f"{BASE}/definitions/{grade.grade['id']}/options"
    low_id = grade.grade["options"][0]["id"]

    assert client.post(base, json={"label": "HIGH"}, headers=grade.headers).status_code == 409
    assert client.patch(f"{base}/{low_id}", json={"label": "high"}, headers=grade.headers).status_code == 409
    assert client.patch(f"{base}/{low_id}", json={"label": "LOW"}, headers=grade.headers).status_code == 200  # itself


def test_only_select_fields_have_options(client: TestClient, cf):
    text_field = define(client, cf).json()

    assert client.post(f"{BASE}/definitions/{text_field['id']}/options", json={"label": "x"}, headers=cf.headers).status_code == 422


@pytest.mark.parametrize("bad", [{"label": ""}, {"label": None}, {"position": None}, {"enabled": None}, {"id": "x"}, {"unexpected": 1}])
def test_option_update_validation(client: TestClient, grade, bad):
    option_id = grade.grade["options"][0]["id"]
    assert client.patch(f"{BASE}/definitions/{grade.grade['id']}/options/{option_id}", json=bad, headers=grade.headers).status_code == 422


def test_a_disabled_option_keeps_existing_values_but_cannot_be_chosen_again(client: TestClient, db_session: Session, grade, cf):
    low, high = grade.grade["options"]
    line_url = f"/api/custom-fields/entities/transaction_line/{cf.lines[0].id}/values"
    client.patch(line_url, json={"values": {"grade": low["id"]}}, headers=cf.headers)

    client.patch(f"{BASE}/definitions/{grade.grade['id']}/options/{low['id']}", json={"enabled": False}, headers=cf.headers)

    shown = client.get(line_url, headers=cf.headers).json()["values"][0]
    assert (shown["display"], shown["active"]) == ("Low", False)  # still displayed
    other = client.patch(line_url, json={"values": {"grade": low["id"]}}, headers=cf.headers)
    assert other.status_code == 200  # the unchanged value may be sent again
    assert client.patch(f"/api/custom-fields/entities/transaction_line/{cf.lines[1].id}/values", json={"values": {"grade": low["id"]}}, headers=cf.headers).status_code == 422
    offered = client.get(f"{BASE}/definitions/{grade.grade['id']}/choices", headers=cf.headers).json()
    assert [c["label"] for c in offered] == ["High"]
    everything = client.get(f"{BASE}/definitions/{grade.grade['id']}/choices", params={"include_inactive": "true"}, headers=cf.headers).json()
    assert [(c["label"], c["active"]) for c in everything] == [("Low", False), ("High", True)]


def test_relabelling_an_option_changes_how_existing_values_display(client: TestClient, grade, cf):
    low = grade.grade["options"][0]
    line_url = f"/api/custom-fields/entities/transaction_line/{cf.lines[0].id}/values"
    client.patch(line_url, json={"values": {"grade": low["id"]}}, headers=cf.headers)

    client.patch(f"{BASE}/definitions/{grade.grade['id']}/options/{low['id']}", json={"label": "Minimal"}, headers=cf.headers)

    shown = client.get(line_url, headers=cf.headers).json()["values"][0]
    assert (shown["value"], shown["display"]) == (low["id"], "Minimal")  # the stored id is unchanged


def test_select_choices_can_be_searched(client: TestClient, grade):
    found = client.get(f"{BASE}/definitions/{grade.grade['id']}/choices", params={"q": "hi"}, headers=grade.headers).json()
    assert [c["label"] for c in found] == ["High"]


def test_an_option_in_the_path_must_belong_to_that_definition(client: TestClient, cf, grade):
    other = define(client, cf, key="size", field_type="select", options=[{"label": "S"}]).json()
    foreign_option = other["options"][0]["id"]

    response = client.patch(f"{BASE}/definitions/{grade.grade['id']}/options/{foreign_option}", json={"label": "x"}, headers=cf.headers)

    assert response.status_code == 404
    assert client.patch(f"{BASE}/definitions/{grade.grade['id']}/options/{uuid.uuid4()}", json={"label": "x"}, headers=cf.headers).status_code == 404
