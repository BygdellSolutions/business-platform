# Bundled fonts

The invoice PDF renderer uses ONLY these files. It never reads a font from the operating system,
so the output does not depend on the machine it runs on. `SHA256SUMS` pins their exact bytes (a test
fails if any file changes) because a PDF's bytes depend on them.

All fonts are Noto fonts under the SIL Open Font License 1.1. They are redistributed UNMODIFIED
(the CJK fonts carry a Reserved Font Name, which only restricts modified versions). The license
notice of each family is kept in `licenses/`. The PDF embeds only the subset of glyphs it uses.

| File | Family | Covers | Source (pinned commit) |
|---|---|---|---|
| NotoSans-{Regular,Bold,Italic,BoldItalic}.ttf | Noto Sans | Latin (incl. extended, Vietnamese), Greek, Cyrillic | notofonts/notofonts.github.io @ e3ff34c3178cb4012124c9e6390b9a3535ff2c3f, `fonts/NotoSans/hinted/ttf/` |
| NotoSansSymbols-Regular.ttf | Noto Sans Symbols | symbols, arrows, dingbat-like marks | same repository and commit, `fonts/NotoSansSymbols/hinted/ttf/` |
| NotoSansSymbols2-Regular.ttf | Noto Sans Symbols 2 | further symbols and pictographs | same, `fonts/NotoSansSymbols2/hinted/ttf/` |
| NotoSansMath-Regular.ttf | Noto Sans Math | mathematical operators and letters | same, `fonts/NotoSansMath/hinted/ttf/` |
| NotoSansSC-VF.ttf | Noto Sans SC (variable, default weight used) | Han (simplified and traditional), kana, CJK punctuation | google/fonts @ 9710da1eacb3be272583c3224dcb70f9da6eadbb, `ofl/notosanssc/NotoSansSC[wght].ttf` |
| NotoSansKR-VF.ttf | Noto Sans KR (variable, default weight used) | Hangul | google/fonts @ same commit, `ofl/notosanskr/NotoSansKR[wght].ttf` |

Selection order (per character, deterministic): Noto Sans, Symbols, Symbols 2, Math, SC, KR. Bold and
italic use the matching Noto Sans face; fallback fonts have one weight.

## What is deliberately NOT supported (renderer capability debt)

ReportLab lays text out left to right, one glyph per character, without OpenType shaping or the
bidirectional algorithm. Scripts that need either would come out as disconnected or reordered letters,
which would corrupt a permanent document, so the renderer REFUSES them even when a bundled font has
glyphs: Arabic, Hebrew, Syriac, Thaana, the Indic scripts, Thai, Lao, Tibetan, Myanmar, Khmer,
Mongolian, and combining marks that have no precomposed form. The same goes for any character no
bundled font contains (for example emoji). This is a limit of the renderer, not a rule about what an
invoice may contain: lifting it means adding a shaping engine (HarfBuzz), not changing invoice data.
