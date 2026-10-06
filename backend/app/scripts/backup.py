"""Back up the application database: a PostgreSQL custom-format dump and a manifest that lets anyone check it is unchanged.

    BACKUP_DATABASE_URL=postgresql://<backup role>:...@<host>/<database> \\
    python -m app.scripts.backup --output /backups/bp-20261006T030000Z.dump --label production [--git-revision <sha>]

What it does, in order:

  1. reads the database from BACKUP_DATABASE_URL ONLY (no fallback, no `.env`, never DATABASE_URL), and refuses an output path
     that exists (neither the dump nor its manifest is ever overwritten);
  2. opens one REPEATABLE READ read-only transaction and exports its snapshot, so the row counts and revision in the manifest
     describe exactly the data the dump contains, even while the application writes;
  3. runs `pg_dump --format=custom --no-owner --no-acl --snapshot=...` (the whole database: every table including auth records
     and `invoice_pdfs`, triggers, functions, constraints, indexes, sequences; ownership and ACLs are deliberately NOT in the dump,
     see docs/backup-restore.md "Roles") into `<output>.partial`, with the password in PGPASSWORD, never on the command line;
  4. checks the dump is readable (`pg_restore --list`), hashes it, writes `<output>.manifest.json` the same way, and only then
     moves both into place atomically (`link`, which cannot replace an existing file);
  5. on any failure removes every partial file, prints a scrubbed reason and exits non-zero. A failed run leaves no final backup.

It uploads nothing: the artifact is local and ready to be copied off-host (docs/backup-restore.md, "Storage"). Exit codes: 0 done,
1 failed, 2 refused (configuration or an existing output).
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql

from app.core import pgtools
from app.core.logging_config import configure_logging, log
from app.core.migration import code_heads

EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2
FORMAT = "bp-backup/1"
DUMP_OPTIONS = ["--format=custom", "--no-owner", "--no-acl", "--no-password"]
LABEL = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
MANIFEST_SUFFIX = ".manifest.json"
PARTIAL_SUFFIX = ".partial"


def manifest_path(dump: Path) -> Path:
    return dump.with_name(dump.name + MANIFEST_SUFFIX)


def sha256_of(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _fsync(path: Path) -> None:
    with path.open("r+b") as handle:  # (a read-only handle cannot be fsynced on Windows)
        os.fsync(handle.fileno())


def _publish(partial: Path, final: Path) -> None:
    """Move `partial` to `final` without ever replacing an existing file."""
    try:
        os.link(partial, final)  # fails if `final` exists: atomic "create, never overwrite"
    except FileExistsError:
        raise pgtools.ToolRefused(f"{final.name} already exists; a backup is never overwritten") from None
    except OSError:  # a file system without hard links: fall back to a check followed by a rename
        if final.exists():
            raise pgtools.ToolRefused(f"{final.name} already exists; a backup is never overwritten") from None
        os.rename(partial, final)
        return
    partial.unlink()


def collect(connection: psycopg.Connection) -> dict:
    """Inside the snapshot transaction: what the database holds, as counts and sizes only (no data)."""
    cursor = connection.cursor()
    cursor.execute("SELECT current_setting('server_version')")
    server_version = cursor.fetchone()[0]
    revisions: list[str] = []
    cursor.execute("SELECT to_regclass('alembic_version') IS NOT NULL")
    if cursor.fetchone()[0]:
        cursor.execute("SELECT version_num FROM alembic_version ORDER BY version_num")
        revisions = [row[0] for row in cursor.fetchall()]
    cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
    counts: dict[str, int] = {}
    for (table,) in cursor.fetchall():
        cursor.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
        counts[table] = cursor.fetchone()[0]
    pdfs = {"count": 0, "bytes": 0}
    if "invoice_pdfs" in counts:
        cursor.execute("SELECT count(*), COALESCE(sum(byte_size), 0)::bigint FROM invoice_pdfs")
        pdfs = dict(zip(("count", "bytes"), cursor.fetchone()))
    cursor.execute("SELECT pg_database_size(current_database())")
    return {"server_version": server_version, "alembic_revisions": revisions, "table_counts": counts, "invoice_pdfs": pdfs, "database_bytes": cursor.fetchone()[0]}


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up the application database (a custom-format dump plus a manifest).")
    parser.add_argument("--output", required=True, type=Path, help="the dump file to create (must not exist)")
    parser.add_argument("--label", required=True, help="a non-sensitive environment label recorded in the manifest, e.g. production")
    parser.add_argument("--git-revision", default=os.environ.get("APP_GIT_REVISION", ""), help="the application revision, if known")
    args = parser.parse_args(argv)
    configure_logging("backup")
    started = time.monotonic()
    output: Path = args.output
    partial, manifest, manifest_partial = output.with_name(output.name + PARTIAL_SUFFIX), manifest_path(output), manifest_path(output).with_name(manifest_path(output).name + PARTIAL_SUFFIX)
    secrets: list[str] = []
    created: list[Path] = []
    try:
        source = pgtools.target_from_env("BACKUP_DATABASE_URL")
        secrets = source.secrets()
        if not LABEL.fullmatch(args.label):
            raise pgtools.ToolRefused("--label must be 1 to 40 characters of letters, digits, dot, underscore or hyphen")
        if not output.parent.is_dir():
            raise pgtools.ToolRefused("the output directory does not exist")
        for existing in (output, manifest, partial, manifest_partial):
            if existing.exists():
                raise pgtools.ToolRefused(f"{existing.name} already exists; a backup is never overwritten")
        dump_command = pgtools.client_command(pgtools.DUMP_ENV, "pg_dump")
        restore_command = pgtools.client_command(pgtools.RESTORE_ENV, "pg_restore")
        client_major = pgtools.client_major_version(dump_command)
        if client_major is None:
            raise pgtools.ToolRefused("could not determine the pg_dump version")
        log(logging.INFO, "backup_started", label=args.label, file=output.name)

        connection_arguments = {"connect_timeout": 15, **source.conn_kwargs(), "options": "-c default_transaction_read_only=on"}
        with psycopg.connect(**connection_arguments) as connection:
            connection.read_only = True
            connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            info = collect(connection)
            if info["server_version"].split(".")[0].isdigit() and int(info["server_version"].split(".")[0]) > client_major:
                raise pgtools.ToolRefused(f"the pg_dump client (major {client_major}) is older than the server ({info['server_version']})")
            snapshot = connection.execute("SELECT pg_export_snapshot()").fetchone()[0]
            partial.touch(exist_ok=False)
            created.append(partial)
            dumped = pgtools.run_client(dump_command, [*DUMP_OPTIONS, f"--snapshot={snapshot}", f"--file={partial}"], source)
            if dumped.returncode != 0:
                raise RuntimeError("pg_dump failed: " + pgtools.scrub(dumped.stderr, secrets))
            connection.rollback()  # the snapshot is no longer needed

        listing = pgtools.run_client(restore_command, ["--list", str(partial)], None, timeout=600)
        entries = [line for line in listing.stdout.splitlines() if line and not line.startswith(";")]
        if listing.returncode != 0 or not entries:
            raise RuntimeError("the dump is not readable by pg_restore: " + pgtools.scrub(listing.stderr, secrets))
        digest, size = sha256_of(partial)
        if size == 0:
            raise RuntimeError("the dump is empty")
        _fsync(partial)
        duration = round(time.monotonic() - started, 2)
        document = {
            "format": FORMAT,
            "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "label": args.label,
            "git_revision": args.git_revision or None,
            "source": {"database": source.database},
            "alembic_revisions": info["alembic_revisions"],
            "code_alembic_heads": list(code_heads()),
            "postgres_server_version": info["server_version"],
            "pg_dump_major_version": client_major,
            "dump_options": DUMP_OPTIONS[:-1],
            "toc_entries": len(entries),
            "dump": {"file": output.name, "bytes": size, "sha256": digest},
            "database_bytes": info["database_bytes"],
            "invoice_pdfs": info["invoice_pdfs"],
            "table_counts": info["table_counts"],
            "duration_seconds": duration,
        }
        manifest_partial.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        created.append(manifest_partial)
        _fsync(manifest_partial)
        _publish(partial, output)
        created.remove(partial)
        created.append(output)  # published: if the manifest cannot be, the dump alone must not remain as a "final" backup
        _publish(manifest_partial, manifest)
        created.remove(manifest_partial)
        created.clear()
        log(logging.INFO, "backup_succeeded", file=output.name, bytes=size, sha256=digest, seconds=duration, tables=len(info["table_counts"]), pdf_bytes=info["invoice_pdfs"]["bytes"])
        return EXIT_OK
    except pgtools.ToolRefused as refusal:
        _cleanup(created, output, manifest)
        log(logging.ERROR, "backup_refused", reason=pgtools.scrub(str(refusal), secrets))
        return EXIT_REFUSED
    except Exception as error:  # noqa: BLE001  (every failure is reported, scrubbed, and leaves no final backup)
        _cleanup(created, output, manifest)
        log(logging.ERROR, "backup_failed", error_type=type(error).__name__, reason=pgtools.scrub(str(error), secrets))
        return EXIT_FAILED


def _cleanup(created: list[Path], output: Path, manifest: Path) -> None:
    """Remove what THIS run created (never a file that existed before it). A final dump or manifest only exists if `_publish` ran."""
    for path in created:
        path.unlink(missing_ok=True)
    created.clear()


if __name__ == "__main__":
    sys.exit(run())
