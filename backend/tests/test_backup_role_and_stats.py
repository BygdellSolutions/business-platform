"""The read-only backup role, the size measurement command and the shared helpers of the backup tools."""

import json

import psycopg
import pytest

from app.core import pgtools
from app.scripts import bootstrap_roles
from tests.db_support import admin_url, disposable_database, disposable_roles, psycopg_url, scalar
from tests.restore_support import restore_world, run_tool  # noqa: F401  (restore_world is a fixture)


# --- the backup role ---------------------------------------------------------------------------------------------------------


def test_the_backup_role_can_read_everything_and_change_nothing(restore_world):
    flags = scalar(restore_world.admin, "select rolsuper::text || rolcreatedb::text || rolcreaterole::text || rolreplication::text || rolbypassrls::text from pg_roles where rolname = :r", r=restore_world.backup)
    assert flags == "falsefalsefalsefalsefalse"
    assert scalar(restore_world.admin, "select pg_has_role(:r, 'pg_read_all_data', 'member')", r=restore_world.backup) is True
    assert scalar(restore_world.admin, "select count(*) from pg_class c join pg_roles r on r.oid = c.relowner where r.rolname = :r", r=restore_world.backup) == 0
    with psycopg.connect(psycopg_url(restore_world.backup_url)) as connection:
        assert connection.execute("select count(*) from invoice_pdfs").fetchone()[0] == 2
        assert connection.execute("select count(*) from user_credentials").fetchone()[0] > 0  # auth records are part of the backup
        for statement in ("insert into customers (id) values (gen_random_uuid())", "update customers set name = 'x'", "delete from customers", "create table x (y int)", "truncate customers"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement)
            connection.rollback()


def test_the_backup_role_is_optional_and_must_be_its_own_role():
    with disposable_roles("owner", "app") as roles, disposable_database("bk") as admin:
        names, passwords = roles["names"], roles["passwords"]
        base = dict(owner_role=names["owner"], owner_password=passwords["owner"], app_role=names["app"], app_password=passwords["app"])
        bootstrap_roles.bootstrap(psycopg_url(admin), **base)  # without a backup role: as before
        for bad in ({"backup_role": names["app"], "backup_password": "x"}, {"backup_role": "somebody", "backup_password": None}):
            with pytest.raises(ValueError):
                bootstrap_roles.bootstrap(psycopg_url(admin), **base, **bad)
        assert scalar(admin, "select count(*) from pg_roles where rolname = 'somebody'") == 0


# --- db_stats ---------------------------------------------------------------------------------------------------------------


def test_db_stats_measures_size_and_pdf_bytes_read_only_and_prints_no_secret(restore_world):
    result = run_tool("app.scripts.db_stats", env={"STATS_DATABASE_URL": restore_world.backup_url})

    assert result.returncode == 0, result.stdout + result.stderr
    stats = json.loads(result.stdout)
    assert stats["database_bytes"] > 0
    assert stats["invoice_pdfs"]["count"] == 2
    assert stats["invoice_pdfs"]["stored_bytes"] == sum(i["pdf_bytes"] for i in restore_world.facts["invoices"].values())
    assert stats["invoice_pdfs"]["table_bytes"] >= stats["invoice_pdfs"]["stored_bytes"] // 2
    assert stats["review"] == {"pdf_bytes_threshold": 2 * 1024**3, "pdf_bytes_reached": False, "backup_seconds_threshold": 900}
    assert len(stats["largest_tables"]) == 5
    for secret in (*restore_world.passwords.values(), "restore-fixture.invalid", "Sentinel", "postgresql://"):
        assert secret not in result.stdout + result.stderr


def test_db_stats_has_no_fallback_database_and_does_not_echo_a_bad_credential(restore_world):
    refused = run_tool("app.scripts.db_stats", env={"DATABASE_URL": restore_world.owner_url})
    assert refused.returncode == 2

    broken = run_tool("app.scripts.db_stats", env={"STATS_DATABASE_URL": restore_world.backup_url.replace(restore_world.passwords["backup"], "wrong-sentinel-pw")})
    assert broken.returncode == 1 and "wrong-sentinel-pw" not in broken.stdout + broken.stderr


# --- pgtools ----------------------------------------------------------------------------------------------------------------


def test_a_url_is_parsed_into_an_identity_and_a_malformed_one_is_refused_without_being_echoed():
    target = pgtools.parse_url("postgresql+psycopg://role:s3cret@Host.Example:6543/db?sslmode=require")
    assert (target.host, target.port, target.user, target.database) == ("Host.Example", 6543, "role", "db")
    assert target.identity() == ("host.example", 6543, "db")
    assert target.client_env()["PGPASSWORD"] == "s3cret" and target.client_env()["PGSSLMODE"] == "require"
    assert target.secrets() == ["s3cret"]
    for bad in ("not a url", "mysql://u:p@h/d", "postgresql://u:p@/d", "postgresql://h/d"):
        with pytest.raises(pgtools.ToolRefused) as refused:
            pgtools.parse_url(bad, "X_URL")
        assert "p@" not in str(refused.value) and bad not in str(refused.value)


def test_a_tool_variable_has_no_fallback():
    with pytest.raises(pgtools.ToolRefused, match="never falls back"):
        pgtools.target_from_env("BACKUP_DATABASE_URL", {"DATABASE_URL": "postgresql://u:p@h/d"})


def test_the_client_program_runs_without_ambient_pg_variables_and_with_the_target_credentials_in_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PGPASSWORD", "ambient-password")
    monkeypatch.setenv("PGHOST", "ambient-host")
    script = tmp_path / "echo_env.py"
    script.write_text("import json, os, sys\nprint(json.dumps({k: v for k, v in os.environ.items() if k.startswith('PG')}))\n", encoding="utf-8")
    target = pgtools.parse_url("postgresql://role:tgt-pw@db.internal:5432/name")

    import sys

    result = pgtools.run_client([sys.executable, str(script)], [], target)

    seen = json.loads(result.stdout)
    assert seen["PGPASSWORD"] == "tgt-pw" and seen["PGHOST"] == "db.internal" and seen["PGUSER"] == "role" and seen["PGDATABASE"] == "name"
    assert "ambient" not in result.stdout


def test_a_client_program_timeout_is_a_failure_not_a_hang(tmp_path):
    import sys

    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")

    result = pgtools.run_client([sys.executable, str(script)], [], None, timeout=1)

    assert result.returncode == 124
