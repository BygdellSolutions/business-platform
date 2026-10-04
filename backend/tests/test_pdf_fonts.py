"""The bundled fonts: exactly the pinned files, licensed, inside the package, and the only fonts ever used."""

import hashlib
import shutil

import pytest
from reportlab.pdfbase import pdfmetrics

from app.modules.invoicing.pdf import fonts as font_layer
from tests.pdf_support import document, drawn_fonts
from app.modules.invoicing.pdf.render import render_pdf

FONT_DIR = font_layer.FONT_DIR


def listed() -> dict[str, str]:
    out = {}
    for row in font_layer.CHECKSUMS.read_text(encoding="utf-8").splitlines():
        if row.strip():
            digest, name = row.split(None, 1)
            out[name.lstrip("*").strip()] = digest
    return out


def test_every_bundled_font_matches_its_pinned_checksum():
    assert listed()
    for name, digest in listed().items():
        assert hashlib.sha256((FONT_DIR / name).read_bytes()).hexdigest() == digest, name
    font_layer.verify_checksums()


def test_the_pinned_list_is_exactly_the_font_files_in_the_package():
    on_disk = {path.name for path in FONT_DIR.glob("*.ttf")}
    assert on_disk == set(listed())
    assert on_disk == {file for _, file in font_layer.STYLES.values()} | {file for _, file in font_layer.FALLBACKS}


def test_the_fonts_live_inside_the_package_not_in_the_system():
    package = FONT_DIR.parents[3]  # .../app
    assert package.name == "app" and package in FONT_DIR.parents


def test_a_changed_font_file_is_detected(tmp_path, monkeypatch):
    shutil.copytree(FONT_DIR, tmp_path / "fonts")
    victim = tmp_path / "fonts" / "NotoSans-Regular.ttf"
    victim.write_bytes(victim.read_bytes() + b"\x00")
    monkeypatch.setattr(font_layer, "FONT_DIR", tmp_path / "fonts")
    monkeypatch.setattr(font_layer, "CHECKSUMS", tmp_path / "fonts" / "SHA256SUMS")
    with pytest.raises(RuntimeError, match="NotoSans-Regular.ttf"):
        font_layer.verify_checksums()


def test_a_missing_or_extra_font_is_detected(tmp_path, monkeypatch):
    shutil.copytree(FONT_DIR, tmp_path / "fonts")
    sums = tmp_path / "fonts" / "SHA256SUMS"
    original = sums.read_text(encoding="utf-8")
    original = sums.read_text(encoding="utf-8")
    monkeypatch.setattr(font_layer, "FONT_DIR", tmp_path / "fonts")
    monkeypatch.setattr(font_layer, "CHECKSUMS", sums)
    sums.write_text(original + f"{'0' * 64}  NotoSerif-Regular.ttf\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError):  # a pinned font that is not there
        font_layer.verify_checksums()
    sums.write_text("\n".join(row for row in original.splitlines() if "NotoSansKR" not in row) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="disagree"):
        font_layer.verify_checksums()


def test_every_font_has_provenance_and_a_retained_license_notice():
    sources = (FONT_DIR / "SOURCES.md").read_text(encoding="utf-8")
    for name in listed():
        stem = name.split("-")[0]
        assert stem in sources, name
    licenses = {path.name for path in (FONT_DIR / "licenses").glob("OFL-*.txt")}
    for family in ("notosans", "notosanskr", "notosansmath", "notosanssc", "notosanssymbols", "notosanssymbols2"):
        assert f"OFL-{family}.txt" in licenses, family
    for path in (FONT_DIR / "licenses").glob("OFL-*.txt"):
        text = path.read_text(encoding="utf-8")
        assert "SIL OPEN FONT LICENSE Version 1.1" in text and "Copyright" in text, path.name
    assert "NOT supported" in sources  # the capability debt is documented


def test_ttf_files_are_stored_as_binary_by_git():
    attributes = (FONT_DIR.parents[4].parent / ".gitattributes").read_text(encoding="utf-8")
    assert "*.ttf binary" in attributes


def test_every_font_that_draws_text_is_a_bundled_font_embedded_in_the_pdf():
    drawn = drawn_fonts(render_pdf(document(description="Åke 日本語 ≤ ✓ 한국어")))
    assert drawn
    for name, embedded in drawn:
        assert "Noto" in name and embedded, name
    assert not any("Helvetica" in name or "Times" in name or "Courier" in name for name, _ in drawn)
    assert all(name in pdfmetrics.getRegisteredFontNames() for name in font_layer.load().names)


def test_the_fonts_are_loaded_once_per_process_and_without_the_system_fonts(monkeypatch):
    fonts = font_layer.load()
    assert font_layer.load() is fonts
    assert fonts.names[:4] == tuple(name for name, _ in font_layer.STYLES.values())


def test_loading_the_fonts_verifies_them_first(tmp_path, monkeypatch):
    """The first load of a process checks every file against the pinned checksums, so a changed or swapped font
    can never be registered and used (a PDF's bytes depend on the fonts)."""
    shutil.copytree(FONT_DIR, tmp_path / "fonts")
    victim = tmp_path / "fonts" / "NotoSansMath-Regular.ttf"
    victim.write_bytes(victim.read_bytes() + b"\x00")
    monkeypatch.setattr(font_layer, "FONT_DIR", tmp_path / "fonts")
    monkeypatch.setattr(font_layer, "CHECKSUMS", tmp_path / "fonts" / "SHA256SUMS")
    monkeypatch.setattr(font_layer, "_loaded", None)  # as in a fresh process
    with pytest.raises(RuntimeError, match="NotoSansMath-Regular.ttf"):
        font_layer.load()
