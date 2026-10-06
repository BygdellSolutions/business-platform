"""The runbooks must keep saying what the tools do (and must not start recommending what they refuse). These are static checks over
docs/backup-restore.md and docs/deployment.md: the commands, flags and variables named there exist in the tools, and the operational rules
(a verified backup before a migration, recovery into a separate database, never in place, retention, RPO) are stated."""

import re
from pathlib import Path

from app.scripts import restore

ROOT = Path(__file__).resolve().parents[2]
BACKUP_DOC = (ROOT / "docs" / "backup-restore.md").read_text(encoding="utf-8")
DEPLOY_DOC = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
SCRIPTS = ROOT / "backend" / "app" / "scripts"


def source(tool: str) -> str:
    return (SCRIPTS / f"{tool}.py").read_text(encoding="utf-8")


TOOLS = {
    "backup": (["--output", "--label", "--git-revision"], ["BACKUP_DATABASE_URL"]),
    "restore": (["--dump", "--manifest", "--reset-target", "--skip-runtime-grants"], ["RESTORE_DATABASE_URL", "RUNTIME_DB_ROLE"]),
    "verify_restore": (["--manifest", "--alembic-check", "--fingerprint"], ["VERIFY_DATABASE_URL", "VERIFY_APP_DATABASE_URL"]),
    "db_stats": ([], ["STATS_DATABASE_URL"]),
    "bootstrap_roles": ([], ["BOOTSTRAP_DATABASE_URL", "BACKUP_ROLE", "BACKUP_PASSWORD"]),
}


def test_every_tool_command_flag_and_variable_in_the_runbook_exists_in_the_tool_and_is_documented():
    for tool, (flags, variables) in TOOLS.items():
        assert f"python -m app.scripts.{tool}" in BACKUP_DOC + DEPLOY_DOC, tool
        for flag in flags:
            assert f'"{flag}"' in source(tool), (tool, flag, "the documented flag is not in the tool")
            assert flag in BACKUP_DOC, (tool, flag, "the tool's flag is not documented")
        for variable in variables:
            assert variable in source(tool), (tool, variable)
            assert variable in BACKUP_DOC + DEPLOY_DOC, (tool, variable)


def test_the_documented_scratch_name_rule_is_the_one_the_restore_tool_enforces():
    assert restore.NAME_GUARD.pattern in BACKUP_DOC


def test_the_runbook_says_recover_into_a_separate_database_and_never_in_place():
    lowered = BACKUP_DOC.lower()
    assert "never a destructive in-place restore" in lowered
    assert "new, separate" in lowered and "verify_restore" in lowered and "switch" in lowered
    for forbidden in ("restore in place", "restore it in place", "restore over the live", "restore into the production database", "drop the production"):
        for found in re.finditer(re.escape(forbidden), lowered):
            assert re.search(r"\b(never|not|no)\b[^.\n]{0,40}$", lowered[max(0, found.start() - 60) : found.start()]), (forbidden, "recommended, not forbidden")
    assert "in-place restore" in DEPLOY_DOC.lower() and "never" in DEPLOY_DOC.lower()


def test_the_deployment_runbook_states_the_pre_migration_backup_contract():
    lowered = DEPLOY_DOC.lower()
    assert "pre-migration backup" in lowered
    assert "must exit `0`" in lowered and "before the migration runs" in lowered
    assert "never backs up silently" in lowered
    assert lowered.index("take a backup") < lowered.index("only then run the migration job")
    assert "pre-migration" in BACKUP_DOC.lower() and "does **not** back up silently" in BACKUP_DOC


def test_the_runbooks_state_the_agreed_objectives_and_contracts():
    for fact in ("up to 24 hours", "14 daily, 8 weekly and 6 monthly", "2 GB", "15 minutes", "baseline, not an RTO", "never overwrites", "wal", "pitr"):
        assert fact in BACKUP_DOC or fact in BACKUP_DOC.lower(), fact
    for fact in ("outside the database host", "tls", "client-side encryption", "least-privilege", "cannot delete backup history", "no key and no credential is in this repository"):
        assert fact in BACKUP_DOC.lower(), fact
    for fact in ("exact head", "liveness", "readiness", "no automatic `alembic downgrade`", "fix forward", "source-ip trust", "d5 verification items"):
        assert fact in DEPLOY_DOC.lower() or fact.replace("source-ip trust", "source-ip trust stays off") in DEPLOY_DOC.lower(), fact


def test_the_first_operator_runbook_matches_the_admin_command():
    admin = source("admin")
    for command in ("bootstrap-user", "reissue-setup-link"):
        assert command in admin and command in DEPLOY_DOC
    for fact in ("PUBLIC_ORIGIN", "single-use", "fragment", "SETUP_TOKEN_TTL_HOURS", "**24**", "no public registration", "no seed data"):
        assert fact.lower() in DEPLOY_DOC.lower(), fact
    assert "seed_dev" not in DEPLOY_DOC  # production never runs the development seed


def test_no_runbook_contains_a_credential_or_a_real_connection_string():
    for name, body in (("backup-restore.md", BACKUP_DOC), ("deployment.md", DEPLOY_DOC)):
        for match in re.finditer(r"[a-z][a-z0-9+.-]*://([^\s/'\"`]+)", body):
            authority = match.group(1)
            assert authority.startswith("<") or "@" not in authority or authority.split("@")[0].split(":", 1)[-1].startswith(("<", ".")), (name, authority)
        assert not re.search(r"(?i)(password|secret|token|key)\s*=\s*[A-Za-z0-9+/_-]{16,}", body), name


KNOWN_FLAGS = {flag for flags, _ in TOOLS.values() for flag in flags} | {
    # other programs' options that the runbooks mention
    "--format", "--no-owner", "--no-acl", "--snapshot", "--single-transaction", "--exit-on-error", "--lock-timeout", "--list", "--email",
    "--name", "--no-org-creation", "--exclude-table", "--wait",
}


def test_every_option_the_runbooks_mention_is_a_real_one():
    """A runbook that tells an operator to pass a flag the tool does not have fails at the worst moment."""
    for name, body in (("backup-restore.md", BACKUP_DOC), ("deployment.md", DEPLOY_DOC)):
        mentioned = set(re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", body))
        assert mentioned <= KNOWN_FLAGS, (name, sorted(mentioned - KNOWN_FLAGS))
