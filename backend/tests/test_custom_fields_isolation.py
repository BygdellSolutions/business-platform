"""Tenant isolation for custom fields beyond the flat contract: options, values, choices,
bulk reads and reference targets.

Both organizations have IDENTICAL-looking data: the same field keys (owner, horse, grade),
customers named Anna Andersson, a horse named Kalle and a transaction with one line. Any
query that forgets the organization shows up as a wrong id, a leaked label or a mix-up of
two definitions that share a key.
"""

import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Customer, Organization, Role, User
from app.modules.custom_fields.models import CustomFieldDefinition, CustomFieldOption, CustomFieldValue
from app.modules.equine.models import Horse
from app.modules.sales.models import Transaction, TransactionLine
from tests.factories import (
    add_member,
    make_customer,
    make_definition,
    make_horse,
    make_org,
    make_transaction,
    make_user,
    make_value,
)

BASE = "/api/custom-fields"


@dataclass
class Side:
    org: Organization
    anna: Customer
    kalle: Horse
    tx: Transaction
    line: TransactionLine
    owner: CustomFieldDefinition
    horse: CustomFieldDefinition
    grade: CustomFieldDefinition
    low: CustomFieldOption


@dataclass
class World:
    a: Side
    b: Side
    a_only: User
    b_only: User
    shared: User


def build_side(db: Session, name: str) -> Side:
    org = make_org(db, name)
    anna = make_customer(db, org, "Anna Andersson")
    kalle = make_horse(db, org, "Kalle", owner=anna)
    tx = make_transaction(db, org, lines=[{}])
    line = db.scalar(select(TransactionLine).where(TransactionLine.transaction_id == tx.id))
    owner = make_definition(db, org, key="owner", label="Owner", field_type="reference", reference_source="customer", position=10)
    horse = make_definition(db, org, key="horse", label="Horse", field_type="reference", reference_source="horse", depends_on=owner, depends_on_filter="owner_customer_id", position=20)
    grade = make_definition(db, org, key="grade", label="Grade", field_type="select", position=30, options=["Low"])
    low = db.scalar(select(CustomFieldOption).where(CustomFieldOption.definition_id == grade.id))
    return Side(org, anna, kalle, tx, line, owner, horse, grade, low)


@pytest.fixture
def world(db_session: Session) -> World:
    a, b = build_side(db_session, "Org A"), build_side(db_session, "Org B")
    a_only, b_only, shared = (make_user(db_session) for _ in range(3))
    add_member(db_session, a.org, a_only, Role.ADMIN)
    add_member(db_session, b.org, b_only, Role.ADMIN)
    add_member(db_session, a.org, shared, Role.OWNER)
    add_member(db_session, b.org, shared, Role.ADMIN)
    return World(a, b, a_only, b_only, shared)


def h(user: User, org: Organization | None = None) -> dict[str, str]:
    headers = {"X-Dev-User-Email": user.email}
    if org is not None:
        headers["X-Organization-Id"] = str(org.id)
    return headers


def values_url(entity_id, entity_type="transaction_line") -> str:
    return f"{BASE}/entities/{entity_type}/{entity_id}/values"


def value_rows(db: Session, org: Organization) -> int:
    return db.scalar(select(func.count()).select_from(CustomFieldValue).where(CustomFieldValue.organization_id == org.id))


# --- two definitions with the same key never mix -------------------------------------------------------------


def test_each_organization_sees_only_its_own_definitions(client: TestClient, world: World):
    a = client.get(f"{BASE}/definitions", headers=h(world.a_only)).json()
    b = client.get(f"{BASE}/definitions", headers=h(world.b_only)).json()

    assert {d["id"] for d in a} == {str(world.a.owner.id), str(world.a.horse.id), str(world.a.grade.id)}
    assert {d["id"] for d in b} == {str(world.b.owner.id), str(world.b.horse.id), str(world.b.grade.id)}
    assert [d["key"] for d in a] == [d["key"] for d in b] == ["owner", "horse", "grade"]  # same keys, different tenants


def test_a_value_is_stored_against_the_definition_of_its_own_organization(client: TestClient, db_session: Session, world: World):
    client.patch(values_url(world.a.line.id), json={"values": {"owner": str(world.a.anna.id)}}, headers=h(world.a_only))

    stored = db_session.scalar(select(CustomFieldValue).where(CustomFieldValue.entity_id == world.a.line.id))
    assert stored.definition_id == world.a.owner.id and stored.organization_id == world.a.org.id
    assert value_rows(db_session, world.b.org) == 0  # B untouched although it has the same keys


def test_writing_in_one_organization_leaves_the_identical_twin_untouched(client: TestClient, world: World):
    client.patch(values_url(world.a.line.id), json={"values": {"owner": str(world.a.anna.id)}}, headers=h(world.a_only))

    b_values = client.get(values_url(world.b.line.id), headers=h(world.b_only)).json()["values"]

    assert b_values == []


def test_the_same_user_gets_the_selected_organizations_definitions(client: TestClient, world: World):
    in_a = client.get(f"{BASE}/definitions", headers=h(world.shared, world.a.org)).json()
    in_b = client.get(f"{BASE}/definitions", headers=h(world.shared, world.b.org)).json()

    assert {d["id"] for d in in_a}.isdisjoint({d["id"] for d in in_b})


def test_a_dependency_can_only_name_a_parent_of_the_same_organization(client: TestClient, db_session: Session, world: World):
    make_definition(db_session, world.b.org, key="only_in_b", field_type="reference", reference_source="customer", position=40)

    response = client.post(
        f"{BASE}/definitions",
        json={"entity_type": "transaction_line", "key": "child", "label": "Child", "field_type": "reference",
              "reference": {"source": "horse", "depends_on": "only_in_b", "filter": "owner_customer_id"}},
        headers=h(world.a_only),
    )

    assert response.status_code == 422


# --- records and references of other organizations ----------------------------------------------------------------


def test_values_of_a_foreign_record_are_indistinguishable_from_a_nonexistent_record(client: TestClient, world: World):
    foreign_get = client.get(values_url(world.b.line.id), headers=h(world.a_only))
    missing_get = client.get(values_url(uuid.uuid4()), headers=h(world.a_only))
    foreign_put = client.patch(values_url(world.b.line.id), json={"values": {"owner": str(world.a.anna.id)}}, headers=h(world.a_only))
    missing_put = client.patch(values_url(uuid.uuid4()), json={"values": {"owner": str(world.a.anna.id)}}, headers=h(world.a_only))

    assert foreign_get.status_code == missing_get.status_code == foreign_put.status_code == missing_put.status_code == 404
    assert foreign_get.json() == missing_get.json() and foreign_put.json() == missing_put.json()


def test_a_foreign_record_cannot_be_given_values_even_by_a_member_of_both_organizations(client: TestClient, db_session: Session, world: World):
    response = client.patch(values_url(world.b.line.id), json={"values": {"owner": str(world.b.anna.id)}}, headers=h(world.shared, world.a.org))

    assert response.status_code == 404
    assert value_rows(db_session, world.b.org) == 0


@pytest.mark.parametrize("field,target", [("owner", "anna"), ("horse", "kalle")])
def test_a_foreign_reference_target_is_indistinguishable_from_a_nonexistent_one(client: TestClient, world: World, field: str, target: str):
    foreign_id = str(getattr(world.b, target).id)
    body = {"owner": str(world.a.anna.id)} if field == "horse" else {}

    foreign = client.patch(values_url(world.a.line.id), json={"values": {**body, field: foreign_id}}, headers=h(world.a_only))
    missing = client.patch(values_url(world.a.line.id), json={"values": {**body, field: str(uuid.uuid4())}}, headers=h(world.a_only))

    assert foreign.status_code == missing.status_code == 422
    assert foreign.json() == missing.json()
    assert foreign_id not in foreign.text


def test_a_foreign_option_is_indistinguishable_from_a_nonexistent_one(client: TestClient, world: World):
    foreign = client.patch(values_url(world.a.line.id), json={"values": {"grade": str(world.b.low.id)}}, headers=h(world.a_only))
    missing = client.patch(values_url(world.a.line.id), json={"values": {"grade": str(uuid.uuid4())}}, headers=h(world.a_only))

    assert foreign.status_code == missing.status_code == 422
    assert foreign.json() == missing.json()


def test_the_dependency_check_only_considers_the_organizations_own_records(client: TestClient, world: World):
    # B's Kalle is "Anna Andersson's horse" in B. From A, with A's Anna as owner, it is not even a record.
    response = client.patch(
        values_url(world.a.line.id),
        json={"values": {"owner": str(world.a.anna.id), "horse": str(world.b.kalle.id)}},
        headers=h(world.a_only),
    )

    assert response.status_code == 422 and response.json()["detail"][0]["type"] == "reference.not_found"


# --- choices ----------------------------------------------------------------------------------------------------------


def test_choices_contain_only_the_organizations_own_records(client: TestClient, db_session: Session, world: World):
    owners_a = client.get(f"{BASE}/definitions/{world.a.owner.id}/choices", headers=h(world.a_only)).json()
    owners_b = client.get(f"{BASE}/definitions/{world.b.owner.id}/choices", headers=h(world.b_only)).json()

    def customers_of(org):
        return {str(i) for i in db_session.scalars(select(Customer.id).where(Customer.organization_id == org.id))}

    assert {c["id"] for c in owners_a} == customers_of(world.a.org)
    assert {c["id"] for c in owners_b} == customers_of(world.b.org)
    assert [c["label"] for c in owners_a].count("Anna Andersson") == 1  # one Anna, not two identical ones


def test_dependent_choices_ignore_a_parent_value_from_another_organization(client: TestClient, world: World):
    base = f"{BASE}/definitions/{world.a.horse.id}/choices"

    own = client.get(base, params={"depends_on_value": str(world.a.anna.id)}, headers=h(world.a_only))
    foreign = client.get(base, params={"depends_on_value": str(world.b.anna.id)}, headers=h(world.a_only))
    missing = client.get(base, params={"depends_on_value": str(uuid.uuid4())}, headers=h(world.a_only))

    assert [c["id"] for c in own.json()] == [str(world.a.kalle.id)]
    assert foreign.status_code == missing.status_code == 200
    assert foreign.json() == missing.json() == []


def test_search_finds_only_the_organizations_own_records(client: TestClient, db_session: Session, world: World):
    make_customer(db_session, world.b.org, "Zelda Stable")

    found_in_a = client.get(f"{BASE}/definitions/{world.a.owner.id}/choices", params={"q": "zelda"}, headers=h(world.a_only))
    found_in_b = client.get(f"{BASE}/definitions/{world.b.owner.id}/choices", params={"q": "zelda"}, headers=h(world.b_only))

    assert found_in_a.json() == [] and len(found_in_b.json()) == 1


def test_choices_of_a_foreign_definition_are_404(client: TestClient, world: World):
    foreign = client.get(f"{BASE}/definitions/{world.b.owner.id}/choices", headers=h(world.a_only))
    missing = client.get(f"{BASE}/definitions/{uuid.uuid4()}/choices", headers=h(world.a_only))

    assert foreign.status_code == missing.status_code == 404 and foreign.json() == missing.json()


# --- bulk reads ---------------------------------------------------------------------------------------------------------------


def test_bulk_reads_silently_omit_other_organizations_records(client: TestClient, world: World):
    client.patch(values_url(world.a.line.id), json={"values": {"owner": str(world.a.anna.id)}}, headers=h(world.a_only))
    client.patch(values_url(world.b.line.id), json={"values": {"owner": str(world.b.anna.id)}}, headers=h(world.b_only))
    ids = f"{world.a.line.id},{world.b.line.id},{uuid.uuid4()}"

    body = client.get(f"{BASE}/values", params={"entity_type": "transaction_line", "entity_ids": ids}, headers=h(world.a_only)).json()

    assert set(body["entities"]) == {str(world.a.line.id)}  # B's id and the random one look the same: absent
    assert body["entities"][str(world.a.line.id)][0]["value"] == str(world.a.anna.id)


# --- options and definitions by id -------------------------------------------------------------------------------------------------


def test_options_cannot_be_reached_through_another_organizations_definition(client: TestClient, db_session: Session, world: World):
    before = db_session.scalar(select(func.count()).select_from(CustomFieldOption))
    foreign_base = f"{BASE}/definitions/{world.b.grade.id}/options"

    responses = [
        client.post(foreign_base, json={"label": "Hijack"}, headers=h(world.a_only)),
        client.patch(f"{foreign_base}/{world.b.low.id}", json={"label": "Hijack"}, headers=h(world.a_only)),
        client.patch(f"{BASE}/definitions/{world.a.grade.id}/options/{world.b.low.id}", json={"label": "Hijack"}, headers=h(world.a_only)),
        client.patch(f"{BASE}/definitions/{world.b.grade.id}/options/{world.a.low.id}", json={"label": "Hijack"}, headers=h(world.a_only)),
        client.patch(f"{BASE}/definitions/{world.b.grade.id}/options/{world.a.low.id}", json={"label": "Hijack"}, headers=h(world.b_only)),
    ]

    assert [r.status_code for r in responses] == [404] * 5
    assert db_session.scalar(select(func.count()).select_from(CustomFieldOption)) == before
    db_session.refresh(world.b.low)
    assert world.b.low.label == "Low"


def test_a_foreign_definition_cannot_be_disabled_or_relabelled(client: TestClient, db_session: Session, world: World):
    response = client.patch(f"{BASE}/definitions/{world.b.grade.id}", json={"enabled": False, "label": "x"}, headers=h(world.a_only))

    assert response.status_code == 404
    db_session.refresh(world.b.grade)
    assert world.b.grade.enabled is True and world.b.grade.label == "Grade"


# --- the delete guard is per organization ----------------------------------------------------------------------------------------


def test_a_reference_in_one_organization_never_protects_a_record_in_another(client: TestClient, db_session: Session, world: World):
    lone_a = make_customer(db_session, world.a.org, "Same Name")
    lone_b = make_customer(db_session, world.b.org, "Same Name")
    client.patch(values_url(world.a.line.id), json={"values": {"owner": str(lone_a.id)}}, headers=h(world.a_only))

    assert client.delete(f"/api/customers/{lone_a.id}", headers=h(world.a_only)).status_code == 409
    assert client.delete(f"/api/customers/{lone_b.id}", headers=h(world.b_only)).status_code == 204  # identical-looking twin is free


def test_the_delete_guard_only_counts_values_of_the_records_own_organization(client: TestClient, db_session: Session, world: World):
    # The API can never produce a value in B that points at a record of A. A row built
    # directly in the database can; the guard must still look at the record's own tenant only,
    # so one organization's data can never keep another organization's record alive.
    lone_a = make_customer(db_session, world.a.org, "Same Name")
    make_value(db_session, world.b.org, world.b.owner, world.b.line.id, value_reference_id=lone_a.id)

    assert client.delete(f"/api/customers/{lone_a.id}", headers=h(world.a_only)).status_code == 204


# --- selector and authentication guard every custom-field route ------------------------------------------------------------------------


def test_selecting_an_organization_you_do_not_belong_to_blocks_every_route(client: TestClient, world: World):
    foreign = h(world.a_only, world.b.org)  # a_only is not a member of B
    b = world.b

    responses = [
        client.get(f"{BASE}/entity-types", headers=foreign),
        client.get(f"{BASE}/definitions", headers=foreign),
        client.post(f"{BASE}/definitions", json={"entity_type": "transaction_line", "key": "x", "label": "X", "field_type": "text"}, headers=foreign),
        client.get(f"{BASE}/definitions/{b.owner.id}", headers=foreign),
        client.patch(f"{BASE}/definitions/{b.owner.id}", json={"label": "x"}, headers=foreign),
        client.post(f"{BASE}/definitions/{b.grade.id}/options", json={"label": "x"}, headers=foreign),
        client.patch(f"{BASE}/definitions/{b.grade.id}/options/{b.low.id}", json={"label": "x"}, headers=foreign),
        client.get(f"{BASE}/definitions/{b.owner.id}/choices", headers=foreign),
        client.get(values_url(b.line.id), headers=foreign),
        client.patch(values_url(b.line.id), json={"values": {}}, headers=foreign),
        client.get(f"{BASE}/values", params={"entity_type": "transaction_line", "entity_ids": str(b.line.id)}, headers=foreign),
    ]

    assert [r.status_code for r in responses] == [404] * len(responses)


def test_every_route_requires_authentication(client: TestClient, world: World):
    a = world.a

    responses = [
        client.get(f"{BASE}/entity-types"),
        client.get(f"{BASE}/definitions"),
        client.post(f"{BASE}/definitions", json={"entity_type": "transaction_line", "key": "x", "label": "X", "field_type": "text"}),
        client.get(f"{BASE}/definitions/{a.owner.id}"),
        client.patch(f"{BASE}/definitions/{a.owner.id}", json={"label": "x"}),
        client.post(f"{BASE}/definitions/{a.grade.id}/options", json={"label": "x"}),
        client.patch(f"{BASE}/definitions/{a.grade.id}/options/{a.low.id}", json={"label": "x"}),
        client.get(f"{BASE}/definitions/{a.owner.id}/choices"),
        client.get(values_url(a.line.id)),
        client.patch(values_url(a.line.id), json={"values": {}}),
        client.get(f"{BASE}/values", params={"entity_type": "transaction_line", "entity_ids": str(a.line.id)}),
    ]

    assert [r.status_code for r in responses] == [401] * len(responses)
