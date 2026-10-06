"""The backend production image: runtime user, one worker, fonts, a real PDF, imports, Alembic files, absences."""

import hashlib
import re
import time
from pathlib import Path

from conftest import BACKEND_DIR, BACKEND_ENV, ROOT, container, docker, inspect

FONT_DIR = BACKEND_DIR / "app" / "modules" / "invoicing" / "pdf" / "fonts"
FONTS_IN_IMAGE = "/app/app/modules/invoicing/pdf/fonts"


def run_python(image: str, code: str, env: dict[str, str] | None = None) -> str:
    args = ["run", "--rm"]
    for key, value in (env or BACKEND_ENV).items():
        args += ["-e", f"{key}={value}"]
    return docker(*args, image, "python", "-c", code).stdout


def sh(image: str, command: str) -> str:
    return docker("run", "--rm", "--entrypoint", "sh", image, "-c", command, check=False).stdout


def test_it_runs_as_a_non_root_user(backend_image):
    assert docker("run", "--rm", backend_image, "id", "-u").stdout.strip() == "10001"
    config = inspect(backend_image)["Config"]
    assert config["User"] == "10001:10001"
    assert config["ExposedPorts"] == {"8000/tcp": {}}


def test_it_starts_with_exactly_one_uvicorn_worker_and_answers_health_without_a_database(backend_image):
    with container(backend_image, BACKEND_ENV) as identifier:
        for _ in range(60):  # the database is unreachable on purpose: the web process must not need it, nor run migrations
            probe = docker("exec", identifier, "python", "-c", "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health').status)", check=False)
            if probe.stdout.strip() == "200":
                break
            time.sleep(0.5)
        status = docker("exec", identifier, "python", "-c", "import urllib.request;r=urllib.request.urlopen('http://127.0.0.1:8000/health');print(r.status, r.read().decode())").stdout.strip()
        assert status == '200 {"status":"ok"}'
        processes = docker(
            "exec", identifier, "python", "-c",
            "import os\nfor p in os.listdir('/proc'):\n    if p.isdigit():\n        c=open(f'/proc/{p}/cmdline','rb').read().replace(b'\\0',b' ').decode()\n        if int(p) != os.getpid() and ('app.main:app' in c or 'multiprocessing.spawn' in c): print(c)",
        ).stdout.strip().splitlines()
        assert len(processes) == 1, processes  # no reloader, no worker pool: one process serves
        assert "--workers 1" in processes[0] and "--port 8000" in processes[0]
        logs = docker("logs", identifier).stdout + docker("logs", identifier).stderr
        assert not re.search(r"alembic|Running upgrade|migrat", logs, re.IGNORECASE)  # the web process migrates nothing
        # liveness answers without a database; READINESS (what the container health check asks) says no, and the process stays up
        ready_probe = (
            "import urllib.request, urllib.error\n"
            "try:\n"
            "    urllib.request.urlopen('http://127.0.0.1:8000/health/ready'); print('ready')\n"
            "except urllib.error.HTTPError as e:\n"
            "    print(e.code, e.read().decode())"
        )
        unready = docker("exec", identifier, "python", "-c", ready_probe).stdout.strip()
        assert unready == '503 {"status":"unready"}', unready
        assert inspect(identifier)["State"]["Health"]["Status"] in ("starting", "unhealthy") and inspect(identifier)["State"]["Running"]


def test_the_bundled_pdf_fonts_are_present_and_match_their_pinned_checksums(backend_image):
    expected = {line.split(" *")[1].strip(): line.split(" *")[0] for line in (FONT_DIR / "SHA256SUMS").read_text().splitlines() if " *" in line}
    assert len(expected) >= 8
    listing = sh(backend_image, f"cd {FONTS_IN_IMAGE} && sha256sum *.ttf")
    found = {line.split()[1].lstrip("*"): line.split()[0] for line in listing.splitlines()}
    assert found == expected
    for name, digest in expected.items():  # and they are the repository's bytes
        assert hashlib.sha256((FONT_DIR / name).read_bytes()).hexdigest() == digest
    assert sh(backend_image, f"ls {FONTS_IN_IMAGE}/licenses | wc -l").strip() == str(len(list((FONT_DIR / "licenses").iterdir())))


PDF_SCRIPT = """
from app.modules.invoicing.pdf.document import PdfDocument, PdfLine, PdfParty, PdfSource, PdfVatRow
from app.modules.invoicing.pdf.render import render_pdf, renderer_identity
line = PdfLine(position="1", description="Horse massage \\u00e5\\u00e4\\u00f6 \\u20ac", unit="session", quantity="1.000", unit_price="850.00", vat_rate="25.00", net="850.00", vat="212.50", gross="1062.50", fields=())
document = PdfDocument(
    number_text="1", invoice_date="2026-10-01", due_date="2026-10-31", currency="SEK", description=None,
    issuer=PdfParty(name="Fredrik Horse Therapy AB", lines=("Storgatan 1", "903 26 Ume\\u00e5")),
    customer=PdfParty(name="Ume\\u00e5 HK", lines=("Ridv\\u00e4gen 2",)),
    sources=(PdfSource(date="2026-09-30", fields=()),), lines=(line,),
    vat_rows=(PdfVatRow(rate="25.00", net="850.00", vat="212.50"),), net="850.00", vat="212.50", gross="1062.50",
)
data = render_pdf(document)
assert data.startswith(b"%PDF-") and len(data) > 3000, len(data)
assert b"NotoSans" in data  # the bundled font was embedded
print("PDF", len(data), renderer_identity())
"""


def test_a_real_pdf_renders_inside_the_final_runtime_image(backend_image):
    out = run_python(backend_image, PDF_SCRIPT)
    assert out.startswith("PDF ") and "reportlab" in out


def test_reportlab_psycopg_argon2_and_the_server_import_and_work(backend_image):
    out = run_python(
        backend_image,
        "import reportlab, psycopg, argon2, uvicorn, alembic, sqlalchemy, fastapi\n"
        "from app.core.passwords import hash_password\n"
        "h = hash_password('a long enough passphrase'); assert h.startswith('$argon2id$')\n"
        "print('ok', reportlab.Version, psycopg.__version__)",
    )
    assert out.startswith("ok ")


def test_alembic_files_for_a_separate_migrate_job_are_present_and_there_is_one_head(backend_image):
    versions = sorted(p.name for p in (BACKEND_DIR / "alembic" / "versions").glob("*.py"))
    in_image = sorted(sh(backend_image, "ls /app/alembic/versions/*.py").split())
    assert [Path(p).name for p in in_image] == versions
    assert sh(backend_image, "test -f /app/alembic/env.py && test -f /app/alembic.ini && echo yes").strip() == "yes"
    heads = docker("run", "--rm", *sum((["-e", f"{k}={v}"] for k, v in BACKEND_ENV.items()), []), backend_image, "python", "-m", "alembic", "heads").stdout
    assert heads.count("(head)") == 1


def test_tests_development_files_secrets_and_build_tools_are_absent(backend_image):
    absent = sh(
        backend_image,
        "cd /app; for p in tests e2e .git .env .venv/Scripts app/scripts/seed_dev.py app/scripts/reset_test_db.py uv.lock pyproject.toml; do test -e $p && echo PRESENT:$p; done; "
        "for c in gcc cc g++ make uv pip pip3 git; do command -v $c >/dev/null 2>&1 && echo TOOL:$c; done; "
        "find / -xdev \\( -name '.env' -o -name '.env.*' -o -name '*.pem' -o -name '*.key' \\) -not -path '/proc/*' -not -path '/usr/lib/*' -not -path '/etc/ssl/*' 2>/dev/null | head; "
        "/app/.venv/bin/python -c 'import pytest' 2>/dev/null && echo DEVDEP:pytest; /app/.venv/bin/python -c 'import pypdf' 2>/dev/null && echo DEVDEP:pypdf; /app/.venv/bin/python -c 'import httpx' 2>/dev/null && echo DEVDEP:httpx; true",
    )
    assert absent.strip() == "", absent
    # no test-database configuration, in files or in the image environment
    # (the ONE mention is the configuration module's own refusal of that variable in production)
    assert sh(backend_image, "grep -rIl -e TEST_DATABASE_URL -e POSTGRES_TEST /app --include=*.py --include=*.ini --include=*.txt --include=*.toml 2>/dev/null | head -3").strip() == "/app/app/core/config.py"
    # a non-vacuous control: the same probe does find things that ARE there
    assert "PRESENT:alembic.ini" in sh(backend_image, "cd /app; for p in alembic.ini; do test -e $p && echo PRESENT:$p; done")
    environment = " ".join(inspect(backend_image)["Config"]["Env"])
    assert not re.search(r"SECURITY_KEY|DATABASE_URL|PASSWORD|SECRET|TOKEN", environment)  # nothing configured at build time
    history = docker("history", "--no-trunc", "--format", "{{.CreatedBy}}", backend_image).stdout
    assert not re.search(r"\.env|SECURITY_KEY|PASSWORD", history)


def test_the_application_tree_is_readable_but_not_writable_by_the_runtime_user(backend_image):
    probe = docker("run", "--rm", "--entrypoint", "sh", backend_image, "-c", "touch /app/app/probe 2>&1; echo rc=$?", check=False).stdout
    assert "rc=1" in probe  # the runtime user cannot write into its own code
    assert "rc=0" in docker("run", "--rm", "--entrypoint", "sh", backend_image, "-c", "touch /tmp/probe 2>&1; echo rc=$?", check=False).stdout  # control: /tmp is writable
