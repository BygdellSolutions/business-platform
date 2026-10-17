"""The payment reference of an invoice: an OCR number (Swedish bank payments), made from the invoice number.

Digits only. The second-to-last digit is the LENGTH digit (the length of the whole reference, modulo 10) and the last digit is a
Luhn (modulus 10) CHECK digit over everything before it, so a bank or a bookkeeping program can see that a number was mistyped.
Bankgiro accepts 2 to 25 digits; an invoice number of up to 9 digits gives at most 11.
"""


def luhn_check_digit(digits: str) -> int:
    """The digit that makes `digits` + it pass the Luhn check (the rightmost payload digit is doubled)."""
    total = 0
    for position, character in enumerate(reversed(digits)):
        value = int(character) * (2 if position % 2 == 0 else 1)
        total += value - 9 if value > 9 else value
    return (10 - total % 10) % 10


def ocr_reference(number: int) -> str:
    """The OCR number of invoice `number` (a positive integer): number + length digit + check digit."""
    if number < 1:
        raise ValueError("an invoice number is a positive integer")
    with_length = f"{number}{(len(str(number)) + 2) % 10}"
    return f"{with_length}{luhn_check_digit(with_length)}"


def is_valid_ocr(reference: str) -> bool:
    """Whether `reference` has the length digit and check digit this module writes."""
    if not reference.isascii() or not reference.isdigit() or not 3 <= len(reference) <= 25:
        return False
    return int(reference[-2]) == len(reference) % 10 and luhn_check_digit(reference[:-1]) == int(reference[-1])
