"""Static checks that the restore drill stays outside the merge gate and stays safe (D4; the general CI rules are in test_ci_workflows.py,
which also covers this workflow through its glob over every workflow file)."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DRILL = ROOT / ".github" / "workflows" / "restore-drill.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def triggers(doc: dict) -> dict:
    return doc.get("on") or doc.get(True) or {}


def test_the_drill_runs_only_on_demand_or_on_a_schedule_and_is_not_a_merge_gate():
    assert set(triggers(load(DRILL))) == {"schedule", "workflow_dispatch"}
    assert "restore-drill" not in " ".join(load(CI)["jobs"]["ci-gate"]["needs"])
    assert "drill" not in load(CI)["jobs"]


def test_the_merge_gating_container_suite_does_not_contain_the_drill():
    assert not list((ROOT / "deploy" / "tests").glob("*restore*"))
    assert (ROOT / "deploy" / "drill" / "test_restore_drill.py").exists()


def test_the_drill_workflow_runs_the_drill_with_a_bounded_timeout_and_no_credentials():
    doc = load(DRILL)
    job = doc["jobs"]["drill"]
    assert 1 <= job["timeout-minutes"] <= 45
    runs = "\n".join(step["run"] for step in job["steps"] if "run" in step)
    assert "pytest ../deploy/drill" in runs and "redact.py" in runs
    body = DRILL.read_text(encoding="utf-8")
    code = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
    for forbidden in ("BACKUP_DATABASE_URL", "RESTORE_DATABASE_URL", "AWS_", "S3", "secrets."):
        assert forbidden not in code, forbidden
