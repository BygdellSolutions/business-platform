import uuid

import pytest
from sqlalchemy.exc import DBAPIError

from tests.test_migration_currency import _downgrade, _execute, _scalar, _upgrade, scratch_url  # noqa: F401

BEFORE = "d18b4c6e2f31"  # membership administration
REVISION = "e29c5d7a3b48"


def _world(url: str) -> dict:
    ids = {name: uuid.uuid4() for name in ("owned", "legacy", "owner", "admin", "legacy_admin")}
    for key, name in (("owned", "Owned"), ("legacy", "Legacy ownerless")):
        _execute(url, "insert into organizations (id, name, default_currency) values (:o, :n, 'SEK')", o=ids[key], n=name)
    for key in ("owner", "admin", "legacy_admin"):
        _execute(url, "insert into users (id, email, name) values (:u, :e, :n)", u=ids[key], e=f"{key}@example.test", n=key)
    for org, user, role in (("owned", "owner", "owner"), ("owned", "admin", "admin"), ("legacy", "legacy_admin", "admin")):
        _execute(url, "insert into organization_users (organization_id, user_id, role) values (:o, :u, :r)", o=ids[org], u=ids[user], r=role)
    return ids


def _state(url: str):
    return (
        _scalar(url, "select string_agg(organization_id::text || user_id::text || role || updated_at::text, ',' order by id) from organization_users"),
        _scalar(url, "select string_agg(email || is_active::text || can_create_organizations::text, ',' order by email) from users"),
        _scalar(url, "select count(*) from organization_users where role = 'owner'"),
    )


def test_upgrade_creates_an_empty_table_and_changes_nothing_else(scratch_url: str):
    _upgrade(scratch_url, BEFORE)
    _world(scratch_url)
    before = _state(scratch_url)

    _upgrade(scratch_url, REVISION)

    assert _state(scratch_url) == before  # no membership, role, user, capability or owner changed; the legacy organization stays ownerless
    assert _scalar(scratch_url, "select count(*) from organization_invitations") == 0
    assert _scalar(scratch_url, "select count(*) from user_credentials") == 0 and _scalar(scratch_url, "select count(*) from security_events") == 0


def test_the_table_enforces_its_shape_and_the_pending_rule_without_a_clock(scratch_url: str):
    _upgrade(scratch_url, "head")
    ids = _world(scratch_url)
    insert = (
        "insert into organization_invitations (organization_id, email, role, token_hash, expires_at, created_by) "
        "values (:o, :e, :r, :h, now() - interval '1 day', :u)"  # already EXPIRED: it still occupies the pending slot
    )
    base = dict(o=ids["owned"], e="a@example.test", r="viewer", h="a" * 64, u=ids["owner"])
    _execute(scratch_url, insert, **base)
    with pytest.raises(DBAPIError, match="uq_organization_invitations_pending"):
        _execute(scratch_url, insert, **{**base, "h": "b" * 64})  # an expired row is not "free": it must be revoked first
    with pytest.raises(DBAPIError, match="uq_organization_invitations_token_hash"):
        _execute(scratch_url, insert, **{**base, "e": "b@example.test"})  # a token hash is unique
    _execute(scratch_url, insert, **{**base, "o": ids["legacy"], "h": "c" * 64})  # another organization: fine
    _execute(scratch_url, "update organization_invitations set revoked_at = now() where token_hash = :h", h="a" * 64)
    _execute(scratch_url, insert, **{**base, "h": "d" * 64})  # revoked: the slot is free
    for bad, match in (
        ({**base, "e": "Upper@example.test", "h": "e" * 64}, "email_normalized"),
        ({**base, "e": "c@example.test", "h": "short"}, "token_hash_shape"),
        ({**base, "e": "d@example.test", "h": "f" * 64, "r": "superuser"}, "role"),
    ):
        with pytest.raises(DBAPIError, match=match):
            _execute(scratch_url, insert, **bad)
    with pytest.raises(DBAPIError, match="one_outcome"):
        _execute(scratch_url, "update organization_invitations set revoked_at = now(), accepted_at = now(), accepted_by = :u where token_hash = :h", u=ids["admin"], h="d" * 64)
    with pytest.raises(DBAPIError, match="accepted_pair"):
        _execute(scratch_url, "update organization_invitations set accepted_at = now() where token_hash = :h", h="d" * 64)


def test_security_events_accept_the_invitation_kinds(scratch_url: str):
    _upgrade(scratch_url, "head")
    for kind in ("invitation_created", "invitation_revoked", "invitation_accepted"):
        _execute(scratch_url, "insert into security_events (occurred_at, event_type) values (now(), :t)", t=kind)
    with pytest.raises(DBAPIError, match="ck_security_events_type"):
        _execute(scratch_url, "insert into security_events (occurred_at, event_type) values (now(), 'invitation_viewed')")


def test_downgrade_removes_the_table_and_its_events_and_keeps_the_data_and_the_s4_backstop(scratch_url: str):
    _upgrade(scratch_url, REVISION)  # this migration's own downgrade (later ones replace the users' creation flag)
    ids = _world(scratch_url)
    _execute(scratch_url, "insert into security_events (occurred_at, event_type) values (now(), 'login_failure'), (now(), 'invitation_created')")
    before = _state(scratch_url)

    _downgrade(scratch_url, BEFORE)

    assert _scalar(scratch_url, "select count(*) from information_schema.tables where table_name = 'organization_invitations'") == 0
    assert _scalar(scratch_url, "select count(*) from security_events") == 1
    assert _state(scratch_url) == before
    assert _scalar(scratch_url, "select count(*) from pg_trigger where tgname like 'trg_organization_users_owner_required%'") == 2  # S4's backstop is untouched
    with pytest.raises(DBAPIError, match="without an owner"):
        _execute(scratch_url, "delete from organization_users where organization_id = :o and role = 'owner'", o=ids["owned"])

    _upgrade(scratch_url, "head")
    assert _scalar(scratch_url, "select count(*) from organization_invitations") == 0
