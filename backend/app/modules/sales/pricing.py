"""Line and transaction amounts. Pure functions, Decimal only, no floats.

Rules (see docs/architecture.md):

    net   = round_half_up(quantity * unit_price_ex_vat, 2)
    vat   = round_half_up(net * vat_rate / 100, 2)
    gross = net + vat

Every line is rounded on its own and the three amounts are STORED on the line
(PostgreSQL CHECK constraints keep them consistent with the inputs). Transaction
totals and the VAT breakdown are plain sums of those stored line amounts. They are
never recomputed from grouped net totals, so the lines always add up to the totals.

PostgreSQL's round(numeric, 2) rounds half away from zero, which equals ROUND_HALF_UP
for the non-negative values the schema allows, so Python and the CHECK constraints agree.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Protocol

CENT = Decimal("0.01")
HUNDRED = Decimal(100)
# Same maximum as a unit price; larger line amounts are rejected, never truncated.
MAX_LINE_NET = Decimal("9999999999.99")


class AmountTooLarge(ValueError):
    pass


@dataclass(frozen=True)
class LineAmounts:
    net: Decimal
    vat: Decimal
    gross: Decimal


class StoredLine(Protocol):
    """What the totals need: the amounts already stored on a line."""

    vat_rate: Decimal
    net_amount: Decimal
    vat_amount: Decimal
    gross_amount: Decimal


@dataclass(frozen=True)
class VatBreakdownRow:
    vat_rate: Decimal
    net_amount: Decimal
    vat_amount: Decimal


@dataclass(frozen=True)
class Totals:
    net_amount: Decimal
    vat_amount: Decimal
    gross_amount: Decimal
    vat_breakdown: tuple[VatBreakdownRow, ...]


def calculate_line(
    quantity: Decimal, unit_price_ex_vat: Decimal, vat_rate: Decimal
) -> LineAmounts:
    # A local context with ample precision: quantity (12,3) * price (12,2) has up to 22 digits.
    with localcontext() as context:
        context.prec = 60
        net = (quantity * unit_price_ex_vat).quantize(CENT, rounding=ROUND_HALF_UP)
        if net > MAX_LINE_NET:
            raise AmountTooLarge(f"line amount exceeds {MAX_LINE_NET}")
        vat = (net * vat_rate / HUNDRED).quantize(CENT, rounding=ROUND_HALF_UP)
        return LineAmounts(net=net, vat=vat, gross=net + vat)


def calculate_totals(lines: Iterable[StoredLine]) -> Totals:
    """Sum STORED line amounts; group the sums by VAT rate. Nothing is re-rounded."""
    net = vat = gross = Decimal("0.00")
    by_rate: dict[Decimal, list[Decimal]] = {}
    for line in lines:
        net += line.net_amount
        vat += line.vat_amount
        gross += line.gross_amount
        rate = line.vat_rate.quantize(CENT)  # 25, 25.0 and 25.00 are one group
        group = by_rate.setdefault(rate, [Decimal("0.00"), Decimal("0.00")])
        group[0] += line.net_amount
        group[1] += line.vat_amount
    breakdown = tuple(
        VatBreakdownRow(vat_rate=rate, net_amount=sums[0], vat_amount=sums[1])
        for rate, sums in sorted(by_rate.items())
    )
    return Totals(net_amount=net, vat_amount=vat, gross_amount=gross, vat_breakdown=breakdown)
