"""The rendering layer is a pure presentation layer: no database, no domain code, no financial arithmetic.

The rendering layer is `document.py`, `format.py`, `fonts.py`, `render.py`, `build.py` and `filename.py`
under `app/modules/invoicing/pdf/`. Only `service.py` (which reads the stored invoice and stores the
artifact) may touch the database. The rules are enforced on the syntax tree, and the checker itself is
tested against samples that break each rule, so it cannot silently stop checking.
"""

import ast
import re
from pathlib import Path

import pytest

PDF = Path(__file__).resolve().parents[1] / "app" / "modules" / "invoicing" / "pdf"
RENDERING_LAYER = ["document.py", "format.py", "fonts.py", "render.py", "build.py", "filename.py", "labels.py"]

FORBIDDEN_IMPORTS = (
    "sqlalchemy", "alembic", "psycopg", "fastapi", "starlette",
    "app.core", "app.models", "app.db", "app.api",
    "app.modules.sales", "app.modules.custom_fields", "app.modules.equine", "app.modules.customers", "app.modules.catalog",
    "app.modules.invoicing.models", "app.modules.invoicing.service", "app.modules.invoicing.api", "app.modules.invoicing.schemas",
    "app.modules.invoicing.pdf.service",
    "decimal", "fractions", "math", "cmath", "numbers", "statistics", "numpy",  # no number types, no arithmetic helpers
    "requests", "httpx", "urllib", "http", "socket", "ssl", "subprocess", "os", "shutil", "tempfile", "ftplib", "smtplib",  # nothing external
)
FORBIDDEN_CALLS = {"float", "int", "Decimal", "Fraction", "round", "sum", "divmod", "pow", "eval", "exec", "open", "__import__"}  # builtins by bare name; re.compile etc. are fine
SKIPPED_FOR_ARITHMETIC = (ast.List, ast.ListComp, ast.Tuple, ast.Dict, ast.JoinedStr, ast.GeneratorExp, ast.SetComp, ast.DictComp)
AMOUNT_WORDS = re.compile(r"(net|vat|gross|price|quantity|qty|rate|amount|total|sum|money|cost|discount|tax)", re.IGNORECASE)


def _words(node: ast.AST) -> list[str]:
    """Names an arithmetic operand refers to. Building a list or a string is not arithmetic: those are not entered."""
    if isinstance(node, SKIPPED_FOR_ARITHMETIC):
        return []
    found: list[str] = []
    if isinstance(node, ast.Name):
        found.append(node.id)
    elif isinstance(node, ast.Attribute):
        found.append(node.attr)
    for child in ast.iter_child_nodes(node):
        found.extend(_words(child))
    return found


def violations(source: str) -> list[str]:
    problems: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or "", *[f"{node.module}.{alias.name}" for alias in node.names]]
            for name in names:
                if any(name == bad or name.startswith(bad + ".") for bad in FORBIDDEN_IMPORTS):
                    problems.append(f"import {name}")
        elif isinstance(node, ast.Call):
            called = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) and node.func.attr in {"Decimal", "Fraction"} else ""
            if called in FORBIDDEN_CALLS:
                problems.append(f"call {called}()")
        elif isinstance(node, (ast.BinOp, ast.AugAssign)):
            # Arithmetic is layout (widths, margins). It must never touch a stored figure: no operand may be named like one.
            operands = [node.left, node.right] if isinstance(node, ast.BinOp) else [node.target, node.value]
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and isinstance(node.left, ast.Constant):
                continue
            for operand in operands:
                for word in _words(operand):
                    if AMOUNT_WORDS.search(word):
                        problems.append(f"arithmetic on {word}")
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            for word in _words(node.operand):
                if AMOUNT_WORDS.search(word):
                    problems.append(f"negation of {word}")
    return sorted(set(problems))


@pytest.mark.parametrize("name", RENDERING_LAYER)
def test_the_rendering_layer_has_no_database_domain_number_or_network_dependency(name):
    assert violations((PDF / name).read_text(encoding="utf-8")) == []


def test_the_rendering_layer_is_the_whole_package_except_the_service():
    present = {path.name for path in PDF.glob("*.py")} - {"__init__.py"}
    assert present == set(RENDERING_LAYER) | {"service.py"}


def test_the_service_is_the_only_file_that_touches_the_database():
    service = (PDF / "service.py").read_text(encoding="utf-8")
    assert "sqlalchemy" in service
    for name in RENDERING_LAYER:
        assert "sqlalchemy" not in (PDF / name).read_text(encoding="utf-8")


def test_the_renderer_signature_takes_a_document_only():
    tree = ast.parse((PDF / "render.py").read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert [arg.arg for arg in functions["render_pdf"].args.args] == ["document"]
    assert [arg.arg for arg in functions["_build"].args.args] == ["document", "fonts"]


# --- the checker catches what it is meant to catch ---------------------------------------------------------------------------------

BAD_SAMPLES = {
    "import sqlalchemy": "from sqlalchemy.orm import Session\n",
    "import app.core": "from app.core.tenant import TenantContext\n",
    "import app.modules.sales": "from app.modules.sales.models import Transaction\n",
    "import app.modules.custom_fields": "import app.modules.custom_fields.service\n",
    "import app.modules.equine": "from app.modules.equine import models\n",
    "import app.modules.invoicing.models": "from app.modules.invoicing.models import Invoice\n",
    "import decimal": "from decimal import Decimal\n",
    "import fractions": "import fractions\n",
    "import math": "import math\n",
    "import urllib": "import urllib.request\n",
    "import requests": "import requests\n",
    "import socket": "import socket\n",
    "call Decimal()": "x = Decimal('1')\n",
    "call float()": "y = float(a)\n",
    "call int()": "y = int(a)\n",
    "call round()": "y = round(a, 2)\n",
    "call sum()": "y = sum(a)\n",
    "call open()": "y = open('f')\n",
    "arithmetic on net": "x = line.net + line.vat\n",
    "arithmetic on unit_price": "x = unit_price * qty\n",
    "arithmetic on gross": "x = gross - 1\n",
    "arithmetic on rate": "x = row.rate / 100\n",
    "arithmetic on total": "total += 1\n",
    "arithmetic on quantity": "x = document.quantity * 2\n",
    "negation of net": "x = -net\n",
}


@pytest.mark.parametrize("expected", list(BAD_SAMPLES))
def test_the_checker_catches_each_kind_of_violation(expected):
    assert any(found == expected or found.startswith(expected + ".") for found in violations(BAD_SAMPLES[expected])), expected


def test_the_checker_accepts_building_lists_and_text_from_figures():
    source = "rows += [[money(row.net), money(row.vat)] for row in rows]\nlabel = f'{net} {vat}'\nrows += [net, vat]\n"
    assert violations(source) == []


def test_the_checker_accepts_layout_arithmetic():
    assert violations("w = page - 2 * margin\nx = widest + 8\nfor each in widths:\n    used += each\ntext = f'{a} {b}'\n") == []
