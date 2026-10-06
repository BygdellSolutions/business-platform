"""The backup tool: where it reads from, what it writes, what it refuses, and that a failure leaves no backup behind.

The PostgreSQL clients are replaced by tests/fake_pg.py so the TOOL's logic is what is under test (the real pg_dump/pg_restore run in the
container drill). The database is real: the manifest's counts and revision are read from the fixture, as the READ-ONLY backup role.
"""

import hashlib
import json
import re

import pytest

from app.core import migration
from app.scripts import backup
from tests.fake_pg import DUMP_BYTES
from tests.restore_support import fake_pg_env, restore_world, run_tool  # noqa: F401  (restore_world is a fixture)

PASSWORD_WORDS = ("password=", "postgresql://", "postgresql+psycopg://")


def run_backup(world, tmp_path, *extra: str, name="bp.dump", env=None, mode="ok", url=None, label="test"):
    log = tmp_path / "fake.log"
    environment = {"BACKUP_DATABASE_URL": url or world.backup_url, **fake_pg_env(mode, log=log), **(env or {})}
    return run_tool("app.scripts.backup", "--output", str(tmp_path / name), "--label", label, *extra, env=environment), log


def calls(log):
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


def files(tmp_path) -> list[str]:
    return sorted(path.name for path in tmp_path.iterdir() if path.name != "fake.log")


# --- a successful backup -----------------------------------------------------------------------------------------------------


def test_a_backup_writes_a_dump_and_a_manifest_that_describe_each_other(restore_world, tmp_path):
    result, _ = run_backup(restore_world, tmp_path, "--git-revision", "abc1234")

    assert result.returncode == 0, result.stdout + result.stderr
    assert files(tmp_path) == ["bp.dump", "bp.dump.manifest.json"]  # no partial file is left
    manifest = json.loads((tmp_path / "bp.dump.manifest.json").read_text(encoding="utf-8"))
    dump = (tmp_path / "bp.dump").read_bytes()
    assert dump == DUMP_BYTES
    assert manifest["format"] == "bp-backup/1"
    assert manifest["dump"] == {"file": "bp.dump", "bytes": len(dump), "sha256": hashlib.sha256(dump).hexdigest()}
    assert manifest["label"] == "test" and manifest["git_revision"] == "abc1234"
    assert manifest["alembic_revisions"] == list(migration.code_heads()) == manifest["code_alembic_heads"]
    assert manifest["source"] == {"database": restore_world.database}
    assert manifest["table_counts"]["customers"] == 4  # two organizations, two customers each (the sentinel and the stable)
    assert manifest["table_counts"]["invoice_pdfs"] == manifest["invoice_pdfs"]["count"] == 2
    assert manifest["invoice_pdfs"]["bytes"] == sum(inv["pdf_bytes"] for inv in restore_world.facts["invoices"].values())
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", manifest["created_at_utc"])
    assert manifest["pg_dump_major_version"] == 17 and manifest["postgres_server_version"]
    assert manifest["duration_seconds"] >= 0 and manifest["database_bytes"] > 0
    assert "--no-password" not in manifest["dump_options"] and "--format=custom" in manifest["dump_options"]


def test_the_manifest_and_the_output_hold_no_credential_or_tenant_data(restore_world, tmp_path):
    result, _ = run_backup(restore_world, tmp_path)
    text = (tmp_path / "bp.dump.manifest.json").read_text(encoding="utf-8") + result.stdout + result.stderr

    for secret in (*restore_world.passwords.values(), restore_world.backup, *PASSWORD_WORDS, "restore-fixture.invalid", "Sentinel", restore_world.facts["session_token"]):
        assert secret not in text, secret


def test_pg_dump_gets_the_password_in_its_environment_and_never_on_its_command_line(restore_world, tmp_path):
    _, log = run_backup(restore_world, tmp_path)

    dump = next(call for call in calls(log) if call["role"] == "dump" and "--format=custom" in call["argv"])
    assert dump["has_pgpassword"] is True
    assert all(restore_world.passwords["backup"] not in argument and "://" not in argument for argument in dump["argv"])
    assert {"--format=custom", "--no-owner", "--no-acl"} <= set(dump["argv"])
    assert any(argument.startswith("--snapshot=") for argument in dump["argv"])  # the manifest and the dump describe ONE snapshot
    assert dump["pguser"] == restore_world.backup and dump["pgdatabase"] == restore_world.database
    assert dump["ambient_database_url"] is False


def test_the_whole_database_is_dumped_not_a_selection(restore_world, tmp_path):
    _, log = run_backup(restore_world, tmp_path)

    arguments = next(call["argv"] for call in calls(log) if "--format=custom" in call["argv"])
    for selective in ("--table", "-t", "--exclude-table", "-T", "--exclude-table-data", "--schema", "-n", "--exclude-schema", "-N", "--data-only", "--schema-only", "--section"):
        assert not any(argument == selective or argument.startswith(selective + "=") for argument in arguments), selective


# --- refusals ---------------------------------------------------------------------------------------------------------------


def test_the_source_is_BACKUP_DATABASE_URL_only(restore_world, tmp_path):
    result = run_tool("app.scripts.backup", "--output", str(tmp_path / "x.dump"), "--label", "test", env={**fake_pg_env(), "DATABASE_URL": restore_world.owner_url, "MIGRATION_DATABASE_URL": restore_world.owner_url})

    assert result.returncode == 2 and "BACKUP_DATABASE_URL is not set" in result.stdout
    assert files(tmp_path) == []


def test_an_existing_dump_is_never_overwritten(restore_world, tmp_path):
    (tmp_path / "bp.dump").write_bytes(b"precious")

    result, _ = run_backup(restore_world, tmp_path)

    assert result.returncode == 2 and (tmp_path / "bp.dump").read_bytes() == b"precious"
    assert files(tmp_path) == ["bp.dump"]


def test_an_existing_manifest_or_partial_file_is_never_overwritten_either(restore_world, tmp_path):
    for existing in ("bp.dump.manifest.json", "bp.dump.partial"):
        (tmp_path / existing).write_bytes(b"precious")
        result, _ = run_backup(restore_world, tmp_path)
        assert result.returncode == 2
        assert (tmp_path / existing).read_bytes() == b"precious"
        (tmp_path / existing).unlink()


def test_a_missing_output_directory_a_bad_label_and_an_old_client_are_refused(restore_world, tmp_path):
    missing = run_tool("app.scripts.backup", "--output", str(tmp_path / "nope" / "x.dump"), "--label", "t", env={"BACKUP_DATABASE_URL": restore_world.backup_url, **fake_pg_env()})
    assert missing.returncode == 2

    result, _ = run_backup(restore_world, tmp_path, label="has space!")
    assert result.returncode == 2

    old, _ = run_backup(restore_world, tmp_path, env={"FAKE_PG_VERSION": "12.4"})
    assert old.returncode == 2 and "older than the server" in old.stdout
    assert files(tmp_path) == []


def test_a_missing_pg_dump_is_a_refusal_not_a_crash(restore_world, tmp_path):
    result, _ = run_backup(restore_world, tmp_path, env={"BP_PG_DUMP": str(tmp_path / "does-not-exist")})

    assert result.returncode == 2 and files(tmp_path) == []


# --- failures leave no backup ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["fail", "partial_fail"])
def test_a_failed_pg_dump_exits_nonzero_leaves_nothing_and_leaks_no_credential(restore_world, tmp_path, mode):
    result, _ = run_backup(restore_world, tmp_path, mode=mode)

    assert result.returncode == 1
    assert files(tmp_path) == []  # no final dump, no manifest, no .partial
    output = result.stdout + result.stderr
    assert restore_world.passwords["backup"] not in output and "10.9.8.7" not in output and "postgresql://" not in output


def test_a_dump_pg_restore_cannot_read_is_not_kept(restore_world, tmp_path):
    result, _ = run_backup(restore_world, tmp_path, env={"FAKE_PG_RESTORE_MODE": "unreadable"})

    assert result.returncode == 1 and files(tmp_path) == []


def test_an_unreachable_source_fails_without_a_trace_or_a_credential(restore_world, tmp_path):
    broken = restore_world.backup_url.replace(restore_world.passwords["backup"], "wrong-password-sentinel")

    result, _ = run_backup(restore_world, tmp_path, url=broken)

    assert result.returncode == 1 and files(tmp_path) == []
    assert "wrong-password-sentinel" not in result.stdout + result.stderr


def test_if_the_manifest_cannot_be_published_the_dump_is_removed_too(restore_world, tmp_path, monkeypatch):
    for key, value in {"BACKUP_DATABASE_URL": restore_world.backup_url, **fake_pg_env()}.items():
        monkeypatch.setenv(key, value)
    real = backup._publish
    published = []

    def second_publish_fails(partial, final):
        if published:
            raise OSError("disk full")
        real(partial, final)
        published.append(final)

    monkeypatch.setattr(backup, "_publish", second_publish_fails)

    code = backup.run(["--output", str(tmp_path / "bp.dump"), "--label", "t"])

    assert code == 1 and files(tmp_path) == []  # a dump without its manifest is not a backup
