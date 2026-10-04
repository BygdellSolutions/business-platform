"""Helpers for the invoice PDF tests: build documents, read a PDF back as text."""

import io
import re

from pypdf import PdfReader

from app.modules.invoicing.pdf.document import PdfDocument, PdfField, PdfLine, PdfParty, PdfSource, PdfVatRow
from app.modules.invoicing.pdf.format import NBSP


def line(index: int = 1, **overrides) -> PdfLine:
    values = dict(
        position=str(index),
        description=f"Horse massage {index}",
        unit="session",
        quantity="1.000",
        unit_price="850.00",
        vat_rate="25.00",
        net="850.00",
        vat="212.50",
        gross="1062.50",
        fields=(),
    )
    values.update(overrides)
    return PdfLine(**values)


def document(**overrides) -> PdfDocument:
    values = dict(
        number_text="1",
        invoice_date="2026-10-01",
        due_date="2026-10-31",
        currency="SEK",
        description=None,
        issuer=PdfParty(name="Fredrik Horse Therapy AB", lines=("Storgatan 1", "903 26 Umeå", "SE", "Registration no. 556000-0001", "VAT no. SE556000000101")),
        customer=PdfParty(name="Umeå HK", lines=("Ridvägen 2", "903 30 Umeå", "SE")),
        sources=(PdfSource(date="2026-09-30", fields=()),),
        lines=(line(1),),
        vat_rows=(PdfVatRow(rate="25.00", net="850.00", vat="212.50"),),
        net="850.00",
        vat="212.50",
        gross="1062.50",
    )
    values.update(overrides)
    return PdfDocument(**values)


def reader(data: bytes) -> PdfReader:
    return PdfReader(io.BytesIO(data))


def page_texts(data: bytes) -> list[str]:
    return [(page.extract_text() or "").replace(NBSP, " ") for page in reader(data).pages]


def pdf_text(data: bytes) -> str:
    return "\n".join(page_texts(data))


def squeezed(text: str) -> str:
    """Text without any whitespace, so a wrapped string can be searched for as one run."""
    return "".join(text.split())


def font_names(data: bytes) -> set[str]:
    names: set[str] = set()
    for page in reader(data).pages:
        resources = page.get("/Resources") or {}
        for font in (resources.get("/Font") or {}).values():
            names.add(str(font.get_object()["/BaseFont"]))
    return names


def field(label: str, text: str) -> PdfField:
    return PdfField(label=label, text=text)


FOOTER = re.compile(r"Invoice[^·]{1,40}·Page\d+of\d+")
TABLE_HEAD = "DescriptionQtyUnitUnitpriceVAT%NetVATGross"


def running_text(data: bytes) -> str:
    """The text of all pages as one whitespace-free run, without the per-page furniture (footer, repeated table
    header), so content that continues across a page break can be searched for as one string."""
    text = squeezed(pdf_text(data))
    return FOOTER.sub("", text).replace(TABLE_HEAD, "")


def drawn_fonts(data: bytes) -> list[tuple[str, bool]]:
    """(base font name, is it embedded) for every font that actually shows text on any page.

    ReportLab also names its default font (Helvetica) in each page's resources and sets it once without drawing
    anything; that is not a use of the font, so only fonts reaching a text-showing operator are listed."""
    from pypdf.generic import ContentStream

    used: dict[str, bool] = {}
    for page in reader(data).pages:
        fonts = (page.get("/Resources") or {}).get("/Font") or {}
        content = ContentStream(page.get_contents(), reader(data))
        current = None
        for operands, operator in content.operations:
            if operator == b"Tf":
                current = operands[0]
            elif operator in (b"Tj", b"TJ", b"'", b'"'):
                font = fonts[current].get_object()
                descriptor = font.get("/FontDescriptor")
                used[str(font["/BaseFont"])] = descriptor is not None and "/FontFile2" in descriptor.get_object()
    return sorted(used.items())


def fragments(data: bytes) -> list[tuple[int, str, float, float, float]]:
    """(page number, text, x, y, font size) of every text fragment, in points from the page's lower left corner."""
    out: list[tuple[int, str, float, float, float]] = []
    for number, page in enumerate(reader(data).pages, start=1):

        def visit(text, cm, tm, font, size, number=number):
            if text.strip():
                x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                out.append((number, text, x, y, float(size)))

        page.extract_text(visitor_text=visit)
    return out
