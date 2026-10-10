"""The migration job (`python -m app.scripts.migrate`) as REAL separate processes against disposable databases: it brings a
database to this image's head under a fixed advisory lock, refuses states it must not touch, exits non-zero on every
failure, and the web process never migrates."""

import ast
import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from app.core import migration
from tests.db_support import BACKEND_DIR, MIGRATE, disposable_database, execute, psycopg_url, run_migrate, scalar

HEAD = migration.code_heads()[0]
PARENT = migration.script_directory().get_revision(HEAD).down_revision
LOCK_KEY = migration.MIGRATION_LOCK_KEY


def events(result: subprocess.CompletedProcess[str]) -> list[dict]:
    return [json.loads(line) for line in (result.stdout + result.stderr).splitlines() if line.startswith("{")]


def names(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [event["event"] for event in events(result)]


def lock_is_free(url: str) -> bool:
    """True when the migration lock can be taken right now (i.e. no session holds it)."""
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            got = connection.execute(text("select pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar()
            if got:
                connection.execute(text("select pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
            return bool(got)
    finally:
        engine.dispose()


def lock_holders(url: str) -> int:
    return scalar(
        url,
        "select count(*) from pg_locks where locktype = 'advisory' and granted and classid = cast(:h as oid) and objid = cast(:l as oid)",
        h=(LOCK_KEY >> 32) & 0xFFFFFFFF, l=LOCK_KEY & 0xFFFFFFFF,
    )


def script_dir(directory: Path, *revisions: tuple[str, str | None, str]) -> Path:
    """A throw-away Alembic script directory (the real env.py, so it runs exactly like the real one) with test revisions."""
    (directory / "versions").mkdir(parents=True)
    for name in ("env.py", "script.py.mako"):
        (directory / name).write_text((BACKEND_DIR / "alembic" / name).read_text(encoding="utf-8"), encoding="utf-8")
    for revision, down, body in revisions:
        (directory / "versions" / f"{revision}.py").write_text(
            "from alembic import op\nimport sqlalchemy as sa\n"
            f"revision = {revision!r}\ndown_revision = {down!r}\nbranch_labels = None\ndepends_on = None\n\n"
            "def upgrade():\n" + textwrap.indent(textwrap.dedent(body).strip(), "    ") + "\n\ndef downgrade():\n    pass\n",
            encoding="utf-8",
        )
    return directory


def upgrade_in_process(url: str, revision: str) -> None:
    config = migration.alembic_config()
    config.attributes["database_url"] = url
    command.upgrade(config, revision)


# --- the job itself ---------------------------------------------------------------------------------------------------------------------------


def test_a_fresh_database_goes_to_head():
    with disposable_database("mig") as url:
        result = run_migrate(url)
        assert result.returncode == 0, result.stdout + result.stderr
        assert scalar(url, "select version_num from alembic_version") == HEAD
        assert scalar(url, "select count(*) from organizations") == 0  # the schema is really there
        assert "migrated" in names(result)
        assert lock_is_free(url)  # released with the session
        assert url.split(":")[2].split("@")[0] not in result.stdout + result.stderr  # the password is never printed


def test_an_old_database_goes_to_head_without_losing_data():
    with disposable_database("mig") as url:
        upgrade_in_process(url, PARENT)
        before = scalar(url, "select version_num from alembic_version")
        assert before == PARENT
        result = run_migrate(url)
        assert result.returncode == 0, result.stdout + result.stderr
        assert scalar(url, "select version_num from alembic_version") == HEAD
        migrating = [e for e in events(result) if e["event"] == "migrating"][0]
        assert (migrating["from_revision"], migrating["to_revision"]) == (PARENT, HEAD)


def test_a_database_already_at_head_is_left_alone_and_succeeds():
    with disposable_database("mig") as url:
        assert run_migrate(url).returncode == 0
        again = run_migrate(url)
        assert again.returncode == 0
        assert "Running upgrade" not in again.stdout + again.stderr
        assert scalar(url, "select version_num from alembic_version") == HEAD


def test_a_failing_migration_exits_non_zero_changes_nothing_and_releases_the_lock(tmp_path):
    with disposable_database("mig") as url:
        directory = script_dir(
            tmp_path / "scripts",
            ("t0000000001", None, 'op.execute("create table first_step (id int)")'),
            ("t0000000002", "t0000000001", 'op.execute("select 1/0")'),
        )
        result = run_migrate(url, "--script-location", str(directory))
        assert result.returncode == 1
        assert "migration_failed" in names(result)
        # transactional DDL: neither step is half applied, and Alembic's own record was rolled back with them
        assert scalar(url, "select to_regclass('first_step') is null") is True
        assert scalar(url, "select to_regclass('alembic_version') is null") is True
        assert lock_is_free(url)


def test_a_failure_message_never_contains_the_password_or_a_bound_value(tmp_path):
    with disposable_database("mig") as url:
        directory = script_dir(
            tmp_path / "scripts",
            ("t0000000001", None, 'op.get_bind().execute(sa.text("select :v from no_such_table"), {"v": "SENTINEL-VALUE"})'),
        )
        result = run_migrate(url, "--script-location", str(directory))
        assert result.returncode == 1
        output = result.stdout + result.stderr
        assert "SENTINEL-VALUE" not in output
        assert url.split(":")[2].split("@")[0] not in output


def test_a_database_at_a_revision_this_image_does_not_know_is_refused_untouched():
    with disposable_database("mig") as url:
        assert run_migrate(url).returncode == 0
        execute(url, "update alembic_version set version_num = 'ffffffffffff'")
        result = run_migrate(url)
        assert result.returncode == 4 and "migration_refused" in names(result)
        assert scalar(url, "select version_num from alembic_version") == "ffffffffffff"
        assert lock_is_free(url)


def test_a_database_that_records_several_revisions_is_refused():
    with disposable_database("mig") as url:
        assert run_migrate(url).returncode == 0
        execute(url, "alter table alembic_version drop constraint alembic_version_pkc")
        execute(url, "insert into alembic_version (version_num) values (:v)", v=PARENT)
        assert run_migrate(url).returncode == 4


def test_a_code_base_with_two_heads_is_refused(tmp_path):
    with disposable_database("mig") as url:
        directory = script_dir(
            tmp_path / "scripts",
            ("t0000000001", None, 'op.execute("create table a (id int)")'),
            ("t0000000002", "t0000000001", 'op.execute("create table b (id int)")'),
            ("t0000000003", "t0000000001", 'op.execute("create table c (id int)")'),
        )
        result = run_migrate(url, "--script-location", str(directory))
        assert result.returncode == 4
        assert scalar(url, "select to_regclass('a') is null") is True


def test_production_requires_the_migration_url_and_never_falls_back_to_the_runtime_url():
    with disposable_database("mig") as url:
        no_migration_url = run_migrate(url, env={"APP_ENV": "production", "MIGRATION_DATABASE_URL": "", "DATABASE_URL": url, "RUNTIME_DB_ROLE": "x"})
        assert no_migration_url.returncode == 2
        assert scalar(url, "select to_regclass('alembic_version') is null") is True  # nothing ran, with the runtime URL or any other


def test_production_requires_the_runtime_role_name():
    with disposable_database("mig") as url:
        result = run_migrate(url, env={"APP_ENV": "production", "RUNTIME_DB_ROLE": ""})
        assert result.returncode == 2
        assert scalar(url, "select to_regclass('alembic_version') is null") is True


# --- the advisory lock -------------------------------------------------------------------------------------------------------------------------


def test_the_job_waits_for_a_held_lock_and_then_proceeds():
    with disposable_database("mig") as url:
        holder = create_engine(url, isolation_level="AUTOCOMMIT").connect()
        try:
            assert holder.execute(text("select pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar()
            process = subprocess.Popen(
                [*MIGRATE, "--lock-timeout", "60"], cwd=BACKEND_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env=_environment(url),
            )
            time.sleep(3)
            assert process.poll() is None  # still waiting
            assert scalar(url, "select to_regclass('alembic_version') is null") is True  # and has changed nothing
        finally:
            holder.execute(text("select pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
            holder.close()
        out, err = process.communicate(timeout=120)
        assert process.returncode == 0, out + err
        assert scalar(url, "select version_num from alembic_version") == HEAD


def test_the_job_gives_up_when_the_lock_is_not_released_in_time():
    with disposable_database("mig") as url:
        holder = create_engine(url, isolation_level="AUTOCOMMIT").connect()
        try:
            holder.execute(text("select pg_advisory_lock(:k)"), {"k": LOCK_KEY})
            result = run_migrate(url, "--lock-timeout", "1")
            assert result.returncode == 3
            assert scalar(url, "select to_regclass('alembic_version') is null") is True
        finally:
            holder.close()


def _environment(url: str) -> dict[str, str]:
    import os

    environment = {k: v for k, v in os.environ.items() if k not in {"DATABASE_URL", "MIGRATION_DATABASE_URL", "RUNTIME_DB_ROLE"}}
    environment.update({"MIGRATION_DATABASE_URL": url, "APP_ENV": "development"})
    return environment


def test_concurrent_jobs_serialize(tmp_path):
    """Job A is inside a slow migration (holding the lock) when job B starts. B must wait, then find the work done: one
    execution of the migration, two clean exits. Without the lock B would run the same migration alongside A and fail."""
    with disposable_database("mig") as url:
        directory = script_dir(
            tmp_path / "scripts",
            ("t0000000001", None, 'op.execute("create table runs (id serial primary key, started timestamptz, finished timestamptz)")'),
            ("t0000000002", "t0000000001", 'op.execute("insert into runs (started) values (clock_timestamp())"); op.execute("select pg_sleep(4)"); op.execute("update runs set finished = clock_timestamp()")'),
        )
        args = [*MIGRATE, "--script-location", str(directory), "--lock-timeout", "90"]
        first = subprocess.Popen(args, cwd=BACKEND_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_environment(url))
        deadline = time.monotonic() + 30
        while lock_holders(url) == 0 and time.monotonic() < deadline:
            time.sleep(0.1)
        assert lock_holders(url) == 1 and first.poll() is None  # A holds the lock and is migrating
        time.sleep(1.5)  # A is now inside its slow step
        second = subprocess.Popen(args, cwd=BACKEND_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_environment(url))
        time.sleep(1.5)
        assert second.poll() is None  # B is waiting, not racing
        out_a, err_a = first.communicate(timeout=120)
        out_b, err_b = second.communicate(timeout=120)
        assert (first.returncode, second.returncode) == (0, 0), (out_a + err_a, out_b + err_b)
        assert scalar(url, "select count(*) from runs") == 1  # the migration ran once
        assert scalar(url, "select count(*) from runs where finished is not null") == 1
        assert sum("Running upgrade" in (o + e) for o, e in ((out_a, err_a), (out_b, err_b))) == 1
        assert lock_is_free(url)


# --- the web process never migrates -------------------------------------------------------------------------------------------------------------


def test_only_the_job_and_the_test_reset_import_alembic_commands():
    offenders = []
    for path in (BACKEND_DIR / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            imported = []
            if isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                imported = [a.name for a in node.names]
            if any(name == "alembic.command" or name.endswith("alembic.command") for name in imported) or (
                isinstance(node, ast.ImportFrom) and node.module == "alembic" and any(a.name == "command" for a in node.names)
            ):
                offenders.append(path.relative_to(BACKEND_DIR).as_posix())
    assert sorted(set(offenders)) == ["app/scripts/migrate.py", "app/scripts/reset_test_db.py"]


def test_starting_the_web_application_does_not_touch_the_schema():
    """Import and start the real app against an EMPTY database in a separate process: it must create nothing."""
    with disposable_database("mig") as url:
        code = (
            "import os\n"
            f"os.environ.update(APP_ENV='development', AUTH_MODE='dev', DATABASE_URL={url!r})\n"
            "from fastapi.testclient import TestClient\n"
            "from app.main import app\n"
            "with TestClient(app) as client:\n"
            "    assert client.get('/health').status_code == 200\n"
            "    assert client.get('/health/ready').status_code == 503\n"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=BACKEND_DIR, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        assert scalar(url, "select count(*) from pg_tables where schemaname = 'public'") == 0


def test_a_malformed_migration_url_is_a_configuration_error_that_never_echoes_it():
    result = run_migrate("postgresql+psycopg//owner:SENTINEL-URL-PASSWORD@db.invalid/x")  # (the colon after the scheme is missing)
    assert result.returncode == 2 and "configuration_error" in names(result)
    assert "SENTINEL-URL-PASSWORD" not in result.stdout + result.stderr


def test_the_failure_scrubber_removes_the_password_and_the_whole_url_from_a_message():
    from app.scripts.migrate import _scrub

    url = "postgresql+psycopg://owner:SENTINEL-SCRUB-PASSWORD@db.invalid/x"
    assert "SENTINEL-SCRUB-PASSWORD" not in _scrub(f"could not connect using {url}: refused", url)
    assert "SENTINEL-SCRUB-PASSWORD" not in _scrub("password authentication failed (SENTINEL-SCRUB-PASSWORD)", url)
    assert _scrub("line one\nline two", url) == "line one"  # only the first line (parameters and statements follow it)


def _alembic_cli(url: str, *args: str, with_web_settings: bool) -> subprocess.CompletedProcess[str]:
    import os

    environment = {k: v for k, v in os.environ.items() if k not in {"DATABASE_URL", "MIGRATION_DATABASE_URL", "APP_ENV", "SECURITY_KEY", "BFF_INTERNAL_SECRET"}}
    environment.update({"MIGRATION_DATABASE_URL": url, "APP_ENV": "development"})
    if with_web_settings:
        environment["DATABASE_URL"] = url
    return subprocess.run([sys.executable, "-m", "alembic", "-c", str(BACKEND_DIR / "alembic.ini"), *args], cwd=BACKEND_DIR, env=environment, capture_output=True, text=True, timeout=120)


def test_the_alembic_cli_upgrade_needs_only_the_migration_credentials_and_check_needs_the_web_settings():
    with disposable_database("mig") as url:
        upgraded = _alembic_cli(url, "upgrade", "head", with_web_settings=False)  # no DATABASE_URL, no secrets: just MIGRATION_DATABASE_URL
        assert upgraded.returncode == 0, upgraded.stderr[-1500:]
        assert scalar(url, "select version_num from alembic_version") == HEAD
        assert _alembic_cli(url, "current", with_web_settings=False).returncode == 0
        checked = _alembic_cli(url, "check", with_web_settings=True)  # the schema matches the models
        assert checked.returncode == 0 and "No new upgrade operations detected" in checked.stdout + checked.stderr
        if __import__("os").environ.get("CI"):  # (a developer's .env would supply the web settings; a clean checkout does not)
            assert _alembic_cli(url, "check", with_web_settings=False).returncode != 0  # check reads the models, which need the web settings


def test_renumbering_runs_only_with_the_exact_confirmation_phrase():
    from app.scripts.renumber_invoices import CONFIRMATION

    with disposable_database("mig") as url:
        wrong = run_migrate(url, env={"RENUMBER_INVOICES": "yes"})
        assert wrong.returncode == 2 and "configuration_error" in names(wrong)
        assert scalar(url, "select count(*) from information_schema.tables where table_name = 'alembic_version'") == 0  # nothing ran

        plain = run_migrate(url)
        assert plain.returncode == 0 and "invoices_renumbered" not in names(plain)
        confirmed = run_migrate(url, env={"RENUMBER_INVOICES": CONFIRMATION})
        assert confirmed.returncode == 0, confirmed.stdout + confirmed.stderr
        assert "invoices_renumbered" in names(confirmed)  # on an empty database: nothing to renumber, and it says so
