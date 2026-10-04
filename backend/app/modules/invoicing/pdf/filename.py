"""The download filename, derived from the stored invoice number text and nothing else.

It is built independently of the document's content: only ASCII letters, digits, dot, underscore and
hyphen survive, the length is bounded, and nothing else about the invoice (customer, organization,
description) is ever part of it. The result is safe inside a quoted Content-Disposition value.
"""

import re

MAX_STEM = 60
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(number_text: str | None) -> str:
    stem = _UNSAFE.sub("_", number_text or "").strip("._-")[:MAX_STEM].strip("._-")
    return f"invoice-{stem}.pdf" if stem else "invoice.pdf"


def content_disposition(filename: str) -> str:
    return f'attachment; filename="{filename}"'
