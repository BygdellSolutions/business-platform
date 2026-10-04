"""The authentication migration: additive, reversible, and it gives nobody a credential.

A scratch database on the TEST server is migrated to the revision before authentication and filled with
users and memberships (as the development database has). The upgrade must add the tables and one column and
leave every existing row exactly as it was: in particular NO credential, session or setup-token row appears, so
no existing user (a seeded development user included) can authenticate in session mode.
"""

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.test_migration_currency import _alembic, _downgrade, _execute, _scalar, _upgrade, scratch_url  # noqa: F401

BEFORE = "a85e1c4d7f90"
AUTH_REVISION = "b96f2d4e8a13"
AUTH_TABLES = {"user_credentials", "auth_sessions", "user_setup_tokens", "security_events"}


def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as c:
            return {r[0] for r in c.execute(text("select table_name from information_schema.tables where table_schema = 'public'"))}
    finally:
        engine.dispose()


def _insert_existing(url: str) -> dict:
    ids = {name: uuid.uuid4() for name in ("org", "fredrik", "maria")}
    _execute(url, "insert into organizations (id, name, default_currency) values (:o, 'Existing Org', 'SEK')", o=ids["org"])
    for key, email, name in (("fredrik", "fredrik@dev.test", "Fredrik"), ("maria", "maria@dev.test", "Maria")):
        _execute(url, "insert into users (id, email, name) values (:u, :e, :n)", u=ids[key], e=email, n=name)
    _execute(url, "insert into organization_users (organization_id, user_id, role) values (:o, :u, 'owner')", o=ids["org"], u=ids["fredrik"])
    _execute(url, "insert into organization_users (organization_id, user_id, role) values (:o, :u, 'employee')", o=ids["org"], u=ids["maria"])
    return ids


def _fingerprint(url: str) -> list:
    engine = create_engine(url)
    try:
        with engine.connect() as c:
            return [
                c.execute(text("select id, email, name, is_active, created_at, updated_at from users order by id")).all(),
                c.execute(text("select id, organization_id, user_id, role, created_at, updated_at from organization_users order by id")).all(),
                c.execute(text("select id, name from organizations order by id")).all(),
            ]
    finally:
        engine.dispose()


def test_upgrade_keeps_every_user_and_membership_unchanged_and_creates_no_credential(scratch_url: str):
    _upgrade(scratch_url, BEFORE)
    assert not AUTH_TABLES & _tables(scratch_url)
    _insert_existing(scratch_url)
    before = _fingerprint(scratch_url)

    _upgrade(scratch_url, "head")

    assert AUTH_TABLES <= _tables(scratch_url)
    assert _fingerprint(scratch_url) == before
    for table in AUTH_TABLES:
        assert _scalar(scratch_url, f"select count(*) from {table}") == 0, table  # nobody can authenticate by password
    assert _scalar(scratch_url, "select count(*) from users where can_create_organizations") == 0  # existing users do not gain the permission
    assert _scalar(scratch_url, "select count(*) from users") == 2 and _scalar(scratch_url, "select count(*) from organization_users") == 2


def test_the_new_column_defaults_to_false_for_new_rows_too(scratch_url: str):
    _upgrade(scratch_url, "head")
    _execute(scratch_url, "insert into users (email, name) values ('new@example.test', 'New')")
    assert _scalar(scratch_url, "select can_create_organizations from users where email = 'new@example.test'") is False


def test_the_credential_table_accepts_only_argon2id_hashes(scratch_url: str):
    _upgrade(scratch_url, "head")
    ids = _insert_existing(scratch_url)
    with pytest.raises(DBAPIError, match="ck_user_credentials_argon2id"):
        _execute(scratch_url, "insert into user_credentials (user_id, password_hash, password_changed_at) values (:u, 'plain text', now())", u=ids["fredrik"])
    with pytest.raises(DBAPIError, match="ck_user_credentials_argon2id"):
        _execute(scratch_url, "insert into user_credentials (user_id, password_hash, password_changed_at) values (:u, '$2b$12$abcdefghijklmnopqrstuv', now())", u=ids["fredrik"])
    _execute(scratch_url, "insert into user_credentials (user_id, password_hash, password_changed_at) values (:u, '$argon2id$v=19$m=65536,t=3,p=1$c2FsdA$aGFzaA', now())", u=ids["fredrik"])


def test_sessions_and_tokens_must_hold_hashes_not_tokens(scratch_url: str):
    _upgrade(scratch_url, "head")
    ids = _insert_existing(scratch_url)
    with pytest.raises(DBAPIError, match="ck_auth_sessions_hash_shape"):
        _execute(
            scratch_url,
            "insert into auth_sessions (user_id, token_hash, csrf_hash, created_at, last_used_at, absolute_expires_at) values (:u, :t, :c, now(), now(), now() + interval '1 day')",
            u=ids["fredrik"], t="A" * 43, c="b" * 64,
        )
    with pytest.raises(DBAPIError, match="ck_user_setup_tokens_hash_shape"):
        _execute(scratch_url, "insert into user_setup_tokens (user_id, token_hash, purpose, created_at, expires_at) values (:u, :t, 'set_password', now(), now() + interval '1 day')", u=ids["fredrik"], t="not a hash")
    with pytest.raises(DBAPIError, match="ck_security_events_identifier_shape"):
        _execute(scratch_url, "insert into security_events (occurred_at, event_type, identifier_hash) values (now(), 'login_failure', 'someone@example.com')")


def test_only_one_outstanding_setup_link_per_user(scratch_url: str):
    _upgrade(scratch_url, "head")
    ids = _insert_existing(scratch_url)
    insert = "insert into user_setup_tokens (user_id, token_hash, purpose, created_at, expires_at) values (:u, :t, 'set_password', now(), now() + interval '1 day')"
    _execute(scratch_url, insert, u=ids["fredrik"], t="a" * 64)
    with pytest.raises(DBAPIError, match="uq_user_setup_tokens_outstanding"):
        _execute(scratch_url, insert, u=ids["fredrik"], t="b" * 64)
    _execute(scratch_url, "update user_setup_tokens set revoked_at = now()")
    _execute(scratch_url, insert, u=ids["fredrik"], t="c" * 64)


def test_security_events_are_append_only(scratch_url: str):
    _upgrade(scratch_url, "head")
    _execute(scratch_url, "insert into security_events (occurred_at, event_type, source) values (now(), 'login_failure', '1.2.3.4'), (now() - interval '3 days', 'login_failure', '5.6.7.8')")
    with pytest.raises(DBAPIError, match="cannot be changed"):
        _execute(scratch_url, "update security_events set detail = 'x'")
    with pytest.raises(DBAPIError, match="younger than one day"):
        _execute(scratch_url, "delete from security_events where source = '1.2.3.4'")
    _execute(scratch_url, "delete from security_events where source = '5.6.7.8'")  # older than a day: purgeable
    assert _scalar(scratch_url, "select count(*) from security_events") == 1


def test_security_events_accept_only_known_event_types(scratch_url: str):
    _upgrade(scratch_url, "head")
    with pytest.raises(DBAPIError, match="ck_security_events_type"):
        _execute(scratch_url, "insert into security_events (occurred_at, event_type) values (now(), 'password_was_hunter2')")


def test_downgrade_removes_exactly_what_the_upgrade_added_and_reupgrade_restores_it(scratch_url: str):
    _upgrade(scratch_url, BEFORE)
    _insert_existing(scratch_url)
    before = _fingerprint(scratch_url)
    _upgrade(scratch_url, "head")

    _downgrade(scratch_url, BEFORE)
    assert not AUTH_TABLES & _tables(scratch_url)
    assert _scalar(scratch_url, "select count(*) from information_schema.columns where table_name = 'users' and column_name = 'can_create_organizations'") == 0
    assert _scalar(scratch_url, "select count(*) from pg_proc where proname = 'security_events_append_only'") == 0
    assert _fingerprint(scratch_url) == before

    _upgrade(scratch_url, "head")
    assert AUTH_TABLES <= _tables(scratch_url)
    assert _scalar(scratch_url, "select count(*) from pg_trigger where tgname = 'trg_security_events_append_only' and not tgisinternal") == 1
    assert _fingerprint(scratch_url) == before


def test_downgrade_works_with_sessions_and_credentials_present(scratch_url: str):
    _upgrade(scratch_url, "head")
    ids = _insert_existing(scratch_url)
    _execute(scratch_url, "insert into user_credentials (user_id, password_hash, password_changed_at) values (:u, '$argon2id$v=19$m=8,t=1,p=1$c2FsdA$aGFzaA', now())", u=ids["fredrik"])
    _execute(scratch_url, "insert into security_events (occurred_at, event_type) values (now(), 'login_success')")
    _downgrade(scratch_url, BEFORE)
    assert not AUTH_TABLES & _tables(scratch_url)
    assert _scalar(scratch_url, "select count(*) from users") == 2


def test_models_and_migrations_agree_at_head_and_the_chain_has_one_head(scratch_url: str):
    _upgrade(scratch_url, "head")
    result = _alembic(scratch_url, "check")
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    heads = _alembic(scratch_url, "heads")
    assert heads.stdout.count("(head)") == 1
    assert AUTH_REVISION in heads.stdout
    assert _scalar(scratch_url, "select version_num from alembic_version") == AUTH_REVISION


def test_seeding_the_development_data_creates_no_credentials(db_session):
    from app.scripts import seed_dev

    seed_dev.seed(db_session)
    seed_dev.seed(db_session)

    assert db_session.scalar(text("select count(*) from user_credentials")) == 0
    assert db_session.scalar(text("select count(*) from auth_sessions")) == 0
    assert db_session.scalar(text("select count(*) from user_setup_tokens")) == 0
    assert db_session.scalar(text("select count(*) from users where email like '%@dev.test'")) == 2


def test_a_seeded_user_cannot_authenticate_in_session_mode(session_client, db_session):
    from app.scripts import seed_dev

    seed_dev.seed(db_session)
    for password in ("fredrik@dev.test", "password", "dev", "fredrik", "correct horse battery staple"):
        assert session_client.post("/api/auth/login", json={"email": "fredrik@dev.test", "password": password}).status_code == 401
    assert session_client.get("/api/me/organizations", headers={"X-Dev-User-Email": "fredrik@dev.test"}).status_code == 401
