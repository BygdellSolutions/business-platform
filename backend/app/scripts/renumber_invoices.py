"""Renumber the issued invoices and credit notes of a TEST database from 1001 (an operator step, never automatic).

Issued invoices are immutable on purpose: a real customer may hold the invoice and its PDF, so a real database must
never be renumbered. On staging (fictional data only) the owner asked on 2026-10-10 for the invoices issued before
numbering started at 1001 to be renumbered, so the test data looks like the real thing.

Runs inside the migration job, with the owner role, only when RENUMBER_INVOICES equals CONFIRMATION (set on that one
resource by the operator and removed again right after). Per organization and series, invoices and credit notes keep
their order (they share one series) and get 1001, 1002, ...; the credit notes' copy of their invoice's number follows;
the stored PDFs are removed (they print the old number; the next download renders them again with the current
template); the counter continues after the last number. A series whose numbers are all 1001 or higher is left
alone, so running it twice changes nothing. History (audit events) keeps the numbers as they were.

The immutability triggers are switched off for these statements only, inside one transaction, and switched on again
before it commits; if anything fails, all of it rolls back.
"""

from sqlalchemy import Connection, text

CONFIRMATION = "renumber-issued-invoices-of-this-test-database"
FIRST = 1001
# Trigger name per table: what refuses changing an issued invoice, a credit note or a stored PDF.
GUARDS = {
    "invoices": "trg_invoices_immutability",
    "credit_notes": "trg_credit_notes_append_only",
    "invoice_pdfs": "trg_invoice_pdfs_guard",
    "credit_note_pdfs": "trg_credit_note_pdfs_append_only",
}
OFFSET = 1_000_000_000  # temporary numbers above every real one, so the unique (organization, series, number) holds


def renumber_invoices(connection: Connection) -> dict[str, int]:
    """Renumber every series that still has numbers below 1001. Returns what changed (counts only)."""
    series = connection.execute(
        text(
            "SELECT organization_id, series FROM ("
            " SELECT organization_id, series, number FROM invoices WHERE status = 'issued'"
            " UNION ALL SELECT organization_id, series, number FROM credit_notes) documents"
            " GROUP BY organization_id, series HAVING min(number) < :first"
        ),
        {"first": FIRST},
    ).all()
    changed = {"series": 0, "invoices": 0, "credit_notes": 0, "pdfs_removed": 0}
    if not series:
        return changed
    for table, trigger in GUARDS.items():
        connection.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}"))
    for organization_id, name in series:
        changed["series"] += 1
        where = {"o": organization_id, "s": name}
        documents = connection.execute(
            text(
                "SELECT kind, id FROM ("
                " SELECT 'invoice' AS kind, id, number FROM invoices WHERE organization_id = :o AND series = :s AND status = 'issued'"
                " UNION ALL SELECT 'credit_note', id, number FROM credit_notes WHERE organization_id = :o AND series = :s) documents"
                " ORDER BY number, kind DESC"
            ),
            where,
        ).all()
        for table in ("invoices", "credit_notes"):  # step 1: out of the way of every new number
            connection.execute(
                text(f"UPDATE {table} SET number = number + :offset WHERE organization_id = :o AND series = :s AND number IS NOT NULL"),
                {**where, "offset": OFFSET},
            )
        for position, (kind, document_id) in enumerate(documents):
            table = "invoices" if kind == "invoice" else "credit_notes"
            number = FIRST + position
            connection.execute(text(f"UPDATE {table} SET number = :n, number_text = :t WHERE id = :id"), {"n": number, "t": str(number), "id": document_id})
            changed["invoices" if kind == "invoice" else "credit_notes"] += 1
        removed = connection.execute(
            text("DELETE FROM invoice_pdfs WHERE invoice_id IN (SELECT id FROM invoices WHERE organization_id = :o AND series = :s)"), where
        ).rowcount
        removed += connection.execute(
            text("DELETE FROM credit_note_pdfs WHERE credit_note_id IN (SELECT id FROM credit_notes WHERE organization_id = :o AND series = :s)"),
            where,
        ).rowcount
        changed["pdfs_removed"] += removed
        connection.execute(
            text(
                "INSERT INTO invoice_counters (organization_id, series, next_number) VALUES (:o, :s, :next)"
                " ON CONFLICT (organization_id, series) DO UPDATE SET next_number = excluded.next_number"
            ),
            {**where, "next": FIRST + len(documents)},
        )
    for table, trigger in GUARDS.items():
        connection.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}"))
    return changed
