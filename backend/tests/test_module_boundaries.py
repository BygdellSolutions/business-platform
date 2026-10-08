"""Import-direction rules between core, generic capabilities and modules.

These tests enforce DEPENDENCIES (who imports whom) and REGISTRATIONS (what each module
registers), not vocabulary: ordinary domain words may appear in generic code and docs.

    core  ◄── customers / catalog / sales / custom fields / domain modules
    domain modules, sales, custom fields do not import each other;
    they meet only through the core registry (see app/core/entity_registry.py).
"""

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
MODULES = APP / "modules"

# The only files that wire modules into the application.
WIRING = {APP / "main.py", APP / "scripts" / "seed_dev.py"}

MODULE_PREFIX = {
    "equine": "app.modules.equine",
    "sales": "app.modules.sales",
    "custom_fields": "app.modules.custom_fields",
    # Does not exist yet. Registered here so that, from the day it appears, nothing but its own
    # package and the wiring may import it: the dependency runs invoicing -> sales / custom
    # fields (never the reverse), and everything else meets it through the core registry.
    "invoicing": "app.modules.invoicing",
    "inventory": "app.modules.inventory",
}


def python_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def imports_of(path: Path) -> set[str]:
    """Absolute module names imported by `path` (`from a import b` yields `a` and `a.b`)."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def imports_matching(path: Path, prefix: str) -> list[str]:
    return sorted(m for m in imports_of(path) if m == prefix or m.startswith(prefix + "."))


def importers_of(prefix: str, *, allowed_dirs: tuple[Path, ...] = ()) -> list[str]:
    """App files that import `prefix`, other than wiring files and `allowed_dirs`."""
    return [
        str(path.relative_to(BACKEND))
        for path in python_files(APP)
        if path not in WIRING
        and not any(d in path.parents for d in allowed_dirs)
        and imports_matching(path, prefix)
    ]


# --- modules are only imported by their own package and the wiring files -------------------------


# The ONE deliberate exception: Invoicing builds on Sales and Custom Fields, so it may import these
# submodules of theirs (and nothing else of theirs). The reverse is forbidden below.
INVOICING_MAY_IMPORT = {
    "app.modules.sales": ("models", "pricing", "versioning"),
    "app.modules.custom_fields": ("service", "schemas"),
}


@pytest.mark.parametrize("name", MODULE_PREFIX)
def test_nothing_imports_a_module_except_itself_and_the_wiring_files(name: str):
    allowed = (MODULES / name,)
    offenders = importers_of(MODULE_PREFIX[name], allowed_dirs=allowed)
    if MODULE_PREFIX[name] in INVOICING_MAY_IMPORT:
        # Invoicing is let in, but only through the named submodules.
        offenders = [o for o in offenders if Path(o).parts[:3] != ("app", "modules", "invoicing")]
    assert offenders == []


def test_invoicing_imports_only_what_it_is_allowed_to():
    """Core, the shared models and schemas, and a narrow list of Sales and Custom Fields
    submodules. Not Equine, not any other part of Sales or Custom Fields (their APIs, registration)."""
    allowed_prefixes = ["app.core", "app.models", "app.schemas", "app.api.deps", "app.modules.invoicing"]
    for module, submodules in INVOICING_MAY_IMPORT.items():
        allowed_prefixes += [f"{module}.{name}" for name in submodules]
    offenders = []
    for path in python_files(MODULES / "invoicing"):
        for name in imports_of(path):
            if name.split(".")[0] != "app":
                continue
            if name in INVOICING_MAY_IMPORT:  # `from app.modules.sales import models` yields the package too
                continue
            if not any(name == p or name.startswith(p + ".") for p in allowed_prefixes):
                offenders.append(f"{path.relative_to(BACKEND)} -> {name}")
    assert offenders == []


def test_invoicing_really_uses_its_allowance():
    # Keeps the allowance honest: if Invoicing stops importing a module, remove it from the list.
    used = {name for path in python_files(MODULES / "invoicing") for name in imports_of(path)}
    for module in INVOICING_MAY_IMPORT:
        assert any(name == module or name.startswith(module + ".") for name in used), module


def test_wiring_files_really_wire_the_modules():
    # If a wiring file stops importing a module, shrink WIRING (keeps the allowlist honest).
    for path in WIRING:
        for name in ("equine", "sales", "invoicing", "inventory"):
            if path.name == "seed_dev.py" and name in ("invoicing", "inventory"):
                continue  # the seed creates no invoices and no stock
            assert imports_matching(path, MODULE_PREFIX[name]), (path, name)


# --- the modules do not depend on each other ---------------------------------------------------------


@pytest.mark.parametrize(
    "package,forbidden",
    [
        ("sales", ["equine", "custom_fields", "invoicing", "inventory"]),
        ("equine", ["sales", "custom_fields", "invoicing", "inventory"]),
        ("custom_fields", ["sales", "equine", "invoicing", "inventory"]),
        ("invoicing", ["equine", "inventory"]),
        # Inventory reacts to Sales through the core lifecycle seam, not by importing it (yet: slice I3 decides).
        ("inventory", ["sales", "equine", "custom_fields", "invoicing"]),
    ],
)
def test_modules_do_not_import_each_other(package: str, forbidden: list[str]):
    offenders = [
        f"{path.relative_to(BACKEND)} -> {MODULE_PREFIX[other]}"
        for path in python_files(MODULES / package)
        for other in forbidden
        if imports_matching(path, MODULE_PREFIX[other])
    ]
    assert offenders == []


# --- core and the standard (non-module) code never import modules ----------------------------------------


def test_core_imports_no_modules_apis_or_schemas():
    offenders = [
        f"{path.relative_to(BACKEND)} -> {m}"
        for path in python_files(APP / "core")
        for prefix in ("app.modules", "app.api", "app.schemas", "app.scripts")
        for m in imports_matching(path, prefix)
    ]
    assert offenders == []


def test_standard_code_does_not_import_modules():
    # Customers, Catalog and the shared schemas/models are below the modules.
    standard = [
        *python_files(APP / "models"),
        *python_files(APP / "schemas"),
        APP / "api" / "customers.py",
        APP / "api" / "items.py",
        APP / "api" / "organization.py",
        APP / "api" / "me.py",
        APP / "api" / "deps.py",
    ]
    registrations = APP / "registrations.py"
    if registrations.exists():
        standard.append(registrations)
    offenders = [
        f"{path.relative_to(BACKEND)} -> {m}"
        for path in standard
        for m in imports_matching(path, "app.modules")
    ]
    assert offenders == []


# --- the custom-fields capability depends on core only -----------------------------------------------------

CUSTOM_FIELDS_MAY_IMPORT = (
    "app.core",
    "app.models.mixins",
    "app.models.organization_user",  # the Role enum
    "app.schemas.money",
    "app.api.deps",
    "app.modules.custom_fields",
)


def test_custom_fields_depends_on_core_only():
    offenders = []
    for path in python_files(MODULES / "custom_fields"):
        for name in imports_of(path):
            if name.split(".")[0] == "app" and not any(
                name == p or name.startswith(p + ".") for p in CUSTOM_FIELDS_MAY_IMPORT
            ):
                offenders.append(f"{path.relative_to(BACKEND)} -> {name}")
    assert offenders == []


def test_alembic_collects_the_models_of_every_module():
    env = (BACKEND / "alembic" / "env.py").read_text(encoding="utf-8")
    for module in ("equine", "sales", "invoicing", "inventory"):
        assert f"app.modules.{module}" in env
