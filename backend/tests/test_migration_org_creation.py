import uuid

import pytest
from sqlalchemy.exc import DBAPIError

from tests.test_migration_currency import _downgrade, _execute, _scalar, _upgrade, scratch_url  # noqa: F401

BEFORE = "b96f2d4e8a13"  # the authentication migration
REVISION = "c07a3e5f9b24"


def _world(url: str) -> dict:
    ids = {name: uuid.uuid4() for name in ("org", "user")}
    _execute(url, "insert into organizations (id, name, default_currency) values (:o, 'Existing Org', 'SEK')", o=ids["org"])
    _execute(url, "insert into users (id, email, name) values (:u, 'u@example.test', 'U')", u=ids["user"])
    _execute(url, "insert into organization_users (organization_id, user_id, role) values (:o, :u, 'owner')", o=ids["org"], u=ids["user"])
    return ids


def test_upgrade_changes_no_existing_row_and_adds_an_empty_request_table(scratch_url: str):
    _upgrade(scratch_url, BEFORE)
    _world(scratch_url)
    _execute(scratch_url, "insert into security_events (occurred_at, event_type, source) values (now(), 'login_failure', '1.2.3.4')")
    organizations_before = _scalar(scratch_url, "select count(*) from organizations")

    _upgrade(scratch_url, REVISION)

    assert _scalar(scratch_url, "select count(*) from organization_creation_requests") == 0
    assert _scalar(scratch_url, "select count(*) from organizations") == organizations_before
    assert _scalar(scratch_url, "select count(*) from security_events where organization_id is not null") == 0
    assert _scalar(scratch_url, "select count(*) from security_events") == 1
    assert _scalar(scratch_url, "select count(*) from users where can_create_organizations") == 0  # nobody gains the right


def test_the_request_table_enforces_its_shape_and_uniqueness(scratch_url: str):
    _upgrade(scratch_url, "head")
    ids = _world(scratch_url)
    second_org = uuid.uuid4()
    _execute(scratch_url, "insert into organizations (id, name) values (:o, 'Second')", o=second_org)
    insert = "insert into organization_creation_requests (user_id, request_key, request_hash, organization_id) values (:u, :k, :h, :o)"
    key, digest = "k" * 43, "a" * 64
    _execute(scratch_url, insert, u=ids["user"], k=key, h=digest, o=ids["org"])
    with pytest.raises(DBAPIError, match="organization_creation_requests_pkey"):
        _execute(scratch_url, insert, u=ids["user"], k=key, h=digest, o=second_org)
    with pytest.raises(DBAPIError, match="organization_id"):  # one request per organization
        _execute(scratch_url, insert, u=ids["user"], k="j" * 43, h=digest, o=ids["org"])
    with pytest.raises(DBAPIError, match="ck_organization_creation_requests_key_shape"):
        _execute(scratch_url, insert, u=ids["user"], k="short", h=digest, o=second_org)
    with pytest.raises(DBAPIError, match="ck_organization_creation_requests_hash_shape"):
        _execute(scratch_url, insert, u=ids["user"], k="i" * 43, h="not a hash", o=second_org)


def test_security_events_accept_the_new_types_and_an_organization_id_and_stay_append_only(scratch_url: str):
    _upgrade(scratch_url, "head")
    org = uuid.uuid4()
    _execute(scratch_url, "insert into security_events (occurred_at, event_type, organization_id) values (now(), 'organization_created', :o), (now(), 'capability_changed', null)", o=org)
    with pytest.raises(DBAPIError, match="ck_security_events_type"):
        _execute(scratch_url, "insert into security_events (occurred_at, event_type) values (now(), 'something_else')")
    with pytest.raises(DBAPIError, match="cannot be changed"):
        _execute(scratch_url, "update security_events set organization_id = null")


def test_downgrade_removes_the_new_events_even_though_events_are_append_only_and_upgrade_restores_the_schema(scratch_url: str):
    _upgrade(scratch_url, "head")
    _world(scratch_url)
    _execute(scratch_url, "insert into security_events (occurred_at, event_type, source) values (now(), 'login_failure', '1.2.3.4'), (now(), 'organization_created', null), (now(), 'capability_changed', null)")

    _downgrade(scratch_url, BEFORE)

    assert _scalar(scratch_url, "select count(*) from security_events") == 1  # only the event the old schema knows
    assert _scalar(scratch_url, "select count(*) from information_schema.tables where table_name = 'organization_creation_requests'") == 0
    assert _scalar(scratch_url, "select count(*) from information_schema.columns where table_name = 'security_events' and column_name = 'organization_id'") == 0
    with pytest.raises(DBAPIError, match="ck_security_events_type"):
        _execute(scratch_url, "insert into security_events (occurred_at, event_type) values (now(), 'organization_created')")
    assert _scalar(scratch_url, "select count(*) from pg_trigger where tgname = 'trg_security_events_append_only' and tgenabled = 'O'") == 1  # the guard is back on
    assert _scalar(scratch_url, "select count(*) from organizations") == 1 and _scalar(scratch_url, "select count(*) from organization_users") == 1

    _upgrade(scratch_url, "head")
    assert _scalar(scratch_url, "select count(*) from organization_creation_requests") == 0
    _execute(scratch_url, "insert into security_events (occurred_at, event_type) values (now(), 'organization_created')")
