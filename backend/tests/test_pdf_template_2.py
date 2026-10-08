"""Invoice PDF template 2: the document's language, payment details, F-tax, delivery dates, line notes.

Everything still comes from the stored invoice alone. A schema-1 issuer snapshot (before slice 10) has no language and
no payment details: it prints in English without a payment block. Stored PDFs are never re-rendered (see the API tests).
"""

from app.modules.invoicing.pdf.build import document_from_invoice
from app.modules.invoicing.pdf.render import render_pdf
from tests.pdf_support import pdf_text
from tests.test_pdf_build import stored

SCHEMA_2 = dict(
    schema=2, name="Fredrik Horse Therapy", legal_name="Fredrik Horse Therapy AB", address_line1="Storgatan 1", address_line2=None,
    postal_code="903 26", city="Umeå", country_code="SE", registration_number="556000-0001", vat_number="SE556000000101",
    email="info@example.test", phone="090-123456", website="https://example.test", bankgiro="123-4567", plusgiro=None,
    iban="SE4550000000058398257466", bic="ESLSSESS", payment_terms_days=30, approved_for_f_tax=True, document_language="sv",
)


def _swedish(**overrides):
    return stored(issuer_snapshot={**SCHEMA_2, **overrides})


def test_a_swedish_invoice_speaks_swedish_and_writes_decimal_commas():
    text = pdf_text(render_pdf(document_from_invoice(_swedish())))

    for expected in ("Faktura", "Fakturanummer", "Fakturadatum", "Förfallodatum", "Leveransdatum", "2026-09-30", "Faktureras till",
                     "Org.nr 556000-0001", "Momsreg.nr SE556000000101", "1 062,50", "212,50", "Att betala (SEK)", "Faktura 7 · Sida 1 av 1"):
        assert expected in text, expected
    for english in ("Invoice no.", "Billed to", "Gross total", "1 062.50"):
        assert english not in text, english


def test_the_payment_block_prints_what_is_stored_with_the_invoice_number_as_reference():
    text = pdf_text(render_pdf(document_from_invoice(_swedish())))

    for expected in ("Betalning", "Bankgiro", "123-4567", "IBAN", "SE4550000000058398257466", "BIC", "ESLSSESS", "Betalningsreferens",
                     "Betalningsvillkor", "30 dagar"):
        assert expected in text, expected
    assert "Plusgiro" not in text  # not stored: not printed


def test_f_tax_and_the_seller_contact_are_on_every_page():
    many = stored(issuer_snapshot=SCHEMA_2)
    many["lines"] = [dict(many["lines"][0], position=index, description=f"Horse massage {index}") for index in range(1, 80)]
    pages = [page for page in pdf_text(render_pdf(document_from_invoice(many))).split("Sida ") if page]

    assert len(pages) > 1
    text = pdf_text(render_pdf(document_from_invoice(many)))
    assert text.count("Godkänd för F-skatt") == text.count("Faktura 7 · Sida")  # once per page, in the footer
    for expected in ("Telefon 090-123456", "E-post info@example.test", "https://example.test", "Bankgiro 123-4567"):
        assert text.count(expected) >= text.count("Faktura 7 · Sida"), expected  # in every page's footer


def test_no_f_tax_statement_unless_the_seller_said_yes():
    for value in (False, None):
        assert "F-skatt" not in pdf_text(render_pdf(document_from_invoice(_swedish(approved_for_f_tax=value))))


def test_discount_steps_and_service_details_are_printed_under_the_line():
    data = _swedish(document_language="en")
    data["lines"][0].update(
        list_unit_price="1000.00", catalog_discount_percent="20.00", customer_discount_percent="10.00", unit_price_ex_vat="720.00",
        service=dict(schema=1, performed_at="2026-10-03T12:00:00Z", performed_at_local="2026-10-03 14:00", performed_by="Tina Therapist",
                     subject_type="horse", subject_label="Kalle", notes="Stiff left shoulder"),
    )
    document = document_from_invoice(data)

    assert document.lines[0].notes == (
        "List price 1 000.00 −20 % campaign −10 % customer discount",
        "Service for Kalle · 2026-10-03 14:00 · by Tina Therapist",
        "Stiff left shoulder",
    )
    text = pdf_text(render_pdf(document))
    assert "Service for Kalle" in text and "Stiff left shoulder" in text and "−20 % campaign" in text


def test_a_schema_1_snapshot_prints_in_english_with_only_the_reference_to_pay_by():
    document = document_from_invoice(stored())  # the fixture's issuer snapshot has no schema-2 fields

    assert document.language == "en" and document.payment is None and document.approved_for_f_tax is False
    text = pdf_text(render_pdf(document))
    assert "Invoice no." in text and "F-tax" not in text
    # The payment section is on every invoice, with only what is stored: the reference, nothing invented.
    assert "Payment reference" in text and "Bankgiro" not in text and "IBAN" not in text


def test_nothing_prints_n_a_or_an_empty_label():
    text = pdf_text(render_pdf(document_from_invoice(_swedish(email=None, phone=None, website=None, bic=None, payment_terms_days=None))))
    assert "N/A" not in text and "None" not in text
    assert "Betalningsvillkor" not in text and "BIC" not in text


def test_a_line_discount_is_printed_as_the_last_step():
    data = _swedish()
    data["lines"][0].update(list_unit_price="500.00", line_discount_percent="20.00", unit_price_ex_vat="400.00")
    assert document_from_invoice(data).lines[0].notes == ("Listpris 500,00 −20 % rabatt",)


def test_the_sender_is_labelled_and_the_footer_has_the_standard_columns():
    document = document_from_invoice(_swedish())
    assert document.issuer_footer == (
        ("Fredrik Horse Therapy AB", "Storgatan 1", "903 26 Umeå", "SE"),
        ("Telefon 090-123456", "E-post info@example.test", "https://example.test"),
        ("Org.nr 556000-0001", "Momsreg.nr SE556000000101", "Godkänd för F-skatt"),
        ("Bankgiro 123-4567", "IBAN SE4550000000058398257466", "BIC ESLSSESS"),
    )
    text = pdf_text(render_pdf(document))
    assert "Från" in text and text.index("Från") < text.index("Fredrik Horse Therapy AB")


def test_a_footer_column_with_nothing_stored_is_left_out():
    document = document_from_invoice(stored())  # no contact or payment details stored
    assert [column[0] for column in document.issuer_footer] == ["Fredrik Horse Therapy AB", "Registration no. 556000-0001"]
