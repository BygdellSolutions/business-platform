"""The catalog's stock columns as SQL, so the catalog list can be sorted by them on the server (hook "item.sort").

Each expression is the per-item form of the function of the same name in `service` (same tables, same filters); a
test sorts by each one and compares with the figures `service` reports. Items that do not track stock get NULL,
so they sort last, as their empty cells read.
"""

from sqlalchemy import ColumnElement, and_, case, func, select

from app.models import Item, ItemType
from app.modules.inventory.models import IncomingStock, LineFulfillment, StockMovement
from app.modules.sales.models import Transaction, TransactionLine, TransactionStatus

ZERO = 0


def _same_item(model) -> ColumnElement[bool]:
    return and_(model.organization_id == Item.organization_id, model.item_id == Item.id)


def _on_hand():
    latest = (
        select(StockMovement.quantity_after).where(_same_item(StockMovement)).order_by(StockMovement.sequence.desc()).limit(1).scalar_subquery()
    )
    return func.coalesce(latest, ZERO)


def _committed():
    return func.coalesce(
        select(func.sum(LineFulfillment.backordered - LineFulfillment.fulfilled_later))
        .where(_same_item(LineFulfillment), LineFulfillment.cancelled_at.is_(None), LineFulfillment.fulfilled_later < LineFulfillment.backordered)
        .scalar_subquery(),
        ZERO,
    )


def _incoming():
    return func.coalesce(
        select(func.sum(IncomingStock.quantity - IncomingStock.received))
        .where(_same_item(IncomingStock), IncomingStock.cancelled_at.is_(None), IncomingStock.received < IncomingStock.quantity)
        .scalar_subquery(),
        ZERO,
    )


def _allocated():
    return func.coalesce(
        select(func.sum(TransactionLine.quantity))
        .join(Transaction, and_(Transaction.organization_id == TransactionLine.organization_id, Transaction.id == TransactionLine.transaction_id))
        .where(_same_item(TransactionLine), Transaction.status == TransactionStatus.DRAFT)
        .scalar_subquery(),
        ZERO,
    )


def _tracked(expression):
    return case((and_(Item.type == ItemType.PRODUCT, Item.track_stock.is_(True)), expression), else_=None)


def item_sorts() -> dict[str, ColumnElement]:
    on_hand, allocated, committed = _on_hand(), _allocated(), _committed()
    return {
        "on_hand": _tracked(on_hand),
        "allocated": _tracked(allocated),
        "available": _tracked(func.greatest(on_hand - allocated - committed, ZERO)),  # `service.free`
        "committed": _tracked(committed),
        "incoming": _tracked(_incoming()),
    }
