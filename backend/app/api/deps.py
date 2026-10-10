from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException, Query, status
from sqlalchemy import ColumnElement, Select


@dataclass(frozen=True)
class Pagination:
    limit: int
    offset: int


def pagination(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Pagination:
    return Pagination(limit=limit, offset=offset)


@dataclass(frozen=True)
class Sorting:
    """A list's sort order from the address: a column key the list names (checked by `sorted_by`) and a direction."""

    key: str | None
    descending: bool


def sorting(
    sort: str | None = Query(default=None, max_length=32, pattern=r"^[a-z_]+$", description="A column key this list sorts by"),
    dir: Literal["asc", "desc"] = Query(default="asc"),
) -> Sorting:
    return Sorting(key=sort, descending=dir == "desc")


def refuse_sort(key: str) -> None:
    """A sort key the list does not offer: a 422, never silently ignored."""
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=[{"loc": ["query", "sort"], "msg": f"Cannot sort this list by {key!r}", "type": "sort.unknown"}],
    )


def sorted_by(query: Select, sort: Sorting, columns: Mapping[str, ColumnElement[Any]], default: Sequence[Any]) -> Select:
    """`query` ordered by the requested column (empty values last either way), then by the list's default order, so
    rows with equal values keep a stable order across pages. A key the list does not offer is a 422, never ignored."""
    if sort.key is None:
        return query.order_by(*default)
    column = columns.get(sort.key)
    if column is None:
        refuse_sort(sort.key)
    return query.order_by((column.desc() if sort.descending else column.asc()).nulls_last(), *default)
