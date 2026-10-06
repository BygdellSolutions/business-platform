"""Measure the database's size, read-only, to watch the "PDFs in PostgreSQL" decision (docs/backup-restore.md, "Measuring").

    STATS_DATABASE_URL=postgresql://<any role that can read>:...@<host>/<database> python -m app.scripts.db_stats

Prints one JSON object: database bytes, the PDF table's row count and TOTAL stored bytes (`sum(byte_size)`, the sum of the PDFs
themselves), its on-disk size, the largest tables, and the review signals. It reads sizes and counts only: no row content, no
credential, nothing that identifies a tenant. Backup size and duration are in each backup's manifest; restore duration is
recorded from the drill (docs/backup-restore.md, "Drill log").

Review signals (decided with the project owner for the first deployment): total PDF bytes of about 2 GB, or a backup that takes
about 15 minutes. Crossing one is a reason to review the storage and backup design, not an error; the exit code is 0 either way.
"""

import json
import sys

import psycopg

from app.core import pgtools

PDF_REVIEW_BYTES = 2 * 1024**3
BACKUP_REVIEW_SECONDS = 15 * 60


def measure(target: pgtools.Target) -> dict:
    with psycopg.connect(**{"connect_timeout": 15, **target.conn_kwargs()}, options="-c default_transaction_read_only=on") as connection:
        connection.read_only = True
        cursor = connection.cursor()
        cursor.execute("SELECT pg_database_size(current_database())")
        database_bytes = cursor.fetchone()[0]
        pdfs = {"count": 0, "stored_bytes": 0, "table_bytes": 0}
        cursor.execute("SELECT to_regclass('invoice_pdfs') IS NOT NULL")
        if cursor.fetchone()[0]:
            cursor.execute("SELECT count(*), COALESCE(sum(byte_size), 0)::bigint, pg_total_relation_size('invoice_pdfs') FROM invoice_pdfs")
            pdfs = dict(zip(("count", "stored_bytes", "table_bytes"), cursor.fetchone()))
        cursor.execute(
            "SELECT c.relname, pg_total_relation_size(c.oid) FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r'"
            " ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 5"
        )
        largest = [{"table": name, "bytes": size} for name, size in cursor.fetchall()]
        connection.rollback()
    return {
        "database_bytes": database_bytes,
        "invoice_pdfs": pdfs,
        "largest_tables": largest,
        "review": {
            "pdf_bytes_threshold": PDF_REVIEW_BYTES,
            "pdf_bytes_reached": pdfs["stored_bytes"] >= PDF_REVIEW_BYTES,
            "backup_seconds_threshold": BACKUP_REVIEW_SECONDS,
        },
    }


def main() -> int:
    try:
        target = pgtools.target_from_env("STATS_DATABASE_URL")
    except pgtools.ToolRefused as refusal:
        print(json.dumps({"refused": str(refusal)}))
        return 2
    try:
        print(json.dumps(measure(target), indent=2))
    except psycopg.Error as error:
        print(json.dumps({"failed": type(error).__name__}))  # never the message: it can quote the host
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
