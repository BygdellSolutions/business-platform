"""The tenant-scoped query helpers and the organization_id change guard."""

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.tenant import TenantContext
from app.core.tenant_scope import create_scoped, get_scoped, get_scoped_or_404, scoped_select
from app.models import Customer, Role
from tests.factories import make_customer, make_org, make_user


@pytest.fixture
def two_orgs(db_session: Session):
    user = make_user(db_session)
    org_a, org_b = make_org(db_session, "A"), make_org(db_session, "B")
    ctx_a = TenantContext(user=user, organization_id=org_a.id, role=Role.OWNER)
    ctx_b = TenantContext(user=user, organization_id=org_b.id, role=Role.OWNER)
    return org_a, org_b, ctx_a, ctx_b


def test_scoped_select_only_returns_active_organization(db_session: Session, two_orgs):
    org_a, org_b, ctx_a, ctx_b = two_orgs
    in_a, in_b = make_customer(db_session, org_a), make_customer(db_session, org_b)

    assert db_session.scalars(scoped_select(Customer, ctx_a)).all() == [in_a]
    assert db_session.scalars(scoped_select(Customer, ctx_b)).all() == [in_b]


def test_get_scoped_does_not_return_foreign_record(db_session: Session, two_orgs):
    org_a, org_b, ctx_a, ctx_b = two_orgs
    in_b = make_customer(db_session, org_b)

    assert get_scoped(db_session, ctx_a, Customer, in_b.id) is None
    assert get_scoped(db_session, ctx_b, Customer, in_b.id) is in_b


def test_get_scoped_or_404_is_the_same_for_foreign_and_missing(db_session: Session, two_orgs):
    _, org_b, ctx_a, _ = two_orgs
    in_b = make_customer(db_session, org_b)

    with pytest.raises(HTTPException) as foreign:
        get_scoped_or_404(db_session, ctx_a, Customer, in_b.id)
    with pytest.raises(HTTPException) as missing:
        get_scoped_or_404(db_session, ctx_a, Customer, uuid.uuid4())

    assert (foreign.value.status_code, foreign.value.detail) == (
        missing.value.status_code,
        missing.value.detail,
    )


def test_create_scoped_uses_context_organization(db_session: Session, two_orgs):
    org_a, _, ctx_a, _ = two_orgs

    customer = create_scoped(db_session, ctx_a, Customer, customer_type="person", name="X")

    assert customer.organization_id == org_a.id


def test_create_scoped_refuses_an_explicit_organization_id(db_session: Session, two_orgs):
    org_a, org_b, ctx_a, _ = two_orgs

    with pytest.raises(ValueError):
        create_scoped(
            db_session, ctx_a, Customer, organization_id=org_b.id, customer_type="person", name="X"
        )


def test_organization_id_cannot_be_changed_on_an_existing_record(db_session: Session, two_orgs):
    org_a, org_b, *_ = two_orgs
    customer = make_customer(db_session, org_a)

    with pytest.raises(ValueError, match="cannot be changed"), db_session.begin_nested():
        customer.organization_id = org_b.id


def test_other_fields_can_still_be_updated(db_session: Session, two_orgs):
    org_a, *_ = two_orgs
    customer = make_customer(db_session, org_a)

    customer.name = "Renamed"
    db_session.flush()

    assert customer.organization_id == org_a.id
