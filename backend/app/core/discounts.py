"""Which discounts apply to a catalog sale (decided 2026-10-08).

Two layers, applied in this order and never added together: the item's temporary discount active on the sale's
date, then the billing customer's permanent discount. The arithmetic lives in `app.core.prices.discounted_unit_price`
and is repeated by a CHECK on every line; this module only answers "which percentages".
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Customer, ItemDiscount


def active_item_discounts(db: Session, organization_id: uuid.UUID, item_ids: list[uuid.UUID], on_date: date) -> dict[uuid.UUID, ItemDiscount]:
    """The discount active on `on_date` for each item that has one (periods never overlap, so at most one each)."""
    if not item_ids:
        return {}
    rows = db.scalars(
        select(ItemDiscount).where(
            ItemDiscount.organization_id == organization_id,
            ItemDiscount.item_id.in_(item_ids),
            ItemDiscount.starts_on <= on_date,
            or_(ItemDiscount.ends_on.is_(None), ItemDiscount.ends_on >= on_date),
        )
    )
    return {row.item_id: row for row in rows}


def catalog_percent(db: Session, organization_id: uuid.UUID, item_id: uuid.UUID, on_date: date) -> Decimal | None:
    discount = active_item_discounts(db, organization_id, [item_id], on_date).get(item_id)
    return discount.percent if discount is not None else None


def customer_percent(db: Session, organization_id: uuid.UUID, customer_id: uuid.UUID) -> Decimal | None:
    return db.scalar(select(Customer.default_discount_percent).where(Customer.organization_id == organization_id, Customer.id == customer_id))
