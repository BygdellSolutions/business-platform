"""Small query/persistence helpers shared by resource routers (not tenant logic)."""

from typing import Any

from sqlalchemy.orm import Session


def contains_pattern(text: str) -> str:
    """LIKE pattern matching `text` literally (so '%' and '_' are not wildcards).

    Use with `column.ilike(pattern, escape="\\\\")`.
    """
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def commit_and_refresh(db: Session, record: Any) -> None:
    """Persist pending changes and reload server-generated values (ids, timestamps, numerics)."""
    db.flush()
    db.refresh(record)
    db.commit()


def apply_update(db: Session, record: Any, values: dict[str, Any]) -> None:
    """Set the given attributes on `record` and persist them.

    `values` must come from a request schema that has no organization_id field.
    """
    for field, value in values.items():
        setattr(record, field, value)
    commit_and_refresh(db, record)
