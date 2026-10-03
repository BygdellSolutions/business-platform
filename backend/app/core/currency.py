"""The organization's default currency: reading it for a new record, and when it may change.

Prices in this platform are plain decimals without a currency of their own: their currency is
the one of the organization they were entered in. Changing the organization's default currency
after prices exist would silently reinterpret them (850 SEK would become 850 EUR), so it is
refused. This is a TEMPORARY rule until a real currency and repricing model exists.

Two writers must not interleave: "a record is created in the current currency" and "the
currency changes". Both go through the organization row:

* creating a currency-dependent record takes the row `FOR SHARE` (many creators at once are fine);
* changing the currency takes it `FOR UPDATE` and checks the guards while holding it.

A creator therefore either committed before the change started (the guard sees its record and
refuses) or starts after it finished (and reads the new currency).
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.entity_registry import registry
from app.models import Item, Organization

CURRENCY_NOT_CONFIGURED = "currency_not_configured"
CURRENCY_LOCKED = "currency_locked"


def default_currency_for_new_record(db: Session, organization_id: uuid.UUID) -> str:
    """The organization's default currency, with the row share-locked until the end of the
    transaction. Raises 409 `currency_not_configured` if an owner/admin has not set one yet."""
    currency = db.scalar(
        select(Organization.default_currency)
        .where(Organization.id == organization_id)
        .with_for_update(read=True)
    )
    if currency is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": CURRENCY_NOT_CONFIGURED,
                "message": "The organization has no default currency yet. "
                "An owner or admin must set it in the organization settings first.",
            },
        )
    return currency


def share_lock_organization(db: Session, organization_id: uuid.UUID) -> None:
    """Take the organization row `FOR SHARE` without needing its currency (used when a record
    that carries a price is created, so it cannot slip past a concurrent currency change)."""
    db.execute(select(Organization.id).where(Organization.id == organization_id).with_for_update(read=True)).one()


def default_currency_lock_reason(db: Session, organization_id: uuid.UUID) -> str | None:
    """Why the default currency may not change now, or None. The caller holds the organization
    row `FOR UPDATE`."""
    if db.scalar(select(Item.id).where(Item.organization_id == organization_id).limit(1)) is not None:
        return "The catalog already has items, whose prices are in the current currency."
    return registry.currency_lock_reason(db, organization_id)
