"""Import-direction rule: domain modules depend on core, never the other way round.

Core, Customers, Catalog and everything that will come later (Sales, UDF engine, ...)
must not import the Equine module or contain horse-specific concepts. Only the
places that wire a module in may mention it. This keeps "a specialized module can
reference generic entities without contaminating them" a failing test, not a convention.
"""

from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
EQUINE = APP / "modules" / "equine"

# The only files outside the module allowed to know it exists.
WIRING = {
    APP / "main.py",  # mounts the router
    APP / "scripts" / "seed_dev.py",  # dev data
}
FORBIDDEN_WORDS = ("horse", "equine")


def python_files(root: Path):
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def test_nothing_outside_the_module_mentions_it_except_the_wiring_files():
    offenders = []
    for path in python_files(APP):
        if EQUINE in path.parents or path in WIRING:
            continue
        text = path.read_text(encoding="utf-8").lower()
        found = [word for word in FORBIDDEN_WORDS if word in text]
        if found:
            offenders.append(f"{path.relative_to(BACKEND)}: {found}")

    assert offenders == []


def test_wiring_allowlist_is_not_stale():
    # If a wiring file stops mentioning the module, shrink WIRING.
    for path in WIRING:
        assert "equine" in path.read_text(encoding="utf-8").lower(), path


def test_only_the_horses_migration_mentions_horses():
    versions = BACKEND / "alembic" / "versions"
    mentioning = [
        p.name for p in versions.glob("*.py") if "horse" in p.read_text(encoding="utf-8").lower()
    ]

    assert len(mentioning) == 1 and "horses" in mentioning[0]


def test_core_models_package_does_not_register_domain_models():
    # Domain models are registered by alembic/env.py, not by the core models package.
    init = (APP / "models" / "__init__.py").read_text(encoding="utf-8").lower()
    assert "modules" not in init and "horse" not in init
