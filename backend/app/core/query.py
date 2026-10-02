"""Small query/persistence helpers shared by resource routers (not tenant logic)."""

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.entity_registry import registry

FOREIGN_KEY_VIOLATION = "23503"  # PostgreSQL SQLSTATE


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


def delete_or_409(db: Session, record: Any, detail: str) -> None:
    """Delete `record`, or answer 409 if other records still reference it.

    The message stays generic on purpose: the module that owns the delete must not
    know which other modules reference its records. Foreign keys protect most links;
    registered reference guards protect polymorphic ones a foreign key cannot express.
    """
    if registry.is_referenced(db, record):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=detail)
    try:
        # Savepoint: a refused delete must not poison the surrounding transaction.
        with db.begin_nested():
            db.delete(record)
            db.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == FOREIGN_KEY_VIOLATION:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=detail)
        raise
    db.commit()


def apply_update(db: Session, record: Any, values: dict[str, Any]) -> None:
    """Set the given attributes on `record` and persist them.

    `values` must come from a request schema that has no organization_id field.
    """
    for field, value in values.items():
        setattr(record, field, value)
    commit_and_refresh(db, record)
