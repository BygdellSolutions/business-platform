"""Reference values: they store ids, display live data, survive rename/deactivation, and
protect their targets from deletion (a link no foreign key can express)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.entity_registry import registry
from tests.factories import make_customer, make_definition, make_horse

BASE = "/api/custom-fields"
GENERIC_409 = {
    "customer": "Customer is referenced by other records",
    "horse": "Horse is referenced by other records",
    "supplier": "Supplier is referenced by other records",
}
DELETE_PATH = {"customer": "/api/customers", "horse": "/api/horses", "supplier": "/api/suppliers"}


def _supplier(db, org):
    from app.models import Supplier

    supplier = Supplier(organization_id=org.id, name="Target Supplies")
    db.add(supplier)
    db.flush()
    return supplier


def values_url(entity_id, entity_type="transaction_line"):
    return f"{BASE}/entities/{entity_type}/{entity_id}/values"


def put(client, cf, line, **values):
    return client.patch(values_url(line.id), json={"values": values}, headers=cf.headers)


def shown(client, cf, line):
    return {v["key"]: v for v in client.get(values_url(line.id), headers=cf.headers).json()["values"]}


# --- stored as ids, displayed live ---------------------------------------------------------------------------


def test_references_store_the_uuid_not_the_display_text(client: TestClient, db_session: Session, cf):
    put(client, cf, cf.lines[0], owner=str(cf.anna.id), horse=str(cf.kalle.id))

    rows = db_session.execute(
        text("select d.key, v.value_reference_id::text, v.value_text from custom_field_values v join custom_field_definitions d on d.id = v.definition_id where v.entity_id = :id order by d.key"),
        {"id": str(cf.lines[0].id)},
    ).all()

    assert rows == [("horse", str(cf.kalle.id), None), ("owner", str(cf.anna.id), None)]


def test_renaming_a_referenced_record_changes_the_display_everywhere(client: TestClient, cf):
    put(client, cf, cf.lines[0], owner=str(cf.anna.id), horse=str(cf.kalle.id))
    put(client, cf, cf.lines[1], owner=str(cf.anna.id))

    client.patch(f"/api/customers/{cf.anna.id}", json={"name": "Anna Svensson"}, headers=cf.headers)
    client.patch(f"/api/horses/{cf.kalle.id}", json={"name": "Kalle II"}, headers=cf.headers)

    first, second = shown(client, cf, cf.lines[0]), shown(client, cf, cf.lines[1])
    assert (first["owner"]["display"], first["horse"]["display"], second["owner"]["display"]) == ("Anna Svensson", "Kalle II", "Anna Svensson")
    assert first["owner"]["value"] == str(cf.anna.id)  # the stored id never moved


# --- deactivated targets ---------------------------------------------------------------------------------------------


def test_a_deactivated_target_still_displays_but_is_flagged_and_not_offered(client: TestClient, cf):
    put(client, cf, cf.lines[0], owner=str(cf.anna.id), horse=str(cf.kalle.id))

    client.patch(f"/api/customers/{cf.anna.id}", json={"active": False}, headers=cf.headers)
    client.patch(f"/api/horses/{cf.kalle.id}", json={"active": False}, headers=cf.headers)

    values = shown(client, cf, cf.lines[0])
    assert (values["owner"]["display"], values["owner"]["active"], values["owner"]["missing"]) == ("Anna Andersson", False, False)
    assert (values["horse"]["display"], values["horse"]["active"]) == ("Kalle", False)
    owners = [c["label"] for c in client.get(f"{BASE}/definitions/{cf.owner.id}/choices", headers=cf.headers).json()]
    horses = client.get(f"{BASE}/definitions/{cf.horse.id}/choices", params={"depends_on_value": str(cf.anna.id)}, headers=cf.headers).json()
    assert "Anna Andersson" not in owners and horses == []


def test_the_existing_link_to_a_deactivated_target_stays_valid(client: TestClient, db_session: Session, cf):
    put(client, cf, cf.lines[0], owner=str(cf.anna.id), horse=str(cf.kalle.id))
    client.patch(f"/api/customers/{cf.anna.id}", json={"active": False}, headers=cf.headers)
    make_definition(db_session, cf.org, key="comment", position=30)

    unrelated = put(client, cf, cf.lines[0], comment="still editable")
    unchanged = put(client, cf, cf.lines[0], owner=str(cf.anna.id))  # sending the same value again

    assert unrelated.status_code == 200 and unchanged.status_code == 200


def test_a_deactivated_target_cannot_be_newly_assigned(client: TestClient, cf):
    client.patch(f"/api/customers/{cf.anna.id}", json={"active": False}, headers=cf.headers)

    response = put(client, cf, cf.lines[1], owner=str(cf.anna.id))

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "reference.inactive"
    assert response.json()["detail"][0]["loc"] == ["body", "values", "owner"]


def test_a_reactivated_target_can_be_assigned_again(client: TestClient, cf):
    client.patch(f"/api/customers/{cf.anna.id}", json={"active": False}, headers=cf.headers)
    assert put(client, cf, cf.lines[1], owner=str(cf.anna.id)).status_code == 422

    client.patch(f"/api/customers/{cf.anna.id}", json={"active": True}, headers=cf.headers)

    assert put(client, cf, cf.lines[1], owner=str(cf.anna.id)).status_code == 200


# --- the delete guard ---------------------------------------------------------------------------------------------------


def test_a_customer_pointed_at_by_a_live_value_cannot_be_deleted(client: TestClient, db_session: Session, cf):
    put(client, cf, cf.lines[0], owner=str(cf.anna.id))

    response = client.delete(f"/api/customers/{cf.anna.id}", headers=cf.headers)

    assert response.status_code == 409
    assert response.json()["detail"] == GENERIC_409["customer"]  # names neither custom fields nor lines
    assert client.get(f"/api/customers/{cf.anna.id}", headers=cf.headers).status_code == 200


def test_a_horse_pointed_at_by_a_live_value_cannot_be_deleted(client: TestClient, cf):
    put(client, cf, cf.lines[0], owner=str(cf.anna.id), horse=str(cf.kalle.id))

    response = client.delete(f"/api/horses/{cf.kalle.id}", headers=cf.headers)

    assert response.status_code == 409 and response.json()["detail"] == GENERIC_409["horse"]


def test_the_record_can_be_deleted_once_nothing_points_at_it(client: TestClient, cf):
    put(client, cf, cf.lines[0], owner=str(cf.anna.id), horse=str(cf.kalle.id))
    assert client.delete(f"/api/horses/{cf.kalle.id}", headers=cf.headers).status_code == 409

    put(client, cf, cf.lines[0], horse=None)

    assert client.delete(f"/api/horses/{cf.kalle.id}", headers=cf.headers).status_code == 204


def test_values_of_deleted_records_do_not_block_anything(client: TestClient, db_session: Session, cf):
    lone = make_customer(db_session, cf.org, "Owns Nothing")  # no horse, so only the value points at it
    put(client, cf, cf.lines[0], owner=str(lone.id))
    assert client.delete(f"/api/customers/{lone.id}", headers=cf.headers).status_code == 409

    # Deleting the line leaves its value row behind (a polymorphic id has no cascade) ...
    assert client.delete(f"/api/transactions/{cf.tx.id}/lines/{cf.lines[0].id}", headers=cf.headers).status_code == 204
    leftover = db_session.scalar(text("select count(*) from custom_field_values where entity_id = :id"), {"id": str(cf.lines[0].id)})
    assert leftover == 1
    # ... but that orphan must not keep the customer alive forever.
    assert client.delete(f"/api/customers/{lone.id}", headers=cf.headers).status_code == 204


def test_deleting_a_whole_draft_transaction_releases_its_references(client: TestClient, cf):
    put(client, cf, cf.lines[0], owner=str(cf.erik.id))
    put(client, cf, cf.lines[1], owner=str(cf.erik.id))
    assert client.delete(f"/api/customers/{cf.erik.id}", headers=cf.headers).status_code == 409

    assert client.delete(f"/api/transactions/{cf.tx.id}", headers=cf.headers).status_code == 204

    # Erik still owns Storm (a foreign key), so release that first
    client.delete(f"/api/horses/{cf.storm.id}", headers=cf.headers)
    assert client.delete(f"/api/customers/{cf.erik.id}", headers=cf.headers).status_code == 204


def test_header_level_references_protect_their_targets_too(client: TestClient, db_session: Session, cf):
    make_definition(db_session, cf.org, entity_type="transaction", key="payer", field_type="reference", reference_source="customer")
    other = make_customer(db_session, cf.org, "Payer AB")
    client.patch(values_url(cf.tx.id, "transaction"), json={"values": {"payer": str(other.id)}}, headers=cf.headers)

    assert client.delete(f"/api/customers/{other.id}", headers=cf.headers).status_code == 409


def test_a_refused_delete_does_not_break_the_session(client: TestClient, cf):
    put(client, cf, cf.lines[0], owner=str(cf.anna.id))

    client.delete(f"/api/customers/{cf.anna.id}", headers=cf.headers)

    assert client.get("/api/customers", headers=cf.headers).status_code == 200


# --- every referenceable entity is under the guard -------------------------------------------------------------------------


def test_every_referenceable_entity_type_has_a_guarded_delete_path_in_this_suite():
    # Add a new referenceable entity -> this fails until its delete route is covered below.
    referenceable = {e.key for e in registry.all() if e.reference is not None}
    assert referenceable == set(DELETE_PATH)


@pytest.mark.parametrize("source", sorted(DELETE_PATH))
def test_deleting_any_referenceable_record_goes_through_the_guard(client: TestClient, db_session: Session, cf, source):
    make_definition(db_session, cf.org, key=f"ref_{source}", field_type="reference", reference_source=source, position=40)
    targets = {
        "customer": lambda: make_customer(db_session, cf.org, "Target AB"),
        "horse": lambda: make_horse(db_session, cf.org, "Target", owner=cf.anna),
        "supplier": lambda: _supplier(db_session, cf.org),
    }
    target = targets[source]()
    put(client, cf, cf.lines[0], **{f"ref_{source}": str(target.id)})

    blocked = client.delete(f"{DELETE_PATH[source]}/{target.id}", headers=cf.headers)
    put(client, cf, cf.lines[0], **{f"ref_{source}": None})
    released = client.delete(f"{DELETE_PATH[source]}/{target.id}", headers=cf.headers)

    assert blocked.status_code == 409 and blocked.json()["detail"] == GENERIC_409[source]
    assert released.status_code == 204


# --- dangling ids render safely ---------------------------------------------------------------------------------------------------


def test_a_reference_whose_target_was_removed_behind_the_apis_back_renders_as_missing(client: TestClient, db_session: Session, cf):
    lone = make_customer(db_session, cf.org, "Lone Customer")
    put(client, cf, cf.lines[0], owner=str(lone.id))
    db_session.execute(text("delete from customers where id = :id"), {"id": str(lone.id)})  # bypasses the guard

    single = shown(client, cf, cf.lines[0])["owner"]
    bulk = client.get(f"{BASE}/values", params={"entity_type": "transaction_line", "entity_ids": str(cf.lines[0].id)}, headers=cf.headers)

    assert (single["value"], single["display"], single["active"], single["missing"]) == (str(lone.id), None, None, True)
    assert bulk.status_code == 200 and bulk.json()["entities"][str(cf.lines[0].id)][0]["missing"] is True


def test_a_dangling_reference_can_be_cleared_or_replaced(client: TestClient, db_session: Session, cf):
    lone = make_customer(db_session, cf.org, "Lone Customer")
    put(client, cf, cf.lines[0], owner=str(lone.id))
    db_session.execute(text("delete from customers where id = :id"), {"id": str(lone.id)})

    assert put(client, cf, cf.lines[0], owner=str(cf.anna.id)).status_code == 200
    assert shown(client, cf, cf.lines[0])["owner"]["display"] == "Anna Andersson"


def test_a_source_that_is_no_longer_registered_renders_as_missing_without_crashing(client: TestClient, cf):
    put(client, cf, cf.lines[0], owner=str(cf.anna.id), horse=str(cf.kalle.id))

    with registry.isolated():
        registry._entities.pop("horse")  # e.g. the module providing horses is switched off
        body = client.get(values_url(cf.lines[0].id), headers=cf.headers)

    values = {v["key"]: v for v in body.json()["values"]}
    assert body.status_code == 200
    assert values["owner"]["display"] == "Anna Andersson"
    assert values["horse"]["missing"] is True and values["horse"]["display"] is None


def test_unknown_reference_ids_are_refused_with_the_standard_error(client: TestClient, cf):
    response = put(client, cf, cf.lines[0], owner=str(uuid.uuid4()))

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "reference.not_found"
