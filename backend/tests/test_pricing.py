"""Line and transaction amounts: per-line half-up rounding, totals as sums of stored amounts."""

import random
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.sales import pricing
from app.modules.sales.pricing import AmountTooLarge, calculate_line, calculate_totals
from tests.factories import make_line, make_org, make_transaction

D = Decimal

# quantity, unit price ex VAT, VAT %, net, VAT, gross
LINE_CASES = [
    ("1", "850.00", "25.00", "850.00", "212.50", "1062.50"),  # the milestone example
    ("0.5", "0.01", "25.00", "0.01", "0.00", "0.01"),  # net 0.005 rounds UP; VAT 0.0025 rounds down
    ("1.5", "0.01", "25.00", "0.02", "0.01", "0.03"),  # 0.015 -> 0.02; VAT 0.005 -> 0.01
    ("1", "0.02", "25.00", "0.02", "0.01", "0.03"),  # VAT exactly 0.005 -> 0.01
    ("1.005", "1.00", "25.00", "1.01", "0.25", "1.26"),  # a float gives 1.00 here
    ("2.675", "1.00", "0.00", "2.68", "0.00", "2.68"),  # classic float failure
    ("0.333", "3.00", "25.00", "1.00", "0.25", "1.25"),  # 0.999 -> 1.00
    ("3", "19.99", "12.00", "59.97", "7.20", "67.17"),  # VAT 7.1964 -> 7.20
    ("10", "0.10", "25.00", "1.00", "0.25", "1.25"),
    ("2.375", "10.00", "6.00", "23.75", "1.43", "25.18"),  # VAT 1.425 -> 1.43
    ("1", "100.00", "12.50", "100.00", "12.50", "112.50"),
    ("0.001", "0.01", "25.00", "0.00", "0.00", "0.00"),  # rounds to zero
    ("1", "0.00", "25.00", "0.00", "0.00", "0.00"),  # free
    ("1", "9999999999.99", "100.00", "9999999999.99", "9999999999.99", "19999999999.98"),  # max
]


@pytest.mark.parametrize("quantity,price,vat_rate,net,vat,gross", LINE_CASES)
def test_line_amounts(quantity, price, vat_rate, net, vat, gross):
    amounts = calculate_line(D(quantity), D(price), D(vat_rate))

    assert (amounts.net, amounts.vat, amounts.gross) == (D(net), D(vat), D(gross))
    assert all(isinstance(v, Decimal) for v in (amounts.net, amounts.vat, amounts.gross))
    assert amounts.gross == amounts.net + amounts.vat


@pytest.mark.parametrize("quantity,price,vat_rate,net,vat,gross", LINE_CASES)
def test_amounts_keep_two_decimal_places(quantity, price, vat_rate, net, vat, gross):
    amounts = calculate_line(D(quantity), D(price), D(vat_rate))
    assert str(amounts.net) == net and str(amounts.vat) == vat and str(amounts.gross) == gross


def test_a_line_amount_above_the_maximum_is_refused_not_truncated():
    with pytest.raises(AmountTooLarge):
        calculate_line(D("999999999"), D("9999999999.99"), D("25.00"))
    # exactly at the limit is fine
    assert calculate_line(D("1"), D("9999999999.99"), D("0.00")).net == D("9999999999.99")


def test_pricing_module_never_uses_floats():
    source = Path(pricing.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\bfloat\s*\(|\bfloat\b\s*[:=,)]", source)


# --- totals are sums of the STORED line amounts -----------------------------------------------


@dataclass
class Stored:
    vat_rate: Decimal
    net_amount: Decimal
    vat_amount: Decimal
    gross_amount: Decimal


def stored(quantity, price, vat_rate) -> Stored:
    amounts = calculate_line(D(quantity), D(price), D(vat_rate))
    return Stored(D(vat_rate), amounts.net, amounts.vat, amounts.gross)


def test_totals_sum_the_stored_line_amounts():
    lines = [stored("1", "850.00", "25.00"), stored("2", "100.00", "12.00"), stored("1", "0.02", "25.00")]

    totals = calculate_totals(lines)

    assert totals.net_amount == D("850.00") + D("200.00") + D("0.02")
    assert totals.vat_amount == D("212.50") + D("24.00") + D("0.01")
    assert totals.gross_amount == sum((line.gross_amount for line in lines), D("0.00"))
    assert totals.gross_amount == totals.net_amount + totals.vat_amount


def test_vat_breakdown_is_not_recomputed_from_grouped_net():
    # Three lines of net 0.02 at 25 %: each line's VAT rounds 0.005 UP to 0.01, so the
    # stored VAT sums to 0.03. Recomputing from the grouped net (0.06 * 25 % = 0.015 -> 0.02)
    # would give a different number. The approved rule is: sum what is stored.
    lines = [stored("1", "0.02", "25.00") for _ in range(3)]

    totals = calculate_totals(lines)

    row = totals.vat_breakdown[0]
    assert (row.net_amount, row.vat_amount) == (D("0.06"), D("0.03"))
    assert totals.vat_amount == D("0.03")
    assert D("0.06") * D("25.00") / 100 != row.vat_amount  # the regrouped figure would differ


def test_totals_use_exactly_what_is_stored_even_if_it_is_not_what_a_recalculation_gives():
    # Proves nothing is re-rounded: feed amounts that a recalculation would never produce.
    lines = [Stored(D("25.00"), D("10.00"), D("2.00"), D("12.00"))]

    totals = calculate_totals(lines)

    assert (totals.net_amount, totals.vat_amount, totals.gross_amount) == (D("10.00"), D("2.00"), D("12.00"))


def test_vat_breakdown_groups_by_rate_and_is_ordered():
    lines = [
        stored("1", "100.00", "25.00"),
        stored("1", "50.00", "6.00"),
        stored("2", "100.00", "25"),  # 25 and 25.00 are one group
        stored("1", "10.00", "12.00"),
        stored("1", "20.00", "0.00"),
    ]

    breakdown = calculate_totals(lines).vat_breakdown

    assert [row.vat_rate for row in breakdown] == [D("0.00"), D("6.00"), D("12.00"), D("25.00")]
    by_rate = {row.vat_rate: row for row in breakdown}
    assert (by_rate[D("25.00")].net_amount, by_rate[D("25.00")].vat_amount) == (D("300.00"), D("75.00"))
    assert by_rate[D("0.00")].vat_amount == D("0.00")


def test_no_lines_gives_zero_totals():
    totals = calculate_totals([])
    assert (totals.net_amount, totals.vat_amount, totals.gross_amount) == (D("0.00"),) * 3
    assert totals.vat_breakdown == ()


def test_many_small_amounts_add_up_exactly():
    # Ten times 0.10 is exactly 1.00 (binary floats give 0.9999999999999999).
    totals = calculate_totals([stored("1", "0.10", "0.00") for _ in range(10)])
    assert totals.net_amount == D("1.00") and totals.gross_amount == D("1.00")


# --- Python and PostgreSQL agree on every rounding decision --------------------------------------

TIE_QUANTITIES = ["0.001", "0.005", "0.015", "0.5", "1.005", "2.375", "2.675", "10.5", "0.333"]
TIE_PRICES = ["0.01", "0.10", "1.00", "3.00", "19.99", "0.07"]
VAT_RATES = ["0.00", "6.00", "12.00", "12.50", "25.00", "0.01", "99.99", "100.00"]


def random_cases(count: int):
    rng = random.Random(20261002)  # fixed seed: reproducible, and integers only (no floats)
    for _ in range(count):
        quantity = D(rng.randint(1, 10**8)).scaleb(-3)
        price = D(rng.randint(0, 10**8)).scaleb(-2)
        vat_rate = D(rng.choice([0, 600, 1200, 1250, 2500, rng.randint(0, 10000)])).scaleb(-2)
        yield quantity, price, vat_rate


def postgres_amounts(db: Session, quantity, price, vat_rate):
    return db.execute(
        text(
            "select round(cast(:q as numeric) * cast(:p as numeric), 2) as net,"
            " round(round(cast(:q as numeric) * cast(:p as numeric), 2)"
            "       * cast(:v as numeric) / 100, 2) as vat"
        ),
        {"q": quantity, "p": price, "v": vat_rate},
    ).one()


def test_python_rounding_matches_postgres_round_on_ties_and_random_values(db_session: Session):
    tie_cases = [(D(q), D(p), D(v)) for q in TIE_QUANTITIES for p in TIE_PRICES for v in VAT_RATES]
    checked = 0
    for quantity, price, vat_rate in [*tie_cases, *random_cases(400)]:
        try:
            amounts = calculate_line(quantity, price, vat_rate)
        except AmountTooLarge:
            continue
        net, vat = postgres_amounts(db_session, quantity, price, vat_rate)
        assert (amounts.net, amounts.vat) == (net, vat), (quantity, price, vat_rate)
        checked += 1
    assert checked > 400


@pytest.mark.parametrize("quantity", TIE_QUANTITIES)
@pytest.mark.parametrize("price", TIE_PRICES)
def test_stored_amounts_pass_the_database_check_constraints(db_session: Session, quantity, price):
    # The CHECK constraints use PostgreSQL's round(); inserting what Python calculated
    # must always be accepted, including every rounding tie.
    org = make_org(db_session)
    tx = make_transaction(db_session, org, lines=[])
    for vat_rate in ("25.00", "12.50", "6.00"):
        make_line(db_session, org, tx, quantity=quantity, unit_price_ex_vat=price, vat_rate=vat_rate)


def test_the_database_rejects_amounts_that_python_would_not_have_calculated(db_session: Session):
    org = make_org(db_session)
    tx = make_transaction(db_session, org, lines=[])
    with pytest.raises(IntegrityError), db_session.begin_nested():
        # 0.005 rounded DOWN (banker's/float-style) instead of half-up
        make_line(db_session, org, tx, quantity="0.5", unit_price_ex_vat="0.01", net_amount=D("0.00"),
                  vat_amount=D("0.00"), gross_amount=D("0.00"))
