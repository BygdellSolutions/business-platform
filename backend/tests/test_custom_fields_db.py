"""What PostgreSQL itself enforces for custom fields (bypassing the API)."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.core.db import engine
from app.modules.custom_fields.models import CustomFieldDefinition, CustomFieldOption, CustomFieldValue
from tests.factories import make_definition, make_option, make_org, make_value


@pytest.fixture
def two_orgs(db_session: Session):
    return make_org(db_session, "A"), make_org(db_session, "B")


def refused(db: Session, build):
    with pytest.raises(IntegrityError), db.begin_nested():
        build()


# --- definitions ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["Owner", "1owner", "has space", "dash-ed", ""])
def test_definition_keys_must_be_slugs(db_session: Session, key):
    org = make_org(db_session)
    refused(db_session, lambda: make_definition(db_session, org, key=key))


def test_definition_keys_have_a_maximum_length(db_session: Session):
    org = make_org(db_session)
    assert make_definition(db_session, org, key="x" * 40).id
    with pytest.raises(DataError), db_session.begin_nested():
        make_definition(db_session, org, key="x" * 41)


def test_definition_field_type_must_be_known(db_session: Session):
    org = make_org(db_session)
    for kind in ("money", "percent", "formula", ""):
        refused(db_session, lambda: make_definition(db_session, org, key="k", field_type=kind))


def test_reference_fields_need_a_source_and_only_they_have_one(db_session: Session):
    org = make_org(db_session)
    refused(db_session, lambda: make_definition(db_session, org, key="a", field_type="reference"))
    refused(db_session, lambda: make_definition(db_session, org, key="b", field_type="text", reference_source="customer"))
    assert make_definition(db_session, org, key="c", field_type="reference", reference_source="customer").id


def test_a_dependency_needs_both_the_parent_and_the_filter_and_only_references_have_one(db_session: Session):
    org = make_org(db_session)
    parent = make_definition(db_session, org, key="owner", field_type="reference", reference_source="customer")
    refused(db_session, lambda: make_definition(db_session, org, key="a", field_type="reference", reference_source="horse", depends_on=parent))
    refused(db_session, lambda: make_definition(db_session, org, key="b", field_type="reference", reference_source="horse", depends_on_filter="owner_customer_id"))
    refused(db_session, lambda: make_definition(db_session, org, key="c", field_type="text", depends_on=parent, depends_on_filter="x"))
    ok = make_definition(db_session, org, key="d", field_type="reference", reference_source="horse", depends_on=parent, depends_on_filter="owner_customer_id")
    assert ok.depends_on_definition_id == parent.id


def test_keys_are_unique_per_organization_and_entity_type(db_session: Session, two_orgs):
    org_a, org_b = two_orgs
    make_definition(db_session, org_a, key="note")

    refused(db_session, lambda: make_definition(db_session, org_a, key="note"))
    assert make_definition(db_session, org_a, key="note", entity_type="transaction").id
    assert make_definition(db_session, org_b, key="note").id  # same key in another tenant is fine


def test_a_dependency_cannot_point_at_another_organizations_definition(db_session: Session, two_orgs):
    org_a, org_b = two_orgs
    foreign_parent = make_definition(db_session, org_b, key="owner", field_type="reference", reference_source="customer")

    refused(
        db_session,
        lambda: make_definition(db_session, org_a, key="horse", field_type="reference", reference_source="horse", depends_on=foreign_parent, depends_on_filter="owner_customer_id"),
    )


def test_a_definition_cannot_be_deleted_while_it_has_values_or_dependents(db_session: Session):
    org = make_org(db_session)
    parent = make_definition(db_session, org, key="owner", field_type="reference", reference_source="customer")
    make_definition(db_session, org, key="horse", field_type="reference", reference_source="horse", depends_on=parent, depends_on_filter="owner_customer_id")
    text_field = make_definition(db_session, org, key="comment")
    make_value(db_session, org, text_field, uuid.uuid4(), value_text="x")

    for definition in (parent, text_field):
        with pytest.raises(IntegrityError), db_session.begin_nested():
            db_session.delete(definition)
            db_session.flush()


# --- options ------------------------------------------------------------------------------------------------


def test_options_belong_only_to_select_definitions(db_session: Session):
    org = make_org(db_session)
    text_field = make_definition(db_session, org, key="comment", field_type="text")
    select = make_definition(db_session, org, key="grade", field_type="select")

    refused(db_session, lambda: make_option(db_session, org, text_field, "Nope"))
    assert make_option(db_session, org, select, "Low").id


def test_an_option_cannot_belong_to_another_organizations_definition(db_session: Session, two_orgs):
    org_a, org_b = two_orgs
    foreign = make_definition(db_session, org_b, key="grade", field_type="select")

    refused(db_session, lambda: make_option(db_session, org_a, foreign, "Low"))


def test_option_field_type_is_pinned_to_select(db_session: Session):
    org = make_org(db_session)
    select = make_definition(db_session, org, key="grade", field_type="select")
    refused(
        db_session,
        lambda: (db_session.add(CustomFieldOption(organization_id=org.id, definition_id=select.id, field_type="text", label="x")), db_session.flush()),
    )


# --- values: one typed column, matching the definition ---------------------------------------------------------


GOOD = {
    "text": {"value_text": "hello"},
    "number": {"value_number": Decimal("12.5")},
    "date": {"value_date": date(2026, 10, 3)},
    "boolean": {"value_boolean": False},
    "reference": {"value_reference_id": uuid.uuid4()},
}


@pytest.mark.parametrize("kind", GOOD)
def test_each_type_stores_its_own_column(db_session: Session, kind):
    org = make_org(db_session)
    definition = make_definition(db_session, org, key="f", field_type=kind, reference_source="customer" if kind == "reference" else None)

    assert make_value(db_session, org, definition, uuid.uuid4(), **GOOD[kind]).id


def test_a_select_value_stores_the_option_id(db_session: Session):
    org = make_org(db_session)
    definition = make_definition(db_session, org, key="grade", field_type="select")
    option = make_option(db_session, org, definition, "Low")

    assert make_value(db_session, org, definition, uuid.uuid4(), value_option_id=option.id).value_option_id == option.id


@pytest.mark.parametrize(
    "kind,wrong",
    [(k, w) for k in GOOD for w in GOOD if k != w],
)
def test_a_value_in_the_wrong_column_is_refused(db_session: Session, kind, wrong):
    org = make_org(db_session)
    definition = make_definition(db_session, org, key="f", field_type=kind, reference_source="customer" if kind == "reference" else None)

    refused(db_session, lambda: make_value(db_session, org, definition, uuid.uuid4(), **GOOD[wrong]))


def test_a_value_needs_exactly_one_column(db_session: Session):
    org = make_org(db_session)
    definition = make_definition(db_session, org, key="comment")

    refused(db_session, lambda: make_value(db_session, org, definition, uuid.uuid4()))  # none
    refused(db_session, lambda: make_value(db_session, org, definition, uuid.uuid4(), value_text="x", value_number=Decimal(1)))  # two


def test_a_value_cannot_disagree_with_its_definition(db_session: Session):
    org = make_org(db_session)
    definition = make_definition(db_session, org, key="comment", field_type="text")

    refused(
        db_session,
        lambda: (
            db_session.add(
                CustomFieldValue(
                    organization_id=org.id, definition_id=definition.id, entity_type=definition.entity_type,
                    field_type="number", entity_id=uuid.uuid4(), value_number=Decimal(1),
                )
            ),
            db_session.flush(),
        ),
    )
    refused(
        db_session,
        lambda: (
            db_session.add(
                CustomFieldValue(
                    organization_id=org.id, definition_id=definition.id, entity_type="somewhere_else",
                    field_type="text", entity_id=uuid.uuid4(), value_text="x",
                )
            ),
            db_session.flush(),
        ),
    )


def test_one_value_per_field_and_record(db_session: Session):
    org = make_org(db_session)
    definition = make_definition(db_session, org, key="comment")
    record = uuid.uuid4()
    make_value(db_session, org, definition, record, value_text="a")

    refused(db_session, lambda: make_value(db_session, org, definition, record, value_text="b"))
    assert make_value(db_session, org, definition, uuid.uuid4(), value_text="b").id  # another record is fine


def test_a_value_cannot_use_an_option_of_another_definition(db_session: Session):
    org = make_org(db_session)
    grade = make_definition(db_session, org, key="grade", field_type="select")
    size = make_definition(db_session, org, key="size", field_type="select")
    size_option = make_option(db_session, org, size, "S")

    refused(db_session, lambda: make_value(db_session, org, grade, uuid.uuid4(), value_option_id=size_option.id))


def test_values_cannot_cross_organizations(db_session: Session, two_orgs):
    org_a, org_b = two_orgs
    foreign_definition = make_definition(db_session, org_b, key="comment")
    foreign_select = make_definition(db_session, org_b, key="grade", field_type="select")
    foreign_option = make_option(db_session, org_b, foreign_select, "Low")
    own_select = make_definition(db_session, org_a, key="grade", field_type="select")

    refused(db_session, lambda: make_value(db_session, org_a, foreign_definition, uuid.uuid4(), value_text="x"))
    refused(db_session, lambda: make_value(db_session, org_a, own_select, uuid.uuid4(), value_option_id=foreign_option.id))


def test_numbers_are_stored_exactly(db_session: Session):
    org = make_org(db_session)
    definition = make_definition(db_session, org, key="weight", field_type="number")
    value = make_value(db_session, org, definition, uuid.uuid4(), value_number=Decimal("0.1235"))

    stored = db_session.scalar(sa.text("select value_number::text from custom_field_values where id = :id"), {"id": value.id})

    assert stored == "0.1235"


def test_the_organization_of_a_definition_cannot_be_changed(db_session: Session):
    org_a, org_b = make_org(db_session, "A"), make_org(db_session, "B")
    definition = make_definition(db_session, org_a, key="comment")

    with pytest.raises(ValueError, match="cannot be changed"), db_session.begin_nested():
        definition.organization_id = org_b.id


# --- schema ------------------------------------------------------------------------------------------------------


def test_value_columns_are_typed_and_exact_numerics():
    columns = {c["name"]: c["type"] for c in sa.inspect(engine).get_columns("custom_field_values")}

    assert isinstance(columns["value_number"], sa.Numeric) and not isinstance(columns["value_number"], sa.Float)
    assert (columns["value_number"].precision, columns["value_number"].scale) == (18, 4)
    assert isinstance(columns["value_date"], sa.Date) and isinstance(columns["value_boolean"], sa.Boolean)


def test_the_lookup_indexes_exist():
    names = {i["name"] for i in sa.inspect(engine).get_indexes("custom_field_values")}

    assert {
        "ix_custom_field_values_organization_entity",
        "ix_custom_field_values_organization_reference",
        "ix_custom_field_values_organization_option",
    } <= names
