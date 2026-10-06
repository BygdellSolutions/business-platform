"""The restore tool's guards: it restores only into a separate, scratch-named, empty database that the owner role owns, and never
anywhere near the source. Every refusal happens before anything is changed.

The PostgreSQL clients are replaced by tests/fake_pg.py (the real pg_restore round trip, grants and the restricted-role backend are in the
container drill, deploy/tests/test_restore_drill.py). The databases are real, on the test server.
"""

import json
import re

import psycopg
import pytest

from app.scripts import restore
from tests.db_support import psycopg_url, scalar, url_for
from tests.restore_support import fake_pg_env, restore_world, run_backup_files, run_tool, scratch_target  # noqa: F401  (restore_world is a fixture)


@pytest.fixture
def backup_files(restore_world, tmp_path):
    return run_backup_files(restore_world, tmp_path)


def run_restore(backup_files, target_url, *extra, runtime_role="x", env=None, mode="ok", tmp_path=None, log=None):
    environment = {"RESTORE_DATABASE_URL": target_url, **fake_pg_env("ok", mode, log=log)}
    if runtime_role is not None:
        environment["RUNTIME_DB_ROLE"] = runtime_role
    environment.update(env or {})
    return run_tool("app.scripts.restore", "--dump", str(backup_files.dump), *extra, env=environment)


def table_count(db, database) -> int:
    return scalar(url_for(database), "select count(*) from pg_class where relnamespace = 'public'::regnamespace and relkind = 'r'")


def fingerprint(world) -> str:
    result = run_tool("app.scripts.verify_restore", "--fingerprint", env={"VERIFY_DATABASE_URL": world.owner_url})
    return json.loads(result.stdout)["fingerprint"]


# --- the name and identity guards ---------------------------------------------------------------------------------------------


def test_the_target_comes_from_RESTORE_DATABASE_URL_only(restore_world, backup_files):
    with scratch_target(world=restore_world) as target:
        result = run_tool("app.scripts.restore", "--dump", str(backup_files.dump), env={**fake_pg_env(), "DATABASE_URL": target.owner_url, "MIGRATION_DATABASE_URL": target.owner_url, "RUNTIME_DB_ROLE": target.app})

    assert result.returncode == 2 and "RESTORE_DATABASE_URL is not set" in result.stdout


@pytest.mark.parametrize("name", ["bp", "business_platform", "bp_production", "bp_prod", "bp_restore_", "Bp_restore", "restore_bp", "bp_restore_a_b", "bp_restored", "_restore", "bp-restore"])
def test_a_database_not_named_like_a_scratch_database_is_refused(name):
    assert not restore.NAME_GUARD.fullmatch(name)


@pytest.mark.parametrize("name", ["bp_restore", "bp_restore_test", "restore_drill_restore", "bp_20261006_restore_a1"])
def test_scratch_names_are_accepted(name):
    assert restore.NAME_GUARD.fullmatch(name)


def test_a_production_looking_target_is_refused_whatever_the_host(restore_world, backup_files):
    before = fingerprint(restore_world)

    for url in (restore_world.owner_url, restore_world.admin, url_for("business_platform"), url_for("bp_production", user=restore_world.owner, password=restore_world.passwords["owner"])):
        for extra in ((), ("--reset-target",)):  # even an explicit reset must not be able to reach a non-scratch database
            result = run_restore(backup_files, url, *extra, runtime_role=restore_world.app)
            assert result.returncode == 2, (url, extra)
            assert "must end in _restore" in result.stdout

    assert fingerprint(restore_world) == before  # the "source" was not touched


def test_the_source_is_refused_even_under_a_scratch_name(restore_world, backup_files, tmp_path):
    with scratch_target(world=restore_world) as target:
        manifest = json.loads(backup_files.manifest.read_text(encoding="utf-8"))
        manifest["source"]["database"] = target.database  # the dump says it came from THIS database
        backup_files.manifest.write_text(json.dumps(manifest), encoding="utf-8")

        result = run_restore(backup_files, target.owner_url, runtime_role=target.app)

        assert result.returncode == 2 and "same name as the database the dump came from" in result.stdout


@pytest.mark.parametrize("variable", ["DATABASE_URL", "MIGRATION_DATABASE_URL", "BACKUP_DATABASE_URL"])
def test_a_target_that_is_any_configured_source_is_refused_whatever_the_credentials(restore_world, backup_files, variable):
    with scratch_target(world=restore_world) as target:
        result = run_restore(backup_files, target.owner_url, runtime_role=target.app, env={variable: target.app_url})  # same host/port/database, other role

        assert result.returncode == 2 and variable in result.stdout
        assert table_count(restore_world, target.database) == 0


def test_the_restore_will_not_connect_as_the_runtime_role_a_superuser_or_a_non_owner(restore_world, backup_files):
    with scratch_target(world=restore_world) as target:
        as_runtime = run_restore(backup_files, target.app_url, runtime_role=target.app)
        as_superuser = run_restore(backup_files, url_for(target.database), runtime_role=target.app)
        as_non_owner = run_restore(backup_files, target.app_url, runtime_role=target.owner)

    assert as_runtime.returncode == 2 and "must not connect as the runtime role" in as_runtime.stdout
    assert as_superuser.returncode == 2 and "superuser" in as_superuser.stdout
    assert as_non_owner.returncode == 2 and "does not own the target database" in as_non_owner.stdout


def test_a_runtime_role_that_does_not_exist_is_refused_and_one_is_required(restore_world, backup_files):
    with scratch_target(world=restore_world) as target:
        missing = run_restore(backup_files, target.owner_url, runtime_role="no_such_role_anywhere")
        unset = run_restore(backup_files, target.owner_url, runtime_role=None)

    assert missing.returncode == 2 and "runtime role does not exist" in missing.stdout
    assert unset.returncode == 2 and "RUNTIME_DB_ROLE is required" in unset.stdout


# --- the dump and its manifest ----------------------------------------------------------------------------------------------


def test_a_dump_that_does_not_match_its_manifest_is_refused(restore_world, backup_files):
    with scratch_target(world=restore_world) as target:
        original = backup_files.dump.read_bytes()
        for damaged in (original[:-5], original + b"x", b"y" + original[1:]):  # truncated, extended, one byte changed
            backup_files.dump.write_bytes(damaged)
            result = run_restore(backup_files, target.owner_url, runtime_role=target.app)
            assert result.returncode == 2 and "does not match its manifest" in result.stdout
        assert table_count(restore_world, target.database) == 0


def test_a_missing_or_foreign_manifest_is_refused(restore_world, backup_files):
    with scratch_target(world=restore_world) as target:
        document = backup_files.manifest.read_text(encoding="utf-8")
        backup_files.manifest.write_text("not json", encoding="utf-8")
        assert run_restore(backup_files, target.owner_url, runtime_role=target.app).returncode == 2
        backup_files.manifest.write_text(json.dumps({**json.loads(document), "format": "something-else"}), encoding="utf-8")
        assert run_restore(backup_files, target.owner_url, runtime_role=target.app).returncode == 2
        backup_files.manifest.unlink()
        assert run_restore(backup_files, target.owner_url, runtime_role=target.app).returncode == 2


# --- the target's state ------------------------------------------------------------------------------------------------------------


def test_a_non_empty_target_is_refused_unless_reset_is_explicit(restore_world, backup_files):
    with scratch_target(world=restore_world) as target:
        with psycopg.connect(psycopg_url(target.owner_url), autocommit=True) as connection:
            connection.execute("CREATE TABLE precious (x integer)")
            connection.execute("INSERT INTO precious VALUES (1)")

        refused = run_restore(backup_files, target.owner_url, runtime_role=target.app)
        assert refused.returncode == 2 and "not empty" in refused.stdout
        assert scalar(target.owner_url, "select count(*) from precious") == 1  # untouched

        # (the fake pg_restore restores nothing, so the grant step is skipped: the point here is the reset)
        reset = run_restore(backup_files, target.owner_url, "--reset-target", "--skip-runtime-grants", runtime_role=None)
        assert reset.returncode == 0, reset.stdout
        assert scalar(target.owner_url, "select to_regclass('precious') is null") is True


def test_reset_only_touches_the_target_database(restore_world, backup_files):
    before = fingerprint(restore_world)
    with scratch_target(world=restore_world) as target:
        with psycopg.connect(psycopg_url(target.owner_url), autocommit=True) as connection:
            connection.execute("CREATE TABLE precious (x integer)")

        run_restore(backup_files, target.owner_url, "--reset-target", "--skip-runtime-grants", runtime_role=None)

    assert fingerprint(restore_world) == before


# --- pg_restore ------------------------------------------------------------------------------------------------------------------


def test_pg_restore_is_called_with_the_safe_options_and_the_password_only_in_its_environment(restore_world, backup_files, tmp_path):
    log = tmp_path / "calls.log"
    with scratch_target(world=restore_world) as target:
        run_restore(backup_files, target.owner_url, "--skip-runtime-grants", runtime_role=None, log=log)

    call = next(json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if "--single-transaction" in line)
    assert {"--no-owner", "--no-acl", "--single-transaction", "--exit-on-error"} <= set(call["argv"])
    assert any(re.fullmatch(r"--dbname=rt_\w+_restore_test", argument) for argument in call["argv"])
    assert call["has_pgpassword"] is True and call["ambient_database_url"] is False
    assert all(restore_world.passwords["owner"] not in argument and "://" not in argument for argument in call["argv"])


def test_a_failed_pg_restore_exits_nonzero_and_leaks_no_credential(restore_world, backup_files):
    with scratch_target(world=restore_world) as target:
        result = run_restore(backup_files, target.owner_url, runtime_role=target.app, mode="fail")

        assert result.returncode == 1 and "pg_restore failed" in result.stdout
        output = result.stdout + result.stderr
        assert restore_world.passwords["owner"] not in output and "10.9.8.7" not in output and "postgresql://" not in output
        assert table_count(restore_world, target.database) == 0


def test_the_tool_prints_no_credential_on_success_or_refusal(restore_world, backup_files):
    with scratch_target(world=restore_world) as target:
        refused = run_restore(backup_files, target.app_url, runtime_role=target.app)
        done = run_restore(backup_files, target.owner_url, "--skip-runtime-grants", runtime_role=None)

    for result in (refused, done):
        for secret in (*restore_world.passwords.values(), "postgresql://"):
            assert secret not in result.stdout + result.stderr
