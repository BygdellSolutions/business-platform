"""Invoicing by delivery (owner, 2026-10-11): what of each order line may be invoiced now.

An order line is invoiced as it is delivered. Its invoiceable quantity is what was handed over (Inventory's figure for
a stock-tracking line, through the core hook `stock.delivered`; the whole quantity for every other line once the order
is completed) less what is already on invoices, draft or issued. An order with a hold ("wait until everything is
delivered") has nothing invoiceable until every line is delivered in full.

Lines invoiced before invoicing by delivery were whole lines: they count as fully invoiced, so a later delivery of
their backordered units creates nothing new to invoice (those units were charged in advance).
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.entity_registry import registry
from app.core.tenant import TenantContext
from app.modules.invoicing.models import InvoiceHold, InvoiceLine
from app.modules.sales.models import Transaction, TransactionLine
from app.modules.sales.pricing import calculate_line

ZERO = Decimal("0")


@dataclass(frozen=True)
class Part:
    """What of one order line goes on an invoice now, priced like the order line."""

    line: TransactionLine
    quantity: Decimal
    net_amount: Decimal
    vat_amount: Decimal
    gross_amount: Decimal
    ordered_quantity: Decimal
    backordered_quantity: Decimal

    @property
    def whole(self) -> bool:
        """The whole order line at once: its stored amounts are copied verbatim."""
        return self.quantity == self.line.quantity

    # sum_lines() and the VAT breakdown read these like a stored line.
    @property
    def vat_rate(self) -> Decimal:
        return self.line.vat_rate


def delivered(db: Session, ctx: TenantContext, lines: Sequence[TransactionLine]) -> dict[uuid.UUID, Decimal]:
    """What of each line was handed over so far. Untracked lines: all of it (the order is completed)."""
    tracked: dict[uuid.UUID, Decimal] = {}
    for answer in registry.call_hooks("stock.delivered", db, ctx.organization_id, [line.id for line in lines]):
        tracked.update(answer)
    return {line.id: tracked.get(line.id, line.quantity) for line in lines}


def invoiced(db: Session, ctx: TenantContext, line_ids: Iterable[uuid.UUID], *, except_invoice: uuid.UUID | None = None) -> dict[uuid.UUID, Decimal]:
    """What of each line is on invoices (draft or issued), optionally leaving one invoice out."""
    ids = list(line_ids)
    if not ids:
        return {}
    query = (
        select(InvoiceLine.source_line_id, func.sum(InvoiceLine.quantity))
        .where(InvoiceLine.organization_id == ctx.organization_id, InvoiceLine.source_line_id.in_(ids))
        .group_by(InvoiceLine.source_line_id)
    )
    if except_invoice is not None:
        query = query.where(InvoiceLine.invoice_id != except_invoice)
    return {line_id: total for line_id, total in db.execute(query).all()}


def held(db: Session, ctx: TenantContext, transaction_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """The orders waiting until everything is delivered."""
    ids = list(transaction_ids)
    if not ids:
        return set()
    return set(
        db.scalars(select(InvoiceHold.transaction_id).where(InvoiceHold.organization_id == ctx.organization_id, InvoiceHold.transaction_id.in_(ids)))
    )


def parts(
    db: Session,
    ctx: TenantContext,
    orders: Sequence[Transaction],
    lines_of: dict[uuid.UUID, list[TransactionLine]],
    *,
    except_invoice: uuid.UUID | None = None,
) -> dict[uuid.UUID, list[Part]]:
    """Per order, the parts of its lines that may be invoiced now (lines with nothing to invoice are left out)."""
    all_lines = [line for order in orders for line in lines_of.get(order.id, [])]
    handed_over = delivered(db, ctx, all_lines)
    already = invoiced(db, ctx, [line.id for line in all_lines], except_invoice=except_invoice)
    waiting = held(db, ctx, [order.id for order in orders])
    result: dict[uuid.UUID, list[Part]] = defaultdict(list)
    for order in orders:
        lines = lines_of.get(order.id, [])
        if order.id in waiting and any(handed_over[line.id] < line.quantity for line in lines):
            continue  # "wait until everything is delivered": not yet
        for line in lines:
            quantity = handed_over[line.id] - already.get(line.id, ZERO)
            if quantity <= ZERO:
                continue
            backordered = line.quantity - handed_over[line.id]
            if quantity == line.quantity:
                net, vat, gross = line.net_amount, line.vat_amount, line.gross_amount
            else:
                amounts = calculate_line(quantity, line.unit_price_ex_vat, line.vat_rate)
                net, vat, gross = amounts.net, amounts.vat, amounts.gross
            result[order.id].append(
                Part(line=line, quantity=quantity, net_amount=net, vat_amount=vat, gross_amount=gross, ordered_quantity=line.quantity, backordered_quantity=backordered)
            )
    return dict(result)


def completes(
    db: Session, ctx: TenantContext, orders: Sequence[Transaction], lines_of: dict[uuid.UUID, list[TransactionLine]], parts_of: dict[uuid.UUID, list[Part]]
) -> dict[uuid.UUID, bool]:
    """Per order: with these parts, every line of it is invoiced in full."""
    all_lines = [line for order in orders for line in lines_of.get(order.id, [])]
    already = invoiced(db, ctx, [line.id for line in all_lines])
    result = {}
    for order in orders:
        now = {part.line.id: part.quantity for part in parts_of.get(order.id, [])}
        result[order.id] = all(already.get(line.id, ZERO) + now.get(line.id, ZERO) >= line.quantity for line in lines_of.get(order.id, []))
    return result


def shortage(db: Session, ctx: TenantContext, lines: Sequence[TransactionLine]) -> list[tuple[TransactionLine, Decimal, Decimal]]:
    """The lines not delivered in full: (line, delivered, still backordered). Empty: everything is delivered."""
    handed_over = delivered(db, ctx, lines)
    return [(line, handed_over[line.id], line.quantity - handed_over[line.id]) for line in lines if handed_over[line.id] < line.quantity]
