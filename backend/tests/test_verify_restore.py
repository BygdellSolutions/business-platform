"""verify_restore: a restored database is trusted only if this says so, so every guarantee it states is also proven to FAIL when broken.

Runs the real tool as a process against the representative fixture (tests/restore_fixture.py) and against throwaway clones of it that
a test damages on purpose. The verifier connects as the READ-ONLY backup role (which proves it needs no write privilege) and, for
the role checks, as the restricted runtime role.
"""

import json

import psycopg
import pytest

from app.core import pgtools
from app.scripts import backup, verify_restore
from tests.db_support import psycopg_url, scalar
from tests.restore_support import clone, restore_world, run_tool  # noqa: F401  (restore_world is a fixture)

SENTINELS = ("restore-fixture.invalid", "$argon2", "Sentinel", "token_hash")


def verify(db, *args: str, app: bool = True, as_role: str = "backup", extra_env: dict[str, str] | None = None):
    env = {"VERIFY_DATABASE_URL": db.backup_url if as_role == "backup" else db.owner_url}
    if app:
        env["VERIFY_APP_DATABASE_URL"] = db.app_url
    env.update(extra_env or {})
    result = run_tool("app.scripts.verify_restore", *args, env=env)
    try:
        report = json.loads(result.stdout)
    except ValueError:
        report = None
    return result, report


def failed(report) -> set[str]:
    return {check["name"] for check in report["checks"] if not check["ok"]}


def manifest_for(db, path) -> str:
    with psycopg.connect(psycopg_url(db.owner_url)) as connection:
        info = backup.collect(connection)
    path.write_text(json.dumps({"table_counts": info["table_counts"]}), encoding="utf-8")
    return str(path)


def sql(db, statement: str) -> None:
    with psycopg.connect(psycopg_url(db.admin), autocommit=True) as connection:
        connection.execute(statement)


# --- the verifier's lists are the schema's ------------------------------------------------------------------------------------


def test_the_required_objects_are_exactly_what_a_migrated_database_contains(restore_world):
    """A new trigger, function or tenant table cannot be forgotten: this fails until the verifier lists it."""
    with psycopg.connect(psycopg_url(restore_world.admin)) as connection:
        triggers = {tuple(row) for row in connection.execute("SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid WHERE c.relnamespace = 'public'::regnamespace AND NOT t.tgisinternal")}
        functions = {row[0] for row in connection.execute("SELECT proname FROM pg_proc WHERE pronamespace = 'public'::regnamespace")}
        tenant = {row[0] for row in connection.execute("SELECT table_name FROM information_schema.columns WHERE table_schema = 'public' AND column_name = 'organization_id' AND is_nullable = 'NO'")}
    assert triggers == verify_restore.REQUIRED_TRIGGERS
    assert functions == verify_restore.REQUIRED_FUNCTIONS
    assert tenant == verify_restore.TENANT_TABLES


# --- a good database ----------------------------------------------------------------------------------------------------------


def test_a_faithful_database_passes_every_check_as_a_read_only_role_and_prints_no_secret(restore_world, tmp_path):
    manifest = manifest_for(restore_world, tmp_path / "m.json")

    result, report = verify(restore_world, "--manifest", manifest, "--alembic-check")

    assert result.returncode == 0, result.stdout + result.stderr
    assert report["ok"] is True
    names = {check["name"] for check in report["checks"]}
    assert {"schema.exact_head", "objects.triggers", "objects.functions", "data.counts_match_manifest", "pdf.integrity", "auth.coherent", "roles.runtime_role", "schema.alembic_check"} <= names
    output = result.stdout + result.stderr
    for forbidden in (*SENTINELS, restore_world.facts["session_token"], *restore_world.passwords.values()):
        assert forbidden not in output


def test_the_verifier_writes_nothing_and_the_fingerprint_is_stable(restore_world):
    first = verify(restore_world, "--fingerprint")[1]["fingerprint"]
    second = verify(restore_world, "--fingerprint")[1]["fingerprint"]
    assert first == second
    assert scalar(restore_world.admin, "select count(*) from pg_stat_activity where datname = current_database() and state = 'idle in transaction'") == 0


def test_the_verifier_connection_is_read_only_even_for_a_role_that_could_write(restore_world, monkeypatch):
    """The OWNER role could create a table; the verifier's own connection must still refuse (it is not the privileges that protect it)."""
    attempts = []

    def try_to_write(cursor, report):
        try:
            cursor.execute("CREATE TABLE verifier_must_not_write (x integer)")
            attempts.append("wrote")
        except psycopg.errors.ReadOnlySqlTransaction:
            attempts.append("refused")

    monkeypatch.setattr(verify_restore, "check_auth", try_to_write)
    target = pgtools.parse_url(restore_world.owner_url)

    verify_restore.verify(target, None, None)

    assert attempts == ["refused"]
    assert scalar(restore_world.admin, "select to_regclass('verifier_must_not_write') is null") is True


def test_the_fingerprint_changes_when_any_data_changes(restore_world):
    with clone(restore_world) as copy:
        before = verify(copy, "--fingerprint")[1]["fingerprint"]
        sql(copy, "UPDATE customers SET name = name || ' changed' WHERE id = (SELECT id FROM customers LIMIT 1)")
        assert verify(copy, "--fingerprint")[1]["fingerprint"] != before


def test_the_clone_and_its_source_have_the_same_fingerprint(restore_world):
    with clone(restore_world) as copy:
        assert verify(copy, "--fingerprint")[1]["fingerprint"] == verify(restore_world, "--fingerprint")[1]["fingerprint"]


# --- damaged databases ----------------------------------------------------------------------------------------------------------

DAMAGE = {
    "no alembic_version": ("DROP TABLE alembic_version", {"schema.single_revision"}),
    "two revisions": ("INSERT INTO alembic_version VALUES ('000000000001')", {"schema.single_revision"}),
    "wrong revision": ("UPDATE alembic_version SET version_num = '000000000001'", {"schema.exact_head"}),
    "append-only trigger missing": ("DROP TRIGGER trg_security_events_append_only ON security_events", {"objects.triggers"}),
    "last-owner trigger missing": ("DROP TRIGGER trg_organization_users_owner_required_delete ON organization_users", {"objects.triggers"}),
    "invoice immutability trigger missing": ("DROP TRIGGER trg_invoices_immutability ON invoices", {"objects.triggers"}),
    "trigger disabled": ("ALTER TABLE invoices DISABLE TRIGGER trg_invoices_immutability", {"objects.triggers"}),
    "function missing": ("DROP FUNCTION invoice_pdfs_guard() CASCADE", {"objects.functions", "objects.triggers"}),
    "constraint not validated": ("ALTER TABLE items ADD CONSTRAINT ck_damage CHECK (name <> 'zzz') NOT VALID", {"objects.constraints_validated"}),
    "tenant column nullable": ("ALTER TABLE customers ALTER COLUMN organization_id DROP NOT NULL", {"objects.tenant_columns"}),
    "PDF bytes corrupted": (
        "ALTER TABLE invoice_pdfs DROP CONSTRAINT ck_invoice_pdfs_sha256_matches; SET session_replication_role = replica; UPDATE invoice_pdfs SET content = overlay(content placing '\\x5858'::bytea from 200 for 2) WHERE id = (SELECT id FROM invoice_pdfs ORDER BY id LIMIT 1)",
        {"pdf.integrity"},
    ),
    "PDF hash mismatched": (
        "ALTER TABLE invoice_pdfs DROP CONSTRAINT ck_invoice_pdfs_sha256_matches; SET session_replication_role = replica; UPDATE invoice_pdfs SET sha256 = repeat('0', 64) WHERE id = (SELECT id FROM invoice_pdfs ORDER BY id LIMIT 1)",
        {"pdf.integrity"},
    ),
    "PDF length mismatched": (
        "ALTER TABLE invoice_pdfs DROP CONSTRAINT ck_invoice_pdfs_byte_size; SET session_replication_role = replica; UPDATE invoice_pdfs SET byte_size = byte_size + 1 WHERE id = (SELECT id FROM invoice_pdfs ORDER BY id LIMIT 1)",
        {"pdf.integrity"},
    ),
    "PDF without a signature (hash and length consistent)": (
        "ALTER TABLE invoice_pdfs DROP CONSTRAINT ck_invoice_pdfs_is_pdf; ALTER TABLE invoice_pdfs DROP CONSTRAINT ck_invoice_pdfs_sha256_matches; ALTER TABLE invoice_pdfs DROP CONSTRAINT ck_invoice_pdfs_byte_size;"
        " SET session_replication_role = replica; UPDATE invoice_pdfs SET content = '\\x4e4f5420412050444620'::bytea || content, byte_size = octet_length('\\x4e4f5420412050444620'::bytea || content),"
        " sha256 = encode(sha256('\\x4e4f5420412050444620'::bytea || content), 'hex') WHERE id = (SELECT id FROM invoice_pdfs ORDER BY id LIMIT 1)",
        {"pdf.integrity"},
    ),
    "credential without a user (a half-restored database)": (
        "SET session_replication_role = replica; DELETE FROM users WHERE email LIKE 'alice@%'", {"auth.coherent"},
    ),
    "the runtime role can create objects": ("GRANT CREATE ON SCHEMA public TO {app}", {"roles.runtime_role"}),
    "the runtime role can truncate": ("GRANT TRUNCATE ON customers TO {app}", {"roles.runtime_role"}),
    "the runtime role lost DML": ("REVOKE INSERT ON customers FROM {app}", {"roles.runtime_role"}),
    "the runtime role can write the revision table": ("GRANT UPDATE ON alembic_version TO {app}", {"roles.runtime_role"}),
    "the runtime role owns a table": ("ALTER TABLE items OWNER TO {app}", {"roles.runtime_role"}),
    "the runtime role lost a sequence": ("REVOKE USAGE ON SEQUENCE security_events_id_seq FROM {app}", {"roles.runtime_role"}),
}


@pytest.mark.parametrize("label", list(DAMAGE))
def test_every_kind_of_damage_is_detected(restore_world, label):
    statement, expected = DAMAGE[label]
    with clone(restore_world) as copy:
        sql(copy, statement.format(app=f'"{restore_world.app}"'))

        result, report = verify(copy)

        assert result.returncode == 1, result.stdout
        assert report["ok"] is False
        assert expected <= failed(report), failed(report)


def test_missing_data_is_detected_against_the_manifest(restore_world, tmp_path):
    with clone(restore_world) as copy:
        manifest = manifest_for(copy, tmp_path / "m.json")  # what the backup said
        sql(copy, "DELETE FROM custom_field_values")  # what the restore lost

        result, report = verify(copy, "--manifest", manifest)

        assert result.returncode == 1 and "data.counts_match_manifest" in failed(report)


def test_a_database_without_tenant_data_is_refused_even_without_a_manifest(restore_world):
    with clone(restore_world) as copy:
        sql(copy, "SET session_replication_role = replica; DELETE FROM organization_users; DELETE FROM organizations")

        assert "data.tenant_core_present" in failed(verify(copy)[1])


def test_schema_drift_is_detected_by_alembic_check(restore_world):
    with clone(restore_world) as copy:
        sql(copy, "ALTER TABLE items ADD COLUMN drifted integer")

        result, report = verify(copy, "--alembic-check", app=False)

        assert result.returncode == 1 and "schema.alembic_check" in failed(report)


# --- refusals and secrecy -------------------------------------------------------------------------------------------------------


def test_the_verifier_has_no_fallback_database(restore_world):
    result = run_tool("app.scripts.verify_restore", env={"DATABASE_URL": restore_world.owner_url})

    assert result.returncode == 2 and "VERIFY_DATABASE_URL is not set" in result.stdout


def test_the_runtime_url_must_be_the_same_database(restore_world):
    with clone(restore_world) as copy:
        result, _ = verify(restore_world, extra_env={"VERIFY_APP_DATABASE_URL": copy.app_url})

        assert result.returncode == 2 and "same database" in result.stdout


def test_an_unreachable_database_fails_without_echoing_the_credentials(restore_world):
    broken = restore_world.backup_url.replace(restore_world.passwords["backup"], "wrong-password-sentinel")

    result = run_tool("app.scripts.verify_restore", env={"VERIFY_DATABASE_URL": broken})

    assert result.returncode == 1
    assert "wrong-password-sentinel" not in result.stdout + result.stderr
    assert json.loads(result.stdout)["ok"] is False


def test_the_scrubber_removes_what_a_client_might_quote():
    text = 'connection to server at "10.1.2.3", port 5432 failed: password authentication failed for user "u" postgresql://u:hunter2@10.1.2.3/db hunter2'
    cleaned = pgtools.scrub(text, ["hunter2"])
    assert "hunter2" not in cleaned and "10.1.2.3" not in cleaned
