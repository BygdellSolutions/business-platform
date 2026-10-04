"""Presentation formatting of stored decimal strings: string handling only, never arithmetic."""

import random
from decimal import Decimal

import pytest

from app.modules.invoicing.pdf.format import NBSP, money, trimmed


@pytest.mark.parametrize(
    "stored, shown",
    [
        ("0.00", "0.00"),
        ("0.10", "0.10"),
        ("850.00", "850.00"),
        ("999.99", "999.99"),
        ("1000.00", f"1{NBSP}000.00"),
        ("1062.50", f"1{NBSP}062.50"),
        ("9999999999.99", f"9{NBSP}999{NBSP}999{NBSP}999.99"),
        ("123456789012345678.00", f"123{NBSP}456{NBSP}789{NBSP}012{NBSP}345{NBSP}678.00"),
        ("-1234.50", f"-1{NBSP}234.50"),
    ],
)
def test_money_keeps_the_stored_digits_and_decimals(stored, shown):
    assert money(stored) == shown


@pytest.mark.parametrize(
    "stored, shown",
    [
        ("1.000", "1"),
        ("7.001", "7.001"),
        ("2.500", "2.5"),
        ("25.00", "25"),
        ("6.00", "6"),
        ("12.50", "12.5"),
        ("0.001", "0.001"),
        ("100.00", "100"),
        ("1000.000", f"1{NBSP}000"),
        ("0.00", "0"),
    ],
)
def test_quantities_and_rates_lose_only_the_padding_zeros(stored, shown):
    assert trimmed(stored) == shown


@pytest.mark.parametrize("odd", ["", "abc", "1,5", "1e3", "NaN", "1.2.3", " 1.00", "1.00 "])
def test_a_value_that_is_not_a_decimal_is_returned_unchanged_never_guessed(odd):
    assert money(odd) == odd and trimmed(odd) == odd


def test_formatting_never_changes_the_value(monkeypatch):
    """For any stored decimal: removing the separators and the padding zeros gives back an equal number.
    (Decimal is used HERE, in the test, to prove it; the formatting code itself has no number type.)"""
    rng = random.Random(7)
    for _ in range(2000):
        whole = str(rng.randrange(0, 10 ** rng.randrange(1, 19)))
        places = rng.randrange(0, 6)
        stored = whole + ("." + "".join(rng.choice("0123456789") for _ in range(places)) if places else "")
        assert Decimal(money(stored).replace(NBSP, "")) == Decimal(stored), stored
        assert Decimal(trimmed(stored).replace(NBSP, "")) == Decimal(stored), stored
        assert money(stored).replace(NBSP, "") == stored  # money keeps every stored digit, in order


def test_formatting_has_no_number_type():
    """No float, int or Decimal is involved: an amount too big for any float is handled exactly."""
    huge = "9" * 40 + "." + "9" * 20
    assert money(huge).replace(NBSP, "") == huge
