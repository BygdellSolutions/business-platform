"""A viewer reads an organization's business records and changes none of them.

Two layers:
  * an inventory of the application's routes: every tenant-scoped write must go through a role
    dependency that refuses viewers (a new route without one fails here, not in production);
  * the behavior: each write a viewer sends is refused with 403 and the database is unchanged,
    while an employee is admitted by the same request.
"""

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.authz import RECORD_WRITERS
from app.core.tenant import get_tenant_context
from app.main import app
from app.models import Role
from tests.factories import add_member, make_customer, make_definition, make_horse, make_item, make_line, make_org, make_transaction, make_user, make_value

READS = {"GET", "HEAD"}

# Tenant-scoped writes that a viewer may perform on purpose, with the reason.
VIEWER_MAY = {
    ("POST", "/api/members/leave"): "anyone may leave an organization",
}

# Tenant-scoped writes whose role rule is decided inside the service from fresh, locked rows
# (membership administration and invitations), so they carry no static role dependency. Their
# own test files cover viewers; listed here so a NEW unchecked route still fails.
DECIDED_UNDER_LOCK = {
    ("PATCH", "/api/members/{membership_id}"),
    ("DELETE", "/api/members/{membership_id}"),
    ("POST", "/api/invitations"),
    ("POST", "/api/invitations/{invitation_id}/regenerate"),
    ("DELETE", "/api/invitations/{invitation_id}"),
    # Owner-only, decided from fresh locked memberships (tests/test_danger_zone.py covers non-owners).
    ("POST", "/api/organization/transfer-ownership"),
    ("POST", "/api/organization/delete"),
}


def _calls(dependant) -> Iterator[Callable]:
    for dependency in dependant.dependencies:
        yield dependency.call
        yield from _calls(dependency)


def _api_routes(routes) -> Iterator[APIRoute]:
    # FastAPI keeps an included router as one wrapper route; its routes carry the full path.
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif (included := getattr(route, "original_router", None)) is not None:
            yield from _api_routes(included.routes)


def _tenant_writes() -> list[tuple[str, str, list[Callable]]]:
    found = []
    for route in _api_routes(app.routes):
        calls = list(_calls(route.dependant))
        if get_tenant_context not in calls:
            continue
        for method in sorted(route.methods - READS):
            found.append((method, route.path, calls))
    return found


def test_the_inventory_sees_the_routes_it_is_about():
    # Control: an empty walk would make the next test pass vacuously.
    routes = {(method, path) for method, path, _ in _tenant_writes()}
    assert ("POST", "/api/customers") in routes
    assert ("PATCH", "/api/custom-fields/entities/{entity_type}/{entity_id}/values") in routes
    assert ("POST", "/api/transactions/{transaction_id}/complete") in routes


def test_every_tenant_scoped_write_refuses_viewers_by_a_role_dependency():
    unguarded = []
    for method, path, calls in _tenant_writes():
        if (method, path) in VIEWER_MAY or (method, path) in DECIDED_UNDER_LOCK:
            continue
        allowed = [call.allowed_roles for call in calls if hasattr(call, "allowed_roles")]
        if not allowed or any(Role.VIEWER in roles for roles in allowed):
            unguarded.append(f"{method} {path}")

    assert unguarded == []


def test_the_exception_lists_name_only_real_routes():
    routes = {(method, path) for method, path, _ in _tenant_writes()}
    assert set(VIEWER_MAY) <= routes
    assert DECIDED_UNDER_LOCK <= routes


def test_record_writers_are_every_role_but_viewer():
    assert RECORD_WRITERS == set(Role) - {Role.VIEWER}


# --- behavior -------------------------------------------------------------------------------------------------------


@dataclass
class Write:
    method: str
    url: Callable[[SimpleNamespace], str]
    body: Callable[[SimpleNamespace], dict] | None = None
    if_match: Callable[[SimpleNamespace], int] | None = None


WRITES: dict[str, Write] = {
    "create customer": Write("POST", lambda w: "/api/customers", lambda w: {"name": "New", "customer_type": "person"}),
    "update customer": Write("PATCH", lambda w: f"/api/customers/{w.customer.id}", lambda w: {"name": "Renamed"}),
    "delete customer": Write("DELETE", lambda w: f"/api/customers/{w.spare_customer.id}"),
    "create item": Write(
        "POST",
        lambda w: "/api/items",
        lambda w: {"type": "service", "name": "New", "unit": "st", "price_ex_vat": "1.00", "vat_rate": "25.00"},
    ),
    "update item": Write("PATCH", lambda w: f"/api/items/{w.item.id}", lambda w: {"name": "Renamed"}),
    "delete item": Write("DELETE", lambda w: f"/api/items/{w.spare_item.id}"),
    "create horse": Write("POST", lambda w: "/api/horses", lambda w: {"name": "New", "owner_customer_id": str(w.customer.id)}),
    "update horse": Write("PATCH", lambda w: f"/api/horses/{w.horse.id}", lambda w: {"name": "Renamed"}),
    "delete horse": Write("DELETE", lambda w: f"/api/horses/{w.horse.id}"),
    "create transaction": Write("POST", lambda w: "/api/transactions", lambda w: {"billing_customer_id": str(w.customer.id)}),
    "update transaction": Write(
        "PATCH", lambda w: f"/api/transactions/{w.draft.id}", lambda w: {"transaction_date": "2026-10-02"}, lambda w: w.draft.header_version
    ),
    "delete transaction": Write("DELETE", lambda w: f"/api/transactions/{w.draft.id}", None, lambda w: w.draft.version),
    "complete transaction": Write("POST", lambda w: f"/api/transactions/{w.draft.id}/complete", None, lambda w: w.draft.version),
    "reopen transaction": Write("POST", lambda w: f"/api/transactions/{w.completed.id}/reopen", None, lambda w: w.completed.version),
    "cancel transaction": Write("POST", lambda w: f"/api/transactions/{w.completed.id}/cancel", None, lambda w: w.completed.version),
    "add line": Write(
        "POST", lambda w: f"/api/transactions/{w.draft.id}/lines", lambda w: {"item_id": str(w.item.id), "quantity": "1"}
    ),
    "update line": Write(
        "PATCH",
        lambda w: f"/api/transactions/{w.draft.id}/lines/{w.line.id}",
        lambda w: {"quantity": "2"},
        lambda w: w.line.version,
    ),
    "delete line": Write(
        "DELETE", lambda w: f"/api/transactions/{w.draft.id}/lines/{w.line.id}", None, lambda w: w.line.version
    ),
    "write custom values": Write(
        "PATCH",
        lambda w: f"/api/custom-fields/entities/transaction/{w.draft.id}/values",
        lambda w: {"values": {"memo": "written"}},
    ),
}

TENANT_TABLES = ("customers", "items", "horses", "transactions", "transaction_lines", "custom_field_values")


def _world(db: Session, role: Role) -> SimpleNamespace:
    org = make_org(db, f"Org of a {role}")
    user = make_user(db)
    add_member(db, org, user, role)
    customer = make_customer(db, org)
    draft = make_transaction(db, org, billing_customer=customer, lines=[])
    line = make_line(db, org, draft)
    make_definition(db, org, entity_type="transaction", key="memo")
    return SimpleNamespace(
        org=org,
        headers={"X-Dev-User-Email": user.email},
        customer=customer,
        spare_customer=make_customer(db, org, "Nobody refers to me", email=None),
        item=make_item(db, org),
        spare_item=make_item(db, org, "Unused"),
        horse=make_horse(db, org, owner=customer),
        draft=draft,
        line=line,
        completed=make_transaction(db, org, billing_customer=customer, status="completed"),
    )


def _fingerprint(db: Session, org) -> str:
    rows = {
        table: db.execute(
            text(f"SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id), '[]') FROM {table} t WHERE organization_id = :org"),
            {"org": org.id},
        ).scalar_one()
        for table in TENANT_TABLES
    }
    return json.dumps(rows, sort_keys=True, default=str)


def _send(client: TestClient, world: SimpleNamespace, write: Write):
    headers = dict(world.headers)
    if write.if_match is not None:
        headers["If-Match"] = f'"{write.if_match(world)}"'
    body = write.body(world) if write.body is not None else None
    return client.request(write.method, write.url(world), json=body, headers=headers)


@pytest.mark.parametrize("name", list(WRITES))
def test_a_viewer_is_refused_and_nothing_changes(client: TestClient, db_session: Session, name: str):
    world = _world(db_session, Role.VIEWER)
    before = _fingerprint(db_session, world.org)

    response = _send(client, world, WRITES[name])

    assert response.status_code == 403, response.text
    db_session.expire_all()
    assert _fingerprint(db_session, world.org) == before


@pytest.mark.parametrize("name", list(WRITES))
def test_an_employee_is_admitted_by_the_same_request(client: TestClient, db_session: Session, name: str):
    world = _world(db_session, Role.EMPLOYEE)
    before = _fingerprint(db_session, world.org)

    response = _send(client, world, WRITES[name])

    assert response.status_code in (200, 201, 204), response.text
    db_session.expire_all()
    assert _fingerprint(db_session, world.org) != before  # control: the observer sees a real write


def test_a_viewer_still_reads_everything(client: TestClient, db_session: Session):
    world = _world(db_session, Role.VIEWER)
    for url in (
        "/api/customers",
        f"/api/customers/{world.customer.id}",
        "/api/items",
        f"/api/items/{world.item.id}",
        "/api/horses",
        f"/api/horses/{world.horse.id}",
        "/api/transactions",
        f"/api/transactions/{world.draft.id}",
        f"/api/custom-fields/entities/transaction/{world.draft.id}/values",
    ):
        assert client.get(url, headers=world.headers).status_code == 200, url


def test_a_viewers_refusal_reveals_nothing_about_foreign_records(client: TestClient, db_session: Session):
    # The role is judged before any lookup, so a viewer gets the same answer for its own record,
    # another organization's record and an id that exists nowhere: it cannot probe with writes.
    viewer = _world(db_session, Role.VIEWER)
    other = _world(db_session, Role.OWNER)
    answers = {
        target: client.patch(f"/api/customers/{customer_id}", json={"name": "x"}, headers=viewer.headers).status_code
        for target, customer_id in (
            ("own", viewer.customer.id),
            ("foreign", other.customer.id),
            ("nowhere", "00000000-0000-4000-8000-000000000000"),
        )
    }
    assert answers == {"own": 403, "foreign": 403, "nowhere": 403}

    # A writer in the viewer's organization still gets the usual 404 for the foreign record.
    writer = make_user(db_session)
    add_member(db_session, viewer.org, writer, Role.EMPLOYEE)
    foreign = client.patch(f"/api/customers/{other.customer.id}", json={"name": "x"}, headers={"X-Dev-User-Email": writer.email})
    assert foreign.status_code == 404


# --- custom-field values: the rule spelled out ----------------------------------------------------------------------
# Values have no endpoint of their own per operation: one PATCH creates (a key without a value), updates (a key with
# one) and deletes (null clears the value and removes its row). Each is checked separately for every role.


def _values_world(db: Session, role: Role) -> SimpleNamespace:
    org = make_org(db, f"Values of a {role}")
    user = make_user(db)
    add_member(db, org, user, role)
    draft = make_transaction(db, org, lines=[])
    memo = make_definition(db, org, entity_type="transaction", key="memo")
    make_definition(db, org, entity_type="transaction", key="note")
    make_value(db, org, memo, draft.id, value_text="original")
    return SimpleNamespace(org=org, draft=draft, headers={"X-Dev-User-Email": user.email})


def _stored_values(db: Session, world: SimpleNamespace) -> dict[str, str]:
    db.expire_all()
    rows = db.execute(
        text(
            "SELECT d.key, v.value_text FROM custom_field_values v JOIN custom_field_definitions d ON d.id = v.definition_id"
            " WHERE v.organization_id = :org AND v.entity_id = :entity"
        ),
        {"org": world.org.id, "entity": world.draft.id},
    )
    return dict(rows.all())


def _write_values(client: TestClient, world: SimpleNamespace, **values):
    return client.patch(f"/api/custom-fields/entities/transaction/{world.draft.id}/values", json={"values": values}, headers=world.headers)


def test_a_viewer_reads_values_but_cannot_create_update_or_delete_them(client: TestClient, db_session: Session):
    world = _values_world(db_session, Role.VIEWER)

    one = client.get(f"/api/custom-fields/entities/transaction/{world.draft.id}/values", headers=world.headers)
    bulk = client.get(f"/api/custom-fields/values?entity_type=transaction&entity_ids={world.draft.id}", headers=world.headers)
    assert one.status_code == 200 and [(v["key"], v["value"]) for v in one.json()["values"]] == [("memo", "original")]
    assert bulk.status_code == 200 and [v["value"] for v in bulk.json()["entities"][str(world.draft.id)]] == ["original"]

    assert _write_values(client, world, note="created").status_code == 403  # create
    assert _write_values(client, world, memo="updated").status_code == 403  # update
    assert _write_values(client, world, memo=None).status_code == 403  # delete
    assert _stored_values(db_session, world) == {"memo": "original"}


@pytest.mark.parametrize("role", sorted(RECORD_WRITERS))
def test_every_writer_role_creates_updates_and_deletes_values(client: TestClient, db_session: Session, role: Role):
    world = _values_world(db_session, role)

    assert _write_values(client, world, note="created").status_code == 200
    assert _stored_values(db_session, world) == {"memo": "original", "note": "created"}
    assert _write_values(client, world, memo="updated").status_code == 200
    assert _stored_values(db_session, world) == {"memo": "updated", "note": "created"}
    assert _write_values(client, world, memo=None).status_code == 200
    assert _stored_values(db_session, world) == {"note": "created"}
