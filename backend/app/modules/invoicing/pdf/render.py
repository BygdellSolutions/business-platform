"""Draw a PdfDocument as a PDF. A presentation layer, nothing more.

Input is a `PdfDocument` (strings). The renderer has no database session and imports no domain code:
it cannot look anything up. It performs NO financial calculation: every amount printed is a stored
string passed through `format` (digit grouping, trailing-zero trimming), never a number.

Hostile text. Every stored string is run through `_markup` before ReportLab's Paragraph parses it:
the font runs are chosen per character, the text is escaped (`&`, `<`, `>`), and only markup this
module generates (a `<font name="...">` with a bundled font name and `<br/>`) reaches the parser. The
document contains no links, JavaScript, forms, attachments or external references, and ReportLab is
configured to open no URL or file named by text.

Output is deterministic for a given document, template version and library version (invariant mode: fixed
dates and ids, bundled fonts only); the service stores the first rendering and serves those exact
bytes forever, so a later library or template change never alters an old invoice.
"""

import hashlib
import io
import threading
from xml.sax.saxutils import escape

import reportlab
from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from reportlab.platypus import KeepTogether, LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.modules.invoicing.pdf import fonts as font_layer
from app.modules.invoicing.pdf.document import DocumentTooLarge, PdfDocument, PdfField, PdfParty, check_bounds, iter_strings
from app.modules.invoicing.pdf.format import money, trimmed
from app.modules.invoicing.pdf.labels import decimal_separator, labels

# 2: the document's language, payment details, F-tax, delivery dates, the seller's identifiers on every page, and the
#    discount steps and service details under a line.
# 3: one layout for every language (accent header, the customer beside an "amount due" box, a coloured table head,
#    the amount due emphasised, a payment section on every invoice). What is not stored is left out, never invented.
TEMPLATE_VERSION = 3
ACCENT = colors.HexColor("#1f4e5f")
ACCENT_LIGHT = colors.HexColor("#e8f0f2")
MIN_DESCRIPTION_WIDTH = 90  # points
MAX_OUTPUT_BYTES = 64 * 1024 * 1024  # defensive: a rendering that large is a bug, not an invoice

# No URL and no file is ever fetched because of what a stored text says.
for _setting in ("trustedHosts", "trustedSchemes"):
    if hasattr(rl_config, _setting):
        setattr(rl_config, _setting, [])

_RENDER_SLOTS = threading.BoundedSemaphore(2)  # at most two renderings at once


def renderer_identity() -> str:
    """Which renderer made an artifact: library version and the exact fonts (the pinned checksum file's hash)."""
    fonts = hashlib.sha256(font_layer.CHECKSUMS.read_bytes()).hexdigest()[:12]
    return f"reportlab {reportlab.Version}; bundled Noto fonts {fonts}"


# --- text ------------------------------------------------------------------------------------------------------------------------

# kind -> (font style, size, leading, alignment (0 left, 1 centre, 2 right), colour)
KINDS = {
    "body": ("regular", 9, 12, 0, colors.black),
    "bold": ("bold", 9, 12, 0, colors.black),
    "label": ("bold", 8, 11, 0, colors.HexColor("#555555")),
    "title": ("bold", 22, 26, 2, colors.HexColor("#1f4e5f")),
    "box_label": ("bold", 8, 11, 0, colors.HexColor("#1f4e5f")),
    "due_label": ("bold", 8, 11, 2, colors.HexColor("#1f4e5f")),
    "due_value": ("bold", 16, 20, 2, colors.black),
    "due_note": ("regular", 8, 11, 2, colors.HexColor("#444444")),
    "issuer": ("bold", 12, 15, 0, colors.black),
    "cell": ("regular", 8, 10, 0, colors.black),
    "cell_right": ("regular", 8, 10, 2, colors.black),
    "cell_head": ("bold", 8, 10, 0, colors.white),
    "cell_head_right": ("bold", 8, 10, 2, colors.white),
    "cell_note": ("italic", 7, 9, 0, colors.HexColor("#444444")),
    "total_label": ("bold", 9, 12, 0, colors.black),
    "total_value": ("bold", 9, 12, 2, colors.black),
    "foot": ("regular", 7, 9, 1, colors.HexColor("#555555")),
}
# Figures are laid out without CJK wrapping: ReportLab's CJK mode treats a no-break space as a break opportunity,
# which would split a grouped amount ("1 062.50") between its digit groups.
NUMERIC_KINDS = {"cell_right", "cell_head_right", "total_value", "due_value"}
_STYLES: dict[str, ParagraphStyle] = {}


def _style(kind: str) -> ParagraphStyle:
    if kind not in _STYLES:
        face, size, leading, align, color = KINDS[kind]
        _STYLES[kind] = ParagraphStyle(kind, fontName=font_layer.STYLES[face][0], fontSize=size, leading=leading, alignment=align, textColor=color, wordWrap=None if kind in NUMERIC_KINDS else "CJK", splitLongWords=1)
    return _STYLES[kind]


def _markup(text: str, kind: str, fonts: font_layer.Registered) -> str:
    """Escaped Paragraph markup for `text`, with a font run per character where the primary font lacks it."""
    face = KINDS[kind][0]
    primary = font_layer.STYLES[face][0]
    parts: list[str] = []
    for font, run in font_layer.runs(text, face, fonts):
        body = escape(run).replace("\n", "<br/>")
        parts.append(body if font == primary else f'<font name="{font}">{body}</font>')
    return "".join(parts)


class _Printer:
    def __init__(self, fonts: font_layer.Registered):
        self.fonts = fonts

    def p(self, text: str, kind: str = "body") -> Paragraph:
        return Paragraph(_markup(text, kind, self.fonts), _style(kind))


def _refuse_unsupported(document: PdfDocument, fonts: font_layer.Registered) -> None:
    """Collect EVERY character that cannot be drawn correctly, so the refusal names them all."""
    problems: dict[str, str] = {}
    for text in iter_strings(document):
        font_layer.runs(text, "regular", fonts, found=problems)
    if problems:
        raise font_layer.UnsupportedCharacters(problems)


# --- layout ------------------------------------------------------------------------------------------------------------------------


def _column_width(texts: list[str], kind: str, minimum: float) -> float:
    """Wide enough for the widest printed figure, so an amount is never split across lines (layout measuring only)."""
    face, size = KINDS[kind][0], KINDS[kind][1]
    font = font_layer.STYLES[face][0]
    widest = max((stringWidth(text, font, size) for text in texts), default=0)
    return max(minimum, widest + 8)


def _used(widths: list[float]) -> float:
    used = 0.0
    for each in widths:
        used += each
    return used


def _party_flowables(printer: _Printer, party: PdfParty, name_kind: str) -> list:
    return [printer.p(party.name, name_kind), *[printer.p(line) for line in party.lines]]


def _fields(printer: _Printer, fields: tuple[PdfField, ...], kind: str) -> list[Paragraph]:
    return [printer.p(f"{field.label}: {field.text}", kind) for field in fields]


def _numbered_canvas(footer):
    class Numbered(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages: list[dict] = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                footer(self, self._pageNumber, total)
                super().showPage()
            super().save()

    return Numbered


def _build(document: PdfDocument, fonts: font_layer.Registered) -> bytes:
    printer = _Printer(fonts)
    margin = 18 * mm
    width = A4[0] - 2 * margin
    buffer = io.BytesIO()

    words = labels(document.language)
    sep = decimal_separator(document.language)

    def footer(page: canvas.Canvas, number: int, total: int) -> None:
        lines = [*document.issuer_footer, words["page"].format(number=document.number_text, page=number, total=total)]
        text = printer.p("\n".join(lines), "foot")
        text.wrap(width, 20 * mm)
        text.drawOn(page, margin, 8 * mm)

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=(20 + 4 * len(document.issuer_footer)) * mm,
        invariant=1,
        pageCompression=1,
        title=f"{words['invoice']} {document.number_text}",
        author=document.issuer.name,
        subject=f"{words['invoice']} {document.number_text} · {document.customer.name}",
        creator="business-platform",
    )

    story: list = []

    # Header: the issuer on the left, the invoice title and details on the right.
    details = [(words["invoice_no"], document.number_text), (words["invoice_date"], document.invoice_date)]
    if document.due_date is not None:
        details.append((words["due_date"], document.due_date))
    if document.delivery_dates:
        details.append((words["delivery_date"], ", ".join(document.delivery_dates)))
    details.append((words["currency"], document.currency))
    detail_table = Table([[printer.p(label, "label"), printer.p(value, "body")] for label, value in details], colWidths=[28 * mm, 36 * mm], hAlign="RIGHT")
    detail_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    header = Table([[_party_flowables(printer, document.issuer, "issuer"), [printer.p(words["invoice"], "title"), Spacer(1, 4), detail_table]]], colWidths=[width * 0.52, width * 0.48])
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LINEBELOW", (0, 0), (-1, 0), 1.5, ACCENT),
            ]
        )
    )
    story += [header, Spacer(1, 7 * mm)]

    # The customer, and beside it what is to be paid and by when (always: the stored total and due date).
    due = [printer.p(words["amount_due"], "due_label"), printer.p(f"{money(document.gross, sep)} {document.currency}", "due_value")]
    if document.due_date is not None:
        due.append(printer.p(f"{words['due_date']} {document.due_date}", "due_note"))
    due.append(printer.p(f"{words['reference']} {document.number_text}", "due_note"))
    parties = Table(
        [[[printer.p(words["billed_to"], "box_label"), Spacer(1, 2), *_party_flowables(printer, document.customer, "bold")], due]],
        colWidths=[width * 0.58, width * 0.42],
    )
    parties.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (1, 0), (1, 0), ACCENT_LIGHT),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("LEFTPADDING", (1, 0), (1, 0), 8),
                ("RIGHTPADDING", (1, 0), (1, 0), 8),
                ("TOPPADDING", (1, 0), (1, 0), 6),
                ("BOTTOMPADDING", (1, 0), (1, 0), 6),
            ]
        )
    )
    story += [parties, Spacer(1, 7 * mm)]

    if document.description:
        story += [printer.p(words["description"], "label"), printer.p(document.description), Spacer(1, 5 * mm)]

    # Reference information: the custom-field values stored per source transaction (generic label and text).
    with_fields = [source for source in document.sources if source.fields]
    if with_fields:
        story.append(printer.p(words["reference_information"], "label"))
        for source in with_fields:
            if len(document.sources) > 1:
                story.append(printer.p(words["transaction_of"].format(date=source.date), "bold"))
            story += _fields(printer, source.fields, "body")
        story.append(Spacer(1, 5 * mm))

    # Lines. Fixed minimum widths for the figure columns; each grows to fit its widest printed figure and the
    # description takes what is left (never less than MIN_DESCRIPTION_WIDTH: beyond that a figure wraps by character).
    heads = [
        (words["description"], "cell_head"),
        (words["qty"], "cell_head_right"),
        (words["unit"], "cell_head"),
        (words["unit_price"], "cell_head_right"),
        (words["vat_pct"], "cell_head_right"),
        (words["net"], "cell_head_right"),
        (words["vat"], "cell_head_right"),
        (words["gross"], "cell_head_right"),
    ]
    shown = [
        [trimmed(line.quantity, sep), line.unit, money(line.unit_price, sep), trimmed(line.vat_rate, sep), money(line.net, sep), money(line.vat, sep), money(line.gross, sep)]
        for line in document.lines
    ]
    minimums = [30, 40, 52, 34, 54, 46, 58]
    kinds = ["cell_right", "cell", "cell_right", "cell_right", "cell_right", "cell_right", "cell_right"]
    fixed = [
        _column_width([heads[i + 1][0], *[row[i] for row in shown]], kinds[i], minimums[i]) if kinds[i] == "cell_right" else minimums[i]
        for i in range(7)
    ]
    # The widest valid figures (a 12-digit line amount, three of them, plus price and quantity) do not fit beside a
    # readable description: shrink the figure columns together so the table always stays on the page. A figure that
    # is then wider than its column wraps by character (splitLongWords) instead of running off the page.
    room = width - MIN_DESCRIPTION_WIDTH
    if _used(fixed) > room:
        shrink = room / _used(fixed)
        fixed = [each * shrink for each in fixed]
    columns = [width - _used(fixed), *fixed]
    rows = [[printer.p(label, kind) for label, kind in heads]]
    for line, cells in zip(document.lines, shown):
        rows.append(
            [
                [printer.p(line.description, "cell"), *[printer.p(note, "cell_note") for note in line.notes], *_fields(printer, line.fields, "cell_note")],
                printer.p(cells[0], "cell_right"),
                printer.p(cells[1], "cell"),
                printer.p(cells[2], "cell_right"),
                printer.p(cells[3], "cell_right"),
                printer.p(cells[4], "cell_right"),
                printer.p(cells[5], "cell_right"),
                printer.p(cells[6], "cell_right"),
            ]
        )
    if document.lines:
        table = LongTable(rows, colWidths=columns, repeatRows=1, splitInRow=1)
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
                    ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#bbbbbb")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(table)
    else:
        story.append(printer.p(words["no_lines"]))
    story.append(Spacer(1, 6 * mm))

    # The stored VAT breakdown, then the stored totals: once, after the last line.
    closing: list = []
    if document.vat_rows:
        breakdown = [[printer.p(words["vat_rate_pct"], "cell_head_right"), printer.p(words["net"], "cell_head_right"), printer.p(words["vat"], "cell_head_right")]]
        breakdown += [[printer.p(trimmed(row.rate, sep), "cell_right"), printer.p(money(row.net, sep), "cell_right"), printer.p(money(row.vat, sep), "cell_right")] for row in document.vat_rows]
        vat_widths = [
            _column_width([trimmed(row.rate, sep) for row in document.vat_rows], "cell_right", 60),
            _column_width([money(row.net, sep) for row in document.vat_rows], "cell_right", 80),
            _column_width([money(row.vat, sep) for row in document.vat_rows], "cell_right", 70),
        ]
        vat_table = Table(breakdown, colWidths=vat_widths, hAlign="RIGHT")
        vat_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), ACCENT), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
        closing += [vat_table, Spacer(1, 4 * mm)]
    totals = Table(
        [
            [printer.p(words["net_total"].format(currency=document.currency), "total_label"), printer.p(money(document.net, sep), "total_value")],
            [printer.p(words["vat_total"].format(currency=document.currency), "total_label"), printer.p(money(document.vat, sep), "total_value")],
            [printer.p(words["gross_total"].format(currency=document.currency), "total_label"), printer.p(money(document.gross, sep), "total_value")],
        ],
        colWidths=[110, _column_width([money(document.net, sep), money(document.vat, sep), money(document.gross, sep)], "total_value", 80)],
        hAlign="RIGHT",
    )
    totals.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 2), (-1, 2), 1.2, ACCENT),
                ("BACKGROUND", (0, 2), (-1, 2), ACCENT_LIGHT),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    closing.append(totals)
    story.append(KeepTogether(closing))

    # How to pay, on every invoice: the reference (the invoice number) and due date always, then whatever the issuer
    # snapshot stores (bankgiro, plusgiro, IBAN, BIC, terms). Nothing that is not stored is printed.
    payment = document.payment
    rows: list[tuple[str, str]] = [(words["reference"], document.number_text)]
    if document.due_date is not None:
        rows.append((words["due_date"], document.due_date))
    if payment is not None:
        rows += [(words[key], value) for key, value in (("bankgiro", payment.bankgiro), ("plusgiro", payment.plusgiro), ("iban", payment.iban), ("bic", payment.bic)) if value]
        if payment.terms_days is not None:
            rows.append((words["terms"], words["terms_days"].format(days=payment.terms_days)))
    pay_table = Table([[printer.p(label, "label"), printer.p(value, "body")] for label, value in rows], colWidths=[36 * mm, width - 36 * mm - 16])
    pay_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (-1, -1), 0.6, ACCENT),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.append(KeepTogether([Spacer(1, 8 * mm), printer.p(words["payment"], "box_label"), Spacer(1, 2), pay_table]))

    doc.build(story, canvasmaker=_numbered_canvas(footer))
    data = buffer.getvalue()
    if len(data) > MAX_OUTPUT_BYTES:
        raise DocumentTooLarge("The rendered PDF would be larger than the service allows.")
    return data


def render_pdf(document: PdfDocument) -> bytes:
    """The PDF of `document`. Raises DocumentTooLarge or UnsupportedCharacters; never returns a document with a missing-glyph box."""
    check_bounds(document)
    fonts = font_layer.load()
    _refuse_unsupported(document, fonts)
    with _RENDER_SLOTS:
        return _build(document, fonts)
