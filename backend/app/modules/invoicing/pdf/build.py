"""Build a PdfDocument from the stored invoice document (the Invoicing API's own read model).

The input is `InvoiceRead` (what GET /api/invoices/{id} returns, read from the invoicing tables only).
Its JSON form is used, so every amount is exactly the string the API shows. Nothing here queries
anything or touches a live record; the navigation ids on the read model (customer, source
transaction and line ids, the issuing user, custom-field definition ids) are left out on purpose, so
nothing downstream could look them up.
"""

from typing import Any

from app.modules.invoicing.pdf.document import PdfDocument, PdfField, PdfLine, PdfParty, PdfPayment, PdfSource, PdfVatRow, clean_text
from app.modules.invoicing.pdf.format import money, trimmed
from app.modules.invoicing.pdf.labels import decimal_separator, labels

GONE = "(no longer existed)"
YES, NO = "Yes", "No"


def _text(value: Any) -> str:
    return clean_text("" if value is None else str(value))


def field_text(entry: dict[str, Any]) -> str:
    """The text of one stored custom-field value. Generic: it depends on the field's TYPE only."""
    kind = entry.get("field_type")
    if kind == "boolean":
        return YES if entry.get("value") is True else NO if entry.get("value") is False else ""
    if kind in ("select", "reference"):
        return GONE if entry.get("missing") else _text(entry.get("display"))
    display = entry.get("display")
    return _text(display if display is not None else entry.get("value"))


def _fields(entries: list[dict[str, Any]]) -> tuple[PdfField, ...]:
    return tuple(PdfField(label=_text(entry.get("label")), text=field_text(entry)) for entry in entries)


def _party(snapshot: dict[str, Any], *, prefer_legal_name: bool, words: dict[str, str]) -> PdfParty:
    name = snapshot.get("legal_name") if prefer_legal_name and snapshot.get("legal_name") else snapshot.get("name")
    place = " ".join(part for part in (snapshot.get("postal_code"), snapshot.get("city")) if part)
    lines = [snapshot.get("address_line1"), snapshot.get("address_line2"), place, snapshot.get("country_code")]
    if snapshot.get("registration_number"):
        lines.append(f"{words['registration']} {snapshot['registration_number']}")
    if snapshot.get("vat_number"):
        lines.append(f"{words['vat_no']} {snapshot['vat_number']}")
    lines += [snapshot.get("email"), snapshot.get("phone")]
    return PdfParty(name=_text(name), lines=tuple(cleaned for cleaned in (_text(line) for line in lines if line) if cleaned))


def _payment(issuer: dict[str, Any], number_text: str) -> PdfPayment | None:
    """The payment block, when the issuer snapshot (schema 2) stores a way to pay. Schema 1 never does."""
    ways = {key: issuer.get(key) for key in ("bankgiro", "plusgiro", "iban", "bic")}
    if not any(ways.values()):
        return None
    days = issuer.get("payment_terms_days")
    return PdfPayment(
        **{key: _text(value) if value else None for key, value in ways.items()},
        terms_days=_text(days) if days is not None else None,
        reference=_text(number_text),
    )


def _footer(issuer: dict[str, Any], words: dict[str, str]) -> tuple[tuple[str, ...], ...]:
    """The seller's details for every page, as a standard invoice footer: company and address, contact, tax
    identifiers, payment. Only stored values; a column with nothing stored is left out."""
    place = " ".join(part for part in (issuer.get("postal_code"), issuer.get("city")) if part)
    company = [issuer.get("legal_name") or issuer.get("name"), issuer.get("address_line1"), issuer.get("address_line2"), place, issuer.get("country_code")]
    contact = [
        f"{words['phone']} {issuer['phone']}" if issuer.get("phone") else None,
        f"{words['email']} {issuer['email']}" if issuer.get("email") else None,
        issuer.get("website"),
    ]
    tax = [
        f"{words['registration']} {issuer['registration_number']}" if issuer.get("registration_number") else None,
        f"{words['vat_no']} {issuer['vat_number']}" if issuer.get("vat_number") else None,
        words["f_tax"] if issuer.get("approved_for_f_tax") is True else None,
    ]
    payment = [f"{words[key]} {issuer[key]}" for key in ("bankgiro", "plusgiro", "iban", "bic") if issuer.get(key)]
    columns = []
    for column in (company, contact, tax, payment):
        lines = tuple(cleaned for cleaned in (_text(line) for line in column if line) if cleaned)
        if lines:
            columns.append(lines)
    return tuple(columns)


def _notes(line: dict[str, Any], words: dict[str, str], separator: str) -> tuple[str, ...]:
    """What the invoice line stores beyond its figures: the discount steps and the service, in the document's words."""
    notes: list[str] = []
    if line.get("list_unit_price") is not None:
        steps = [f"{words['list_price']} {money(str(line['list_unit_price']), separator)}"]
        if line.get("catalog_discount_percent") is not None:
            steps.append(f"−{trimmed(str(line['catalog_discount_percent']), separator)} % {words['catalog_discount']}")
        if line.get("customer_discount_percent") is not None:
            steps.append(f"−{trimmed(str(line['customer_discount_percent']), separator)} % {words['customer_discount']}")
        if line.get("line_discount_percent") is not None:
            steps.append(f"−{trimmed(str(line['line_discount_percent']), separator)} % {words['line_discount']}")
        if len(steps) > 1:
            notes.append(" ".join(steps))
    service = line.get("service")
    if service:
        subject = service.get("subject_label") or words["service_gone"]
        parts = [words["service_for"].format(subject=subject), service.get("performed_at_local")]
        if service.get("performed_by"):
            parts.append(words["by"].format(name=service["performed_by"]))
        notes.append(" · ".join(_text(part) for part in parts if part))
        if service.get("notes"):
            notes.append(_text(service["notes"]))
    return tuple(_text(note) for note in notes)


def document_from_invoice(invoice: Any) -> PdfDocument:
    """`invoice` is an InvoiceRead (or anything with the same JSON form)."""
    data = invoice.model_dump(mode="json") if hasattr(invoice, "model_dump") else invoice
    if data["status"] != "issued" or data.get("number_text") is None:
        raise ValueError("only an issued invoice has a PDF document")
    issuer = data["issuer_snapshot"]
    language = issuer.get("document_language") or "en"
    words = labels(language)
    separator = decimal_separator(language)
    return PdfDocument(
        language=language if language in ("en", "sv") else "en",
        delivery_dates=tuple(sorted({_text(source["transaction_date"]) for source in data["transactions"]})),
        payment=_payment(issuer, data["number_text"]),
        approved_for_f_tax=issuer.get("approved_for_f_tax") is True,
        issuer_footer=_footer(issuer, words),
        number_text=_text(data["number_text"]),
        invoice_date=_text(data["invoice_date"]),
        due_date=_text(data["due_date"]) if data.get("due_date") else None,
        currency=_text(data["currency"]),
        description=_text(data["description"]) if data.get("description") else None,
        issuer=_party(issuer, prefer_legal_name=True, words=words).model_copy(
            update={"reference": _text(f"{words['our_reference']}: {issuer['our_reference']}") if issuer.get("our_reference") else None}
        ),
        customer=_party(data["customer_snapshot"], prefer_legal_name=False, words=words),
        sources=tuple(PdfSource(date=_text(source["transaction_date"]), fields=_fields(source["fields"])) for source in data["transactions"]),
        lines=tuple(
            PdfLine(
                position=_text(line["position"]),
                description=_text(line["description"]),
                unit=_text(line["unit"]),
                quantity=_text(line["quantity"]),
                unit_price=_text(line["unit_price_ex_vat"]),
                vat_rate=_text(line["vat_rate"]),
                net=_text(line["net_amount"]),
                vat=_text(line["vat_amount"]),
                gross=_text(line["gross_amount"]),
                fields=_fields(line["fields"]),
                notes=_notes(line, words, separator),
            )
            for line in data["lines"]
        ),
        vat_rows=tuple(PdfVatRow(rate=_text(row["vat_rate"]), net=_text(row["net_amount"]), vat=_text(row["vat_amount"])) for row in data["vat_breakdown"]),
        net=_text(data["net_amount"]),
        vat=_text(data["vat_amount"]),
        gross=_text(data["gross_amount"]),
    )
