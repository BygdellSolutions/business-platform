"""Verify a restored (or any) application database, READ-ONLY, and say plainly whether it is fit to serve.

    VERIFY_DATABASE_URL=postgresql://<any role that can read>:...@<host>/<restored database> \\
    [VERIFY_APP_DATABASE_URL=postgresql://<the runtime application role>:...@<host>/<restored database>] \\
    python -m app.scripts.verify_restore [--manifest <dump>.manifest.json] [--alembic-check] [--fingerprint]

The report is one JSON object on stdout (`ok`, and one entry per check with a short, non-sensitive detail); the exit code is 0
only if every check passed, 1 if any failed, 2 for a configuration refusal. Nothing it prints holds a row of data, an email, a
hash, a token or a credential: counts, names of database objects and ids of PDF rows only.

It never writes. Its main connection is opened with `default_transaction_read_only` and `read_only=True`. The only connection
that may attempt a change is the OPTIONAL runtime-role one, and the single change it attempts (a CREATE TABLE, which must be
DENIED) is rolled back whatever happens.

Checks:
  schema     reachable; exactly one alembic revision; it equals this image's single head; optionally `alembic check` (no drift
             between the models and the schema);
  objects    every trigger and function the schema's guarantees rest on exists and is enabled (last-owner backstop, append-only
             security events, invoice and PDF immutability, the currency lock); every constraint is validated (so foreign keys
             and CHECKs hold); tenant tables keep their NOT NULL organization_id;
  data       row counts equal the manifest's (when given); otherwise the tenant core is not empty; the PDF rows are all intact;
  pdf        for every stored invoice PDF: bytes exist, their length equals `byte_size`, SHA-256(bytes) equals `sha256`, and they
             start with %PDF-;
  auth       presence and shape of users, credentials, sessions, setup tokens, invitations and security events, as counts and
             integrity only;
  roles      (with VERIFY_APP_DATABASE_URL) the runtime role is not a superuser or creator, owns nothing, has DML on every table
             and sequence (and only SELECT on alembic_version), cannot CREATE in the schema or the database, and a real CREATE TABLE
             is denied.

`--fingerprint` adds a content fingerprint (per-table counts and a hash over every row, plus sequence positions), identical for a
database and its faithful restore, so "the source did not change" and "the restore equals the source" are comparable facts.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg import errors, sql

from app.core import migration, pgtools

EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2

# What the schema's guarantees rest on (derived from the migrations; tests/test_verify_restore.py proves this list equals what a
# freshly migrated database really contains, so a new trigger or function cannot be forgotten).
REQUIRED_TRIGGERS = {
    ("audit_events", "trg_audit_events_append_only"),
    ("stock_movements", "trg_stock_movements_append_only"),
    ("invoice_lines", "trg_invoice_lines_immutability"),
    ("invoice_pdfs", "trg_invoice_pdfs_guard"),
    ("invoice_transactions", "trg_invoice_transactions_immutability"),
    ("invoice_vat_rows", "trg_invoice_vat_rows_immutability"),
    ("invoices", "trg_invoices_immutability"),
    ("organization_users", "trg_organization_users_owner_limit"),
    ("organization_users", "trg_organization_users_owner_required_delete"),
    ("organization_users", "trg_organization_users_owner_required_update"),
    ("security_events", "trg_security_events_append_only"),
    ("transactions", "trg_transactions_currency_immutable"),
}
DEFERRED_CONSTRAINT_TRIGGERS = {"trg_organization_users_owner_required_delete", "trg_organization_users_owner_required_update"}
REQUIRED_FUNCTIONS = {
    "audit_events_append_only",
    "stock_movements_append_only",
    "invoice_children_immutability",
    "invoice_pdfs_guard",
    "invoices_immutability",
    "organization_users_owner_limit",
    "organization_users_owner_required",
    "security_events_append_only",
    "transactions_currency_is_immutable",
}
TENANT_TABLES = {
    "audit_events", "custom_field_definitions", "custom_field_options", "custom_field_values", "customers", "horses", "invoice_counters", "invoice_lines",
    "invoice_pdfs", "invoice_transactions", "invoice_vat_rows", "invoices", "item_discounts", "items", "organization_creation_requests",
    "organization_invitations", "organization_users", "stock_movements", "transaction_lines", "transactions",
}  # `security_events.organization_id` is deliberately nullable (an event may be about no organization)
CORE_TABLES = ("organizations", "users", "organization_users")
HEX64 = "^[0-9a-f]{64}$"


class Report:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    @property
    def ok(self) -> bool:
        return all(check["ok"] for check in self.checks)


def _one(cursor, query: str, params=None):
    cursor.execute(query, params)
    return cursor.fetchone()[0]


def check_schema(cursor, report: Report) -> None:
    report.add("schema.reachable", _one(cursor, "SELECT 1") == 1)
    if not _one(cursor, "SELECT to_regclass('alembic_version') IS NOT NULL"):
        report.add("schema.single_revision", False, "alembic_version does not exist")
        return
    cursor.execute("SELECT version_num FROM alembic_version")
    revisions = [row[0] for row in cursor.fetchall()]
    report.add("schema.single_revision", len(revisions) == 1, f"{len(revisions)} revision row(s)")
    heads = migration.code_heads()
    report.add("schema.exact_head", len(heads) == 1 and revisions == list(heads), "equals the code's single head" if revisions == list(heads) else "does not equal the code's head")


def check_alembic(target: pgtools.Target, report: Report) -> None:
    """`alembic check` against the database (no drift between models and schema), run in a child process whose environment holds
    only what the models' import needs: the target as both URLs, in development mode."""
    environment = {key: value for key, value in os.environ.items() if key in {"PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "LANG"}}
    from sqlalchemy.engine import URL

    url = URL.create("postgresql+psycopg", username=target.user, password=target.password, host=target.host, port=target.port, database=target.database, query=dict(target.options)).render_as_string(hide_password=False)
    environment.update({"APP_ENV": "development", "AUTH_MODE": "dev", "DATABASE_URL": url, "MIGRATION_DATABASE_URL": url, "PYTHONPATH": str(migration.BACKEND_DIR)})
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(migration.BACKEND_DIR / "alembic.ini"), "check"],
        cwd=migration.BACKEND_DIR, env=environment, capture_output=True, text=True, timeout=300, check=False,
    )
    clean = result.returncode == 0 and "No new upgrade operations detected" in result.stdout + result.stderr
    report.add("schema.alembic_check", clean, "no drift between the models and the schema" if clean else pgtools.scrub((result.stderr or result.stdout).splitlines()[-1] if (result.stderr or result.stdout) else "failed", target.secrets(), 200))


def check_objects(cursor, report: Report) -> None:
    cursor.execute(
        "SELECT c.relname, t.tgname, t.tgenabled, t.tgdeferrable FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
        " JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND NOT t.tgisinternal"
    )
    present = {(table, name): (enabled, deferrable) for table, name, enabled, deferrable in cursor.fetchall()}
    missing = sorted(REQUIRED_TRIGGERS - present.keys())
    disabled = sorted(key for key in REQUIRED_TRIGGERS & present.keys() if present[key][0] not in ("O", "A"))
    not_deferred = sorted(name for (table, name), (_, deferrable) in present.items() if name in DEFERRED_CONSTRAINT_TRIGGERS and not deferrable)
    report.add("objects.triggers", not (missing or disabled or not_deferred), "all required triggers exist, are enabled and the last-owner backstop is deferred" if not (missing or disabled or not_deferred) else f"missing: {[m[1] for m in missing]}, disabled: {[d[1] for d in disabled]}, not deferred: {not_deferred}")
    cursor.execute("SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public'")
    functions = {row[0] for row in cursor.fetchall()}
    absent = sorted(REQUIRED_FUNCTIONS - functions)
    report.add("objects.functions", not absent, "all required functions exist" if not absent else f"missing: {absent}")
    cursor.execute("SELECT conrelid::regclass::text, conname FROM pg_constraint WHERE NOT convalidated AND connamespace = 'public'::regnamespace")
    unvalidated = [f"{table}.{name}" for table, name in cursor.fetchall()]
    report.add("objects.constraints_validated", not unvalidated, "every constraint (foreign keys, CHECKs) is validated" if not unvalidated else f"not validated: {unvalidated[:10]}")
    cursor.execute(
        "SELECT table_name FROM information_schema.columns WHERE table_schema = 'public' AND column_name = 'organization_id' AND is_nullable = 'NO'"
    )
    have = {row[0] for row in cursor.fetchall()}
    lacking = sorted(TENANT_TABLES - have)
    report.add("objects.tenant_columns", not lacking, "every tenant table keeps a NOT NULL organization_id" if not lacking else f"missing or nullable organization_id: {lacking}")


def table_counts(cursor) -> dict[str, int]:
    cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
    counts = {}
    for (table,) in cursor.fetchall():
        counts[table] = _one(cursor, sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
    return counts


def check_data(cursor, report: Report, manifest: dict | None) -> None:
    counts = table_counts(cursor)
    if manifest is not None:
        expected = manifest.get("table_counts", {})
        differences = sorted(name for name in set(expected) | set(counts) if expected.get(name) != counts.get(name))
        report.add("data.counts_match_manifest", not differences, f"{len(expected)} tables match the manifest exactly" if not differences else f"differ from the manifest: {differences[:10]}")
    empty_core = [name for name in CORE_TABLES if counts.get(name, 0) == 0]
    report.add("data.tenant_core_present", not empty_core, "organizations, users and memberships are present" if not empty_core else f"empty: {empty_core}")


def check_pdfs(connection: psycopg.Connection, report: Report) -> None:
    bad: list[str] = []
    total = 0
    if _one(connection.cursor(), "SELECT to_regclass('invoice_pdfs') IS NOT NULL"):
        with connection.cursor(name="verify_pdfs") as cursor:  # a server-side cursor: bytes are streamed, not all held at once
            cursor.itersize = 20
            cursor.execute("SELECT id, content, byte_size, sha256 FROM invoice_pdfs ORDER BY id")
            for identifier, content, byte_size, stored_hash in cursor:
                total += 1
                data = bytes(content) if content is not None else b""
                if not data or len(data) != byte_size or hashlib.sha256(data).hexdigest() != stored_hash or not data.startswith(b"%PDF-"):
                    bad.append(str(identifier))
    report.add("pdf.integrity", not bad, f"{total} stored PDF(s): content present, length, SHA-256 and signature all match" if not bad else f"{len(bad)} of {total} damaged: {bad[:10]}")


def check_auth(cursor, report: Report) -> None:
    problems: list[str] = []
    counts = {name: _one(cursor, sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(name))) for name in ("users", "user_credentials", "auth_sessions", "user_setup_tokens", "organization_invitations", "security_events")}
    shape = {
        "credentials without an argon2id hash": "SELECT count(*) FROM user_credentials WHERE left(password_hash, 10) <> '$argon2id$'",
        "credentials without a user": "SELECT count(*) FROM user_credentials c LEFT JOIN users u ON u.id = c.user_id WHERE u.id IS NULL",
        "sessions with a malformed token or CSRF hash": f"SELECT count(*) FROM auth_sessions WHERE token_hash !~ '{HEX64}' OR csrf_hash !~ '{HEX64}'",
        "sessions without a user": "SELECT count(*) FROM auth_sessions s LEFT JOIN users u ON u.id = s.user_id WHERE u.id IS NULL",
        "setup tokens with a malformed hash": f"SELECT count(*) FROM user_setup_tokens WHERE token_hash !~ '{HEX64}'",
        "invitations with a malformed hash": f"SELECT count(*) FROM organization_invitations WHERE token_hash !~ '{HEX64}'",
        "invitations accepted and revoked": "SELECT count(*) FROM organization_invitations WHERE accepted_at IS NOT NULL AND revoked_at IS NOT NULL",
        "security events with a malformed identifier hash": f"SELECT count(*) FROM security_events WHERE identifier_hash IS NOT NULL AND identifier_hash !~ '{HEX64}'",
    }
    for label, query in shape.items():
        found = _one(cursor, query)
        if found:
            problems.append(f"{found} {label}")
    summary = ", ".join(f"{name}={value}" for name, value in counts.items())
    report.add("auth.coherent", not problems, f"present and coherent ({summary})" if not problems else f"{'; '.join(problems)} ({summary})")


def check_roles(app_target: pgtools.Target, report: Report) -> None:
    problems: list[str] = []
    with psycopg.connect(**{"connect_timeout": 15, **app_target.conn_kwargs()}) as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        if any(cursor.fetchone()):
            problems.append("the runtime role has a dangerous attribute (superuser, createdb, createrole, replication or bypassrls)")
        if _one(cursor, "SELECT has_schema_privilege(current_user, 'public', 'CREATE') OR has_database_privilege(current_user, current_database(), 'CREATE')"):
            problems.append("the runtime role can CREATE in the schema or the database")
        if _one(cursor, "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner WHERE r.rolname = current_user AND c.relnamespace = 'public'::regnamespace"):
            problems.append("the runtime role owns tables or sequences")
        cursor.execute(
            "SELECT c.relname, has_table_privilege(current_user, c.oid, 'SELECT'), has_table_privilege(current_user, c.oid, 'INSERT'),"
            " has_table_privilege(current_user, c.oid, 'UPDATE'), has_table_privilege(current_user, c.oid, 'DELETE'),"
            " has_table_privilege(current_user, c.oid, 'TRUNCATE') FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')"
        )
        for table, select, insert, update, delete, truncate in cursor.fetchall():
            if table == "alembic_version":
                if not select or insert or update or delete:
                    problems.append("alembic_version must be readable and not writable by the runtime role")
            elif not (select and insert and update and delete):
                problems.append(f"missing DML on {table}")
            if truncate:
                problems.append(f"{table}: the runtime role can TRUNCATE")
        cursor.execute("SELECT c.relname FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'S' AND NOT has_sequence_privilege(current_user, c.oid, 'USAGE')")
        for (sequence,) in cursor.fetchall():
            problems.append(f"no USAGE on sequence {sequence}")
        _one(cursor, "SELECT count(*) FROM organizations")  # a real read
        connection.rollback()
        try:
            cursor.execute("CREATE TABLE verify_restore_probe (x integer)")
            problems.append("a CREATE TABLE by the runtime role SUCCEEDED")
        except errors.InsufficientPrivilege:
            pass
        finally:
            connection.rollback()  # whatever happened, nothing is kept
    report.add("roles.runtime_role", not problems, "DML yes, DDL denied, nothing owned, no dangerous attribute" if not problems else "; ".join(problems[:6]))


def fingerprint(cursor) -> str:
    cursor.execute("SET TIME ZONE 'UTC'")
    cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
    digest = hashlib.sha256()
    for (table,) in cursor.fetchall():
        identifier = sql.Identifier(table)
        cursor.execute(sql.SQL("SELECT count(*), COALESCE(md5(string_agg(md5(t::text), '' ORDER BY md5(t::text))), '') FROM {} t").format(identifier))
        count, rows_hash = cursor.fetchone()
        digest.update(f"{table}:{count}:{rows_hash}\n".encode())
    cursor.execute("SELECT sequencename, COALESCE(last_value, 0) FROM pg_sequences WHERE schemaname = 'public' ORDER BY sequencename")
    for name, value in cursor.fetchall():
        digest.update(f"seq:{name}:{value}\n".encode())
    return digest.hexdigest()


def verify(target: pgtools.Target, app_target: pgtools.Target | None, manifest: dict | None, *, alembic: bool = False, with_fingerprint: bool = False) -> tuple[Report, str | None]:
    report = Report()
    digest = None
    with psycopg.connect(**{"connect_timeout": 15, **target.conn_kwargs()}, options="-c default_transaction_read_only=on") as connection:
        connection.read_only = True
        cursor = connection.cursor()
        check_schema(cursor, report)
        if any(c["name"] == "schema.single_revision" and c["ok"] for c in report.checks):
            check_objects(cursor, report)
            check_data(cursor, report, manifest)
            check_pdfs(connection, report)
            check_auth(cursor, report)
            if with_fingerprint:
                digest = fingerprint(cursor)
        connection.rollback()
    if alembic:
        check_alembic(target, report)
    if app_target is not None:
        check_roles(app_target, report)
    return report, digest


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a restored database, read-only.")
    parser.add_argument("--manifest", type=Path, help="compare row counts with this backup manifest")
    parser.add_argument("--alembic-check", action="store_true", help="also run `alembic check` (needs the models importable: the backend image or checkout)")
    parser.add_argument("--fingerprint", action="store_true", help="also print a content fingerprint of the database")
    args = parser.parse_args(argv)
    secrets: list[str] = []
    try:
        target = pgtools.target_from_env("VERIFY_DATABASE_URL")
        secrets = target.secrets()
        app_target = pgtools.target_from_env("VERIFY_APP_DATABASE_URL") if os.environ.get("VERIFY_APP_DATABASE_URL", "").strip() else None
        if app_target is not None:
            secrets += app_target.secrets()
            if app_target.identity() != target.identity():
                raise pgtools.ToolRefused("VERIFY_APP_DATABASE_URL must point at the same database as VERIFY_DATABASE_URL")
        manifest = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest else None
        report, digest = verify(target, app_target, manifest, alembic=args.alembic_check, with_fingerprint=args.fingerprint)
    except pgtools.ToolRefused as refusal:
        print(json.dumps({"ok": False, "refused": pgtools.scrub(str(refusal), secrets)}))
        return EXIT_REFUSED
    except Exception as error:  # noqa: BLE001  (an unreadable database is a failed verification, not a crash with a traceback)
        print(json.dumps({"ok": False, "checks": [], "error": f"{type(error).__name__}: {pgtools.scrub(str(error), secrets, 200)}"}))
        return EXIT_FAILED
    output = {"ok": report.ok, "checks": report.checks}
    if digest:
        output["fingerprint"] = digest
    print(json.dumps(output, indent=2))
    return EXIT_OK if report.ok else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(run())
