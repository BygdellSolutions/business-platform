"""The document a PDF is rendered from.

`PdfDocument` is a frozen, plain-data copy of an ISSUED invoice as it is stored: strings only, in
the form the invoicing tables hold them. The renderer receives nothing else: no database session, no
ORM object, no customer, organization, item, horse, transaction or custom-field record, so it
cannot look anything up and cannot print anything the invoice itself does not say.

Money, quantities and VAT rates are the STORED decimal strings ("850.00", "1.000", "25.00"). Nothing
in this package adds, multiplies or recomputes them; presentation formatting (grouping digits,
trimming zeros) is string manipulation in `format.py`.

Text is cleaned once, here (see `clean_text`), so the hash of the document (`source_sha256`) is the
hash of exactly what will be printed.
"""

import hashlib
import json
import unicodedata

from pydantic import BaseModel, ConfigDict

# Pre-render resource bounds, based on input size. They protect the service from a pathological
# invoice; they are not a business rule, and nothing here limits pages or truncates text: long
# legitimate text paginates.
MAX_LINES = 2_000
MAX_FIELD_ENTRIES = 20_000  # custom-field snapshot values across the whole document
MAX_TOTAL_TEXT_CHARS = 1_000_000  # all text of the document together


class PdfError(Exception):
    """Base of what the PDF layer refuses, with a machine-readable code."""

    code = "pdf_error"


class DocumentTooLarge(PdfError):
    code = "document_too_large"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PdfField(Frozen):
    """One stored custom-field value, as text: its stored label and the text it resolved to."""

    label: str
    text: str


class PdfParty(Frozen):
    name: str
    lines: tuple[str, ...]  # address and identifier lines, only those that are stored


class PdfLine(Frozen):
    position: str
    description: str
    unit: str
    quantity: str
    unit_price: str
    vat_rate: str
    net: str
    vat: str
    gross: str
    fields: tuple[PdfField, ...]
    # Printed under the description, already in the document's language: the discount steps and the service details
    # the invoice line stores (template 2).
    notes: tuple[str, ...] = ()


class PdfVatRow(Frozen):
    rate: str
    net: str
    vat: str


class PdfSource(Frozen):
    date: str  # the date the invoice recorded for this source transaction
    fields: tuple[PdfField, ...]


class PdfPayment(Frozen):
    """How to pay, as the issuer snapshot stores it (template 2). Only stored values; nothing is derived."""

    bankgiro: str | None = None
    plusgiro: str | None = None
    iban: str | None = None
    bic: str | None = None
    terms_days: str | None = None
    reference: str  # the invoice number


class PdfDocument(Frozen):
    number_text: str
    invoice_date: str
    due_date: str | None
    currency: str
    description: str | None
    issuer: PdfParty
    customer: PdfParty
    sources: tuple[PdfSource, ...]
    lines: tuple[PdfLine, ...]
    vat_rows: tuple[PdfVatRow, ...]
    net: str
    vat: str
    gross: str
    # Template 2. The defaults print exactly what template 1 printed.
    language: str = "en"
    delivery_dates: tuple[str, ...] = ()
    payment: PdfPayment | None = None
    approved_for_f_tax: bool = False
    # The seller's details for every page's footer, in four columns (company and address, contact, tax identifiers,
    # payment), each a tuple of lines already labelled in the document's language. Empty columns are left out.
    issuer_footer: tuple[tuple[str, ...], ...] = ()


# --- text -------------------------------------------------------------------------------------------------------------------


def clean_text(value: str) -> str:
    """Make stored text safe and printable, without changing what it says.

    * Unicode NFC (canonically equivalent text; composes e + combining accent into one letter, which
      the renderer can place correctly).
    * Line breaks (CR, LF, CRLF, U+2028/2029) become one `\\n`; tabs become spaces.
    * Other control characters, invisible format characters (zero-width, bidirectional overrides, the
      joiner), variation selectors and unpaired surrogates are removed: they print nothing, and the
      bidirectional ones can make text appear in a different order than it is stored.
    """
    out: list[str] = []
    text = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    for char in text:
        category = unicodedata.category(char)
        if char == "\n" or category in ("Zl", "Zp"):
            out.append("\n")
        elif char == "\t":
            out.append(" ")
        elif category in ("Cc", "Cf", "Cs"):
            continue
        elif "︀" <= char <= "️" or "\U000e0100" <= char <= "\U000e01ef":
            continue  # variation selectors
        else:
            out.append(char)
    return "".join(out)


# --- hashing --------------------------------------------------------------------------------------------------------------------


def canonical_json(document: PdfDocument) -> bytes:
    """The document as stable bytes: keys sorted, no insignificant whitespace, UTF-8."""
    return json.dumps(document.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def source_sha256(document: PdfDocument) -> str:
    return hashlib.sha256(canonical_json(document)).hexdigest()


# --- bounds -----------------------------------------------------------------------------------------------------------------------


def iter_strings(document: PdfDocument):
    yield from (document.number_text, document.invoice_date, document.currency, document.net, document.vat, document.gross)
    yield from (value for value in (document.due_date, document.description) if value is not None)
    yield from document.delivery_dates
    for column in document.issuer_footer:
        yield from column
    if document.payment is not None:
        payment = document.payment
        yield from (value for value in (payment.bankgiro, payment.plusgiro, payment.iban, payment.bic, payment.terms_days, payment.reference) if value is not None)
    for party in (document.issuer, document.customer):
        yield party.name
        yield from party.lines
    for source in document.sources:
        yield source.date
        for field in source.fields:
            yield field.label
            yield field.text
    for line in document.lines:
        yield from (line.position, line.description, line.unit, line.quantity, line.unit_price, line.vat_rate, line.net, line.vat, line.gross)
        yield from line.notes
        for field in line.fields:
            yield field.label
            yield field.text
    for row in document.vat_rows:
        yield from (row.rate, row.net, row.vat)


def check_bounds(document: PdfDocument) -> None:
    """Refuse a document whose INPUT is larger than the service will render. Cheap: no layout is done."""
    if len(document.lines) > MAX_LINES:
        raise DocumentTooLarge(f"This invoice has {len(document.lines)} lines; the PDF renderer accepts at most {MAX_LINES}.")
    entries = 0  # counting values and characters: sizes of the input, not figures
    for line in document.lines:
        entries += len(line.fields)
    for source in document.sources:
        entries += len(source.fields)
    if entries > MAX_FIELD_ENTRIES:
        raise DocumentTooLarge(f"This invoice has {entries} custom-field values; the PDF renderer accepts at most {MAX_FIELD_ENTRIES}.")
    characters = 0
    for text in iter_strings(document):
        characters += len(text)
    if characters > MAX_TOTAL_TEXT_CHARS:
        raise DocumentTooLarge(f"This invoice holds {characters} characters of text; the PDF renderer accepts at most {MAX_TOTAL_TEXT_CHARS}.")
