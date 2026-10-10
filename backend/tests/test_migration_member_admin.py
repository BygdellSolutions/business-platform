import uuid

import pytest
from sqlalchemy.exc import DBAPIError

from tests.test_migration_currency import _downgrade, _execute, _scalar, _upgrade, scratch_url  # noqa: F401

BEFORE = "c07a3e5f9b24"  # organization onboarding
REVISION = "d18b4c6e2f31"


def _world(url: str) -> dict:
    """An organization with an owner, a legacy organization without one, and a few memberships."""
    ids = {name: uuid.uuid4() for name in ("owned", "legacy", "owner", "admin", "employee", "legacy_admin")}
    for key, name in (("owned", "Owned"), ("legacy", "Legacy ownerless")):
        _execute(url, "insert into organizations (id, name, default_currency) values (:o, :n, 'SEK')", o=ids[key], n=name)
    for key in ("owner", "admin", "employee", "legacy_admin"):
        _execute(url, "insert into users (id, email, name) values (:u, :e, :n)", u=ids[key], e=f"{key}@example.test", n=key)
    for org, user, role in (("owned", "owner", "owner"), ("owned", "admin", "admin"), ("owned", "employee", "employee"), ("legacy", "legacy_admin", "admin"), ("legacy", "employee", "employee")):
        _execute(url, "insert into organization_users (organization_id, user_id, role) values (:o, :u, :r)", o=ids[org], u=ids[user], r=role)
    return ids


def _memberships(url: str):
    return _scalar(url, "select string_agg(organization_id::text || ':' || user_id::text || ':' || role || ':' || updated_at::text, ',' order by id) from organization_users")


def test_upgrade_changes_no_membership_promotes_nobody_and_grants_nothing(scratch_url: str):
    _upgrade(scratch_url, BEFORE)
    _world(scratch_url)
    before, owners = _memberships(scratch_url), _scalar(scratch_url, "select count(*) from organization_users where role = 'owner'")

    _upgrade(scratch_url, REVISION)

    assert _memberships(scratch_url) == before  # same rows, same roles, same timestamps
    assert _scalar(scratch_url, "select count(*) from organization_users where role = 'owner'") == owners == 1  # nobody promoted; the legacy organization stays ownerless
    assert _scalar(scratch_url, "select count(*) from users where can_create_organizations") == 0
    assert _scalar(scratch_url, "select count(*) from security_events") == 0


def test_the_backstop_is_installed_and_deferred(scratch_url: str):
    _upgrade(scratch_url, REVISION)  # exactly this migration's triggers (a later one adds the ownership limit's)
    names = _scalar(scratch_url, "select string_agg(tgname, ',' order by tgname) from pg_trigger where tgrelid = 'organization_users'::regclass and not tgisinternal")
    assert names == "trg_organization_users_owner_required_delete,trg_organization_users_owner_required_update"
    assert _scalar(scratch_url, "select bool_and(tgdeferrable and tginitdeferred) from pg_trigger where tgname like 'trg_organization_users_owner_required%'") is True


def test_raw_sql_at_a_real_commit_cannot_orphan_an_organization_but_may_touch_a_legacy_one(scratch_url: str):
    _upgrade(scratch_url, "head")
    ids = _world(scratch_url)
    with pytest.raises(DBAPIError, match="without an owner"):
        _execute(scratch_url, "delete from organization_users where organization_id = :o and role = 'owner'", o=ids["owned"])
    with pytest.raises(DBAPIError, match="without an owner"):
        _execute(scratch_url, "update organization_users set role = 'admin' where organization_id = :o and role = 'owner'", o=ids["owned"])
    assert _scalar(scratch_url, "select count(*) from organization_users where role = 'owner'") == 1

    # unrelated changes in either organization, and any change in the legacy one, are fine
    _execute(scratch_url, "update organization_users set role = 'viewer' where organization_id = :o and role = 'employee'", o=ids["owned"])
    _execute(scratch_url, "update organization_users set role = 'accountant' where organization_id = :o and role = 'admin'", o=ids["legacy"])
    _execute(scratch_url, "delete from organization_users where organization_id = :o", o=ids["legacy"])
    # a second owner makes losing the first legal
    _execute(scratch_url, "update organization_users set role = 'owner' where organization_id = :o and role = 'admin'", o=ids["owned"])
    _execute(scratch_url, "delete from organization_users where organization_id = :o and user_id = :u", o=ids["owned"], u=ids["owner"])
    assert _scalar(scratch_url, "select count(*) from organization_users where organization_id = :o and role = 'owner'", o=ids["owned"]) == 1


def test_security_events_accept_the_membership_kinds(scratch_url: str):
    _upgrade(scratch_url, "head")
    for kind in ("member_role_changed", "member_removed", "member_left", "owner_repaired"):
        _execute(scratch_url, "insert into security_events (occurred_at, event_type) values (now(), :t)", t=kind)
    with pytest.raises(DBAPIError, match="ck_security_events_type"):
        _execute(scratch_url, "insert into security_events (occurred_at, event_type) values (now(), 'member_promoted')")


def test_downgrade_removes_the_backstop_and_the_new_events_and_keeps_the_data_and_upgrade_restores_them(scratch_url: str):
    _upgrade(scratch_url, "head")
    ids = _world(scratch_url)
    _execute(scratch_url, "insert into security_events (occurred_at, event_type, source) values (now(), 'login_failure', '1.2.3.4'), (now(), 'member_removed', null), (now(), 'owner_repaired', null)")
    before = _memberships(scratch_url)

    _downgrade(scratch_url, BEFORE)

    assert _scalar(scratch_url, "select count(*) from pg_trigger where tgname like 'trg_organization_users_owner_required%'") == 0
    assert _scalar(scratch_url, "select count(*) from pg_proc where proname = 'organization_users_owner_required'") == 0
    assert _scalar(scratch_url, "select count(*) from security_events") == 1
    assert _memberships(scratch_url) == before
    _execute(scratch_url, "delete from organization_users where organization_id = :o and role = 'owner'", o=ids["owned"])  # the backstop is gone

    _upgrade(scratch_url, "head")
    assert _scalar(scratch_url, "select count(*) from pg_trigger where tgname like 'trg_organization_users_owner_required%'") == 2
