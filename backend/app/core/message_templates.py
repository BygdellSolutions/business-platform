"""Pre-made email text with placeholders, like a template in a mail program.

An organization writes, in its settings, the subject and the message it usually sends with an invoice, with words in curly brackets
where the facts of the invoice go ("Hej {customer}, här är faktura {invoice_number} på {amount}"). The text is filled in when the
person opens the send form, so they see exactly what will go out and can change it before sending.

Only the placeholders below exist. Saving a text with any other word in curly brackets is refused (a typo such as {custmer} would
otherwise be sent to a customer as it stands). Filling in never evaluates anything: it replaces each known placeholder by its value.
"""

import re

PLACEHOLDERS: dict[str, str] = {
    "customer": "the customer's name",
    "invoice_number": "the invoice number",
    "amount": "the amount to pay, with currency",
    "due_date": "the due date",
    "payment_reference": "the payment reference (OCR)",
    "organization": "your organization's name",
}
_BRACKETS = re.compile(r"\{([^{}]*)\}")
_STRAY = re.compile(r"[{}]")


def unknown_placeholders(text: str) -> list[str]:
    """Everything in curly brackets that is not a placeholder, plus a lone bracket as '{' or '}'. Empty means the text is fine."""
    bad = [word for word in _BRACKETS.findall(text) if word not in PLACEHOLDERS]
    bad_set = [f"{{{word}}}" for word in dict.fromkeys(bad)]
    if _STRAY.search(_BRACKETS.sub("", text)):
        bad_set.append("a single { or }")
    return bad_set


def render(text: str, values: dict[str, str]) -> str:
    """`text` with each placeholder replaced; a placeholder without a value becomes empty (nothing else in the text is touched)."""
    return _BRACKETS.sub(lambda match: values.get(match.group(1), "") if match.group(1) in PLACEHOLDERS else match.group(0), text)


def problem(text: str) -> str | None:
    """The sentence shown when a template is refused, or None."""
    bad = unknown_placeholders(text)
    if not bad:
        return None
    allowed = ", ".join(f"{{{name}}}" for name in PLACEHOLDERS)
    return f"These are not known: {', '.join(bad)}. You can use {allowed}."
