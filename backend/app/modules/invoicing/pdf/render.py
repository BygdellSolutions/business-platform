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

TEMPLATE_VERSION = 1
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
    "title": ("bold", 20, 24, 2, colors.black),
    "issuer": ("bold", 12, 15, 0, colors.black),
    "cell": ("regular", 8, 10, 0, colors.black),
    "cell_right": ("regular", 8, 10, 2, colors.black),
    "cell_head": ("bold", 8, 10, 0, colors.black),
    "cell_head_right": ("bold", 8, 10, 2, colors.black),
    "cell_note": ("italic", 7, 9, 0, colors.HexColor("#444444")),
    "total_label": ("bold", 9, 12, 0, colors.black),
    "total_value": ("bold", 9, 12, 2, colors.black),
    "foot": ("regular", 7, 9, 1, colors.HexColor("#555555")),
}
# Figures are laid out without CJK wrapping: ReportLab's CJK mode treats a no-break space as a break opportunity,
# which would split a grouped amount ("1 062.50") between its digit groups.
NUMERIC_KINDS = {"cell_right", "cell_head_right", "total_value"}
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

    def footer(page: canvas.Canvas, number: int, total: int) -> None:
        text = printer.p(f"Invoice {document.number_text} · Page {number} of {total}", "foot")
        text.wrap(width, 20 * mm)
        text.drawOn(page, margin, 10 * mm)

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=20 * mm,
        invariant=1,
        pageCompression=1,
        title=f"Invoice {document.number_text}",
        author=document.issuer.name,
        subject=f"Invoice {document.number_text} for {document.customer.name}",
        creator="business-platform",
    )

    story: list = []

    # Header: the issuer on the left, the invoice title and details on the right.
    details = [("Invoice no.", document.number_text), ("Invoice date", document.invoice_date)]
    if document.due_date is not None:
        details.append(("Due date", document.due_date))
    details.append(("Currency", document.currency))
    detail_table = Table([[printer.p(label, "label"), printer.p(value, "body")] for label, value in details], colWidths=[24 * mm, 36 * mm], hAlign="RIGHT")
    detail_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    header = Table([[_party_flowables(printer, document.issuer, "issuer"), [printer.p("Invoice", "title"), Spacer(1, 4), detail_table]]], colWidths=[width * 0.52, width * 0.48])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [header, Spacer(1, 10 * mm)]

    story += [printer.p("Billed to", "label"), *_party_flowables(printer, document.customer, "bold"), Spacer(1, 6 * mm)]

    if document.description:
        story += [printer.p("Description", "label"), printer.p(document.description), Spacer(1, 5 * mm)]

    # Reference information: the custom-field values stored per source transaction (generic label and text).
    with_fields = [source for source in document.sources if source.fields]
    if with_fields:
        story.append(printer.p("Reference information", "label"))
        for source in with_fields:
            if len(document.sources) > 1:
                story.append(printer.p(f"Transaction of {source.date}", "bold"))
            story += _fields(printer, source.fields, "body")
        story.append(Spacer(1, 5 * mm))

    # Lines. Fixed minimum widths for the figure columns; each grows to fit its widest printed figure and the
    # description takes what is left (never less than MIN_DESCRIPTION_WIDTH: beyond that a figure wraps by character).
    heads = [("Description", "cell_head"), ("Qty", "cell_head_right"), ("Unit", "cell_head"), ("Unit price", "cell_head_right"), ("VAT %", "cell_head_right"), ("Net", "cell_head_right"), ("VAT", "cell_head_right"), ("Gross", "cell_head_right")]
    shown = [
        [trimmed(line.quantity), line.unit, money(line.unit_price), trimmed(line.vat_rate), money(line.net), money(line.vat), money(line.gross)]
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
                [printer.p(line.description, "cell"), *_fields(printer, line.fields, "cell_note")],
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
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
                    ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#bbbbbb")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(table)
    else:
        story.append(printer.p("This invoice has no lines."))
    story.append(Spacer(1, 6 * mm))

    # The stored VAT breakdown, then the stored totals: once, after the last line.
    closing: list = []
    if document.vat_rows:
        breakdown = [[printer.p("VAT rate (%)", "cell_head_right"), printer.p("Net", "cell_head_right"), printer.p("VAT", "cell_head_right")]]
        breakdown += [[printer.p(trimmed(row.rate), "cell_right"), printer.p(money(row.net), "cell_right"), printer.p(money(row.vat), "cell_right")] for row in document.vat_rows]
        vat_widths = [
            _column_width([trimmed(row.rate) for row in document.vat_rows], "cell_right", 60),
            _column_width([money(row.net) for row in document.vat_rows], "cell_right", 80),
            _column_width([money(row.vat) for row in document.vat_rows], "cell_right", 70),
        ]
        vat_table = Table(breakdown, colWidths=vat_widths, hAlign="RIGHT")
        vat_table.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
        closing += [vat_table, Spacer(1, 4 * mm)]
    totals = Table(
        [
            [printer.p(f"Net total ({document.currency})", "total_label"), printer.p(money(document.net), "total_value")],
            [printer.p(f"VAT total ({document.currency})", "total_label"), printer.p(money(document.vat), "total_value")],
            [printer.p(f"Gross total ({document.currency})", "total_label"), printer.p(money(document.gross), "total_value")],
        ],
        colWidths=[90, _column_width([money(document.net), money(document.vat), money(document.gross)], "total_value", 80)],
        hAlign="RIGHT",
    )
    totals.setStyle(TableStyle([("LINEABOVE", (0, 2), (-1, 2), 0.8, colors.black), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    closing.append(totals)
    story.append(KeepTogether(closing))

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
