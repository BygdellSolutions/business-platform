# Bundled UI fonts

The application's sans font is **Noto Sans** (Regular and Bold), SIL Open Font License 1.1 (`OFL-notosans.txt`),
bundled UNMODIFIED and loaded with `next/font/local`. They are byte-identical to the files the invoice PDF renderer
bundles in `backend/app/modules/invoicing/pdf/fonts/` (same pinned upstream commit; see that directory's `SOURCES.md`
and `SHA256SUMS`; a test compares the checksums).

Nothing is fetched from a font service at build time or at runtime, so a production image builds offline from the
repository's own assets. The monospace face is the system's (`ui-monospace` stack): no font file is bundled for it.
