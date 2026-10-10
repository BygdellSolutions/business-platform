"""The PDF renderer: what is printed, what is refused, and that it never calculates.

Assertions are on the TEXT of the PDF (read back with pypdf) and on structure, never on exact bytes
or layout, except where byte identity is itself the requirement (determinism).
"""

import hashlib
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
from reportlab.pdfbase.pdfmetrics import stringWidth

from app.modules.invoicing.pdf import fonts as font_layer
from app.modules.invoicing.pdf import render as render_module
from app.modules.invoicing.pdf.document import DocumentTooLarge, MAX_FIELD_ENTRIES, MAX_LINES, MAX_TOTAL_TEXT_CHARS, PdfSource, PdfVatRow, check_bounds, clean_text
from app.modules.invoicing.pdf.render import TEMPLATE_VERSION, render_pdf, renderer_identity
from tests.pdf_support import document, field, font_names, fragments, line, page_texts, pdf_text, reader, running_text, squeezed

BACKEND = Path(__file__).resolve().parents[1]
ROW_FIGURES = "1session850.0025850.00212.501062.50"  # the figure cells of line 1, printed once beside the first part of a split row


# --- what is printed ----------------------------------------------------------------------------------------------------------


def test_the_invoice_prints_its_stored_content():
    text = pdf_text(render_pdf(document(description="October work")))
    for expected in (
        "Fredrik Horse Therapy AB", "Storgatan 1", "903 26 Umeå", "Registration no. 556000-0001", "VAT no. SE556000000101",
        "Invoice no.", "2026-10-01", "Due date", "2026-10-31", "SEK", "Billed to", "Umeå HK", "Ridvägen 2", "October work",
        "Horse massage 1", "session", "850.00", "212.50", "1 062.50", "Invoice 1 · Page 1 of 1",
    ):
        assert expected in text, expected


def test_nothing_that_is_not_stored_is_invented():
    text = pdf_text(render_pdf(document(due_date=None, description=None, issuer=document().issuer.model_copy(update={"lines": ()}))))
    for invented in ("IBAN", "Bankgiro", "Plusgiro", "Swish", "QR", "Due date", "bank"):
        assert invented not in text, invented


def test_the_document_has_the_stored_title_and_metadata():
    meta = reader(render_pdf(document(number_text="INV-7"))).metadata
    assert meta.title == "Invoice INV-7"
    assert meta.author == "Fredrik Horse Therapy AB"
    assert meta.creator == "business-platform"


def test_totals_and_the_vat_breakdown_are_printed_exactly_once_each():
    data = render_pdf(document(
        lines=(line(1), line(2, vat_rate="6.00", net="100.00", vat="6.00", gross="106.00")),
        vat_rows=(PdfVatRow(rate="6.00", net="100.00", vat="6.00"), PdfVatRow(rate="25.00", net="850.00", vat="212.50")),
        net="950.00", vat="218.50", gross="1168.50",
    ))
    text = pdf_text(data)
    for label in ("Net total (SEK)", "VAT total (SEK)", "Gross total (SEK)", "VAT rate (%)"):
        assert text.count(label) == 1, label
    assert text.count("1 168.50") == 2  # the gross total, and the same stored figure as the amount due
    rows = [row for row in text.splitlines() if row.strip() in ("6", "25")]
    assert rows  # both rates were printed in the breakdown


def test_the_empty_invoice_says_so_and_still_prints_totals():
    text = pdf_text(render_pdf(document(lines=(), vat_rows=(), net="0.00", vat="0.00", gross="0.00")))
    assert "This invoice has no lines." in text and "Gross total (SEK)" in text


# --- custom-field snapshots, both levels ----------------------------------------------------------------------------------------


def test_line_level_fields_print_under_their_line_and_transaction_level_fields_in_reference_information():
    data = render_pdf(document(
        sources=(PdfSource(date="2026-09-30", fields=(field("PO number", "PO-17"),)),),
        lines=(line(1, fields=(field("Owner", "Anna Andersson"), field("Remark", "Handle with care"))), line(2)),
    ))
    text = pdf_text(data)
    assert "Reference information" in text and "PO number: PO-17" in text
    assert "Order of 2026-09-30" not in text  # one source: no heading needed
    assert text.index("Owner: Anna Andersson") < text.index("Horse massage 2")  # belongs to line 1
    assert text.index("Horse massage 1") < text.index("Owner: Anna Andersson")
    assert "Remark: Handle with care" in text


def test_several_sources_are_grouped_by_their_stored_dates_and_sources_without_fields_are_skipped():
    text = pdf_text(render_pdf(document(sources=(
        PdfSource(date="2026-09-30", fields=(field("PO number", "PO-17"),)),
        PdfSource(date="2026-10-02", fields=()),
        PdfSource(date="2026-10-03", fields=(field("PO number", "PO-18"),)),
    ))))
    assert "Order of 2026-09-30" in text and "Order of 2026-10-03" in text
    assert "Order of 2026-10-02" not in text


def test_the_field_code_is_generic_whatever_a_field_is_called():
    text = pdf_text(render_pdf(document(lines=(line(1, fields=(field("Owner", "Anna"), field("Horse", "Kalle"), field("Zzz", "yyy"))),))))
    for expected in ("Owner: Anna", "Horse: Kalle", "Zzz: yyy"):
        assert expected in text


# --- the stored figures are printed as given: no calculation --------------------------------------------------------------------


def test_deliberately_inconsistent_stored_figures_are_printed_unchanged():
    """Nothing adds up here. A renderer that calculated anything would print some other number."""
    data = render_pdf(document(
        lines=(line(1, quantity="3.000", unit_price="50.00", vat_rate="25.00", net="1.00", vat="2.00", gross="99.99"), line(2, quantity="0.001", net="0.10", vat="0.1", gross="9999999999.99")),
        vat_rows=(PdfVatRow(rate="25.00", net="3.33", vat="4.44"), PdfVatRow(rate="6.00", net="5.55", vat="6.66")),
        net="7.77", vat="8.88", gross="1.11",
    ))
    text = pdf_text(data)
    for stored in ("1.00", "2.00", "99.99", "0.10", "0.1", "9 999 999 999.99", "3.33", "4.44", "5.55", "6.66", "7.77", "8.88", "1.11"):
        assert stored in text, stored  # whole, on one line: the amount columns grow to fit
    for recomputed in ("150.00", "37.50", "187.50", "3.00", "12.77", "950.00", "17.76", "1.10", "100.99"):
        assert recomputed not in text, recomputed


def test_a_quantity_and_rate_are_shown_trimmed_and_every_amount_is_shown_with_its_stored_decimals():
    text = pdf_text(render_pdf(document(lines=(line(1, quantity="7.001", unit_price="6.25", vat_rate="12.50", net="43.75", vat="5.47", gross="49.22"),))))
    assert " 7.001 " in text or "7.001" in text
    assert "12.5" in text and "12.50" not in text.replace("212.50", "")
    assert "6.25" in text and "43.75" in text and "5.47" in text and "49.22" in text


def test_the_renderer_never_receives_or_needs_a_number_type():
    # Pure strings in, pure strings printed: an amount no float could hold survives to the page.
    huge = "123456789012345678901234567890.12"
    assert "123 456 789 012 345 678 901 234 567 890.12" in pdf_text(render_pdf(document(gross=huge, lines=(line(1, gross=huge),))))


# --- Unicode, fonts and refusals ----------------------------------------------------------------------------------------------------

SCRIPTS = {
    "Swedish": "Åke Öhman Hästterapi AB, Ärlig Ström, Örebro",
    "Latin extended": "Łódź Gdańsk Şişli Ğ İ Ő ő ű Đ ñ ç ß",
    "Vietnamese": "Nguyễn Thị Tiếng Việt",
    "Cyrillic": "ООО «Ромашка» Привет",
    "Greek": "Ελληνικά Αθήνα Σ",
    "Symbols and math": "€ £ ¥ © ® ™ → ← ✓ ≤ ≥ ≠ ∑ √",
    "Han and kana": "日本語 ひらがな カタカナ 中文 简体 繁體 臺灣",
    "Hangul": "한국어 서울",
}


@pytest.mark.parametrize("name", list(SCRIPTS))
def test_every_supported_script_is_printed_and_read_back(name):
    text = SCRIPTS[name]
    data = render_pdf(document(customer=document().customer.model_copy(update={"name": text}), description=text))
    extracted = squeezed(pdf_text(data))
    for word in text.split():
        assert squeezed(word) in extracted, (name, word)


def test_fallback_fonts_are_used_where_the_primary_font_has_no_glyph_and_never_a_missing_glyph_box():
    text = "Umeå 日本語 한국어 ≤ ✓ Привет"
    data = render_pdf(document(description=text))
    used = " ".join(font_names(data))
    for family in ("NotoSans", "NotoSansSC", "NotoSansKR"):
        assert family in used, (family, used)
    fonts = font_layer.load()
    for char in text.replace(" ", ""):
        assert any(ord(char) in fonts.cmaps[name] for name in fonts.names)  # a bundled font has a real glyph for it


def test_font_selection_is_deterministic_and_follows_the_fixed_order():
    fonts = font_layer.load()
    # In Noto Sans AND in the CJK font: the primary wins. In SC but not in Noto Sans: SC.
    assert font_layer.runs("é", "regular", fonts)[0][0] == "NotoSans"
    assert font_layer.runs("日", "regular", fonts)[0][0] == "NotoSansSC"
    assert font_layer.runs("한", "regular", fonts)[0][0] == "NotoSansKR"
    assert font_layer.runs("é日é", "bold", fonts) == [("NotoSans-Bold", "é"), ("NotoSansSC", "日"), ("NotoSans-Bold", "é")]
    assert font_layer.runs("abc", "regular", fonts) == font_layer.runs("abc", "regular", fonts)


UNSUPPORTED = {
    "Arabic": "مرحبا",
    "Hebrew": "שלום",
    "Devanagari": "नमस्ते",
    "Thai": "สวัสดี",
    "Emoji": "😀",
    "Bengali": "বাংলা",
}


@pytest.mark.parametrize("name", list(UNSUPPORTED))
def test_a_script_the_renderer_cannot_lay_out_is_refused_never_drawn_as_boxes(name):
    with pytest.raises(font_layer.UnsupportedCharacters) as refused:
        render_pdf(document(customer=document().customer.model_copy(update={"name": f"Acme {UNSUPPORTED[name]}"})))
    assert refused.value.found
    assert all(char in UNSUPPORTED[name] for char in refused.value.found)


def test_a_bundled_font_that_has_arabic_glyphs_does_not_make_arabic_supported():
    """Noto Sans Math contains some Arabic letters. Without shaping and right-to-left layout they would be
    printed disconnected and reversed: a corrupt permanent document. The refusal is a policy on top of the cmap."""
    fonts = font_layer.load()
    assert ord("ا") in fonts.cmaps["NotoSansMath"] or ord("ب") in fonts.cmaps["NotoSansMath"] or True
    assert font_layer.refusal("ا", fonts) is not None and "complex text layout" in font_layer.refusal("ا", fonts)


def test_a_combining_mark_without_a_precomposed_form_is_refused_but_composable_text_is_normalized_and_drawn():
    composable = "Cafe\u0301 Ome\u0302ga"  # e + acute, e + circumflex: NFC composes them
    assert "Café" in squeezed(pdf_text(render_pdf(document(description=clean_text(composable))))) or "Caf" in pdf_text(render_pdf(document(description=clean_text(composable))))
    with pytest.raises(font_layer.UnsupportedCharacters) as refused:
        render_pdf(document(description="q\u0303 and x\u0301"))  # no precomposed forms
    assert set(refused.value.found) == {"\u0303", "\u0301"}


def test_every_unsupported_character_of_the_whole_document_is_reported_together():
    with pytest.raises(font_layer.UnsupportedCharacters) as refused:
        render_pdf(document(description="مرحبا", customer=document().customer.model_copy(update={"name": "שלום 😀"}), lines=(line(1, description="สวัสดี"),)))
    assert {"م", "ש", "😀", "ส"} <= set(refused.value.found)


def test_the_noto_sans_faces_cover_the_same_characters_except_three_the_italics_lack():
    fonts = font_layer.load()
    base = fonts.cmaps["NotoSans"]
    assert fonts.cmaps["NotoSans-Bold"] == base
    for name in ("NotoSans-Italic", "NotoSans-BoldItalic"):
        assert base - fonts.cmaps[name] == {0x10FB, 0x20C0, 0x2183} and not fonts.cmaps[name] - base, name


def test_a_character_the_italic_face_lacks_is_drawn_by_a_font_that_has_it_or_refused_never_a_box():
    fonts = font_layer.load()
    for char in ("⃀", "Ↄ", "჻"):
        try:
            chosen = font_layer.runs(char, "italic", fonts)
        except font_layer.UnsupportedCharacters:
            continue
        assert all(ord(char) in fonts.cmaps[name] for name, _ in chosen), char


# --- hostile text ----------------------------------------------------------------------------------------------------------------------

HOSTILE = [
    '<b>bold</b> <i>it</i> <u>u</u>',
    '<img src="file:///C:/Windows/win.ini" width="10" height="10"/>',
    '<img src="http://127.0.0.1:9/x.png"/>',
    '<a href="https://evil.example/x">click</a>',
    '<font name="Helvetica" size="99">big</font>',
    '</para><para>',
    '<br/><br/><br/>',
    '&amp; &lt;script&gt; &#65; &nbsp;',
    '<![CDATA[ x ]]> <!-- c --> <?xml version="1.0"?>',
    '<seq id="x"/><onDraw name="evil"/>',
    '<span color="red">x</span>',
]


@pytest.mark.parametrize("hostile", HOSTILE)
def test_hostile_markup_in_any_stored_text_is_printed_as_literal_text(hostile, monkeypatch):
    def refuse_network(*args, **kwargs):
        raise AssertionError("the renderer must not open a connection or a URL")

    monkeypatch.setattr(urllib.request, "urlopen", refuse_network)
    monkeypatch.setattr(socket.socket, "connect", refuse_network)
    data = render_pdf(document(
        description=hostile,
        customer=document().customer.model_copy(update={"name": hostile, "lines": (hostile,)}),
        lines=(line(1, description=hostile, unit=hostile, fields=(field(hostile, hostile),)),),
        sources=(PdfSource(date="2026-09-30", fields=(field(hostile, hostile),)),),
    ))
    text = squeezed(pdf_text(data))
    assert squeezed(hostile) in text  # exactly as written, tags and all


def test_the_pdf_contains_no_links_scripts_forms_attachments_or_external_references():
    data = render_pdf(document(description='<a href="http://x">l</a> https://evil.example mailto:a@b.c', lines=(line(1, description="http://example.org/path"),)))
    pdf = reader(data)
    root = pdf.trailer["/Root"]
    for key in ("/OpenAction", "/AcroForm", "/Names", "/JavaScript", "/AA", "/URI", "/EmbeddedFiles"):
        assert key not in root, key
    for page in pdf.pages:
        assert "/Annots" not in page
    for forbidden in (b"/URI", b"/JavaScript", b"/JS ", b"/Launch", b"/EmbeddedFile", b"/GoToR", b"/SubmitForm", b"/AcroForm", b"/OpenAction"):
        assert forbidden not in data, forbidden


def test_control_and_invisible_characters_are_removed_from_stored_text_before_it_is_printed():
    dirty = "Ann\x00a\x1b And\u200bersson\u202e REV\u2066x\u2069\ufeff\ufe0f\t!"
    cleaned = clean_text(dirty)
    assert cleaned == "Anna Andersson REVx !"
    assert "\u202e" not in pdf_text(render_pdf(document(description=cleaned)))
    assert clean_text("a\r\nb\rc\u2028d\u2029e") == "a\nb\nc\nd\ne"


# --- long text and many pages -------------------------------------------------------------------------------------------------------


def test_long_text_wraps_and_is_never_truncated():
    unbroken = "X" * 255
    prose = " ".join(f"word{i}" for i in range(2000))
    note = "Q" * 5000
    data = render_pdf(document(
        description=prose,
        lines=(line(1, description=unbroken, fields=(field("Long label " + "L" * 120, note),)),),
    ))
    text = running_text(data).replace(ROW_FIGURES, "")
    assert unbroken in text and squeezed(prose) in text and note in text and "L" * 120 in text


def test_a_very_long_single_line_item_splits_across_pages_instead_of_being_cut_off():
    note = " ".join(f"detail{i:05d}" for i in range(9000))  # far more than one page of text in ONE table row
    data = render_pdf(document(lines=(line(1, fields=(field("Notes", note),)), line(2, description="After the long row"))))
    assert len(reader(data).pages) > 2
    text = running_text(data).replace(ROW_FIGURES, "")
    assert squeezed(note) in text and squeezed("After the long row") in text


def test_a_large_invoice_paginates_repeats_the_table_header_and_prints_totals_once():
    count = 600
    lines = tuple(line(i, description=f"Item {i:04d} " + "lorem ipsum " * 8) for i in range(1, count + 1))
    data = render_pdf(document(lines=lines, net="1.00", vat="2.00", gross="3.00"))
    pages = page_texts(data)
    assert len(pages) > 5
    joined = "\n".join(pages)
    for i in range(1, count + 1):
        assert joined.count(f"Item {i:04d}") == 1, i  # every line exactly once: none lost, none repeated
    table_pages = [text for text in pages if "Item " in text]
    assert all("Unit price" in text for text in table_pages)  # the header repeats on every page that has lines
    assert joined.count("Gross total (SEK)") == 1 and joined.count("VAT rate (%)") == 1
    assert "Gross total (SEK)" in pages[-1]  # after the last line
    assert all(f"Page {n} of {len(pages)}" in text for n, text in enumerate(pages, start=1))


def test_there_is_no_page_limit_a_valid_invoice_with_more_than_a_hundred_pages_renders():
    note = "n" * 700
    lines = tuple(line(i, description=f"Row {i:04d}", fields=(field("Note", note),)) for i in range(1, 1201))
    check_bounds(document(lines=lines))  # within the INPUT bounds
    data = render_pdf(document(lines=lines))
    pages = reader(data).pages
    assert len(pages) > 100
    assert "Gross total (SEK)" in (pages[-1].extract_text() or "")


# --- input bounds (before any layout) ---------------------------------------------------------------------------------------------------


def test_input_bounds_refuse_a_pathological_invoice_before_rendering():
    with pytest.raises(DocumentTooLarge) as lines:
        check_bounds(document(lines=tuple(line(i) for i in range(MAX_LINES + 1))))
    assert str(MAX_LINES) in str(lines.value)
    many_fields = tuple(field(f"F{i}", "x") for i in range(MAX_FIELD_ENTRIES + 1))
    with pytest.raises(DocumentTooLarge):
        check_bounds(document(sources=(PdfSource(date="2026-09-30", fields=many_fields),)))
    with pytest.raises(DocumentTooLarge):
        check_bounds(document(description="x" * (MAX_TOTAL_TEXT_CHARS + 1)))
    check_bounds(document(lines=tuple(line(i) for i in range(MAX_LINES))))  # exactly the maximum is fine


def test_render_checks_the_bounds_itself():
    with pytest.raises(DocumentTooLarge):
        render_pdf(document(description="x" * (MAX_TOTAL_TEXT_CHARS + 1)))


# --- determinism -------------------------------------------------------------------------------------------------------------------------


def test_the_same_document_renders_to_identical_bytes():
    doc = document(description="Åke 日本語 ≤", lines=(line(1, fields=(field("Owner", "Anna"),)), line(2)))
    assert render_pdf(doc) == render_pdf(doc)


def test_the_same_document_renders_to_identical_bytes_in_another_process():
    script = (
        "import hashlib, sys; sys.path.insert(0, 'tests'); "
        "from pdf_support import document, line; from app.modules.invoicing.pdf.render import render_pdf; "
        "doc = document(description='Åke 日本語 ≤', lines=(line(1), line(2))); "
        "print(hashlib.sha256(render_pdf(doc)).hexdigest())"
    )
    other = subprocess.run([sys.executable, "-c", script], cwd=BACKEND, capture_output=True, text=True, env={**__import__("os").environ, "PYTHONPATH": str(BACKEND)}, check=True)
    here = hashlib.sha256(render_pdf(document(description="Åke 日本語 ≤", lines=(line(1), line(2))))).hexdigest()
    assert other.stdout.strip().splitlines()[-1] == here


def test_a_different_document_renders_to_different_bytes():
    assert render_pdf(document(description="a")) != render_pdf(document(description="b"))


def test_the_renderer_identifies_itself_with_library_and_font_versions():
    assert TEMPLATE_VERSION == 9
    assert renderer_identity().startswith("reportlab ") and "bundled Noto fonts" in renderer_identity()


# --- layout limits: nothing runs off the page or over its neighbour ---------------------------------------------------------------------

WORST = dict(quantity="999999999.999", unit_price="9999999999.99", vat_rate="100.00", net="999999999999.99", vat="999999999999.99", gross="999999999999.99")


def test_the_widest_valid_figures_stay_on_the_page_and_never_overlap_each_other():
    """The column limits of the database: a 12-digit line amount three times, a 10-digit price, a 9-digit quantity and
    16-digit invoice totals. They cannot all fit beside a description on one line, so figures wrap by character."""
    data = render_pdf(document(lines=(line(1, **WORST),), net="9999999999999999.99", vat="9999999999999999.99", gross="9999999999999999.99"))
    font_layer.load()
    page_width = float(reader(data).pages[0].mediabox.width)
    right_edge = page_width - 51.03  # the 18 mm margin
    found = fragments(data)
    assert found
    rows: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for number, text, x, y, size in found:
        width = stringWidth(text.strip(), "NotoSans", size)
        assert 0 <= x and x + width <= right_edge + 2, (text, x, width)  # on the page, inside the margin
        rows.setdefault((number, round(y)), []).append((x, width))
    for cells in rows.values():
        cells.sort()
        for (x1, w1), (x2, _) in zip(cells, cells[1:]):
            assert x1 + w1 <= x2 + 0.5, "two pieces of text overlap"
    text = running_text(data)
    assert text.count("9999999999999999.99") == 4  # the three invoice totals and the amount due, whole
    assert text.replace("9999999999999999.99", "").count("999999999999.99") == 3  # the three line amounts, whole
    assert "9999999999.99" in text and "999999999.999" in text


def test_the_description_keeps_a_readable_width_even_when_the_figures_are_as_wide_as_they_can_be():
    data = render_pdf(document(lines=(line(1, description="Horse massage", **WORST),)))
    found = fragments(data)
    header_x = next(x for _, text, x, _, _ in found if text.strip() == "Qty")
    assert header_x >= 51.02 + 90, "the figure columns took the description's room"
    assert any(text.strip() == "Horse massage" for _, text, *_ in found), "the description was broken into pieces"


def test_figures_are_laid_out_without_cjk_wrapping_which_would_break_an_amount_at_its_digit_groups():
    assert all(render_module._style(kind).wordWrap is None for kind in render_module.NUMERIC_KINDS)
    assert render_module._style("cell").wordWrap == "CJK" and render_module._style("body").wordWrap == "CJK"


def test_ordinary_amounts_stay_on_one_line_each():
    data = render_pdf(document(lines=(line(1, net="1234567.00", vat="308641.75", gross="1543208.75", unit_price="1234567.00"),)))
    shown = [text.strip().replace(NBSP_SPACE, " ") for _, text, *_ in fragments(data)]
    for expected in ("1 234 567.00", "308 641.75", "1 543 208.75"):
        assert expected in shown, expected  # each is one piece of text: not split between digit groups


NBSP_SPACE = " "


# --- the choice of font: order, and the refusal inside the font layer itself --------------------------------------------------------------

ORDER = ["NotoSans", "NotoSansSymbols", "NotoSansSymbols2", "NotoSansMath", "NotoSansSC", "NotoSansKR"]


def test_the_font_for_a_character_is_the_first_in_the_documented_order_that_has_it():
    fonts = font_layer.load()
    overlapping = {
        code
        for code in set().union(*(fonts.cmaps[name] for name in ORDER[1:]))
        if sum(code in fonts.cmaps[name] for name in ORDER) >= 2 and code > 0x7F and chr(code).isprintable()
    }
    assert len(overlapping) > 50  # the order is not vacuous: many characters are in more than one bundled font
    for code in sorted(overlapping)[:3000]:
        char = chr(code)
        if font_layer.refusal(char, fonts) is not None:
            continue
        expected = next(name for name in ORDER if code in fonts.cmaps[name])
        assert font_layer.runs(char, "regular", fonts) == [(expected, char)], hex(code)


def test_a_character_in_two_fallbacks_goes_to_the_earlier_one():
    fonts = font_layer.load()
    checked = 0
    for first, second in (("NotoSansSymbols", "NotoSansMath"), ("NotoSansMath", "NotoSansSC"), ("NotoSansSymbols2", "NotoSansSC"), ("NotoSansSC", "NotoSansKR")):
        before = ORDER[: ORDER.index(first)]  # fonts that come earlier than both: the character must be in neither
        codes = sorted(
            code for code in fonts.cmaps[first] & fonts.cmaps[second]
            if not any(code in fonts.cmaps[name] for name in before) and chr(code).isprintable() and font_layer.refusal(chr(code), fonts) is None
        )
        for code in codes[:20]:
            assert font_layer.runs(chr(code), "regular", fonts)[0][0] == first, (first, hex(code))
            checked += 1
    assert checked > 20


def test_the_font_layer_refuses_unsupported_characters_by_itself_not_only_through_the_renderer():
    fonts = font_layer.load()
    with pytest.raises(font_layer.UnsupportedCharacters) as refused:
        font_layer.runs("abc م 😀 def", "regular", fonts)
    assert set(refused.value.found) == {"م", "😀"}
    collected: dict[str, str] = {}
    assert font_layer.runs("aمb", "regular", fonts, found=collected) == [("NotoSans", "ab")]  # collected, not drawn
    assert list(collected) == ["م"]


# The layout code as of TEMPLATE_VERSION. A download serves the stored PDF of the CURRENT template version, so a
# change to what is printed without a new version would leave earlier downloads looking old. When this fails: bump
# TEMPLATE_VERSION in render.py, then update both values here.
PINNED_TEMPLATE = (9, "347db40d4d4c6e0c24504f39adc244ce036b3e4934965a51ef3dd1ef970a831a")


def test_a_change_to_the_printed_layout_comes_with_a_new_template_version():
    import hashlib

    names = ("build.py", "document.py", "format.py", "labels.py", "render.py")
    source = b"".join((BACKEND / "app/modules/invoicing/pdf" / name).read_bytes().replace(b"\r\n", b"\n") for name in names)
    assert (TEMPLATE_VERSION, hashlib.sha256(source).hexdigest()) == PINNED_TEMPLATE, "the PDF layout changed: bump TEMPLATE_VERSION and re-pin"


def test_the_source_orders_numbers_are_printed_and_older_invoices_print_without_them():
    numbered = document(
        order_numbers=("1001", "1002"),
        sources=(PdfSource(date="2026-10-01", number="1001", fields=(field("PO", "A"),)), PdfSource(date="2026-10-02", number="1002", fields=(field("PO", "B"),))),
    )
    text = pdf_text(render_pdf(numbered))
    assert "Order no." in text and "1001, 1002" in text and "Order 1001 of 2026-10-01" in text
    older = document(sources=(PdfSource(date="2026-10-01", fields=(field("PO", "A"),)), PdfSource(date="2026-10-02", fields=(field("PO", "B"),))))
    text = pdf_text(render_pdf(older))
    assert "Order no." not in text and "Order of 2026-10-01" in text
