"""The step from the stored invoice document to the PDF document: what is taken, what is left out, how a field becomes text."""

import uuid

import pytest

from app.modules.invoicing.pdf.build import GONE, NO, YES, document_from_invoice, field_text
from app.modules.invoicing.pdf.document import canonical_json, source_sha256

DEFINITION = str(uuid.uuid4())
SOURCE_ID, SOURCE_LINE_ID, CUSTOMER_ID, USER_ID = (str(uuid.uuid4()) for _ in range(4))


def entry(field_type="text", **overrides):
    values = dict(key="k", label="Label", field_type=field_type, value="v", display="v", missing=False, position=10, definition_id=DEFINITION)
    values.update(overrides)
    return values


def stored(**overrides):
    """An issued invoice in the shape GET /api/invoices/{id} returns."""
    data = dict(
        id=str(uuid.uuid4()), status="issued", version=4, number=7, number_text="7", customer_id=CUSTOMER_ID, issued_by=USER_ID,
        invoice_date="2026-10-01", due_date="2026-10-31", currency="SEK", description="October work",
        issuer_snapshot=dict(name="Fredrik Horse Therapy", legal_name="Fredrik Horse Therapy AB", address_line1="Storgatan 1", address_line2=None,
                             postal_code="903 26", city="Umeå", country_code="SE", registration_number="556000-0001", vat_number="SE556000000101", email=None, phone=None),
        customer_snapshot=dict(name="Umeå HK", legal_name=None, address_line1="Ridvägen 2", address_line2=None, postal_code="903 30", city="Umeå",
                               country_code="SE", registration_number=None, vat_number=None, email="hk@example.test", phone="070-1"),
        transactions=[dict(transaction_id=SOURCE_ID, position=1, transaction_date="2026-09-30", source_version=1, fields=[entry("text", label="PO", value="PO-17", display="PO-17")])],
        lines=[
            dict(id=str(uuid.uuid4()), position=1, source_transaction_id=SOURCE_ID, source_line_id=SOURCE_LINE_ID, description="Horse massage", unit="session",
                 quantity="1.000", unit_price_ex_vat="850.00", vat_rate="25.00", net_amount="850.00", vat_amount="212.50", gross_amount="1062.50",
                 fields=[entry("reference", label="Owner", value=str(uuid.uuid4()), display="Anna Andersson")]),
        ],
        vat_breakdown=[dict(vat_rate="25.00", net_amount="850.00", vat_amount="212.50")],
        net_amount="850.00", vat_amount="212.50", gross_amount="1062.50",
    )
    data.update(overrides)
    return data


def test_the_stored_figures_are_copied_as_the_strings_they_are():
    document = document_from_invoice(stored())
    line = document.lines[0]
    assert (line.quantity, line.unit_price, line.vat_rate, line.net, line.vat, line.gross) == ("1.000", "850.00", "25.00", "850.00", "212.50", "1062.50")
    assert (document.net, document.vat, document.gross) == ("850.00", "212.50", "1062.50")
    assert [(row.rate, row.net, row.vat) for row in document.vat_rows] == [("25.00", "850.00", "212.50")]
    assert (document.number_text, document.invoice_date, document.due_date, document.currency, document.description) == ("7", "2026-10-01", "2026-10-31", "SEK", "October work")


def test_inconsistent_figures_are_not_corrected():
    data = stored(net_amount="1.11", vat_amount="2.22", gross_amount="3.33")
    data["lines"][0].update(net_amount="4.44", vat_amount="5.55", gross_amount="6.66")
    document = document_from_invoice(data)
    assert (document.net, document.vat, document.gross) == ("1.11", "2.22", "3.33")
    assert (document.lines[0].net, document.lines[0].vat, document.lines[0].gross) == ("4.44", "5.55", "6.66")


def test_the_issuer_is_printed_under_its_legal_name_and_the_customer_under_its_name():
    document = document_from_invoice(stored())
    assert document.issuer.name == "Fredrik Horse Therapy AB" and document.customer.name == "Umeå HK"
    assert document.issuer.lines == ("Storgatan 1", "903 26 Umeå", "SE", "Registration no. 556000-0001", "VAT no. SE556000000101")
    assert document.customer.lines == ("Ridvägen 2", "903 30 Umeå", "SE", "hk@example.test", "070-1")


def test_a_party_without_stored_details_has_no_invented_lines():
    data = stored()
    data["issuer_snapshot"] = dict(name="Solo", legal_name=None)
    data["customer_snapshot"] = dict(name="Anna")
    document = document_from_invoice(data)
    assert document.issuer.name == "Solo" and document.issuer.lines == ()
    assert document.customer.name == "Anna" and document.customer.lines == ()


def test_the_document_carries_no_navigation_ids_so_nothing_downstream_could_look_anything_up():
    document = document_from_invoice(stored())
    text = canonical_json(document).decode("utf-8")
    for private in (DEFINITION, SOURCE_ID, SOURCE_LINE_ID, CUSTOMER_ID, USER_ID):
        assert private not in text


def test_source_dates_and_both_field_levels_are_kept_in_order():
    document = document_from_invoice(stored())
    assert [(source.date, [(f.label, f.text) for f in source.fields]) for source in document.sources] == [("2026-09-30", [("PO", "PO-17")])]
    assert [(f.label, f.text) for f in document.lines[0].fields] == [("Owner", "Anna Andersson")]


@pytest.mark.parametrize(
    "stored_entry, expected",
    [
        (entry("text", value="plain", display=None), "plain"),
        (entry("text", value="plain", display="shown"), "shown"),
        (entry("number", value="12.5", display="12.5"), "12.5"),
        (entry("date", value="2026-10-01", display="2026-10-01"), "2026-10-01"),
        (entry("boolean", value=True, display="Yes"), YES),
        (entry("boolean", value=False, display="No"), NO),
        (entry("boolean", value=None, display=None), ""),
        (entry("select", value="opt", display="Therapy"), "Therapy"),
        (entry("reference", value="id", display="Anna Andersson"), "Anna Andersson"),
        (entry("reference", value="id", display=None, missing=True), GONE),
        (entry("select", value="id", display=None, missing=True), GONE),
        (entry("text", value=None, display=None), ""),
    ],
)
def test_a_field_becomes_the_text_its_type_says(stored_entry, expected):
    assert field_text(stored_entry) == expected


def test_a_reference_that_no_longer_exists_says_so_in_the_document():
    data = stored()
    data["lines"][0]["fields"] = [entry("reference", label="Owner", display=None, missing=True)]
    assert [(f.label, f.text) for f in document_from_invoice(data).lines[0].fields] == [("Owner", "(no longer existed)")]


def test_text_is_cleaned_on_the_way_in():
    data = stored(description="Hello\x00 wor‮ld\r\nnext")
    data["lines"][0]["description"] = "Café"
    document = document_from_invoice(data)
    assert document.description == "Hello world\nnext"
    assert document.lines[0].description == "Café"


def test_only_an_issued_invoice_has_a_pdf_document():
    for overrides in (dict(status="draft"), dict(number_text=None)):
        with pytest.raises(ValueError):
            document_from_invoice(stored(**overrides))


def test_the_source_hash_is_stable_and_changes_with_any_printed_value():
    first = document_from_invoice(stored())
    assert source_sha256(first) == source_sha256(document_from_invoice(stored()))  # (the random ids of stored() are not in the document)
    assert len(source_sha256(first)) == 64
    changed = [
        stored(description="other"),
        stored(gross_amount="1062.51"),
        stored(number_text="8"),
    ]
    for other in changed:
        assert source_sha256(document_from_invoice(other)) != source_sha256(first)
    data = stored()
    data["lines"][0]["fields"][0]["display"] = "Someone else"
    assert source_sha256(document_from_invoice(data)) != source_sha256(first)
