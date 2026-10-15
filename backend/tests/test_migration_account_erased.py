"""Migration e7a9c1d3f5b8: one more security event type. Additive; append-only events are never deleted by the downgrade."""

import pytest
from sqlalchemy.exc import DBAPIError

from tests.test_migration_currency import _downgrade, _execute, _scalar, _upgrade, scratch_url  # noqa: F401

BEFORE = "d6f8b0c2e4a7"


def test_the_new_event_is_accepted_and_an_unknown_one_still_is_not(scratch_url: str):
    _upgrade(scratch_url, "head")

    _execute(scratch_url, "insert into security_events (occurred_at, event_type) values (now(), 'account_erased')")
    with pytest.raises(DBAPIError, match="ck_security_events_type"):
        _execute(scratch_url, "insert into security_events (occurred_at, event_type) values (now(), 'account_vanished')")


def test_downgrade_narrows_the_list_when_unused_and_keeps_it_when_an_event_exists(scratch_url: str):
    _upgrade(scratch_url, "head")
    _downgrade(scratch_url, BEFORE)
    with pytest.raises(DBAPIError, match="ck_security_events_type"):
        _execute(scratch_url, "insert into security_events (occurred_at, event_type) values (now(), 'account_erased')")  # narrow again

    _upgrade(scratch_url, "head")
    _execute(scratch_url, "insert into security_events (occurred_at, event_type) values (now(), 'account_erased')")
    _downgrade(scratch_url, BEFORE)

    assert _scalar(scratch_url, "select count(*) from security_events where event_type = 'account_erased'") == 1  # append-only: kept
