"""Presentation formatting of STORED decimal strings. Strings in, strings out.

Formatting is not calculation. "850.00" becomes "850.00", "1062.50" becomes "1 062.50" (digits grouped
by a no-break space), "1.000" becomes "1": the digits of the stored value are only re-spaced or have
trailing zeros of the fraction dropped. There is no number type here: nothing is parsed into one, so
nothing can be rounded, added, multiplied or reconstructed. A value that does not look like a decimal
is returned unchanged rather than guessed at.

V1 is locale-neutral: a dot as the decimal separator and a no-break space between digit groups.
Localized formats are future work.
"""

import re

NBSP = " "
_DECIMAL = re.compile(r"^(-?)(\d+)(?:\.(\d+))?$")


def _group(digits: str) -> str:
    head = len(digits) % 3 or 3
    parts = [digits[:head]] + [digits[i : i + 3] for i in range(head, len(digits), 3)]
    return NBSP.join(parts)


def money(value: str) -> str:
    """A stored amount or unit price: grouped integer digits, the stored decimals untouched."""
    found = _DECIMAL.match(value)
    if found is None:
        return value
    sign, whole, fraction = found.groups()
    return f"{sign}{_group(whole)}" + (f".{fraction}" if fraction is not None else "")


def trimmed(value: str) -> str:
    """A stored quantity or rate: without the zeros that only pad its fraction ("1.000" -> "1", "12.50" -> "12.5")."""
    found = _DECIMAL.match(value)
    if found is None:
        return value
    sign, whole, fraction = found.groups()
    fraction = (fraction or "").rstrip("0")
    return f"{sign}{_group(whole)}" + (f".{fraction}" if fraction else "")
