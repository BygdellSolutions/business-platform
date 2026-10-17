"""The mark-counter migration: an existing entry starts one past the highest mark number it has (a finalized one too, although it is frozen)."""

import json
import uuid

from tests.test_migration_currency import _alembic, _downgrade, _execute, _scalar, _upgrade, scratch_url  # noqa: F401

BEFORE = "b5e7a9c1d3f4"
AFTER = "c6f8b0d2e4a5"


def _mark(number: int | None) -> dict:
    return {"id": str(uuid.uuid4()), "side": "left", "tool": "x", "points": [[0.5, 0.5]], **({"number": number} if number is not None else {})}


def _entry(url: str, org: uuid.UUID, horse: uuid.UUID, user: uuid.UUID, marks: list[dict], final: bool) -> uuid.UUID:
    row = uuid.uuid4()
    _execute(
        url,
        "insert into horse_journal_entries (id, organization_id, horse_id, visit_at, practitioner_name, marks, status, finalized_at, finalized_by, diagram_version) "
        "values (:r, :o, :h, now(), 'Someone', cast(:m as jsonb), :s, :f, :u, 2)",
        r=row, o=org, h=horse, m=json.dumps(marks), s="final" if final else "draft", f=None if not final else "2026-10-01T10:00:00+00:00", u=user if final else None,
    )
    return row


def test_existing_entries_start_one_past_their_highest_mark_number(scratch_url: str):
    _upgrade(scratch_url, BEFORE)
    org, user, customer, horse = (uuid.uuid4() for _ in range(4))
    _execute(scratch_url, "insert into organizations (id, name, default_currency) values (:o, 'Org', 'SEK')", o=org)
    _execute(scratch_url, "insert into users (id, email, name) values (:u, :e, 'User')", u=user, e=f"{user}@tests.invalid")
    _execute(scratch_url, "insert into customers (id, organization_id, customer_type, name) values (:c, :o, 'person', 'Anna')", c=customer, o=org)
    _execute(scratch_url, "insert into horses (id, organization_id, name, owner_customer_id) values (:h, :o, 'Kalle', :c)", h=horse, o=org, c=customer)
    draft = _entry(scratch_url, org, horse, user, [_mark(1), _mark(3)], final=False)  # 2 was deleted: the next is 4, not 3
    frozen = _entry(scratch_url, org, horse, user, [_mark(2), _mark(5)], final=True)
    bare = _entry(scratch_url, org, horse, user, [], final=False)
    unnumbered = _entry(scratch_url, org, horse, user, [_mark(None)], final=False)  # a mark from before numbers existed

    _upgrade(scratch_url, AFTER)

    def counter(row: uuid.UUID) -> int:
        return _scalar(scratch_url, "select next_mark_number from horse_journal_entries where id = :r", r=row)

    assert (counter(draft), counter(frozen), counter(bare), counter(unnumbered)) == (4, 6, 1, 1)
    assert _scalar(scratch_url, "select status from horse_journal_entries where id = :r", r=frozen) == "final"  # still frozen

    _downgrade(scratch_url, BEFORE)
    assert _scalar(scratch_url, "select count(*) from horse_journal_entries where organization_id = :o", o=org) == 4
