import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.entity_registry import EntityType, ParentSpec, Registry
from app.core.tenant import TenantContext
from app.core.tenant_scope import get_scoped
from app.modules.sales.models import Transaction, TransactionLine, TransactionStatus


def _transaction_is_editable(db: Session, ctx: TenantContext, transaction_id: uuid.UUID) -> bool:
    """Only a draft can change. Takes the same row lock as every Sales mutation, so a
    concurrent write and a lifecycle step on the same transaction are serialized."""
    transaction = get_scoped(db, ctx, Transaction, transaction_id, for_update=True)
    return transaction is not None and transaction.status == TransactionStatus.DRAFT


def _line_is_editable(db: Session, ctx: TenantContext, line_id: uuid.UUID) -> bool:
    line = get_scoped(db, ctx, TransactionLine, line_id)
    return line is not None and _transaction_is_editable(db, ctx, line.transaction_id)


def _transactions_fix_the_currency(db: Session, organization_id: uuid.UUID) -> str | None:
    """Any transaction, whatever its status, has prices that mean something only in the
    organization's current currency; changing the currency would silently reinterpret them."""
    exists = db.scalar(
        select(Transaction.id).where(Transaction.organization_id == organization_id).limit(1)
    )
    return "Transactions already exist, and their prices are in the current currency." if exists else None


def register(registry: Registry) -> None:
    """Expose transactions and their lines to generic capabilities (custom fields,
    lifecycle validation) without importing any of them."""
    registry.register(
        EntityType(
            key="transaction",
            label="Transaction",
            model=Transaction,
            custom_fields=True,
            is_editable=_transaction_is_editable,
        )
    )
    registry.register(
        EntityType(
            key="transaction_line",
            label="Transaction line",
            model=TransactionLine,
            custom_fields=True,
            parent=ParentSpec(entity="transaction", column="transaction_id"),
            is_editable=_line_is_editable,
        )
    )
    registry.add_currency_guard(_transactions_fix_the_currency)
