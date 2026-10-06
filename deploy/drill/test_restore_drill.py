"""The restore drill (D4): a real backup of a real, representative database, restored into a SEPARATE database in a SEPARATE
"recovery environment" with DIFFERENT role names, verified, and served by the real backend image as the restricted runtime role.

Everything runs in throw-away containers (two PostgreSQL 17 servers on tmpfs, one Docker network and volume) with the production
backend image, which carries the real PostgreSQL 17 client programs. No development or production database is involved; nothing
here can reach one. The steps (docs/backup-restore.md, "The drill"):

   1  create the source database          2  migrate it with the real migration job (owner role, grants)
   3  fill it through the application     4  freeze invoice PDFs, create sessions/invitations (the fixture does 3 and 4)
   5  fingerprint the source              6  back up (read-only backup role, snapshot, custom format) + manifest
   7  create the SEPARATE restore database and its scratch roles (different names)
   8  restore (restore tool: guards, pg_restore, role reconciliation)     9  verify_restore (read-only; app-role DDL denied)
  10  start the real backend image against the restored database as the RESTRICTED role
  11  readiness, session lookup, tenant-scoped read, invoice read, PDF retrieval, cross-tenant 404
  12  PDF bytes and SHA-256 from the restored backend equal the source's    13  restored fingerprint equals the source's
  14  the source is unchanged (fingerprint)  15  timings recorded           16  negative: a damaged dump is refused/failed
  17  destroy the scratch databases, containers, volume and images

    cd backend && uv run pytest ../deploy/drill -q -s      (-s prints the measured record)

It is NOT part of `deploy/tests` (the `containers` job of ci.yml, a merge gate): it runs from `.github/workflows/restore-drill.yml`
(on demand and weekly), so a slow or flaky disposable drill can never block a merge, while recoverability is still exercised regularly.
"""

import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from drill_support import BACKEND_DIR, BFF_SECRET, RUN, SECURITY_KEY, docker, host_port

NETWORK = f"bp-d4-{RUN}"
VOLUME = f"bp-d4-backups-{RUN}"
SENTINEL_ORG_A = "Sentinel Stable AB"


def run_in_image(image: str, module: str, env: dict[str, str], *args: str, timeout: int = 900) -> tuple[subprocess.CompletedProcess, float]:
    """An operator tool as a one-shot container of the production image (the shape D5 will run), timed."""
    command = ["run", "--rm", "--network", NETWORK, "-v", f"{VOLUME}:/backups"]
    for key, value in env.items():
        command += ["-e", f"{key}={value}"]
    command += [image, "python", "-m", f"app.scripts.{module}", *args]
    started = time.monotonic()
    result = docker(*command, check=False, timeout=timeout)
    return result, time.monotonic() - started


def must(result_and_time, what: str):
    result, seconds = result_and_time
    assert result.returncode == 0, f"{what} failed ({result.returncode}):\n{result.stdout[-3000:]}\n{result.stderr[-2000:]}"
    return result, seconds


class PostgresServer:
    def __init__(self, name: str):
        self.name = f"bp-d4-{name}-{RUN}"
        self.superuser_password = secrets.token_hex(12)
        docker(
            "run", "-d", "--name", self.name, "--network", NETWORK, "-e", "POSTGRES_USER=bp", "-e", f"POSTGRES_PASSWORD={self.superuser_password}",
            "-e", "POSTGRES_DB=postgres", "--tmpfs", "/var/lib/postgresql/data", "-p", "127.0.0.1::5432", "postgres:17",
        )
        for _ in range(60):
            if docker("exec", self.name, "pg_isready", "-U", "bp", "-d", "postgres", check=False).returncode == 0 and self.psql("select 1", "postgres", check=False) == "1":
                break
            time.sleep(1)
        else:
            raise AssertionError("PostgreSQL did not start:\n" + docker("logs", self.name, check=False).stdout[-1500:])
        self.port = host_port(self.name, 5432)

    def psql(self, query: str, database: str, *, check: bool = True) -> str:
        result = docker("exec", self.name, "psql", "-U", "bp", "-d", database, "-At", "-v", "ON_ERROR_STOP=1", "-c", query, check=check)
        return result.stdout.strip()

    def url(self, role: str, password: str, database: str, *, host: str | None = None, port: int = 5432, scheme: str = "postgresql") -> str:
        return f"{scheme}://{role}:{password}@{host or self.name}:{port}/{database}"

    def remove(self) -> None:
        docker("rm", "-f", "-v", self.name, check=False)


class Roles:
    def __init__(self, prefix: str, with_backup: bool):
        self.owner, self.app = f"{prefix}_owner", f"{prefix}_app"
        self.backup = f"{prefix}_backup" if with_backup else None
        self.passwords = {name: secrets.token_hex(12) for name in ("owner", "app", "backup")}

    def bootstrap_env(self, url: str) -> dict[str, str]:
        env = {"BOOTSTRAP_DATABASE_URL": url, "OWNER_ROLE": self.owner, "OWNER_PASSWORD": self.passwords["owner"], "APP_ROLE": self.app, "APP_PASSWORD": self.passwords["app"]}
        if self.backup:
            env.update({"BACKUP_ROLE": self.backup, "BACKUP_PASSWORD": self.passwords["backup"]})
        return env


def probe(container: str, path: str, headers: dict[str, str] | None = None) -> tuple[int, bytes, dict[str, str]]:
    """An HTTP GET made from inside the (unpublished) backend container."""
    script = (
        "import sys, json, base64, urllib.request, urllib.error\n"
        "req = urllib.request.Request('http://127.0.0.1:8000' + sys.argv[1], headers=json.loads(sys.argv[2]))\n"
        "try:\n    r = urllib.request.urlopen(req, timeout=30); status, body, hdrs = r.status, r.read(), dict(r.headers)\n"
        "except urllib.error.HTTPError as e:\n    status, body, hdrs = e.code, e.read(), dict(e.headers)\n"
        "print(json.dumps({'status': status, 'body': base64.b64encode(body).decode(), 'headers': {k.lower(): v for k, v in hdrs.items()}}))\n"
    )
    out = docker("exec", container, "python", "-c", script, path, json.dumps(headers or {})).stdout
    document = json.loads(out.strip().splitlines()[-1])
    return document["status"], base64.b64decode(document["body"]), document["headers"]


def build_fixture(url: str) -> dict:
    """The representative fixture, built by the application code from the host checkout (the image carries no tests), connected as
    the source's restricted runtime role through the published port."""
    environment = {key: value for key, value in os.environ.items() if key not in {"DATABASE_URL", "MIGRATION_DATABASE_URL", "BFF_INTERNAL_SECRET", "SECURITY_KEY"}}
    environment.update({"DATABASE_URL": url, "APP_ENV": "development", "AUTH_MODE": "dev", "PYTHONPATH": str(BACKEND_DIR)})
    result = subprocess.run([sys.executable, "-m", "tests.restore_fixture"], cwd=BACKEND_DIR, env=environment, capture_output=True, text=True, timeout=600, check=False)
    assert result.returncode == 0, result.stdout[-1500:] + result.stderr[-2500:]
    return json.loads([line for line in result.stdout.splitlines() if line.startswith("{") and '"org_a"' in line][-1])


def fingerprint(image: str, url: str) -> str:
    result, _ = must(run_in_image(image, "verify_restore", {"VERIFY_DATABASE_URL": url}, "--fingerprint"), "fingerprint")
    return json.loads(result.stdout)["fingerprint"]


@pytest.fixture(scope="module")
def drill(backend_image):
    docker("network", "create", NETWORK)
    docker("volume", "create", VOLUME)
    # a named volume is created root-owned; the tools run as the image's non-root user
    docker("run", "--rm", "-v", f"{VOLUME}:/backups", "--user", "0", "--entrypoint", "chown", backend_image, "10001:10001", "/backups")
    servers = []
    backend_container = None
    try:
        record: dict = {}
        source, recovery = PostgresServer("src"), PostgresServer("dst")
        servers += [source, recovery]
        src, dst = Roles("src", with_backup=True), Roles("dst", with_backup=False)  # the recovery environment's roles are NOT the source's
        record["postgres_version"] = source.psql("show server_version", "postgres")

        # 1-2. the source database, its roles, the real migration job
        source.psql("CREATE DATABASE bp_src", "postgres")
        must(run_in_image(backend_image, "bootstrap_roles", src.bootstrap_env(source.url("bp", source.superuser_password, "bp_src"))), "source role bootstrap")
        migrated, _ = must(run_in_image(backend_image, "migrate", {"APP_ENV": "production", "MIGRATION_DATABASE_URL": source.url(src.owner, src.passwords["owner"], "bp_src", scheme="postgresql+psycopg"), "RUNTIME_DB_ROLE": src.app}), "source migration")

        # 3-4. fill it through the application as the restricted runtime role
        facts = build_fixture(source.url(src.app, src.passwords["app"], "bp_src", host="127.0.0.1", port=source.port, scheme="postgresql+psycopg"))
        source_backup_url = source.url(src.backup, src.passwords["backup"], "bp_src")
        record["source_fingerprint"] = fingerprint(backend_image, source_backup_url)
        record["pdf_hashes_in_source"] = dict(row.split("|") for row in source.psql("select invoice_id || '|' || sha256 from invoice_pdfs", "bp_src").splitlines())

        # 6. the backup, as the read-only backup role, with the tool's own (non-fake) pg_dump
        backup_result, record["backup_wall_seconds"] = must(
            run_in_image(backend_image, "backup", {"BACKUP_DATABASE_URL": source_backup_url, "APP_GIT_REVISION": "drill"}, "--output", "/backups/bp-drill.dump", "--label", "drill"), "backup"
        )
        manifest_text = docker("run", "--rm", "-v", f"{VOLUME}:/backups", "--entrypoint", "cat", backend_image, "/backups/bp-drill.dump.manifest.json").stdout
        manifest = json.loads(manifest_text)
        record["backup_log"] = backup_result.stdout
        record["manifest"] = manifest
        record["source_fingerprint_after_backup"] = fingerprint(backend_image, source_backup_url)

        # 7-8. the separate restore database and its scratch roles, then the restore itself
        recovery.psql("CREATE DATABASE bp_drill_restore", "postgres")
        must(run_in_image(backend_image, "bootstrap_roles", dst.bootstrap_env(recovery.url("bp", recovery.superuser_password, "bp_drill_restore"))), "recovery role bootstrap")
        target_url = recovery.url(dst.owner, dst.passwords["owner"], "bp_drill_restore")
        restore_env = {"RESTORE_DATABASE_URL": target_url, "RUNTIME_DB_ROLE": dst.app}
        restore_result, record["restore_wall_seconds"] = must(run_in_image(backend_image, "restore", restore_env, "--dump", "/backups/bp-drill.dump"), "restore")
        record["restore_log"] = restore_result.stdout

        # 9. verification of the restored database (read-only), with the scratch app role's credentials for the role checks
        verify_env = {"VERIFY_DATABASE_URL": target_url, "VERIFY_APP_DATABASE_URL": recovery.url(dst.app, dst.passwords["app"], "bp_drill_restore")}
        verified, record["verify_wall_seconds"] = run_in_image(backend_image, "verify_restore", verify_env, "--manifest", "/backups/bp-drill.dump.manifest.json", "--alembic-check", "--fingerprint")
        record["verify_returncode"], record["verify_output"] = verified.returncode, verified.stdout
        record["restored_fingerprint"] = fingerprint(backend_image, target_url)

        # 10. the real backend image against the restored database, as the RESTRICTED runtime role
        backend_container = f"bp-d4-be-{RUN}"
        docker(
            "run", "-d", "--name", backend_container, "--network", NETWORK,
            "-e", "APP_ENV=production", "-e", "AUTH_MODE=session", "-e", f"SECURITY_KEY={SECURITY_KEY}", "-e", f"BFF_INTERNAL_SECRET={BFF_SECRET}",
            "-e", "PUBLIC_ORIGIN=https://app.example.test", "-e", "CORS_ORIGINS=[]",
            "-e", "DATABASE_URL=" + recovery.url(dst.app, dst.passwords["app"], "bp_drill_restore", scheme="postgresql+psycopg"),
            backend_image,
        )
        for _ in range(60):
            if json.loads(docker("inspect", backend_container).stdout)[0]["State"]["Health"]["Status"] == "healthy":
                break
            time.sleep(1)
        else:
            raise AssertionError("the backend never became ready on the restored database:\n" + docker("logs", backend_container, check=False).stdout[-2000:])

        smoke = {}
        secret = {"x-bff-secret": BFF_SECRET}
        bearer = {"Authorization": f"Bearer {facts['session_token']}"}
        smoke["ready"] = probe(backend_container, "/health/ready")[0]
        smoke["me"] = probe(backend_container, "/api/me/user", {**secret, **bearer})
        smoke["organizations"] = probe(backend_container, "/api/me/organizations", {**secret, **bearer})
        org_a = {**secret, **bearer, "X-Organization-Id": facts["org_a"]}
        smoke["customers"] = probe(backend_container, "/api/customers", org_a)
        smoke["invoice"] = probe(backend_container, f"/api/invoices/{facts['invoices']['a']['id']}", org_a)
        smoke["foreign_invoice"] = probe(backend_container, f"/api/invoices/{facts['invoices']['b']['id']}", org_a)
        smoke["pdf"] = probe(backend_container, f"/api/invoices/{facts['invoices']['a']['id']}/pdf", org_a)
        smoke["no_token"] = probe(backend_container, "/api/customers", {**secret, "X-Organization-Id": facts["org_a"]})
        record["smoke"] = smoke
        record["backend_connection_roles"] = recovery.psql("select distinct usename from pg_stat_activity where datname = 'bp_drill_restore' and usename in ('%s', '%s') order by 1" % (dst.owner, dst.app), "postgres")
        record["restored_pdf_hashes"] = dict(row.split("|") for row in recovery.psql("select invoice_id || '|' || sha256 from invoice_pdfs", "bp_drill_restore").splitlines())

        # 16. a damaged dump with a manifest that agrees with it: the manifest guard passes, pg_restore itself must fail
        make_damaged = (
            "import hashlib, json\n"
            "data = open('/backups/bp-drill.dump', 'rb').read()\n"
            "bad = data[: len(data) // 2]\n"
            "open('/backups/bad.dump', 'wb').write(bad)\n"
            "m = json.load(open('/backups/bp-drill.dump.manifest.json'))\n"
            "m['dump'] = {'file': 'bad.dump', 'bytes': len(bad), 'sha256': hashlib.sha256(bad).hexdigest()}\n"
            "json.dump(m, open('/backups/bad.dump.manifest.json', 'w'))\n"
        )
        docker("run", "--rm", "-v", f"{VOLUME}:/backups", backend_image, "python", "-c", make_damaged)
        recovery.psql("CREATE DATABASE bp_drill_bad_restore", "postgres")
        must(run_in_image(backend_image, "bootstrap_roles", dst.bootstrap_env(recovery.url("bp", recovery.superuser_password, "bp_drill_bad_restore"))), "bad target bootstrap")
        bad_env = {"RESTORE_DATABASE_URL": recovery.url(dst.owner, dst.passwords["owner"], "bp_drill_bad_restore"), "RUNTIME_DB_ROLE": dst.app}
        damaged, _ = run_in_image(backend_image, "restore", bad_env, "--dump", "/backups/bad.dump")
        record["damaged"] = SimpleNamespace(returncode=damaged.returncode, output=damaged.stdout + damaged.stderr, tables=recovery.psql("select count(*) from pg_class where relnamespace = 'public'::regnamespace", "bp_drill_bad_restore"))
        again, _ = run_in_image(backend_image, "restore", restore_env, "--dump", "/backups/bp-drill.dump")  # onto the now non-empty restore database
        record["non_empty"] = SimpleNamespace(returncode=again.returncode, output=again.stdout)

        record["source_fingerprint_at_end"] = fingerprint(backend_image, source_backup_url)
        record["facts"] = facts
        record["roles"] = {"source": SimpleNamespace(owner=src.owner, app=src.app, backup=src.backup), "recovery": SimpleNamespace(owner=dst.owner, app=dst.app), "passwords": [*src.passwords.values(), *dst.passwords.values(), source.superuser_password, recovery.superuser_password]}
        record["recovery_roles_query"] = recovery.psql(f"select rolname || ':' || rolsuper::text from pg_roles where rolname in ('{dst.owner}', '{dst.app}') order by 1", "postgres")
        record["restored_owner_of_tables"] = recovery.psql("select distinct tableowner from pg_tables where schemaname = 'public'", "bp_drill_restore")
        record["restored_database_bytes"] = int(recovery.psql("select pg_database_size('bp_drill_restore')", "postgres"))
        yield SimpleNamespace(**record)
    finally:
        if backend_container:
            docker("rm", "-f", backend_container, check=False)
        for server in servers:
            server.remove()
        docker("volume", "rm", "-f", VOLUME, check=False)
        docker("network", "rm", NETWORK, check=False)


# --- the drill's facts ----------------------------------------------------------------------------------------------------------


def test_the_backup_is_a_whole_database_custom_format_dump_with_a_manifest(drill):
    manifest = drill.manifest
    assert manifest["format"] == "bp-backup/1" and manifest["label"] == "drill" and manifest["git_revision"] == "drill"
    assert manifest["pg_dump_major_version"] == 17 and manifest["postgres_server_version"].startswith("17")
    assert manifest["dump"]["bytes"] > 0 and len(manifest["dump"]["sha256"]) == 64
    for table in ("organizations", "users", "organization_users", "user_credentials", "auth_sessions", "user_setup_tokens", "organization_invitations", "security_events", "customers", "items", "horses", "transactions", "invoices", "invoice_pdfs", "custom_field_values", "alembic_version"):
        assert manifest["table_counts"][table] > 0, table  # nothing is excluded, and the fixture is representative
    assert manifest["invoice_pdfs"]["count"] == 2 and manifest["invoice_pdfs"]["bytes"] > 40_000
    assert manifest["toc_entries"] > 100  # tables, indexes, triggers, functions, constraints, sequences, data


def test_the_manifest_holds_no_credential_or_tenant_data(drill):
    text = json.dumps(drill.manifest) + drill.backup_log + drill.restore_log + drill.verify_output
    for forbidden in (*drill.roles["passwords"], "restore-fixture.invalid", "Sentinel", drill.facts["session_token"], "postgresql://", "argon2"):
        assert forbidden not in text, forbidden


def test_the_restore_verified_clean_in_a_database_with_different_role_names(drill):
    assert drill.verify_returncode == 0, drill.verify_output
    report = json.loads(drill.verify_output)
    assert report["ok"] is True and all(check["ok"] for check in report["checks"])
    names = {check["name"] for check in report["checks"]}
    assert {"schema.exact_head", "schema.alembic_check", "objects.triggers", "objects.functions", "pdf.integrity", "auth.coherent", "roles.runtime_role", "data.counts_match_manifest"} <= names
    assert drill.recovery_roles_query.splitlines() == [f"{drill.roles['recovery'].app}:false", f"{drill.roles['recovery'].owner}:false"]  # neither is a superuser
    assert drill.restored_owner_of_tables == drill.roles["recovery"].owner  # objects belong to the scratch OWNER role: no source role name survived


def test_the_restored_database_equals_the_source_content_for_content(drill):
    assert drill.restored_fingerprint == drill.source_fingerprint


def test_the_source_was_never_changed_by_backup_restore_or_verification(drill):
    assert drill.source_fingerprint_after_backup == drill.source_fingerprint
    assert drill.source_fingerprint_at_end == drill.source_fingerprint


def test_the_real_backend_serves_the_restored_database_as_the_restricted_role(drill):
    smoke = drill.smoke
    assert smoke["ready"] == 200
    assert smoke["me"][0] == 200 and json.loads(smoke["me"][1])["email"] == drill.facts["session_user"]  # the session created BEFORE the backup still works
    assert smoke["organizations"][0] == 200 and {o["name"] for o in json.loads(smoke["organizations"][1])} == {"Sentinel Stable AB", "Sentinel Clinic AB"}  # two memberships, two roles
    assert smoke["customers"][0] == 200 and {c["name"] for c in json.loads(smoke["customers"][1])} >= {"Anna Sentinel"}
    assert smoke["invoice"][0] == 200 and json.loads(smoke["invoice"][1])["status"] == "issued"
    assert smoke["foreign_invoice"][0] == 404  # tenant isolation survived the round trip
    assert smoke["no_token"][0] in (401, 403)


def test_the_backend_really_ran_as_the_restricted_runtime_role(drill):
    assert drill.backend_connection_roles == drill.roles["recovery"].app  # not the owner: the smoke test proves what the app role can do


def test_the_restored_pdf_is_byte_for_byte_the_frozen_one(drill):
    status, body, headers = drill.smoke["pdf"]
    assert status == 200 and body.startswith(b"%PDF-")
    assert len(body) == drill.facts["invoices"]["a"]["pdf_bytes"]
    assert hashlib.sha256(body).hexdigest() == drill.pdf_hashes_in_source[drill.facts["invoices"]["a"]["id"]]
    assert drill.restored_pdf_hashes == drill.pdf_hashes_in_source  # both frozen artifacts


def test_a_damaged_dump_with_a_matching_manifest_fails_in_pg_restore_and_leaves_the_target_empty(drill):
    assert drill.damaged.returncode == 1 and "pg_restore failed" in drill.damaged.output
    assert drill.damaged.tables == "0"  # the single transaction rolled everything back
    for secret in drill.roles["passwords"]:
        assert secret not in drill.damaged.output


def test_restoring_onto_a_database_that_already_holds_data_is_refused(drill):
    assert drill.non_empty.returncode == 2 and "not empty" in drill.non_empty.output


def test_the_measured_baseline_is_recorded(drill):
    record = {
        "postgres": drill.postgres_version,
        "dump_bytes": drill.manifest["dump"]["bytes"], "database_bytes": drill.manifest["database_bytes"], "pdf_bytes": drill.manifest["invoice_pdfs"]["bytes"],
        "backup_seconds_in_tool": drill.manifest["duration_seconds"], "backup_seconds_with_container_start": round(drill.backup_wall_seconds, 1),
        "restore_seconds_with_container_start": round(drill.restore_wall_seconds, 1), "verify_seconds_with_container_start": round(drill.verify_wall_seconds, 1),
        "restored_database_bytes": drill.restored_database_bytes,
    }
    print("\nRESTORE DRILL RECORD " + json.dumps(record, sort_keys=True))
    assert drill.restore_wall_seconds < 300 and drill.backup_wall_seconds < 300  # a synthetic database: a sanity bound, not an RTO
