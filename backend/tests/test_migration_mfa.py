"""Migration c5e7a9b1d3f4: the second factor tables and event types. Additive; the downgrade leaves nothing of it behind."""

import uuid

import pytest
from sqlalchemy.exc import DBAPIError

from tests.test_migration_currency import _downgrade, _execute, _scalar, _upgrade, scratch_url  # noqa: F401

BEFORE = "a2c4e6f81b03"
REVISION = "c5e7a9b1d3f4"


def _user(url: str) -> uuid.UUID:
    user = uuid.uuid4()
    _execute(url, "insert into users (id, email, name) values (:u, :e, 'Ada')", u=user, e=f"{user}@example.test")
    return user


def test_upgrade_adds_empty_tables_and_changes_no_user_or_session(scratch_url: str):
    _upgrade(scratch_url, BEFORE)
    user = _user(scratch_url)
    before = _scalar(scratch_url, "select count(*) from users")

    _upgrade(scratch_url, REVISION)

    assert _scalar(scratch_url, "select count(*) from users") == before and _scalar(scratch_url, "select email from users where id = :u", u=user)
    for table in ("user_mfa", "user_recovery_codes", "mfa_challenges"):
        assert _scalar(scratch_url, f"select count(*) from {table}") == 0


def test_the_tables_enforce_their_shape(scratch_url: str):
    _upgrade(scratch_url, "head")
    user = _user(scratch_url)

    with pytest.raises(DBAPIError, match="ck_user_mfa_seed_shape"):
        _execute(scratch_url, "insert into user_mfa (user_id, seed) values (:u, 'not-hex')", u=user)
    _execute(scratch_url, "insert into user_mfa (user_id, seed) values (:u, :s)", u=user, s="ab" * 16)
    with pytest.raises(DBAPIError):
        _execute(scratch_url, "insert into user_mfa (user_id, seed) values (:u, :s)", u=user, s="cd" * 16)  # one factor per person

    with pytest.raises(DBAPIError, match="ck_user_recovery_codes_hash_shape"):
        _execute(scratch_url, "insert into user_recovery_codes (user_id, code_hash) values (:u, 'plain-code')", u=user)
    _execute(scratch_url, "insert into user_recovery_codes (user_id, code_hash) values (:u, :h)", u=user, h="a" * 64)
    with pytest.raises(DBAPIError):
        _execute(scratch_url, "insert into user_recovery_codes (user_id, code_hash) values (:u, :h)", u=user, h="a" * 64)  # a hash is unique

    insert = "insert into mfa_challenges (user_id, token_hash, created_at, expires_at, attempts) values (:u, :h, now(), now(), :a)"
    with pytest.raises(DBAPIError, match="ck_mfa_challenges_hash_shape"):
        _execute(scratch_url, insert, u=user, h="short", a=0)
    with pytest.raises(DBAPIError, match="ck_mfa_challenges_attempts"):
        _execute(scratch_url, insert, u=user, h="b" * 64, a=-1)


def test_the_new_events_are_accepted_and_an_unknown_one_still_is_not(scratch_url: str):
    _upgrade(scratch_url, "head")
    for event in ("mfa_enabled", "mfa_disabled", "mfa_failure", "mfa_reset_by_operator", "recovery_code_used", "sessions_revoked_by_operator", "login_success"):
        _execute(scratch_url, "insert into security_events (occurred_at, event_type) values (now(), :t)", t=event)
    with pytest.raises(DBAPIError, match="ck_security_events_type"):
        _execute(scratch_url, "insert into security_events (occurred_at, event_type) values (now(), 'mfa_bypassed')")


def test_a_person_with_a_factor_cannot_be_deleted_by_accident(scratch_url: str):
    _upgrade(scratch_url, "head")
    user = _user(scratch_url)
    _execute(scratch_url, "insert into user_mfa (user_id, seed) values (:u, :s)", u=user, s="ab" * 16)

    with pytest.raises(DBAPIError):
        _execute(scratch_url, "delete from users where id = :u", u=user)  # RESTRICT, like every other record about a person


def test_downgrade_removes_the_tables_and_narrows_the_events_when_none_of_the_new_kind_exist(scratch_url: str):
    _upgrade(scratch_url, "head")
    user = _user(scratch_url)
    _execute(scratch_url, "insert into user_mfa (user_id, seed) values (:u, :s)", u=user, s="ab" * 16)
    _execute(scratch_url, "insert into security_events (occurred_at, event_type) values (now(), 'login_success')")

    _downgrade(scratch_url, BEFORE)

    assert _scalar(scratch_url, "select count(*) from users") == 1
    assert _scalar(scratch_url, "select count(*) from security_events where event_type = 'login_success'") == 1
    assert _scalar(scratch_url, "select to_regclass('user_mfa') is null") is True
    with pytest.raises(DBAPIError, match="ck_security_events_type"):
        _execute(scratch_url, "insert into security_events (occurred_at, event_type) values (now(), 'mfa_enabled')")  # the narrow list is back


def test_downgrade_never_deletes_security_events_it_keeps_the_wider_list_instead(scratch_url: str):
    _upgrade(scratch_url, "head")
    _execute(scratch_url, "insert into security_events (occurred_at, event_type) values (now(), 'mfa_enabled')")

    _downgrade(scratch_url, BEFORE)

    assert _scalar(scratch_url, "select count(*) from security_events where event_type = 'mfa_enabled'") == 1  # append-only: kept
    assert _scalar(scratch_url, "select to_regclass('user_mfa') is null") is True
