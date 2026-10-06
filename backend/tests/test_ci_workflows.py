"""Static checks over the CI configuration (.github/): CI must not be able to quietly stop enforcing an important boundary.

These run inside the backend gate itself, so a pull request that removes a gate, weakens one, adds a secret, a real-looking
URL, an unpinned action, an uploaded credential or a fallback to a developer browser or database fails the very job it touches.
Standard-library text analysis plus PyYAML (already a transitive dependency of uvicorn[standard]).
"""

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GITHUB = ROOT / ".github"
WORKFLOWS = GITHUB / "workflows"
CI_PATH, AUDIT_PATH = WORKFLOWS / "ci.yml", WORKFLOWS / "audit.yml"
ACTION_PATH = GITHUB / "actions" / "disposable-db" / "action.yml"
GATES = ["backend", "frontend", "containers", "e2e-dev", "e2e-session"]
ALL_JOBS = ["workflows", *GATES, "ci-gate"]
SHA = re.compile(r"^[0-9a-f]{40}$")


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def code_only(source: str) -> str:
    """The file without YAML comment lines (comments may explain what is forbidden)."""
    return "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))


def triggers(doc: dict) -> dict:
    return doc.get("on") or doc.get(True) or {}  # (PyYAML reads the bare key `on` as the boolean True)


def steps(job: dict) -> list[dict]:
    return job["steps"]


def run_blocks(job: dict) -> list[str]:
    return [step["run"] for step in steps(job) if "run" in step]


def all_runs(path: Path) -> list[str]:
    doc = load(path)
    return [block for job in doc["jobs"].values() for block in run_blocks(job)]


@pytest.fixture(scope="module")
def ci() -> dict:
    return load(CI_PATH)


def job_text(ci: dict, name: str) -> str:
    return yaml.safe_dump(ci["jobs"][name])


# --- structure -------------------------------------------------------------------------------------------------------------------------------


def test_the_expected_files_exist():
    assert CI_PATH.exists() and AUDIT_PATH.exists() and ACTION_PATH.exists()
    assert (GITHUB / "scripts" / "assert_ci_database.py").exists() and (GITHUB / "scripts" / "redact.py").exists()


def test_ci_runs_on_pull_requests_and_main_and_audit_only_on_a_schedule(ci):
    on = triggers(ci)
    assert "pull_request" in on and on["push"]["branches"] == ["main"] and "workflow_dispatch" in on
    audit_on = triggers(load(AUDIT_PATH))
    assert set(audit_on) == {"schedule", "workflow_dispatch"}  # the audit is never a pull-request or push gate


def test_every_gate_exists_and_the_aggregate_requires_all_of_them(ci):
    assert set(ci["jobs"]) == set(ALL_JOBS)
    gate = ci["jobs"]["ci-gate"]
    assert sorted(gate["needs"]) == sorted(["workflows", *GATES])
    assert gate["if"] == "${{ always() }}"  # the gate runs (and fails) even when a needed job failed, was skipped or was cancelled
    body = "\n".join(run_blocks(gate))
    assert 'result == "success"' in body and "all(" in body


def test_concurrency_cancels_obsolete_pull_request_runs_but_never_a_push_to_main(ci):
    concurrency = ci["concurrency"]
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
    assert "github.run_id" in concurrency["group"] and "pull_request.number" in concurrency["group"]  # a push gets a group of its own


def test_every_job_has_a_bounded_timeout():
    for path in (CI_PATH, AUDIT_PATH):
        for name, job in load(path)["jobs"].items():
            assert isinstance(job.get("timeout-minutes"), int) and 1 <= job["timeout-minutes"] <= 45, (path.name, name)


# --- no secrets, no real environments --------------------------------------------------------------------------------------------------------


def test_no_workflow_uses_a_repository_secret_or_a_privileged_trigger():
    for path in [*WORKFLOWS.glob("*.yml"), ACTION_PATH]:
        body = code_only(text(path))
        assert not re.search(r"secrets\.(?!GITHUB_TOKEN\b)", body), path.name
        assert "secrets: inherit" not in body, path.name
        for forbidden in ("pull_request_target", "workflow_run", "self-hosted", "environment:", "id-token"):
            assert forbidden not in body, (path.name, forbidden)


def test_workflows_are_least_privilege():
    for path in WORKFLOWS.glob("*.yml"):
        doc = load(path)
        assert doc["permissions"] == {"contents": "read"}, path.name
        for name, job in doc["jobs"].items():
            assert "permissions" not in job, (path.name, name)  # no job widens the token


def test_checkouts_do_not_persist_credentials():
    for path in WORKFLOWS.glob("*.yml"):
        for job in load(path)["jobs"].values():
            for step in steps(job):
                if step.get("uses", "").startswith("actions/checkout@"):
                    assert step["with"]["persist-credentials"] is False


ALLOWED_URL_HOSTS = {"127.0.0.1", "localhost", "github.com", "docs.github.com"}


def test_no_real_looking_url_or_credential_anywhere_in_ci():
    for path in [*WORKFLOWS.glob("*.yml"), ACTION_PATH, *(GITHUB / "scripts").glob("*.py")]:
        body = code_only(text(path))
        for match in re.finditer(r"[a-z][a-z0-9+.-]*://([^\s/'\"]+)", body):
            authority = match.group(1)
            host = authority.rsplit("@", 1)[-1].split(":")[0]
            if path.suffix == ".py":
                continue  # (the scripts only contain patterns and examples; checked by their own tests)
            assert host in ALLOWED_URL_HOSTS or host.startswith(("$", "{")), (path.name, host)
            if "@" in authority:
                password = authority.split("@")[0].split(":", 1)[-1]
                assert password.startswith(("$", "{")), (path.name, "a literal credential in a URL")
        assert not re.search(r"(?i)\b(coolify|traefik|staging|bygdell)\b", body), path.name
        assert not re.search(r"(?i)(password|secret|token|key)\s*[:=]\s*['\"][A-Za-z0-9+/_-]{12,}['\"]", body), path.name


def test_the_only_database_url_is_the_disposable_loopback_one():
    action = code_only(text(ACTION_PATH))
    urls = re.findall(r"postgresql\+?\w*://[^\s\"']+", action)
    assert urls == ["postgresql+psycopg://ci_admin:${password}@127.0.0.1:5433/business_platform_test"]
    for path in WORKFLOWS.glob("*.yml"):
        body = code_only(text(path))
        assert "DATABASE_URL:" not in body.replace("MIGRATION_DATABASE_URL=", "")  # no env block hands any job a database URL
        assert "TEST_DATABASE_URL:" not in body


# --- supply chain ----------------------------------------------------------------------------------------------------------------------------


ALLOWED_OWNERS = {"actions", "astral-sh", "aquasecurity"}


def uses_lines() -> list[tuple[str, str, str]]:
    found = []
    for path in [*WORKFLOWS.glob("*.yml"), ACTION_PATH]:
        for line in text(path).splitlines():
            match = re.match(r"\s*-?\s*uses:\s*(\S+)(?:\s*#\s*(.*))?$", line)
            if match:
                found.append((path.name, match.group(1), match.group(2) or ""))
    return found


def test_every_external_action_is_a_known_publisher_pinned_to_a_full_commit_sha_with_a_version_comment():
    external = [(f, ref, comment) for f, ref, comment in uses_lines() if not ref.startswith("./")]
    assert len(external) >= 8
    for file, ref, comment in external:
        name, _, pin = ref.partition("@")
        assert name.split("/")[0] in ALLOWED_OWNERS, (file, name)
        assert SHA.match(pin), (file, ref, "pin to an immutable commit SHA")
        assert re.match(r"v\d+(\.\d+){0,2}$", comment.strip()), (file, ref, "the SHA needs a `# vX.Y.Z` comment")


def test_docker_images_run_by_the_workflows_have_an_explicit_version_tag():
    for path in WORKFLOWS.glob("*.yml"):
        for block in all_runs(path):
            for image in re.findall(r"docker run [^\n]*?\b([\w./-]+:[\w.-]+)", block):
                assert not image.endswith(":latest"), (path.name, image)
    assert "rhysd/actionlint:1.7.7" in text(CI_PATH)


# --- the gates themselves --------------------------------------------------------------------------------------------------------------------


NEVER_IN_A_GATE = ("continue-on-error", "|| true", "--deselect", "--co ", "--collect-only", " -x ", "--maxfail", "--lf", "-k ")


def test_no_gate_can_pass_by_skipping_or_swallowing_failures(ci):
    body = code_only(text(CI_PATH))
    for forbidden in NEVER_IN_A_GATE:
        # the one legitimate `-k`/`--ignore` style filter does not exist in ci.yml: the backend split uses --ignore only
        assert forbidden not in body, forbidden
    for name in GATES:
        assert "if" not in ci["jobs"][name], f"{name} must not be conditional"  # (upload steps are conditional, jobs are not)


def test_installs_are_frozen_and_clean():
    for path in WORKFLOWS.glob("*.yml"):
        for block in all_runs(path):
            for line in block.splitlines():
                if re.search(r"\buv sync\b", line):
                    assert "--frozen" in line, (path.name, line)
                if re.search(r"\bnpm (install|i)\b", line):
                    raise AssertionError(f"use `npm ci`, not `{line.strip()}`")


def test_backend_runs_everything_exactly_once_with_the_real_migration_job_and_the_restricted_role(ci):
    runs = "\n".join(run_blocks(ci["jobs"]["backend"]))
    main = next(block for block in run_blocks(ci["jobs"]["backend"]) if "--ignore=tests/test_migrate_runner.py" in block)
    assert main.count("--ignore=") == 2 and "--ignore=tests/test_db_roles.py" in main
    assert re.findall(r"pytest[^\n]*(tests/test_\w+\.py)", runs.replace(main, "")) == ["tests/test_migrate_runner.py", "tests/test_db_roles.py"]
    # a fresh database through the ACTUAL job, then raw alembic and the schema-drift check, in that order
    assert runs.index("python -m app.scripts.migrate") < runs.index("alembic upgrade head") < runs.index("alembic check")
    assert "MIGRATION_DATABASE_URL" in runs and "APP_ENV=development" in runs
    assert any(step.get("uses") == "./.github/actions/disposable-db" for step in steps(ci["jobs"]["backend"]))


def test_the_restricted_role_proof_cannot_run_as_the_owner_or_a_superuser():
    roles = text(ROOT / "backend" / "tests" / "test_db_roles.py")
    assert "TEST_RUNTIME_ROLE_URL" in roles and 'app_url' in roles  # the child run is connected as the APP role
    assert "flags == \"falsefalsefalsefalsefalse\"" in roles  # rolsuper, createdb, createrole, replication, bypassrls are all false
    for path in WORKFLOWS.glob("*.yml"):
        assert "TEST_RUNTIME_ROLE_URL" not in text(path)  # CI never hands the suite a role URL of its own


def test_frontend_runs_every_check_with_no_configuration_at_build_time(ci):
    job = ci["jobs"]["frontend"]
    runs = "\n".join(run_blocks(job))
    for command in ("npm ci", "npm run typecheck", "npm run lint", "npm run test", "npm run build"):
        assert command in runs
    assert "env" not in job and not any("env" in step for step in steps(job))
    for name in ("AUTH_MODE", "APP_ENV", "PUBLIC_ORIGIN", "BACKEND_URL", "BFF_INTERNAL_SECRET", "NEXT_PUBLIC"):
        assert name not in code_only(job_text(ci, "frontend"))
    package = text(ROOT / "frontend" / "package.json")
    for script in ("typecheck", "lint", "test", "build", "test:e2e", "test:e2e:session"):
        assert f'"{script}":' in package


def test_containers_builds_and_runs_the_whole_d1_d2_suite(ci):
    runs = "\n".join(run_blocks(ci["jobs"]["containers"]))
    assert "pytest ../deploy/tests" in runs and "docker compose version" in runs
    assert " -k" not in runs and "--ignore" not in runs
    assert (ROOT / "deploy" / "tests").is_dir()


def test_browser_jobs_use_installed_chromium_never_edge(ci):
    for name, script in (("e2e-dev", "npm run test:e2e "), ("e2e-session", "npm run test:e2e:session")):
        job = ci["jobs"][name]
        assert job["env"]["E2E_BROWSER"] == "chromium"
        runs = "\n".join(run_blocks(job))
        assert "npx playwright install --with-deps chromium" in runs  # installs exactly the browser the run uses, and nothing else
        assert re.search(r"npx playwright install --with-deps (?!chromium)\w+", runs) is None
        assert script in runs + " " and "--grep" not in runs and "--project" not in runs and "--shard" not in runs
        assert any(step.get("uses") == "./.github/actions/disposable-db" for step in steps(job))
    assert "msedge" not in code_only(text(CI_PATH)) and "chrome-stable" not in text(CI_PATH)


def test_the_playwright_configs_have_no_silent_edge_fallback_in_ci_and_keep_no_traces():
    env = text(ROOT / "frontend" / "e2e" / "env.ts")
    assert "process.env.CI" in env and "throw new Error(\"E2E_BROWSER is not set" in env and '"off"' in env
    for name in ("playwright.config.ts", "playwright.session.config.ts"):
        config = text(ROOT / "frontend" / name)
        assert "channel: browserChannel()" in config and "trace: TRACE_MODE" in config
        assert 'process.env.E2E_BROWSER ?? "msedge"' not in config


def test_the_session_job_generates_masked_credentials_and_runs_with_the_bff_secret(ci):
    job = ci["jobs"]["e2e-session"]
    generate = next(step for step in steps(job) if "E2E_BFF_SECRET" in step.get("run", ""))
    run = generate["run"]
    for name in ("E2E_BFF_SECRET", "E2E_SECURITY_KEY", "E2E_PASSWORD"):
        assert name in run
    assert "::add-mask::" in run and "openssl rand" in run
    order = [step.get("name", "") for step in steps(job)]
    assert order.index(generate["name"]) < order.index(next(s["name"] for s in steps(job) if "test:e2e:session" in s.get("run", "")))
    # the session configuration really takes the secret from this environment and enforces it on both servers
    auth = text(ROOT / "frontend" / "e2e" / "auth-support.ts")
    for name in ("E2E_BFF_SECRET", "E2E_SECURITY_KEY", "E2E_PASSWORD"):
        assert f"process.env.{name}" in auth
    config = text(ROOT / "frontend" / "playwright.session.config.ts")
    assert "BFF_INTERNAL_SECRET: E2E_BFF_SECRET" in config and "...BACKEND_AUTH_ENV" in config
    assert "BFF_INTERNAL_SECRET" in auth and "hardening.spec.ts" not in config  # (the hardening spec runs through the ordinary glob)
    assert (ROOT / "frontend" / "e2e" / "session" / "hardening.spec.ts").exists()


def test_generated_secrets_are_masked_before_use_and_never_echoed():
    for path in [CI_PATH, ACTION_PATH]:
        source = text(path)
        blocks = re.findall(r"run: \|\n((?:\s{8,}.*\n)+)", source)
        assert blocks, path.name  # (the pattern must really find the run blocks: a check that matches nothing proves nothing)
        for block in blocks:
            lines = [line.strip() for line in block.splitlines()]
            if any("GITHUB_ENV" in line for line in lines) and re.search(r"openssl rand", block):
                assert "::add-mask::" in block, path.name
                assert block.index("::add-mask::") < block.index("GITHUB_ENV")
            in_group = False
            for line in lines:
                if line == "{":
                    in_group = True
                if re.match(r"echo\b.*\$\{?(password|value)\b", line) or re.match(r"echo\b.*(SECRET|PASSWORD|SECURITY_KEY)", line):
                    assert in_group or "GITHUB_ENV" in line or "::add-mask::" in line, (path.name, "a generated value is echoed")
                if line.startswith("}"):
                    in_group = False
            assert not re.search(r"\bset\s+-[a-z]*x|xtrace|\bsh\s+-[a-z]*x|bash\s+-[a-z]*x", block), path.name  # xtrace would print every expansion
    assert "ACTIONS_STEP_DEBUG" not in text(CI_PATH)


# --- disposable infrastructure ---------------------------------------------------------------------------------------------------------------


def test_the_disposable_database_action_is_generated_masked_bounded_unique_and_guarded():
    action = text(ACTION_PATH)
    assert "openssl rand -hex 24" in action and action.index("::add-mask::") < action.index("GITHUB_ENV")
    assert 'POSTGRES_TEST_DB=business_platform_test' in action and "127.0.0.1:5433" in action
    assert "--wait-timeout" in action  # the wait is bounded
    assert "docker compose up -d --wait" in action and "postgres-test" in action and "postgres-test" in text(ROOT / "docker-compose.yml")
    assert action.index("up -d --wait") < action.index("assert_ci_database.py")  # the guard runs against the server that is really up
    assert "COMPOSE_PROJECT_NAME=bp-ci-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${LABEL}" in action


def test_every_database_job_uses_a_distinct_compose_project_label(ci):
    labels = []
    for name, job in ci["jobs"].items():
        for step in steps(job):
            if step.get("uses") == "./.github/actions/disposable-db":
                labels.append(step["with"]["label"])
    assert sorted(labels) == ["backend", "e2e-dev", "e2e-session"]  # unique: two jobs never share Docker resources
    for path in WORKFLOWS.glob("*.yml"):
        for block in all_runs(path):
            assert "docker compose -p" not in block and "container_name" not in block


def test_the_compose_file_the_ci_uses_has_no_fixed_names_that_could_collide():
    compose = yaml.safe_load(text(ROOT / "docker-compose.yml"))
    for service in compose["services"].values():
        assert "container_name" not in service
    rehearsal = text(ROOT / "deploy" / "compose.rehearsal.yml")
    assert "container_name" not in rehearsal and "name: ${REHEARSAL_PROJECT:-bp-rehearsal}" in rehearsal


def test_the_container_suite_gives_every_stack_a_unique_project_and_image_name():
    support = text(ROOT / "deploy" / "tests" / "stack_support.py")
    assert "RUN = secrets.token_hex" in text(ROOT / "deploy" / "tests" / "conftest.py")
    assert 'self.project = f"bp-d2-{label}-{RUN}"' in support and "REHEARSAL_BACKEND_IMAGE" in support


# --- caching ---------------------------------------------------------------------------------------------------------------------------------


def test_caches_are_keyed_by_lockfiles_and_never_hold_state(ci):
    assert "actions/cache@" not in text(CI_PATH) + text(AUDIT_PATH) + text(ACTION_PATH)  # no hand-rolled cache that could hold data
    for name in ("backend", "containers", "e2e-dev", "e2e-session"):
        uv = next(step for step in steps(ci["jobs"][name]) if step.get("uses", "").startswith("astral-sh/setup-uv@"))
        assert uv["with"]["enable-cache"] is True and uv["with"]["cache-dependency-glob"] == "backend/uv.lock"
    for name in ("frontend", "e2e-dev", "e2e-session"):
        node = next(step for step in steps(ci["jobs"][name]) if step.get("uses", "").startswith("actions/setup-node@"))
        assert node["with"]["cache"] == "npm" and node["with"]["cache-dependency-path"] == "frontend/package-lock.json"
    body = text(CI_PATH)
    for forbidden in ("~/.cache/ms-playwright", "postgres_data", "docker/buildx", "cache-from", "cache-to", "type=gha"):
        assert forbidden not in code_only(body), forbidden  # browsers are installed fresh; Docker layers are never cached
    assert re.search(r"\.env(?![\w])", code_only(body)) is None


# --- artifacts -------------------------------------------------------------------------------------------------------------------------------


FORBIDDEN_ARTIFACT_PATH = re.compile(r"test-results|playwright-report|\.zip|trace|\.env|storage|cookie|\.dump|\.sql|\.png|\.webm|\.json|\*\*|^\.?/?\*$", re.IGNORECASE)


def test_artifacts_are_only_redacted_logs_uploaded_on_failure_with_a_short_retention(ci):
    uploads = []
    for path in WORKFLOWS.glob("*.yml"):
        for name, job in load(path)["jobs"].items():
            for step in steps(job):
                if step.get("uses", "").startswith("actions/upload-artifact@"):
                    uploads.append((path.name, name, step))
    assert len(uploads) == 5  # the four ci.yml jobs that upload and the restore drill (D4); each is held to the rules below
    for file, name, step in uploads:
        assert step["if"] == "failure()", (file, name)
        assert step["with"]["path"] == "ci-logs/*.log", (file, name)
        assert not FORBIDDEN_ARTIFACT_PATH.search(step["with"]["path"])
        assert step["with"]["retention-days"] <= 7
        assert step["with"]["if-no-files-found"] == "ignore"


def test_everything_written_to_ci_logs_passes_through_the_redactor():
    for path in WORKFLOWS.glob("*.yml"):
        for block in all_runs(path):
            for line in block.splitlines():
                if "ci-logs/" in line and "tee" in line:
                    assert "redact.py" in line and line.index("redact.py") < line.index("tee"), (path.name, line.strip())
                    assert "2>&1" in line  # stderr is part of the log, so it is redacted too


# --- the CI scripts ---------------------------------------------------------------------------------------------------------------------------


def import_script(name: str):
    spec = importlib.util.spec_from_file_location(name, GITHUB / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GOOD_URL = "postgresql+psycopg://ci_admin:pw@127.0.0.1:5433/business_platform_test"


@pytest.fixture(scope="module")
def guard():
    return import_script("assert_ci_database")


def test_the_ci_database_guard_accepts_exactly_the_disposable_server(guard, tmp_path):
    assert guard.problems({"TEST_DATABASE_URL": GOOD_URL}, tmp_path) == []
    assert guard.problems({"TEST_DATABASE_URL": GOOD_URL.replace("127.0.0.1", "localhost")}, tmp_path) == []


@pytest.mark.parametrize(
    "env, expect",
    [
        ({}, "not set"),
        ({"TEST_DATABASE_URL": GOOD_URL.replace("127.0.0.1", "db.example.com")}, "loopback"),
        ({"TEST_DATABASE_URL": GOOD_URL.replace("127.0.0.1", "10.0.0.5")}, "loopback"),
        ({"TEST_DATABASE_URL": GOOD_URL.replace("5433", "5432")}, "port"),
        ({"TEST_DATABASE_URL": GOOD_URL.replace("business_platform_test", "business_platform")}, "_test"),
        ({"TEST_DATABASE_URL": GOOD_URL.replace("business_platform_test", "prod_test_backup")}, "_test"),
        ({"TEST_DATABASE_URL": GOOD_URL.replace("postgresql+psycopg", "mysql")}, "PostgreSQL"),
        ({"TEST_DATABASE_URL": GOOD_URL, "DATABASE_URL": "postgresql://x@127.0.0.1:5432/business_platform"}, "DATABASE_URL"),
        ({"TEST_DATABASE_URL": GOOD_URL, "DATABASE_URL": "x"}, "DATABASE_URL"),  # not even URL-shaped: the named variable alone is refused
        ({"TEST_DATABASE_URL": GOOD_URL, "MIGRATION_DATABASE_URL": "x"}, "MIGRATION_DATABASE_URL"),
        ({"TEST_DATABASE_URL": GOOD_URL, "BOOTSTRAP_DATABASE_URL": "x"}, "BOOTSTRAP_DATABASE_URL"),
        ({"TEST_DATABASE_URL": GOOD_URL, "PGHOST": "prod.example.com"}, "PGHOST"),
        ({"TEST_DATABASE_URL": GOOD_URL, "SOMETHING": "postgresql://u:p@prod.example.com/db"}, "SOMETHING"),
    ],
)
def test_the_ci_database_guard_refuses_everything_else(guard, tmp_path, env, expect):
    found = guard.problems(env, tmp_path)
    assert found and any(expect in message for message in found), found
    assert not any("example.com" in message or "pw" in message for message in found)  # it never echoes a URL or a value


def test_the_ci_database_guard_refuses_a_checkout_with_an_env_file(guard, tmp_path):
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://dev@localhost/business_platform\n")
    assert any(".env" in message for message in guard.problems({"TEST_DATABASE_URL": GOOD_URL}, tmp_path))


def test_the_ci_database_guard_runs_as_a_script_and_exits_non_zero_on_refusal(tmp_path):
    import subprocess
    import sys

    # a copy inside a scratch "repository", so a developer's own .env (which the guard rightly refuses) cannot interfere
    scripts = tmp_path / ".github" / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "assert_ci_database.py"
    script.write_text((GITHUB / "scripts" / "assert_ci_database.py").read_text(encoding="utf-8"), encoding="utf-8")
    base = {"PATH": "", "SYSTEMROOT": ""}
    refused = subprocess.run([sys.executable, str(script)], env={**base, "TEST_DATABASE_URL": GOOD_URL.replace("_test", "")}, capture_output=True, text=True)
    assert refused.returncode == 1 and "REFUSING" in refused.stderr
    accepted = subprocess.run([sys.executable, str(script)], env={**base, "TEST_DATABASE_URL": GOOD_URL}, capture_output=True, text=True)
    assert accepted.returncode == 0, accepted.stderr


SECRETS = {
    "session cookie": ("Cookie: bp_session=" + "A" * 43 + "; bp_csrf=" + "B" * 43, ["A" * 43, "B" * 43]),
    "host cookie": ("set-cookie: __Host-bp_session=" + "C" * 43 + "; Path=/; HttpOnly", ["C" * 43]),
    "bearer header": ("authorization: Bearer " + "D" * 43, ["D" * 43]),
    "short bearer header": ("authorization: Bearer abc-short-123", ["abc-short-123"]),
    "short session cookie": ("cookie: bp_session=shortvalue99; other=1", ["shortvalue99"]),
    "short csrf header": ("x-csrf-token: shortcsrf77", ["shortcsrf77"]),
    "short setup link": ("/setup#abcdefghij0123456789", ["abcdefghij0123456789"]),
    "bff secret header": ("x-bff-secret: 9f3c1a7e5b2d8046c1e7a95b3d20f648a1c7e903d5b6f2a8", ["9f3c1a7e5b2d8046c1e7a95b3d20f648a1c7e903d5b6f2a8"]),
    "csrf header": ("'x-csrf-token': '" + "E" * 43 + "'", ["E" * 43]),
    "setup link": ("open http://127.0.0.1:3101/setup#" + "F" * 43, ["F" * 43]),
    "invite link": ("/invite#" + "G" * 43, ["G" * 43]),
    "bare token": ("token is " + "Hh_-" * 10 + "Hh_", ["Hh_-" * 10 + "Hh_"]),
    "json password": ('{"email": "a@b.test", "password": "SENTINEL-PASSWORD-9f2a"}', ["SENTINEL-PASSWORD-9f2a"]),
    "database url": ("postgresql+psycopg://ci_admin:SENTINEL-DB-PW@127.0.0.1:5433/x_test", ["SENTINEL-DB-PW"]),
    "hex secret": ("secret " + "ab12" * 12, ["ab12" * 12]),
    "dev identity cookie": ("bp_dev_user=maria@dev.test", ["maria@dev.test"]),
}


@pytest.mark.parametrize("name", list(SECRETS), ids=list(SECRETS))
def test_the_redactor_removes_every_kind_of_authentication_material(name):
    redact = import_script("redact").redact
    line, sentinels = SECRETS[name]
    cleaned = redact(line)
    for sentinel in sentinels:
        assert sentinel not in cleaned, (name, cleaned)
    assert "[redacted]" in cleaned


def test_the_redactor_leaves_ordinary_log_lines_alone():
    redact = import_script("redact").redact
    for line in (
        "2788 passed, 6 skipped, 2 warnings in 410.79s (0:06:50)",
        "  ok 351 e2e/transactions.spec.ts:539:7 > lifecycle > an empty draft cannot be completed (650ms)",
        "INFO  [alembic.runtime.migration] Running upgrade d18b4c6e2f31 -> e29c5d7a3b48, add organization invitations",
        '{"ts":"2026-10-05T21:10:25.480+00:00","level":"info","service":"migrate","event":"migrated","revision":"e29c5d7a3b48"}',
        "FAILED tests/test_readiness.py::test_exactly_the_head_is_ready - AssertionError",
    ):
        assert redact(line) == line, line
