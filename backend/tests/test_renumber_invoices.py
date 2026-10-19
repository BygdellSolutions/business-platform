"""The operator step that renumbers a TEST database's issued invoices and credit notes from 1001 (staging only)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.scripts.renumber_invoices import GUARDS, renumber_invoices
from tests.test_invoice_credit_notes import _credit, _issued


def _numbers(db: Session, org_id) -> list[tuple[str, int, str]]:
    return [
        tuple(row)
        for row in db.execute(
            text(
                "SELECT kind, number, number_text FROM ("
                " SELECT 'invoice' AS kind, number, number_text FROM invoices WHERE organization_id = :o AND status = 'issued'"
                " UNION ALL SELECT 'credit_note', number, number_text FROM credit_notes WHERE organization_id = :o) d ORDER BY number"
            ),
            {"o": org_id},
        )
    ]


def _old_style(db: Session, org_id) -> None:
    """What staging had: the series counted from 1 (the documents keep their order)."""
    for table, trigger in GUARDS.items():
        db.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}"))
    for table in ("invoices", "credit_notes"):
        db.execute(text(f"UPDATE {table} SET number = number - 1000, number_text = (number - 1000)::text WHERE organization_id = :o AND number IS NOT NULL"), {"o": org_id})
    db.execute(text("UPDATE invoice_counters SET next_number = next_number - 1000 WHERE organization_id = :o"), {"o": org_id})
    for table, trigger in GUARDS.items():
        db.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}"))


def test_old_numbers_move_to_1001_in_order_pdfs_are_dropped_and_the_protection_comes_back(client: TestClient, db_session: Session, sales):
    first = _issued(client, db_session, sales)
    assert _credit(client, sales.headers, first, (first["lines"][0]["id"], "1")).status_code == 201
    second = _issued(client, db_session, sales)
    assert client.get(f"/api/invoices/{first['id']}/pdf", headers=sales.headers).status_code == 200  # a stored PDF
    _old_style(db_session, sales.org.id)
    assert _numbers(db_session, sales.org.id) == [("invoice", 1, "1"), ("credit_note", 2, "2"), ("invoice", 3, "3")]

    changed = renumber_invoices(db_session.connection())

    assert changed == {"series": 1, "invoices": 2, "credit_notes": 1, "pdfs_removed": 1}
    assert _numbers(db_session, sales.org.id) == [("invoice", 1001, "1001"), ("credit_note", 1002, "1002"), ("invoice", 1003, "1003")]
    assert db_session.scalar(text("SELECT count(*) FROM invoice_pdfs WHERE invoice_id = :i"), {"i": first["id"]}) == 0
    assert db_session.scalar(text("SELECT next_number FROM invoice_counters WHERE organization_id = :o"), {"o": sales.org.id}) == 1004
    # The protection is on again: an issued invoice still cannot change.
    with pytest.raises(DBAPIError):
        with db_session.begin_nested():
            db_session.execute(text("UPDATE invoices SET number = 7 WHERE id = :i"), {"i": second["id"]})
    # Run again: nothing left to do.
    assert renumber_invoices(db_session.connection())["series"] == 0
    # The next invoice continues the series, and the PDF is rendered again with the new number.
    assert _issued(client, db_session, sales)["number"] == 1004
    pdf = client.get(f"/api/invoices/{first['id']}/pdf", headers=sales.headers)
    assert pdf.status_code == 200 and "invoice-1001.pdf" in pdf.headers["content-disposition"]
