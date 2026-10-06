"""Restore a backup into a SEPARATE, scratch database, and make it usable by the restricted runtime role.

    RESTORE_DATABASE_URL=postgresql://<owner role>:...@<host>/<name>_restore_<tag> \\
    RUNTIME_DB_ROLE=<the application role of THIS environment> \\
    python -m app.scripts.restore --dump /backups/bp-....dump [--manifest <dump>.manifest.json] [--reset-target]

It restores only into a database that satisfies every guard below, never touches the source, and is not an in-place restore:
the normal recovery path is "restore into a new database, verify it (`python -m app.scripts.verify_restore`), then point the
application's DATABASE_URL at it". See docs/backup-restore.md.

Guards (all refuse with exit 2 before anything is changed):
  * the target comes from RESTORE_DATABASE_URL ONLY; there is no fallback to DATABASE_URL or anything else;
  * the target database NAME must end in `_restore` or `_restore_<tag>` (lowercase letters, digits, underscore): a scratch
    database is something you named on purpose. A name that is not, such as the development or production database, is refused
    whatever the host is;
  * the target must not be the database the manifest says the dump came from, nor the database of any URL in this environment
    (DATABASE_URL, MIGRATION_DATABASE_URL, BACKUP_DATABASE_URL): same host, port and database name, whatever the credentials;
  * the dump must match its manifest (size and SHA-256): a changed or truncated file is refused;
  * the connecting role must OWN the target database and must not be a superuser, and must not be the runtime role: objects are
    created owned by that role (the migration/owner role of the target environment), never by the application role, and the
    application role is never made powerful to make a restore work;
  * a target that already holds tables, sequences or functions is refused unless --reset-target (which drops and recreates only
    the target's `public` schema).

Ownership and privileges (the model, docs/backup-restore.md "Roles"): the dump contains neither. `pg_restore --no-owner --no-acl`
creates everything as the connecting owner role, then the same grant reconciliation the migration job runs gives the runtime
role DML on tables and sequences, nothing else, and revokes its write access to `alembic_version`. The result is identical
whether the target environment's role names equal the source's or differ, and no password is ever in a dump.

Exit codes: 0 restored, 1 failed (pg_restore error: the single transaction left the target empty), 2 refused.
"""

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

import psycopg

from app.core import migration, pgtools
from app.core.logging_config import configure_logging, log
from app.scripts.backup import FORMAT, MANIFEST_SUFFIX, sha256_of

EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2
NAME_GUARD = re.compile(r"^[a-z][a-z0-9_]*_restore(_[a-z0-9]+)?$")
KNOWN_SOURCE_VARIABLES = ("DATABASE_URL", "MIGRATION_DATABASE_URL", "BACKUP_DATABASE_URL")
RESTORE_OPTIONS = ["--no-owner", "--no-acl", "--single-transaction", "--exit-on-error", "--no-password"]

NOT_EMPTY = """
SELECT (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f'))
     + (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public')
"""


def load_manifest(dump: Path, manifest: Path) -> dict:
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise pgtools.ToolRefused("the manifest is missing or is not valid JSON: a restore needs the manifest that was written with the dump") from None
    if document.get("format") != FORMAT:
        raise pgtools.ToolRefused("the manifest is not a bp-backup/1 manifest")
    if not dump.is_file():
        raise pgtools.ToolRefused("the dump file does not exist")
    digest, size = sha256_of(dump)
    expected = document.get("dump", {})
    if size != expected.get("bytes") or digest != expected.get("sha256"):
        raise pgtools.ToolRefused("the dump does not match its manifest (size or SHA-256): it has changed or is incomplete")
    return document


def target_problems(target: pgtools.Target, manifest: dict, runtime_role: str | None, environ: dict[str, str]) -> list[str]:
    """Every reason this target may not be restored into, decided before connecting."""
    problems: list[str] = []
    if not NAME_GUARD.fullmatch(target.database):
        problems.append("the target database name must end in _restore or _restore_<tag> (lowercase letters, digits, underscore): a scratch database is named on purpose")
    if target.database == manifest.get("source", {}).get("database"):
        problems.append("the target database has the same name as the database the dump came from")
    for variable in KNOWN_SOURCE_VARIABLES:
        raw = environ.get(variable, "").strip()
        if not raw:
            continue
        try:
            known = pgtools.parse_url(raw, variable)
        except pgtools.ToolRefused:
            continue
        if known.identity() == target.identity():
            problems.append(f"the target is the same database as {variable}: that is a source, not a scratch database")
    if runtime_role and runtime_role == target.user:
        problems.append("the restore must not connect as the runtime role (RUNTIME_DB_ROLE): that role never owns or creates anything")
    return problems


def run(argv: list[str] | None = None) -> int:
    import os

    parser = argparse.ArgumentParser(description="Restore a backup into a separate scratch database.")
    parser.add_argument("--dump", required=True, type=Path)
    parser.add_argument("--manifest", type=Path, help="default: <dump>.manifest.json")
    parser.add_argument("--reset-target", action="store_true", help="allow a target that is not empty: drop and recreate ITS public schema first")
    parser.add_argument("--skip-runtime-grants", action="store_true", help="restore only; do not grant the runtime role (verification of the dump itself)")
    args = parser.parse_args(argv)
    configure_logging("restore")
    started = time.monotonic()
    secrets: list[str] = []
    try:
        target = pgtools.target_from_env("RESTORE_DATABASE_URL")
        secrets = target.secrets()
        runtime_role = os.environ.get("RUNTIME_DB_ROLE", "").strip() or None
        if runtime_role is None and not args.skip_runtime_grants:
            raise pgtools.ToolRefused("RUNTIME_DB_ROLE is required (the role the application will run as); use --skip-runtime-grants only to restore without making the database usable")
        manifest_file = args.manifest or args.dump.with_name(args.dump.name + MANIFEST_SUFFIX)
        manifest = load_manifest(args.dump, manifest_file)
        problems = target_problems(target, manifest, runtime_role, dict(os.environ))
        if problems:
            raise pgtools.ToolRefused("; ".join(problems))
        restore_command = pgtools.client_command(pgtools.RESTORE_ENV, "pg_restore")

        with psycopg.connect(**{"connect_timeout": 15, **target.conn_kwargs()}) as connection:
            owner_problem = connection.execute(
                "SELECT CASE WHEN r.rolsuper THEN 'the restoring role is a superuser: objects would not be owned by the owner role'"
                " WHEN d.datdba <> r.oid THEN 'the restoring role does not own the target database' END"
                " FROM pg_roles r, pg_database d WHERE r.rolname = current_user AND d.datname = current_database()"
            ).fetchone()[0]
            if owner_problem:
                raise pgtools.ToolRefused(owner_problem)
            if runtime_role and not connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (runtime_role,)).fetchone():
                raise pgtools.ToolRefused("the runtime role does not exist in the target environment (create the roles first: python -m app.scripts.bootstrap_roles)")
            present = connection.execute(NOT_EMPTY).fetchone()[0]
            if present and not args.reset_target:
                raise pgtools.ToolRefused("the target database is not empty; restore into a new database, or pass --reset-target to drop and recreate its public schema")
            if present:
                connection.execute("DROP SCHEMA public CASCADE")
                connection.execute("CREATE SCHEMA public")
                connection.commit()
                log(logging.WARNING, "target_reset", database=target.database)
            connection.commit()

        log(logging.INFO, "restore_started", database=target.database, dump=args.dump.name, dump_bytes=manifest["dump"]["bytes"])
        restored = pgtools.run_client(restore_command, [*RESTORE_OPTIONS, f"--dbname={target.database}", str(args.dump)], target)
        if restored.returncode != 0:
            raise RuntimeError("pg_restore failed: " + pgtools.scrub(restored.stderr, secrets))

        engine = migration.migration_engine(psycopg_url(target))
        try:
            with engine.connect() as connection:
                if runtime_role and not args.skip_runtime_grants:
                    migration.reconcile_runtime_grants(connection, runtime_role)
                    connection.commit()
        finally:
            engine.dispose()
        # Fresh planner statistics: a restore carries none, and ANALYZE cannot run inside a transaction block.
        with psycopg.connect(**{"connect_timeout": 15, **target.conn_kwargs()}, autocommit=True) as connection:
            connection.execute("ANALYZE")
        log(logging.INFO, "restore_succeeded", database=target.database, seconds=round(time.monotonic() - started, 2), runtime_grants=not args.skip_runtime_grants)
        return EXIT_OK
    except pgtools.ToolRefused as refusal:
        log(logging.ERROR, "restore_refused", reason=pgtools.scrub(str(refusal), secrets))
        return EXIT_REFUSED
    except Exception as error:  # noqa: BLE001  (a failure is reported, scrubbed, and exits non-zero)
        log(logging.ERROR, "restore_failed", error_type=type(error).__name__, reason=pgtools.scrub(str(error), secrets))
        return EXIT_FAILED


def psycopg_url(target: pgtools.Target) -> str:
    """A SQLAlchemy URL for the (already validated) target, for the grant step. Built in memory; never logged."""
    from sqlalchemy.engine import URL

    return URL.create("postgresql+psycopg", username=target.user, password=target.password, host=target.host, port=target.port, database=target.database, query=dict(target.options)).render_as_string(hide_password=False)


if __name__ == "__main__":
    sys.exit(run())
