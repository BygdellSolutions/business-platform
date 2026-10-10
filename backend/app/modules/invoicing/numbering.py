"""Invoice numbers.

A number is handed out only when an invoice is issued, inside the same database transaction,
from the `invoice_counters` row of (organization, series). That row is locked until the
transaction ends, so:

* two issuances never get the same number, and numbers are allocated in commit order;
* a failed issuance rolls the counter back together with everything else;
* a number that was issued is never handed out again;
* a draft has no number, so deleting a draft leaves nothing behind.

This is a mechanism, not a legal claim. It is NOT presented as "gapless numbering": whether a
jurisdiction requires an unbroken series, and what a number looks like (prefixes, per-year
series), is policy that can be layered on `series` and the stored `number_text`.
"""

import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.modules.invoicing.models import InvoiceCounter


# A series starts here (decided by the owner 2026-10-10: invoices count from 1001, like orders). Credit notes are
# numbered from the invoice's series, so they continue the same count.
FIRST_NUMBER = 1001


def allocate_number(db: Session, organization_id: uuid.UUID, series: str) -> int:
    """The next number of the series, in one atomic statement (no MAX()+1, no read-then-write)."""
    statement = insert(InvoiceCounter).values(organization_id=organization_id, series=series, next_number=FIRST_NUMBER + 1)
    statement = statement.on_conflict_do_update(
        index_elements=[InvoiceCounter.organization_id, InvoiceCounter.series],
        set_={"next_number": InvoiceCounter.next_number + 1},
    ).returning(InvoiceCounter.next_number - 1)
    return db.execute(statement).scalar_one()


def format_number(number: int) -> str:
    """V1 label: the plain integer. Stored on the invoice, so a later format change never
    rewrites an issued invoice."""
    return str(number)
