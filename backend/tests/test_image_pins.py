"""Every base image the deployment builds on or runs is pinned by digest, so a rebuilt image cannot silently change under us.

The tag stays (readable, and what a bump starts from); the digest decides. A new image reference without a digest fails here."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = ("backend/Dockerfile", "frontend/Dockerfile")
COMPOSE_FILES = ("deploy/compose.rehearsal.yml", "deploy/coolify/migrate.yml", "deploy/coolify/backend.yml", "deploy/coolify/frontend.yml", "deploy/coolify/bootstrap.yml")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}")


def test_every_from_line_names_a_digest_or_an_earlier_build_stage():
    for name in DOCKERFILES:
        text = (ROOT / name).read_text(encoding="utf-8")
        stages = {m.group(1).lower() for m in re.finditer(r"^FROM\s+\S+\s+AS\s+(\S+)", text, flags=re.M | re.I)}
        for match in re.finditer(r"^FROM\s+(\S+)", text, flags=re.M):
            reference = match.group(1)
            assert DIGEST.search(reference) or reference.lower() in stages, (name, reference)


def test_every_image_in_the_deployment_files_names_a_digest_unless_it_is_built_here_or_a_variable():
    for name in COMPOSE_FILES:
        for match in re.finditer(r"^\s*image:\s*[\"']?([^\s\"'#]+)", (ROOT / name).read_text(encoding="utf-8"), flags=re.M):
            reference = match.group(1)
            assert reference.startswith("${") or DIGEST.search(reference), (name, reference)


def test_the_restore_drill_database_image_is_pinned_too():
    assert DIGEST.search((ROOT / "deploy/drill/test_restore_drill.py").read_text(encoding="utf-8").split('"postgres:17', 1)[1].split('"', 1)[0])


def test_the_pins_are_found_in_the_files_this_test_scans():
    """A control: the scan must see the references it claims to check."""
    assert "@sha256:" in (ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    assert "image:" in (ROOT / "deploy/coolify/migrate.yml").read_text(encoding="utf-8")
