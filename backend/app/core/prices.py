"""Unit prices shown and entered in the catalog. Pure functions, Decimal only, no floats.

Lives in core so that the catalog (core) and Sales use the SAME rules; `app.modules.sales.pricing` re-exports
`discounted_unit_price`. Rounding is half-up to two decimals, exactly as a line of quantity 1 is calculated
(`pricing.calculate_line`): incl. VAT = price + round(price * rate / 100).
"""

from decimal import ROUND_HALF_UP, Decimal, localcontext

CENT = Decimal("0.01")
HUNDRED = Decimal(100)


def discounted_unit_price(
    list_price: Decimal, catalog_percent: Decimal | None, customer_percent: Decimal | None, line_percent: Decimal | None = None
) -> Decimal:
    """The unit price after the ordered discount layers (decided 2026-10-08): first the catalog's temporary discount,
    then the customer's permanent one, each rounded half-up to two decimals BEFORE the next is applied, so every
    printed step is the value actually used (100.00, -15% = 85.00, -10% = 76.50). Never additive (20% + 10% is 28%,
    not 30%). The line's own discount, when a person gives one, is the last layer. The database CHECK on the line
    repeats exactly this rule."""
    price = list_price
    with localcontext() as context:
        context.prec = 60
        for percent in (catalog_percent, customer_percent, line_percent):
            if percent:
                price = (price * (HUNDRED - percent) / HUNDRED).quantize(CENT, rounding=ROUND_HALF_UP)
    return price


def price_inc_vat(price_ex_vat: Decimal, vat_rate: Decimal) -> Decimal:
    """What one unit costs with VAT, rounded as a line of quantity 1 is."""
    with localcontext() as context:
        context.prec = 60
        return price_ex_vat + (price_ex_vat * vat_rate / HUNDRED).quantize(CENT, rounding=ROUND_HALF_UP)


def price_ex_vat_from_inc(price_inc: Decimal, vat_rate: Decimal) -> Decimal:
    """The price excl. VAT for a price a person entered incl. VAT: the nearest cent. Not every amount incl. VAT can be
    reached exactly (at 25 % one cent excl. VAT is 1.25 cents incl.), so the caller shows the result back."""
    with localcontext() as context:
        context.prec = 60
        return (price_inc * HUNDRED / (HUNDRED + vat_rate)).quantize(CENT, rounding=ROUND_HALF_UP)
