"""The drop-downs of the list pages must offer exactly the values the backend has.

A filter that lists a status the records no longer have, or misses one they have gained, is a bug people notice (the invoice list once
offered "Draft / Issued" while the badges said Unpaid, Partially paid, Paid). The invoice list now reads its choices from the backend
(`/api/invoices/filter-options`, defined in app/modules/invoicing/filters.py). The other lists still write their choices on the screen, so this test
reads the screens' source and compares each choice list with the backend's own definition. When it fails, change the screen (or the
backend) so the two agree again.
"""

import re
import typing
from pathlib import Path

from app.models.item import ItemType
from app.modules.inventory import api as inventory_api
from app.modules.sales.models import TransactionStatus

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def source(path: str) -> str:
    return (FRONTEND / path).read_text(encoding="utf-8")


def option_values(text: str) -> set[str]:
    return set(re.findall(r'<option value="([a-z_]+)"', text))


def test_the_order_status_filter_offers_the_statuses_an_order_has():
    page = source("app/o/[orgId]/transactions/page.tsx")
    assert option_values(page) == {status.value for status in TransactionStatus}


def test_the_item_type_filter_and_labels_cover_every_item_type():
    labels = source("features/catalog/item-types.ts")
    block = labels[labels.index("ITEM_TYPE_LABELS") :]
    keys = set(re.findall(r"^\s{2}([a-z_]+):", block, flags=re.M))
    assert keys == {item_type.value for item_type in ItemType}
    # and the type the screen's code knows about
    types = source("lib/api/types.ts")
    declared = set(re.findall(r'"([a-z_]+)"', re.search(r"export type ItemType =([^;]+);", types).group(1)))
    assert declared == {item_type.value for item_type in ItemType}


def test_the_stock_state_filter_offers_the_states_the_stock_list_accepts():
    page = source("app/o/[orgId]/inventory/page.tsx")
    block = page[page.index("const STATES = [") : page.index("] as const;", page.index("const STATES = ["))]
    offered = set(re.findall(r'value: "([a-z_]+)"', block)) - {""}
    parameter = inspect_state_literal()
    assert offered == set(parameter)


def inspect_state_literal() -> tuple[str, ...]:
    import inspect

    annotation = inspect.signature(inventory_api.list_stock_items).parameters["state"].annotation
    return tuple(value for part in typing.get_args(annotation) for value in typing.get_args(part))


def test_the_invoice_list_writes_none_of_its_choices_on_the_screen():
    page = source("app/o/[orgId]/invoices/page.tsx")
    assert option_values(page) == set(), "the invoice list reads its choices from /api/invoices/filter-options"
    assert "filter-options" in page
