"""Small query/persistence helpers shared by resource routers (not tenant logic)."""

from collections.abc import Callable, Iterable, Iterator
from typing import Any, TypeVar

from fastapi import HTTPException, status
from sqlalchemy import false
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.entity_registry import registry

FOREIGN_KEY_VIOLATION = "23503"  # PostgreSQL SQLSTATE

T = TypeVar("T")
IN_CHUNK = 60  # an id costs about 76 bytes on the wire (its place in the statement plus its value): 60 ids stay under 5 KB, clear of the stall seen at about 8 KB


def in_chunks(values: Iterable[T], size: int = IN_CHUNK) -> Iterator[list[T]]:
    """`values` in lists of at most `size`, for a query that filters with `IN (...)`.

    One statement with hundreds of ids is slow in a way the database does not explain: on a development machine the same
    query took 0.8 ms with 200 ids and 44 ms with 250 (the message crossed a size where the network path stalled; the server
    itself needed 0.05 ms), and it shows in the dashboard. Several short statements are always cheap, so a list that can
    grow with the data is asked for in chunks.
    """
    batch: list[T] = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def number_matches(column, text: str):
    """`column == N` when the search text is a record number ("1001", " 7 "), otherwise a condition that is never true,
    so a list search finds a record by its number as well as by its name."""
    digits = text.strip()
    if digits.isascii() and digits.isdigit() and len(digits) <= 18:  # fits a bigint
        return column == int(digits)
    return false()


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


def delete_or_409(db: Session, record: Any, detail: str, *, after_delete: Callable[[], None] | None = None) -> None:
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
    if after_delete is not None:
        after_delete()  # e.g. the history event: written only for a deletion that really happened
    db.commit()


def apply_update(db: Session, record: Any, values: dict[str, Any]) -> None:
    """Set the given attributes on `record` and persist them.

    `values` must come from a request schema that has no organization_id field.
    """
    for field, value in values.items():
        setattr(record, field, value)
    commit_and_refresh(db, record)
