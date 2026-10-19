"""The invoice list's filters, defined ONCE: what values exist, what each is called, and (in the API) what each means.

The screen does not hard-code these lists. It asks `GET /api/invoices/filter-options`, which is built from the `Literal` types below, so a
value that is added to a filter appears in the drop-down by itself, and a value without a label fails when the application starts (and in
the test that checks it), instead of showing up as a raw word or not at all.

"State" is what a person sees as the invoice's status: the badges say Draft, Unpaid, Partially paid, Paid or Credited, so the filter offers
the same words in the same order. An invoice that is fully credited is "Credited", whatever else is true of its payments, exactly as the badge says.
"""

from typing import Literal, get_args

# What the status badge of an invoice says (plus "issued": everything but drafts, which the dashboard's "Invoiced" card links to).
InvoiceState = Literal["draft", "issued", "unpaid", "partially_paid", "paid", "credited", "partly_credited"]
# Whether the invoice has been emailed to the customer (only meaningful while it is still waiting to be settled).
SentFilter = Literal["sent", "not_sent"]
# Things to chase, which are not statuses.
PaymentFilter = Literal["open", "overdue", "not_yet_due", "refund_due"]
# The older payment words, still accepted so that a saved link keeps working; the screen no longer offers them (they are states now).
LegacyPaymentFilter = Literal["unpaid", "partially_paid", "paid"]
ReturnsFilter = Literal["open", "follow_up_due"]

LABELS: dict[str, dict[str, str]] = {
    "state": {
        "draft": "Draft",
        "issued": "Issued (all but drafts)",
        "unpaid": "Unpaid",
        "partially_paid": "Partially paid",
        "paid": "Paid",
        "credited": "Credited",
        "partly_credited": "Partly credited",
    },
    "sent": {"sent": "Sent to the customer", "not_sent": "Not sent yet"},
    "payment": {"open": "Not fully paid", "overdue": "Overdue", "not_yet_due": "Not yet due", "refund_due": "Refund due"},
    "returns": {"open": "Return open", "follow_up_due": "Follow-up due"},
}
VALUES: dict[str, tuple[str, ...]] = {
    "state": get_args(InvoiceState),
    "sent": get_args(SentFilter),
    "payment": get_args(PaymentFilter),
    "returns": get_args(ReturnsFilter),
}

# A value without a label is a mistake made when it was added: stop here, not on a customer's screen.
for _filter, _values in VALUES.items():
    _missing = [value for value in _values if value not in LABELS[_filter]]
    _extra = [value for value in LABELS[_filter] if value not in _values]
    if _missing or _extra:
        raise RuntimeError(f"invoice filter {_filter!r}: values without a label {_missing}, labels without a value {_extra}")


def options() -> dict[str, list[dict[str, str]]]:
    """Every filter with its values in order, each with the words the screen shows."""
    return {name: [{"value": value, "label": LABELS[name][value]} for value in values] for name, values in VALUES.items()}
