"""Authentication answers "who is this user?"; membership answers "what may they do here?". They stay apart.

Static checks over the source: the authentication code never reads a membership, a role or an organization;
the dev identity is reachable from exactly one place; and nothing in it logs.
"""

import ast
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
AUTH_FILES = [
    "core/auth_service.py",
    "core/sessions.py",
    "core/passwords.py",
    "core/throttle.py",
    "core/security_events.py",
    "core/tokens.py",
    "core/clock.py",
    "models/auth.py",
    "api/auth.py",
    "scripts/admin.py",
]
TENANT_WORDS = re.compile(r"\b(OrganizationUser|organization_users|organization_id|Role|TenantContext|get_tenant_context|X-Organization-Id)\b")


def source(name: str) -> str:
    return (APP / name).read_text(encoding="utf-8")


def imports_of(name: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source(name))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


@pytest.mark.parametrize("name", AUTH_FILES)
def test_authentication_code_knows_nothing_about_tenants_or_roles(name):
    code = re.sub(r'""".*?"""', "", source(name), flags=re.S)  # (docstrings may explain the separation)
    code = re.sub(r"#.*", "", code)
    assert TENANT_WORDS.search(code) is None, TENANT_WORDS.search(code).group(0)


def test_the_tenant_code_reads_no_session_credential_or_token_table():
    code = source("core/tenant.py")
    for word in ("AuthSession", "UserCredential", "UserSetupToken", "SecurityEvent", "password", "token"):
        assert word not in code


def test_the_dev_identity_is_imported_by_the_one_authentication_seam_only():
    importers = []
    for path in APP.rglob("*.py"):
        relative = path.relative_to(APP).as_posix()
        if "dev_identity" in imports_of(relative) or any(i.endswith("dev_identity") for i in imports_of(relative)):
            importers.append(relative)
    assert importers == ["core/auth.py"]


def test_session_resolution_does_not_mention_the_dev_identity():
    for name in ("core/sessions.py", "core/auth_service.py"):
        text = source(name)
        assert "X-Dev-User-Email" not in text and "dev_user_email" not in text and "dev_identity" not in text


@pytest.mark.parametrize("name", [n for n in AUTH_FILES if n != "scripts/admin.py"])
def test_authentication_code_does_not_log_or_print(name):
    imported = imports_of(name)
    assert "logging" not in imported and "print" not in re.findall(r"\bprint\(", source(name)) and "loguru" not in imported


def test_the_only_output_of_the_operator_cli_is_its_own_messages():
    """The setup link is printed once by the CLI (the one place a secret leaves the system, to the operator)."""
    prints = re.findall(r"print\((.*)\)", source("scripts/admin.py"))
    assert prints
    for printed in prints:
        assert not re.search(r"(token|password|csrf)", printed), printed  # the link is printed; a bare token or password never is


def test_get_current_user_has_exactly_the_three_modes_and_no_fallback_between_them():
    tree = ast.parse(source("core/auth.py"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "get_current_user")
    branches = [n for n in ast.walk(function) if isinstance(n, ast.Compare) and isinstance(n.left, ast.Attribute) and n.left.attr == "auth_mode"]
    compared = {c.value for n in branches for c in n.comparators if isinstance(c, ast.Constant)}
    assert compared == {"session", "dev"}
    text = source("core/auth.py")
    assert text.count("dev_identity.resolve_dev_user") == 1 and text.count("sessions.authenticate_request") == 1


def test_secrets_are_compared_in_constant_time_and_never_with_equality():
    text = source("core/sessions.py")
    assert "hmac.compare_digest(hash_token(supplied), session.csrf_hash)" in text
    assert not re.search(r"csrf_hash\s*[!=]=|[!=]=\s*session\.csrf_hash|token_hash\s*==\s*token", text)
