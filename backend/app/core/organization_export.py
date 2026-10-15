"""A complete copy of one organization's data, as a ZIP the owner can keep (portability, and the safety net before a deletion).

What is in it: one JSON Lines file per tenant table (every row of THIS organization, nothing else), the members (name, email and
role, never a credential), every stored invoice, credit note and receipt PDF as a file, a manifest with the row counts, and a plain
README. What is deliberately not in it: password hashes, sessions, security events (the platform's own log), invitation token hashes
and the retry records of organization creation.

Tenant safety: every statement filters on `organization_id` (the id comes from the authenticated request's membership, never from
the body), joins use BOTH the organization id and the row id, and a test checks the export of one organization against another
that holds a record with the same name. The table list is the deletion list, and a test fails when a tenant table is neither
exported nor deliberately left out, so a new table cannot be forgotten.

Streaming: the ZIP is produced chunk by chunk (nothing is built in memory), from one read-only transaction at a repeatable-read
snapshot, so the copy is consistent even while people keep working. Only when the whole archive has been produced is the
organization marked as exported.
"""

import io
import json
import re
import unicodedata
import uuid
import zipfile
from collections.abc import Iterator
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import metrics
from app.core.db import SessionLocal
from app.core.organization_deletion import TABLES_IN_DELETE_ORDER

FORMAT_VERSION = 1
BATCH = 500
# A long export must not be cut short by the ordinary limits, but it must still end: a client that stops reading for ten
# minutes, or a statement that runs for twenty, ends the export (the person starts again).
EXPORT_STATEMENT_TIMEOUT_MS = 20 * 60 * 1000
EXPORT_IDLE_TIMEOUT_MS = 10 * 60 * 1000

# Tenant tables whose rows are NOT exported, and why. Every other tenant table is exported.
LEFT_OUT = {
    "organization_creation_requests": "retry records of creating the organization (not business data)",
}
# Columns left out of a table's rows.
COLUMNS_LEFT_OUT = {
    "organization_invitations": ("token_hash",),  # a hash of a credential
    "invoice_pdfs": ("content",),  # the bytes are in the archive as files
    "credit_note_pdfs": ("content",),
}
EXPORTED_TABLES = tuple(table for table in reversed(TABLES_IN_DELETE_ORDER) if table not in LEFT_OUT)

README = """This is a complete copy of the data of one organization, taken from business-platform.

data/<table>.jsonl      one JSON object per line: every row of that table that belongs to the organization
members.jsonl           who has access (name, email, role); never a password or any other credential
invoices/*.pdf          the stored PDF of every issued invoice (file names: invoice number, template version, short id)
credit-notes/*.pdf      the stored PDF of every credit note
manifest.json           when the copy was taken, the format version and the number of rows of every file; the counts
                        ending in without_stored_pdf say how many documents have no PDF in this copy (their data is in the
                        JSON files; open them in the application and download the PDF)

The copy is a snapshot taken at one moment. Amounts are text (never rounded), dates are ISO dates and times are UTC.
Keep it somewhere safe: it contains your customers' personal data.
"""


class _Sink(io.RawIOBase):
    """Where the ZIP writer puts bytes; they are handed to the response as they arrive. Not seekable, so the archive uses
    data descriptors and nothing has to be rewound."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._position = 0

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def write(self, data) -> int:  # type: ignore[override]
        self._chunks.append(bytes(data))
        self._position += len(data)
        return len(data)

    def tell(self) -> int:
        return self._position

    def drain(self) -> bytes:
        data = b"".join(self._chunks)
        self._chunks.clear()
        return data


def archive_name(organization_name: str, now: datetime) -> str:
    """export-<name>-<date>.zip with a plain ASCII name (the file name is also shown in a header)."""
    ascii_name = unicodedata.normalize("NFKD", organization_name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:40] or "organization"
    return f"export-{slug}-{now.date().isoformat()}.zip"


def _safe(part: object) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(part)).strip("-")[:60] or "document"


def _rows(db: Session, table: str, organization_id: uuid.UUID) -> Iterator[str]:
    """One JSON text per row of `table` for this organization. The table name comes from the fixed list above, never from input."""
    dropped = list(COLUMNS_LEFT_OUT.get(table, ()))
    result = db.execute(
        text(f"SELECT (to_jsonb(t) - CAST(:dropped AS text[]))::text FROM {table} t WHERE t.organization_id = :org").execution_options(
            stream_results=True, yield_per=BATCH
        ),
        {"org": organization_id, "dropped": dropped},
    )
    for (value,) in result:
        yield value


# The three seams a test replaces to run the export inside its own rollback-only transaction (a session of our own is the
# production case: a fresh connection whose first statement makes it a consistent, read-only snapshot).
def _open_session():
    return SessionLocal()


def _begin_snapshot(db: Session) -> None:
    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))


def _end_snapshot(db: Session) -> None:
    db.rollback()  # read-only: nothing to keep


def stream_export(*, organization_id: uuid.UUID, organization_name: str, now: datetime) -> Iterator[bytes]:
    """The ZIP, in chunks. Opens its own session: it outlives the request's dependencies."""
    sink = _Sink()
    archive = zipfile.ZipFile(sink, "w")
    counts: dict[str, int] = {}

    def write(name: str, chunks: Iterator[bytes], *, compress: bool = True) -> Iterator[bytes]:
        info = zipfile.ZipInfo(name, date_time=now.timetuple()[:6])
        info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        with archive.open(info, "w", force_zip64=True) as entry:
            for chunk in chunks:
                entry.write(chunk)
                data = sink.drain()
                if data:
                    yield data
        data = sink.drain()
        if data:
            yield data

    def lines(table: str, rows: Iterator[str]) -> Iterator[bytes]:
        counts[table] = 0
        for row in rows:
            counts[table] += 1
            yield (row + "\n").encode("utf-8")

    with _open_session() as db:
        _begin_snapshot(db)  # the FIRST statement of the transaction
        db.execute(text(f"SET LOCAL statement_timeout = {EXPORT_STATEMENT_TIMEOUT_MS}"))
        db.execute(text(f"SET LOCAL idle_in_transaction_session_timeout = {EXPORT_IDLE_TIMEOUT_MS}"))

        yield from write("README.txt", iter([README.encode("utf-8")]))
        for table in EXPORTED_TABLES:
            yield from write(f"data/{table}.jsonl", lines(table, _rows(db, table, organization_id)))

        members = db.execute(
            text(
                "SELECT jsonb_build_object('email', u.email, 'name', u.name, 'role', ou.role, 'member_since', ou.created_at)::text "
                "FROM organization_users ou JOIN users u ON u.id = ou.user_id WHERE ou.organization_id = :org ORDER BY u.email"
            ),
            {"org": organization_id},
        )
        yield from write("members.jsonl", lines("members", (row for (row,) in members)))

        for folder, table, parent, key in (("invoices", "invoice_pdfs", "invoices", "invoice_id"), ("credit-notes", "credit_note_pdfs", "credit_notes", "credit_note_id")):
            counts[f"{folder}/pdf"] = 0
            label = "COALESCE(p.number_text, p.number::text)" if parent == "invoices" else "p.number::text"
            documents = db.execute(
                text(
                    f"SELECT {label}, f.template_version, f.id::text, f.content FROM {table} f "
                    f"JOIN {parent} p ON p.organization_id = f.organization_id AND p.id = f.{key} "
                    "WHERE f.organization_id = :org ORDER BY f.created_at, f.id"
                ).execution_options(stream_results=True, yield_per=20),
                {"org": organization_id},
            )
            for number, version, pdf_id, content in documents:
                counts[f"{folder}/pdf"] += 1
                name = f"{folder}/{_safe(number)}-v{version}-{pdf_id[:8]}.pdf"
                yield from write(name, iter([bytes(content)]), compress=False)

        counts["invoices/without_stored_pdf"] = db.scalar(
            text(
                "SELECT count(*) FROM invoices i WHERE i.organization_id = :org AND i.status = 'issued' AND NOT EXISTS "
                "(SELECT 1 FROM invoice_pdfs p WHERE p.organization_id = i.organization_id AND p.invoice_id = i.id)"
            ),
            {"org": organization_id},
        )
        counts["credit-notes/without_stored_pdf"] = db.scalar(
            text(
                "SELECT count(*) FROM credit_notes c WHERE c.organization_id = :org AND NOT EXISTS "
                "(SELECT 1 FROM credit_note_pdfs p WHERE p.organization_id = c.organization_id AND p.credit_note_id = c.id)"
            ),
            {"org": organization_id},
        )
        manifest = {
            "format_version": FORMAT_VERSION,
            "organization_id": str(organization_id),
            "organization_name": organization_name,
            "taken_at": now.isoformat(),
            "rows": counts,
            "left_out": LEFT_OUT,
        }
        yield from write("manifest.json", iter([json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")]))
        _end_snapshot(db)

    archive.close()
    data = sink.drain()
    if data:
        yield data

    # Only an archive that was produced to the very end counts as "a copy was made".
    with _open_session() as db:
        db.execute(text("UPDATE organizations SET last_export_at = :now WHERE id = :org"), {"now": now, "org": organization_id})
        db.commit()
    metrics.count("organization_export_completed")
