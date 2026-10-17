"""The OCR payment reference: a length digit and a Luhn check digit after the invoice number."""

import pytest

from app.modules.invoicing.ocr import is_valid_ocr, luhn_check_digit, ocr_reference


def test_luhn_matches_the_published_example():
    assert luhn_check_digit("7992739871") == 3  # the standard worked example


def test_the_first_invoice_number_has_the_expected_reference():
    # 1001 + length digit (6 digits in all) + check digit
    assert ocr_reference(1001) == "10016" + str(luhn_check_digit("10016"))  # 6 digits in all: the length digit is 6
    assert ocr_reference(1001) == "100164"


@pytest.mark.parametrize("number", [1, 9, 10, 1001, 1002, 99999, 123456789])
def test_every_reference_is_valid_digits_only_and_short_enough(number):
    reference = ocr_reference(number)
    assert reference.isdigit() and is_valid_ocr(reference) and len(reference) <= 25
    assert reference.startswith(str(number))


def test_different_invoice_numbers_never_share_a_reference():
    references = {ocr_reference(number) for number in range(1, 20001)}
    assert len(references) == 20000


def test_a_mistyped_digit_or_a_swapped_pair_is_noticed():
    good = ocr_reference(1001)
    for position in range(len(good)):
        bad = good[:position] + str((int(good[position]) + 1) % 10) + good[position + 1:]
        assert not is_valid_ocr(bad), bad  # any single wrong digit
    for position in range(len(good) - 1):  # two different neighbours swapped (Luhn misses only 09 <-> 90)
        pair = good[position:position + 2]
        if pair[0] != pair[1] and set(pair) != {"0", "9"}:
            assert not is_valid_ocr(good[:position] + pair[::-1] + good[position + 2:]), (good, position)


def test_not_a_reference():
    for value in ("", "12", "abc", "1001６2", "1" * 26):
        assert not is_valid_ocr(value)
    with pytest.raises(ValueError):
        ocr_reference(0)
