import uuid

from sqlalchemy.orm import Session

from app.core.entity_registry import Registry
from app.core.lifecycle import CANCEL, COMPLETE, REOPEN
from app.core.tenant import TenantContext
from app.modules.inventory import service, sorting

TRANSACTION = "transaction"


def _transaction_effect(db: Session, ctx: TenantContext, event: str, entity_key: str, entity_id: uuid.UUID) -> None:
    """Completion delivers what is available and backorders the rest; reopen and cancel give it all back.

    Registered on the core lifecycle seam, so Sales never learns about stock. It runs after the validators (a reopen
    or cancel of an invoiced transaction is vetoed by Invoicing before it gets here) and inside the step's database
    transaction: if anything here fails, the step itself is rolled back.
    """
    if entity_key != TRANSACTION:
        return
    if event == COMPLETE:
        service.deliver_on_completion(db, ctx, entity_id)
    elif event == REOPEN:
        service.return_on_undo(db, ctx, entity_id, "reopen")
    elif event == CANCEL:
        service.return_on_undo(db, ctx, entity_id, "cancel")


def register(registry: Registry) -> None:
    registry.add_effect(_transaction_effect)
    # Credit notes: which invoiced lines can go back into stock, and putting them back.
    registry.add_hook("stock.returnable", service.returnable)
    registry.add_hook("stock.return", service.return_on_credit)
    # The catalog list sorts by stock columns through these expressions (core never imports Inventory).
    registry.add_hook("item.sort", sorting.item_sorts)
