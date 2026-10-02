"""What PostgreSQL itself enforces for transactions and lines (bypassing the API)."""

from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import engine
from app.models import Customer, Item
from app.modules.sales.models import Transaction, TransactionLine
from tests.factories import make_customer, make_item, make_line, make_org, make_transaction

D = Decimal


@pytest.fixture
def two_orgs(db_session: Session):
    org_a, org_b = make_org(db_session, "A"), make_org(db_session, "B")
    return org_a, org_b


def count(db: Session, model, **where) -> int:
    query = select(func.count()).select_from(model)
    for column, value in where.items():
        query = query.where(getattr(model, column) == value)
    return db.scalar(query)


# --- line amounts are CHECK-enforced ---------------------------------------------------------


def test_consistent_line_is_accepted_and_stored_exactly(db_session: Session):
    org = make_org(db_session)
    tx = make_transaction(db_session, org, lines=[])

    line = make_line(db_session, org, tx, quantity="3", unit_price_ex_vat="19.99", vat_rate="12.00")
    db_session.refresh(line)

    assert (line.net_amount, line.vat_amount, line.gross_amount) == (D("59.97"), D("7.20"), D("67.17"))
    assert isinstance(line.quantity, Decimal) and line.quantity == D("3.000")


@pytest.mark.parametrize(
    "wrong",
    [
        {"net_amount": D("850.01")},
        {"vat_amount": D("212.51")},
        {"gross_amount": D("1062.51")},
        {"net_amount": D("0.00"), "vat_amount": D("0.00"), "gross_amount": D("0.00")},
    ],
    ids=["net off by a cent", "vat off by a cent", "gross off by a cent", "all zero"],
)
def test_database_rejects_amounts_inconsistent_with_the_inputs(db_session: Session, wrong):
    org = make_org(db_session)
    tx = make_transaction(db_session, org, lines=[])
    with pytest.raises(IntegrityError), db_session.begin_nested():
        make_line(db_session, org, tx, **wrong)  # 1 x 850.00 at 25 %


@pytest.mark.parametrize(
    "fields",
    [
        {"quantity": "0"},
        {"quantity": "-1"},
        {"unit_price_ex_vat": "-0.01"},
        {"vat_rate": "100.01"},
        {"vat_rate": "-0.01"},
    ],
    ids=["zero quantity", "negative quantity", "negative price", "vat over 100", "negative vat"],
)
def test_database_rejects_out_of_range_inputs(db_session: Session, fields):
    org = make_org(db_session)
    tx = make_transaction(db_session, org, lines=[])
    with pytest.raises(IntegrityError), db_session.begin_nested():
        make_line(db_session, org, tx, **fields)


def test_database_rejects_unknown_status(db_session: Session):
    org = make_org(db_session)
    with pytest.raises(IntegrityError), db_session.begin_nested():
        make_transaction(db_session, org, status="invoiced")  # not a lifecycle state


def test_database_requires_a_billing_customer_and_an_organization(db_session: Session, two_orgs):
    org_a, _ = two_orgs
    for fields in ({"billing_customer_id": None}, {"organization_id": None}):
        with pytest.raises(IntegrityError), db_session.begin_nested():
            db_session.add(
                Transaction(
                    **{
                        "organization_id": org_a.id,
                        "billing_customer_id": make_customer(db_session, org_a).id,
                        "transaction_date": "2026-10-01",
                        **fields,
                    }
                )
            )
            db_session.flush()


def test_status_defaults_to_draft(db_session: Session):
    org = make_org(db_session)
    tx = Transaction(
        organization_id=org.id,
        billing_customer_id=make_customer(db_session, org).id,
        transaction_date="2026-10-01",
    )
    db_session.add(tx)
    db_session.flush()
    db_session.refresh(tx)
    assert tx.status == "draft"


# --- composite foreign keys: no cross-tenant link, even through direct SQL ---------------------


def test_database_rejects_a_billing_customer_from_another_organization(db_session: Session, two_orgs):
    org_a, org_b = two_orgs
    with pytest.raises(IntegrityError), db_session.begin_nested():
        make_transaction(db_session, org_a, billing_customer=make_customer(db_session, org_b))


def test_database_rejects_an_item_from_another_organization(db_session: Session, two_orgs):
    org_a, org_b = two_orgs
    tx = make_transaction(db_session, org_a, lines=[])
    with pytest.raises(IntegrityError), db_session.begin_nested():
        make_line(db_session, org_a, tx, item=make_item(db_session, org_b))


def test_database_rejects_a_line_whose_transaction_is_in_another_organization(
    db_session: Session, two_orgs
):
    org_a, org_b = two_orgs
    foreign_tx = make_transaction(db_session, org_b, lines=[])
    with pytest.raises(IntegrityError), db_session.begin_nested():
        make_line(db_session, org_a, foreign_tx)


def test_a_line_may_have_no_item(db_session: Session, two_orgs):
    org_a, _ = two_orgs
    tx = make_transaction(db_session, org_a, lines=[])
    assert make_line(db_session, org_a, tx, item=None).item_id is None


def test_same_organization_references_are_accepted(db_session: Session, two_orgs):
    org_a, _ = two_orgs
    tx = make_transaction(db_session, org_a, lines=[])
    assert make_line(db_session, org_a, tx, item=make_item(db_session, org_a)).id


# --- deletion behaviour ---------------------------------------------------------------------------


def test_deleting_a_transaction_removes_its_lines(db_session: Session, two_orgs):
    org_a, _ = two_orgs
    tx = make_transaction(db_session, org_a, lines=[{}, {}])
    assert count(db_session, TransactionLine, transaction_id=tx.id) == 2

    db_session.delete(tx)
    db_session.flush()

    assert count(db_session, TransactionLine, transaction_id=tx.id) == 0


def test_a_billing_customer_cannot_be_deleted_while_referenced(db_session: Session, two_orgs):
    org_a, _ = two_orgs
    customer = make_customer(db_session, org_a)
    make_transaction(db_session, org_a, billing_customer=customer, lines=[])

    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.delete(customer)
        db_session.flush()


def test_an_item_cannot_be_deleted_while_a_line_references_it(db_session: Session, two_orgs):
    org_a, _ = two_orgs
    item = make_item(db_session, org_a)
    tx = make_transaction(db_session, org_a, lines=[])
    make_line(db_session, org_a, tx, item=item)

    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.delete(item)
        db_session.flush()


def test_the_organization_of_a_transaction_cannot_be_changed(db_session: Session, two_orgs):
    org_a, org_b = two_orgs
    tx = make_transaction(db_session, org_a, lines=[])
    with pytest.raises(ValueError, match="cannot be changed"), db_session.begin_nested():
        tx.organization_id = org_b.id


# --- schema ----------------------------------------------------------------------------------------


def test_reference_targets_have_the_unique_pair_composite_keys_need():
    inspector = sa.inspect(engine)
    for table in ("customers", "items", "transactions"):
        pairs = [u["column_names"] for u in inspector.get_unique_constraints(table)]
        assert ["organization_id", "id"] in pairs, table


def test_money_and_quantity_columns_are_exact_numerics():
    columns = {c["name"]: c["type"] for c in sa.inspect(engine).get_columns("transaction_lines")}
    expected = {
        "quantity": (12, 3),
        "unit_price_ex_vat": (12, 2),
        "vat_rate": (5, 2),
        "net_amount": (14, 2),
        "vat_amount": (14, 2),
        "gross_amount": (14, 2),
    }
    for name, (precision, scale) in expected.items():
        assert isinstance(columns[name], sa.Numeric) and not isinstance(columns[name], sa.Float), name
        assert (columns[name].precision, columns[name].scale) == (precision, scale), name


def test_sales_tables_hold_no_industry_specific_columns():
    inspector = sa.inspect(engine)
    names = [
        column["name"]
        for table in ("transactions", "transaction_lines")
        for column in inspector.get_columns(table)
    ]
    assert not [n for n in names if any(w in n for w in ("horse", "owner", "stable", "vehicle", "project"))]
    assert "billing_customer_id" in [c["name"] for c in inspector.get_columns("transactions")]
    assert "billing_customer_id" not in [c["name"] for c in inspector.get_columns("transaction_lines")]
