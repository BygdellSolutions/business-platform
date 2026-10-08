"""Exact decimal types for money and percentages. Floating point never enters.

Input rule: a JSON string ("19.99") or an integer (850). JSON numbers with a
fractional part are rejected, because by the time the backend sees `19.99` it
has already been parsed into a binary float and the original text is gone.
Accepting only strings/integers means no float is ever converted to a Decimal.

Strings are matched against a strict pattern, so exponents ("1e2"), signs,
spaces, "NaN" and "Infinity" are all rejected, and more decimals than the
column allows is an error instead of silent rounding.

Output: always a string with a fixed number of decimals: two for money and
percentages ("850.00", "25.00"), three for quantities ("1.000").
"""

import re
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BeforeValidator, Field, PlainSerializer

_MONEY_RE = re.compile(r"\d{1,10}(\.\d{1,2})?")  # NUMERIC(12,2): up to 10 integer digits
_PERCENT_RE = re.compile(r"\d{1,3}(\.\d{1,2})?")  # NUMERIC(5,2): up to 3 integer digits
_QUANTITY_RE = re.compile(r"\d{1,9}(\.\d{1,3})?")  # NUMERIC(12,3): up to 9 integer digits

_HINT = 'send it as a decimal string such as "19.99", not as a JSON number with decimals'


def _strict_decimal_input(pattern: re.Pattern[str]):
    def validate(value: Any) -> Any:
        if isinstance(value, bool) or isinstance(value, float):
            raise ValueError(_HINT)
        if isinstance(value, str) and not pattern.fullmatch(value):
            raise ValueError("must be a non-negative decimal within the allowed precision; " + _HINT)
        return value  # ints and valid strings go straight to Decimal

    return validate


def _two_decimals(value: Decimal) -> str:
    return f"{value:.2f}"


def _three_decimals(value: Decimal) -> str:
    return f"{value:.3f}"


_two_decimals_out = PlainSerializer(_two_decimals, return_type=str, when_used="json")
_three_decimals_out = PlainSerializer(_three_decimals, return_type=str, when_used="json")

# NUMERIC(12,2), >= 0.
MoneyIn = Annotated[
    Decimal,
    BeforeValidator(_strict_decimal_input(_MONEY_RE)),
    Field(ge=0, max_digits=12, decimal_places=2, allow_inf_nan=False),
]
# NUMERIC(5,2), 0..100.
PercentIn = Annotated[
    Decimal,
    BeforeValidator(_strict_decimal_input(_PERCENT_RE)),
    Field(ge=0, le=100, max_digits=5, decimal_places=2, allow_inf_nan=False),
]

# A discount: NUMERIC(5,2), strictly between 0 and 100 (0 is "no discount", which is null; 100 would be free).
DiscountPercentIn = Annotated[
    Decimal,
    BeforeValidator(_strict_decimal_input(_PERCENT_RE)),
    Field(gt=0, lt=100, max_digits=5, decimal_places=2, allow_inf_nan=False),
]

# NUMERIC(12,3), strictly positive (quantities of units, hours, kilograms, ...).
QuantityIn = Annotated[
    Decimal,
    BeforeValidator(_strict_decimal_input(_QUANTITY_RE)),
    Field(gt=0, max_digits=12, decimal_places=3, allow_inf_nan=False),
]

MoneyOut = Annotated[Decimal, _two_decimals_out]
PercentOut = Annotated[Decimal, _two_decimals_out]
QuantityOut = Annotated[Decimal, _three_decimals_out]
