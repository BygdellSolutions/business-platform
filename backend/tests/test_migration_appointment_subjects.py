"""The appointment-subjects migration: the single subject of an appointment becomes a row of its own, an appointment without one gets none,
and the downgrade keeps one subject per appointment."""

import uuid

from tests.test_migration_currency import _alembic, _downgrade, _execute, _scalar, _upgrade, scratch_url  # noqa: F401

BEFORE = "c6f8b0d2e4a5"
AFTER = "d7a9c1e3f5b6"


def test_single_subjects_become_rows_and_come_back_on_downgrade(scratch_url: str):
    _upgrade(scratch_url, BEFORE)
    org, other_org, horse, other_horse = (uuid.uuid4() for _ in range(4))
    for o, name in ((org, "Org"), (other_org, "Other")):
        _execute(scratch_url, "insert into organizations (id, name, default_currency) values (:o, :n, 'SEK')", o=o, n=name)

    def appointment(o: uuid.UUID, subject: uuid.UUID | None) -> uuid.UUID:
        row = uuid.uuid4()
        _execute(
            scratch_url,
            "insert into appointments (id, organization_id, title, starts_at, ends_at, subject_type, subject_id) "
            "values (:r, :o, 'Visit', '2026-10-19T08:00:00Z', '2026-10-19T09:00:00Z', :t, :s)",
            r=row, o=o, t="horse" if subject else None, s=subject,
        )
        return row

    with_horse, without, theirs = appointment(org, horse), appointment(org, None), appointment(other_org, other_horse)

    _upgrade(scratch_url, AFTER)

    def subjects_of(row: uuid.UUID) -> list[uuid.UUID]:
        return [
            _scalar(scratch_url, "select subject_id from appointment_subjects where appointment_id = :r and organization_id = :o", r=row, o=o)
            for o in [org if row != theirs else other_org]
        ]

    assert subjects_of(with_horse) == [horse]
    assert subjects_of(theirs) == [other_horse]  # in its own organization, never shared
    assert _scalar(scratch_url, "select count(*) from appointment_subjects where appointment_id = :r", r=without) == 0
    assert _scalar(scratch_url, "select count(*) from appointment_subjects") == 2

    _downgrade(scratch_url, BEFORE)
    assert _scalar(scratch_url, "select subject_id from appointments where id = :r", r=with_horse) == horse
    assert _scalar(scratch_url, "select subject_type from appointments where id = :r", r=without) is None
