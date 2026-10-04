"""The fonts of the invoice PDF and the deterministic choice of a font for every character.

Only the font files bundled in `fonts/` are used, and they are verified against `fonts/SHA256SUMS`
the first time they are loaded: the operating system's fonts are never consulted, so the output does
not depend on the machine. See `fonts/SOURCES.md` for sources and licenses.

Per character, the font is chosen in a fixed order (Noto Sans, Symbols, Symbols 2, Math, SC, KR): the
first bundled font that contains the character. A character is NEVER drawn with a missing-glyph box.
If no bundled font can draw it correctly the whole document is refused (`UnsupportedCharacters`) and
nothing is stored. That is a statement about the renderer, not about invoice data.

What counts as "cannot draw correctly", even when a font has a glyph for it: characters of scripts that
need shaping or bidirectional layout (the renderer places one glyph per character, left to right), and
combining marks that remain after NFC normalization (it does not do mark positioning).
"""

import hashlib
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_DIR = Path(__file__).resolve().parent / "fonts"
CHECKSUMS = FONT_DIR / "SHA256SUMS"

# Primary face per style, then the fallbacks (one weight each), in the order they are tried.
STYLES = {
    "regular": ("NotoSans", "NotoSans-Regular.ttf"),
    "bold": ("NotoSans-Bold", "NotoSans-Bold.ttf"),
    "italic": ("NotoSans-Italic", "NotoSans-Italic.ttf"),
    "bolditalic": ("NotoSans-BoldItalic", "NotoSans-BoldItalic.ttf"),
}
FALLBACKS = (
    ("NotoSansSymbols", "NotoSansSymbols-Regular.ttf"),
    ("NotoSansSymbols2", "NotoSansSymbols2-Regular.ttf"),
    ("NotoSansMath", "NotoSansMath-Regular.ttf"),
    ("NotoSansSC", "NotoSansSC-VF.ttf"),
    ("NotoSansKR", "NotoSansKR-VF.ttf"),
)

# Unicode blocks whose scripts need shaping and/or right-to-left layout, which this renderer lacks.
NEEDS_COMPLEX_LAYOUT = (
    (0x0590, 0x08FF),  # Hebrew, Arabic, Syriac, Thaana, N'Ko, Samaritan, Mandaic, Arabic extended
    (0x0900, 0x0DFF),  # Devanagari, Bengali, Gurmukhi, Gujarati, Oriya, Tamil, Telugu, Kannada, Malayalam, Sinhala
    (0x0E00, 0x0FFF),  # Thai, Lao, Tibetan
    (0x1000, 0x109F),  # Myanmar
    (0x1780, 0x18AF),  # Khmer, Mongolian
    (0xA800, 0xA82F),  # Syloti Nagri
    (0xFB1D, 0xFDFF),  # Hebrew and Arabic presentation forms
    (0xFE70, 0xFEFF),  # Arabic presentation forms B
    (0x10800, 0x10FFF),  # right-to-left historic scripts
    (0x1E800, 0x1EFFF),  # right-to-left scripts, Arabic mathematical alphabetic symbols
)


class UnsupportedCharacters(Exception):
    code = "unsupported_characters"

    def __init__(self, found: dict[str, str]):
        self.found = dict(sorted(found.items()))
        super().__init__("the renderer cannot draw: " + ", ".join(f"{_name(c)} ({why})" for c, why in self.found.items()))


def _name(char: str) -> str:
    return f"U+{ord(char):04X}"


@dataclass(frozen=True)
class Registered:
    names: tuple[str, ...]  # primary first
    cmaps: dict[str, frozenset[int]]


_lock = threading.Lock()
_loaded: Registered | None = None


def verify_checksums() -> None:
    """Every bundled font must be exactly the pinned file. Raises RuntimeError otherwise."""
    expected: dict[str, str] = {}
    for line in CHECKSUMS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, name = line.split(None, 1)
            expected[name.lstrip("*").strip()] = digest
    for name, digest in expected.items():
        actual = hashlib.sha256((FONT_DIR / name).read_bytes()).hexdigest()
        if actual != digest:
            raise RuntimeError(f"bundled font {name} does not match SHA256SUMS")
    used = {file for _, file in STYLES.values()} | {file for _, file in FALLBACKS}
    if used != set(expected):
        raise RuntimeError("the font list and SHA256SUMS disagree")


def load() -> Registered:
    """Register the bundled fonts with ReportLab (once per process)."""
    global _loaded
    with _lock:
        if _loaded is not None:
            return _loaded
        verify_checksums()
        names: list[str] = []
        cmaps: dict[str, frozenset[int]] = {}
        for name, filename in [*STYLES.values(), *FALLBACKS]:
            font = TTFont(name, str(FONT_DIR / filename))
            pdfmetrics.registerFont(font)
            cmaps[name] = frozenset(font.face.charToGlyph)
            names.append(name)
        pdfmetrics.registerFontFamily("NotoSans", normal="NotoSans", bold="NotoSans-Bold", italic="NotoSans-Italic", boldItalic="NotoSans-BoldItalic")
        _loaded = Registered(tuple(names), cmaps)
        return _loaded


def _in_complex_script(code: int) -> bool:
    return any(low <= code <= high for low, high in NEEDS_COMPLEX_LAYOUT)


def refusal(char: str, fonts: Registered) -> str | None:
    """Why this character cannot be drawn correctly, or None."""
    code = ord(char)
    if _in_complex_script(code):
        return "needs complex text layout (shaping or right-to-left), which the renderer does not support"
    if unicodedata.category(char) in ("Mn", "Me"):
        return "a combining mark without a precomposed form; the renderer does not position marks"
    if not any(code in fonts.cmaps[name] for name in fonts.names):
        return "no bundled font contains it"
    return None


def runs(text: str, style: str, fonts: Registered, found: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """Split `text` into (font name, text) runs, choosing each character's font deterministically.

    Characters that cannot be drawn are added to `found` (when given, so a caller can collect the whole
    document's problems) or raise UnsupportedCharacters. Newlines are kept in the primary font's runs.
    """
    primary = STYLES[style][0]
    # The upright regular face comes right after the styled one: the italics lack a few characters it has.
    order = [primary, STYLES["regular"][0], *(name for name, _ in FALLBACKS)]
    problems: dict[str, str] = {} if found is None else found
    out: list[tuple[str, str]] = []
    for char in text:
        if char == "\n":
            font = out[-1][0] if out else primary
        else:
            why = refusal(char, fonts)
            if why is not None:
                problems[char] = why
                continue
            code = ord(char)
            font = next((name for name in order if code in fonts.cmaps[name]), None)
            if font is None:  # refusal() passed, so some bundled font has it, but none that can be used in this order
                problems[char] = "no bundled font contains it"
                continue
        if out and out[-1][0] == font:
            out[-1] = (font, out[-1][1] + char)
        else:
            out.append((font, char))
    if found is None and problems:
        raise UnsupportedCharacters(problems)
    return out
