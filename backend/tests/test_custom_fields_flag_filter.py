"""Generic "fields flagged X" retrieval, used later to find the fields eligible for an invoice.

The filter names a PROPERTY of a definition (show_on_invoice, required, ...). Custom Fields
learns nothing about who asks: it has no invoice-specific code, only the existing flag, and
this file proves the filter works for every flag in the same way and stays inside the tenant.
"""

import pytest

from app.models import Role
from app.modules.custom_fields import api as cf_api, service
from tests.factories import add_member, make_definition, make_org, make_transaction, make_user, make_value

BASE = "/api/custom-fields"
FLAGS = ["required", "show_in_form", "show_in_table", "show_on_invoice"]


def keys(rows) -> list[str]:
    return [row["key"] for row in rows]


@pytest.fixture
def fields(db_session, sales):
    org = sales.org
    return {
        "plain": make_definition(db_session, org, key="plain", position=10, show_in_form=False),
        "invoiced": make_definition(db_session, org, key="invoiced", position=20, show_on_invoice=True),
        "invoiced_header": make_definition(db_session, org, entity_type="transaction", key="po_number", position=10, show_on_invoice=True),
        "off": make_definition(db_session, org, key="off", position=30, show_on_invoice=True, enabled=False),
        "needed": make_definition(db_session, org, key="needed", position=40, required=True),
        "table": make_definition(db_session, org, key="in_table", position=50, show_in_table=True),
    }


def test_the_api_and_the_service_agree_on_the_list_of_flags():
    from typing import get_args

    assert list(get_args(cf_api.Flag)) == list(service.FLAGS) == FLAGS


def test_definitions_can_be_filtered_by_a_flag(client, sales, fields):
    rows = client.get(f"{BASE}/definitions", params={"flag": "show_on_invoice"}, headers=sales.headers).json()
    # enabled ones only (the default), in their normal order: entity type, position, key
    assert keys(rows) == ["po_number", "invoiced"]
    assert {row["show_on_invoice"] for row in rows} == {True}


def test_the_filter_combines_with_entity_type_and_include_disabled(client, sales, fields):
    only_lines = client.get(f"{BASE}/definitions", params={"flag": "show_on_invoice", "entity_type": "transaction_line"}, headers=sales.headers).json()
    assert keys(only_lines) == ["invoiced"]
    with_disabled = client.get(
        f"{BASE}/definitions", params={"flag": "show_on_invoice", "entity_type": "transaction_line", "include_disabled": "true"}, headers=sales.headers
    ).json()
    assert keys(with_disabled) == ["invoiced", "off"]


@pytest.mark.parametrize("flag, expected", [("required", ["needed"]), ("show_in_table", ["in_table"]), ("show_in_form", None)])
def test_every_flag_filters_the_same_way(client, sales, fields, flag, expected):
    rows = client.get(f"{BASE}/definitions", params={"flag": flag}, headers=sales.headers).json()
    if expected is not None:
        assert keys(rows) == expected
    assert all(row[flag] is True for row in rows)
    assert rows  # the fixture has at least one of each


def test_an_unknown_flag_is_refused(client, sales, fields):
    for bad in ("enabled", "key", "field_type", "show_on_invoices", "", "1=1"):
        assert client.get(f"{BASE}/definitions", params={"flag": bad}, headers=sales.headers).status_code == 422, bad


def test_without_a_flag_nothing_changes(client, sales, fields):
    # Every ENABLED definition, in entity-type / position order (the disabled one is left out).
    assert keys(client.get(f"{BASE}/definitions", headers=sales.headers).json()) == [
        "po_number", "plain", "invoiced", "needed", "in_table",
    ]


def test_values_can_be_filtered_by_the_same_flag(client, db_session, sales, fields):
    tx = make_transaction(db_session, sales.org, billing_customer=sales.billing)
    lines = client.get(f"/api/transactions/{tx.id}", headers=sales.headers).json()["lines"]
    line_id = lines[0]["id"]
    make_value(db_session, sales.org, fields["plain"], line_id, value_text="internal")
    make_value(db_session, sales.org, fields["invoiced"], line_id, value_text="for the invoice")
    make_value(db_session, sales.org, fields["off"], line_id, value_text="disabled one")
    make_value(db_session, sales.org, fields["invoiced_header"], tx.id, value_text="PO-1")

    everything = client.get(f"{BASE}/entities/transaction_line/{line_id}/values", headers=sales.headers).json()
    assert {v["key"] for v in everything["values"]} == {"plain", "invoiced"}

    flagged = client.get(f"{BASE}/entities/transaction_line/{line_id}/values", params={"flag": "show_on_invoice"}, headers=sales.headers).json()
    assert [(v["key"], v["value"]) for v in flagged["values"]] == [("invoiced", "for the invoice")]

    header = client.get(f"{BASE}/entities/transaction/{tx.id}/values", params={"flag": "show_on_invoice"}, headers=sales.headers).json()
    assert [(v["key"], v["value"]) for v in header["values"]] == [("po_number", "PO-1")]

    bulk = client.get(f"{BASE}/values", params={"entity_type": "transaction_line", "entity_ids": line_id, "flag": "show_on_invoice"}, headers=sales.headers).json()
    assert [v["key"] for v in bulk["entities"][line_id]] == ["invoiced"]
    assert client.get(f"{BASE}/values", params={"entity_type": "transaction_line", "entity_ids": line_id, "flag": "nope"}, headers=sales.headers).status_code == 422


def test_other_organizations_flagged_fields_and_values_never_appear(client, db_session, sales, fields):
    other = make_org(db_session, "Other")
    make_definition(db_session, other, key="their_invoiced", show_on_invoice=True)
    their_tx = make_transaction(db_session, other)
    their_line = client.get(f"/api/transactions/{their_tx.id}", headers=_headers(db_session, other)).json()["lines"][0]["id"]
    their_flagged = make_definition(db_session, other, key="v", show_on_invoice=True)
    make_value(db_session, other, their_flagged, their_line, value_text="secret")

    mine = client.get(f"{BASE}/definitions", params={"flag": "show_on_invoice"}, headers=sales.headers).json()
    assert "their_invoiced" not in keys(mine)
    # Asking for their record by id answers exactly like asking for a nonexistent one.
    foreign = client.get(f"{BASE}/entities/transaction_line/{their_line}/values", params={"flag": "show_on_invoice"}, headers=sales.headers)
    assert foreign.status_code == 404
    bulk = client.get(f"{BASE}/values", params={"entity_type": "transaction_line", "entity_ids": their_line, "flag": "show_on_invoice"}, headers=sales.headers).json()
    assert bulk["entities"] == {}


def _headers(db, org):
    user = make_user(db)
    add_member(db, org, user, Role.OWNER)
    return {"X-Dev-User-Email": user.email, "X-Organization-Id": str(org.id)}


def test_the_service_refuses_an_unknown_flag_instead_of_ignoring_it(db_session, sales):
    from app.core.tenant import TenantContext

    ctx = TenantContext(user=None, organization_id=sales.org.id, role=Role.OWNER)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        service.load_definitions(db_session, ctx, flag="enabled")
