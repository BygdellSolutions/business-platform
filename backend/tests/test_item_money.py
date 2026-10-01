"""Money and VAT must stay exact: API -> Python -> PostgreSQL -> API, never a float.

The values below are chosen because binary floating point gets them wrong
(0.1 + 0.2, 4.35 * 100, 8.20, ...). If any float conversion sneaks into the path,
the exact-string comparisons here fail.
"""

from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import Base, engine
from app.models import Item
from tests.factories import make_item, make_org

BODY = {"type": "service", "name": "Money test", "unit": "pcs", "vat_rate": "25.00"}

# (input as sent, expected output string)
PRICES = [
    ("0.00", "0.00"),
    ("0", "0.00"),
    ("0.01", "0.01"),
    ("0.10", "0.10"),
    ("0.20", "0.20"),
    ("0.30", "0.30"),
    ("1.15", "1.15"),
    ("4.35", "4.35"),
    ("8.20", "8.20"),
    ("19.99", "19.99"),
    ("850", "850.00"),
    ("850.5", "850.50"),
    ("123456.78", "123456.78"),
    ("9999999999.99", "9999999999.99"),  # maximum
    (850, "850.00"),  # JSON integer
]
VAT_RATES = [
    ("0", "0.00"),
    ("0.01", "0.01"),
    ("6", "6.00"),
    ("12", "12.00"),
    ("12.5", "12.50"),
    ("25", "25.00"),
    ("25.00", "25.00"),
    ("99.99", "99.99"),
    ("100", "100.00"),  # maximum
    (25, "25.00"),
]


def post_item(client: TestClient, headers, **fields):
    return client.post("/api/items", json={**BODY, "price_ex_vat": "1.00", **fields}, headers=headers)


@pytest.mark.parametrize("sent,expected", PRICES)
def test_price_roundtrips_exactly_through_api_python_and_postgres(
    client: TestClient, db_session: Session, member, sent, expected
):
    _, headers = member

    created = post_item(client, headers, price_ex_vat=sent)

    assert created.status_code == 201, created.text
    assert created.json()["price_ex_vat"] == expected
    assert client.get(f"/api/items/{created.json()['id']}", headers=headers).json()["price_ex_vat"] == expected

    # What PostgreSQL actually stored, as text (no Python numeric involved).
    stored = db_session.scalar(
        text("select price_ex_vat::text from items where id = :id"), {"id": created.json()["id"]}
    )
    assert stored == expected

    # What Python loads back is a Decimal equal to the exact value, not a float.
    db_session.expire_all()
    item = db_session.get(Item, created.json()["id"])
    assert isinstance(item.price_ex_vat, Decimal)
    assert item.price_ex_vat == Decimal(expected)


@pytest.mark.parametrize("sent,expected", VAT_RATES)
def test_vat_rate_roundtrips_exactly(client: TestClient, db_session: Session, member, sent, expected):
    _, headers = member

    created = post_item(client, headers, vat_rate=sent)

    assert created.status_code == 201, created.text
    assert created.json()["vat_rate"] == expected
    stored = db_session.scalar(
        text("select vat_rate::text from items where id = :id"), {"id": created.json()["id"]}
    )
    assert stored == expected

    db_session.expire_all()
    item = db_session.get(Item, created.json()["id"])
    assert isinstance(item.vat_rate, Decimal)
    assert item.vat_rate == Decimal(expected)


@pytest.mark.parametrize("sent,expected", PRICES[:6])
def test_patching_the_price_roundtrips_exactly(client: TestClient, member, sent, expected):
    _, headers = member
    item_id = post_item(client, headers).json()["id"]

    patched = client.patch(f"/api/items/{item_id}", json={"price_ex_vat": sent}, headers=headers)

    assert patched.json()["price_ex_vat"] == expected
    assert patched.json()["vat_rate"] == "25.00"  # untouched


def test_sql_arithmetic_on_stored_prices_is_exact(db_session: Session):
    # With floats, ten times 0.10 sums to 0.9999999999999999.
    org = make_org(db_session)
    for _ in range(10):
        make_item(db_session, org, price_ex_vat="0.10")

    total = db_session.scalar(select(func.sum(Item.price_ex_vat)).where(Item.organization_id == org.id))

    assert isinstance(total, Decimal)
    assert total == Decimal("1.00")


# --- inputs that would require a float, or would be silently rounded, are rejected ------


@pytest.mark.parametrize("raw", ["19.99", "0.1", "850.0", "0.30000000000000004", "1e2", "4.35"])
def test_json_numbers_with_decimals_are_rejected_for_price(client: TestClient, member, raw):
    # Sent as raw JSON text so the number really is a JSON float literal.
    _, headers = member
    body = '{"type":"service","name":"n","unit":"u","vat_rate":"25","price_ex_vat":%s}' % raw

    response = client.post(
        "/api/items", content=body, headers={**headers, "Content-Type": "application/json"}
    )

    assert response.status_code == 422


@pytest.mark.parametrize("raw", ["25.0", "12.5", "6.0"])
def test_json_numbers_with_decimals_are_rejected_for_vat(client: TestClient, member, raw):
    _, headers = member
    body = '{"type":"service","name":"n","unit":"u","price_ex_vat":"1","vat_rate":%s}' % raw

    response = client.post(
        "/api/items", content=body, headers={**headers, "Content-Type": "application/json"}
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "bad",
    [
        "-0.01",  # negative
        "-1",
        "1.001",  # a third decimal is an error, not rounded
        "0.005",
        "1.005",
        "10000000000.00",  # over the maximum
        "99999999999",
        "1e2",  # exponent notation
        "1E2",
        "NaN",
        "Infinity",
        "-Infinity",
        "",
        " ",
        " 1.00",
        "1.00 ",
        "+1.00",
        "1,50",
        "1.",
        ".5",
        "abc",
        "0x10",
        True,
        False,
        None,
        [],
        {},
        10**12,
        -1,
    ],
    ids=repr,
)
def test_invalid_price_values_are_rejected(client: TestClient, member, bad):
    _, headers = member
    assert post_item(client, headers, price_ex_vat=bad).status_code == 422


@pytest.mark.parametrize(
    "bad",
    ["-1", "100.01", "101", "1000", "25.555", "0.001", "1e1", "NaN", "Infinity", "", "abc", "25%", True, None, -1],
    ids=repr,
)
def test_invalid_vat_values_are_rejected(client: TestClient, member, bad):
    _, headers = member
    assert post_item(client, headers, vat_rate=bad).status_code == 422


def test_rejected_values_create_nothing(client: TestClient, db_session: Session, member):
    _, headers = member
    before = db_session.scalar(select(func.count()).select_from(Item))

    for bad in ("1.001", "-1", "NaN"):
        post_item(client, headers, price_ex_vat=bad)

    assert db_session.scalar(select(func.count()).select_from(Item)) == before


# --- the database itself enforces the same limits and uses exact types -----------------------


def test_money_and_vat_columns_are_exact_numeric_types():
    columns = {c["name"]: c["type"] for c in sa.inspect(engine).get_columns("items")}

    assert isinstance(columns["price_ex_vat"], sa.Numeric) and not isinstance(
        columns["price_ex_vat"], sa.Float
    )
    assert (columns["price_ex_vat"].precision, columns["price_ex_vat"].scale) == (12, 2)
    assert (columns["vat_rate"].precision, columns["vat_rate"].scale) == (5, 2)


def test_no_table_anywhere_uses_a_floating_point_column():
    # Guard for every current and future table, including Transactions later.
    float_types = (sa.Float, sa.REAL, sa.dialects.postgresql.DOUBLE_PRECISION)

    declared = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, float_types)
    ]
    inspector = sa.inspect(engine)
    in_database = [
        f"{table}.{column['name']}"
        for table in inspector.get_table_names()
        for column in inspector.get_columns(table)
        if isinstance(column["type"], float_types)
    ]

    assert declared == [] and in_database == []


@pytest.mark.parametrize(
    "fields",
    [
        {"price_ex_vat": Decimal("-0.01")},
        {"vat_rate": Decimal("100.01")},
        {"vat_rate": Decimal("-0.01")},
        {"type": "gadget"},
    ],
    ids=["negative price", "vat over 100", "negative vat", "unknown type"],
)
def test_database_check_constraints(db_session: Session, fields):
    org = make_org(db_session)
    with pytest.raises(IntegrityError), db_session.begin_nested():
        make_item(db_session, org, **fields)


def test_database_numeric_overflow_is_rejected(db_session: Session):
    org = make_org(db_session)
    with pytest.raises(sa.exc.DBAPIError), db_session.begin_nested():
        make_item(db_session, org, price_ex_vat=Decimal("10000000000.00"))  # exceeds NUMERIC(12,2)


def test_database_requires_organization_and_rejects_unknown_one(db_session: Session):
    import uuid

    for org_id in (None, uuid.uuid4()):
        with pytest.raises(IntegrityError), db_session.begin_nested():
            db_session.add(
                Item(
                    organization_id=org_id,
                    type="service",
                    name="x",
                    unit="u",
                    price_ex_vat=Decimal("1.00"),
                    vat_rate=Decimal("25.00"),
                )
            )
            db_session.flush()
