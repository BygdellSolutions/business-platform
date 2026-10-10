"""People read "order", never "transaction" (decided 2026-10-09): messages, notes and labels the backend sends to the
screen say order. Names, routes, table and column names, SQL, comments, docstrings and API documentation keep
"transaction". The scan reads the source's string values (not comments or docstrings) and flags the word in a phrase.
"""

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
WORD = re.compile(r"(^|[\s(])[Tt]ransactions?(?=$|[\s.,!?:;)])")
# Strings that are not shown to people: API documentation and SQL.
NOT_SHOWN_KEYWORDS = {"description", "summary"}
SQL = re.compile(r"\b(select|insert|update|delete|from|where|join)\b", re.IGNORECASE)


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def _documentation(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg in NOT_SHOWN_KEYWORDS:
            ids.update(id(sub) for sub in ast.walk(node.value))
    return ids


def offenders_in(source: str) -> list[int]:
    tree = ast.parse(source)
    skip = _docstrings(tree) | _documentation(tree)
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            text = node.value
            if " " in text.strip() and WORD.search(text) and not SQL.search(text):
                lines.append(node.lineno)
    return sorted(set(lines))


def test_messages_say_order_never_transaction():
    found = [
        f"{path.relative_to(APP.parent)}:{line}"
        for path in sorted(APP.rglob("*.py"))
        for line in offenders_in(path.read_text(encoding="utf-8"))
    ]
    assert found == []


def test_the_scan_reads_messages_and_skips_names_docs_and_sql():
    assert offenders_in('raise ValueError("each transaction may be listed once")') == [1]
    assert offenders_in('note = "Transaction reopened"') == [1]
    assert offenders_in('message = f"A {status} transaction cannot be {verb}"') == [1]
    assert offenders_in('"""A transaction module docstring."""\nx = "transaction"\npath = "/api/transactions"') == []
    assert offenders_in('Query(description="Comma-separated transaction ids")') == []
    assert offenders_in('text("select id from transactions where x")') == []
    assert offenders_in("# a transaction comment\nkey = 'transaction_line'") == []
