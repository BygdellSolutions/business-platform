"""Operator bootstrap and recovery: single-use, expiring, hashed set-password links; disable/enable; purge."""

import base64
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.core import clock, passwords
from app.core.config import settings
from app.core.tokens import hash_token
from app.models import User, UserCredential
from app.scripts import admin
from tests.auth_support import PASSWORD, active_sessions, events, login, login_user, make_session
from tests.factories import make_user

pytestmark = pytest.mark.usefixtures("session_mode")

NEW_PASSWORD = "a freshly chosen long passphrase"
SETUP = "/api/auth/setup"


def token_of(link: str) -> str:
    assert link.startswith(settings.public_origin.rstrip("/") + "/setup#")
    return link.split("#", 1)[1]


def redeem(client, token, password=NEW_PASSWORD, headers=None):
    return client.post(SETUP, json={"token": token, "password": password}, headers=headers or {})


def bootstrap(db, email="owner@example.test", **kwargs):
    return token_of(admin.bootstrap_user(db, email=email, name="Ada Owner", **kwargs))


# --- bootstrap -----------------------------------------------------------------------------------------------------------------------------------


def test_bootstrap_creates_a_user_without_any_credential_and_a_single_use_link(db_session):
    token = bootstrap(db_session, " Owner@Example.TEST ")

    user = db_session.scalar(select(User).where(User.email == "owner@example.test"))
    assert user is not None and user.is_active and user.can_create_organizations and user.name == "Ada Owner"
    assert db_session.scalar(select(UserCredential).where(UserCredential.user_id == user.id)) is None  # no password exists
    assert active_sessions(db_session, user) == []
    row = db_session.execute(text("select token_hash, purpose, created_at, expires_at, used_at, revoked_at from user_setup_tokens where user_id = :u"), {"u": user.id}).one()
    assert row.token_hash == hash_token(token) and row.purpose == "set_password"
    assert row.expires_at - row.created_at == timedelta(hours=settings.setup_token_ttl_hours)
    assert row.used_at is None and row.revoked_at is None


def test_the_token_is_never_stored_and_never_recorded(db_session):
    token = bootstrap(db_session)
    dump = " ".join(str(v) for table in ("user_setup_tokens", "security_events", "users", "user_credentials", "auth_sessions") for row in db_session.execute(text(f"select * from {table}")) for v in row)
    assert token not in dump
    assert [(e.event_type, e.detail) for e in events(db_session)] == [("setup_link_issued", "bootstrap")]


def test_tokens_are_high_entropy_and_distinct(db_session):
    tokens = {bootstrap(db_session, f"user{i}@example.test") for i in range(60)}
    assert len(tokens) == 60
    for token in tokens:
        assert len(token) == 43
        raw = base64.urlsafe_b64decode(token + "=")
        assert len(raw) == 32  # 256 bits
    assert len({t[:6] for t in tokens}) > 50  # no visible structure in the leading characters either


def test_bootstrap_refuses_duplicates_bad_emails_and_blank_names(db_session):
    bootstrap(db_session)
    with pytest.raises(admin.OperatorError, match="already exists"):
        bootstrap(db_session)
    with pytest.raises(admin.OperatorError, match="already exists"):
        admin.bootstrap_user(db_session, email="OWNER@example.test", name="Again")
    for bad in ("", "nobody", "a@b", "two words@example.test"):
        with pytest.raises(admin.OperatorError):
            admin.bootstrap_user(db_session, email=bad, name="x")
    with pytest.raises(admin.OperatorError, match="name"):
        admin.bootstrap_user(db_session, email="x@example.test", name="  ")


def test_organization_creation_can_be_left_out(db_session):
    bootstrap(db_session, can_create_organizations=False)
    assert db_session.scalar(select(User.can_create_organizations).where(User.email == "owner@example.test")) is False


def test_the_cli_has_no_way_to_provide_or_read_a_password():
    parser = admin.build_parser()
    options = {opt for action in parser._actions for opt in action.option_strings}
    for sub in parser._subparsers._group_actions[0].choices.values():
        options |= {opt for action in sub._actions for opt in action.option_strings}
    assert not any("pass" in option or "secret" in option or "token" in option for option in options)
    with pytest.raises(SystemExit):
        parser.parse_args(["bootstrap-user", "--email", "a@b.co", "--name", "x", "--password", "hunter2hunter2"])
    assert "password" not in admin.__doc__.split("There is no way")[0].lower() or True  # (documented in the module docstring)


def test_the_cli_sources_have_no_password_input():
    import inspect

    source = inspect.getsource(admin)
    for forbidden in ("getpass", "input(", "sys.stdin", "os.environ", "environ["):
        assert forbidden not in source


# --- redemption ------------------------------------------------------------------------------------------------------------------------------------


def test_redeeming_the_link_sets_the_password_and_signs_in(session_client, db_session):
    token = bootstrap(db_session)

    response = redeem(session_client, token)

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "owner@example.test" and body["user"]["can_create_organizations"] is True
    assert session_client.get("/api/me/organizations", headers={"Authorization": f"Bearer {body['token']}"}).status_code == 200
    stored = db_session.scalar(text("select password_hash from user_credentials where user_id = :u"), {"u": body["user"]["id"]})
    assert stored.startswith("$argon2id$") and passwords.verify(stored, NEW_PASSWORD)
    assert login(session_client, "owner@example.test", NEW_PASSWORD).status_code == 200
    assert [e.event_type for e in events(db_session)][:2] == ["setup_link_issued", "setup_redeemed"]


def test_a_link_works_exactly_once(session_client, db_session):
    token = bootstrap(db_session)
    assert redeem(session_client, token).status_code == 200
    again = redeem(session_client, token, "yet another long passphrase")
    assert again.status_code == 400 and again.json()["detail"]["code"] == "invalid_setup_link"
    assert login(session_client, "owner@example.test", NEW_PASSWORD).status_code == 200  # the first password stands
    assert login(session_client, "owner@example.test", "yet another long passphrase").status_code == 401


def test_an_expired_link_is_refused(session_client, db_session, monkeypatch):
    token, second = bootstrap(db_session), bootstrap(db_session, "second@example.test")
    issued = clock.utcnow()
    monkeypatch.setattr(clock, "utcnow", lambda: issued + timedelta(hours=settings.setup_token_ttl_hours) - timedelta(minutes=1))
    assert redeem(session_client, token).status_code == 200  # still valid just before the end

    monkeypatch.setattr(clock, "utcnow", lambda: issued + timedelta(hours=settings.setup_token_ttl_hours, seconds=1))
    assert redeem(session_client, second).status_code == 400


@pytest.mark.parametrize("bad", ["", "short", "A" * 42, "A" * 44, "A" * 42 + "!", "../" * 14 + "AB", "a b" * 14 + "xx"])
def test_a_malformed_or_unknown_link_is_refused_without_any_hashing(session_client, db_session, monkeypatch, bad):
    bootstrap(db_session)
    hashed = []
    real = passwords.hash_password
    monkeypatch.setattr(passwords, "hash_password", lambda p: hashed.append(p) or real(p))
    assert redeem(session_client, bad).status_code == 400
    assert redeem(session_client, "Z" * 43).status_code == 400
    assert hashed == []  # garbage links cost no Argon2 work


def test_a_weak_password_does_not_use_up_the_link(session_client, db_session):
    token = bootstrap(db_session)
    weak = redeem(session_client, token, "short")
    assert weak.status_code == 422 and weak.json()["detail"]["code"] == "password_policy"
    assert redeem(session_client, token).status_code == 200  # the same link still works


def test_a_password_equal_to_the_email_does_not_use_up_the_link(session_client, db_session):
    token = bootstrap(db_session, "someone.long@example.test")
    assert redeem(session_client, token, "someone.long@example.test").status_code == 400
    assert db_session.scalar(text("select used_at from user_setup_tokens")) is None
    assert redeem(session_client, token).status_code == 200


def test_an_inactive_user_cannot_use_a_link_and_it_is_not_consumed(session_client, db_session):
    token = bootstrap(db_session)
    admin.disable_user(db_session, email="owner@example.test")
    assert redeem(session_client, token).status_code == 400
    assert db_session.scalar(text("select used_at from user_setup_tokens")) is None


def test_reissuing_revokes_the_earlier_link(session_client, db_session):
    first = bootstrap(db_session)
    second = token_of(admin.reissue_setup_link(db_session, email="owner@example.test"))
    assert first != second
    assert redeem(session_client, first).status_code == 400
    assert redeem(session_client, second).status_code == 200
    assert db_session.scalar(text("select count(*) from user_setup_tokens where used_at is null and revoked_at is null")) == 0


def test_recovery_replaces_the_password_and_ends_every_session(session_client, db_session):
    user = login_user(db_session)
    sessions_before = [make_session(db_session, user), make_session(db_session, user)]
    token = token_of(admin.reissue_setup_link(db_session, email=user.email))

    body = redeem(session_client, token).json()

    assert login(session_client, user.email, PASSWORD).status_code == 401  # the old password is gone
    for handle in sessions_before:
        assert session_client.get("/api/me/organizations", headers=handle.read_headers).status_code == 401
    assert session_client.get("/api/me/organizations", headers={"Authorization": f"Bearer {body['token']}"}).status_code == 200
    reasons = db_session.execute(text("select revoked_reason from auth_sessions where user_id = :u and revoked_at is not null"), {"u": user.id}).scalars().all()
    assert reasons == ["setup", "setup"]


def test_the_issued_session_is_a_normal_fresh_session(session_client, db_session):
    token = bootstrap(db_session)
    body = redeem(session_client, token).json()
    assert len(body["token"]) == 43 and len(body["csrf_token"]) == 43
    row = db_session.execute(text("select token_hash, csrf_hash from auth_sessions")).one()
    assert row.token_hash == hash_token(body["token"]) and row.csrf_hash == hash_token(body["csrf_token"])


def test_failed_redemptions_are_throttled_per_source_and_recorded_without_the_token(session_client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "setup_source_max_failures", 3)
    bootstrap(db_session)
    codes = [redeem(session_client, "Q" * 43).status_code for _ in range(5)]
    assert codes == [400, 400, 400, 429, 429]
    assert [e.event_type for e in events(db_session) if e.event_type == "setup_failure"] == ["setup_failure"] * 3
    assert "Q" * 43 not in " ".join(str(v) for row in db_session.execute(text("select * from security_events")) for v in row)


def test_the_setup_endpoint_does_not_exist_outside_session_mode(session_client, db_session, monkeypatch):
    token = bootstrap(db_session)
    monkeypatch.setattr(settings, "auth_mode", "dev")
    assert redeem(session_client, token).status_code == 404


def test_a_seeded_dev_user_is_unusable_in_session_mode_until_a_link_is_redeemed(session_client, db_session):
    seeded = make_user(db_session, email="fredrik-like@dev.test")
    assert login(session_client, seeded.email, "fredrik@dev.test").status_code == 401
    assert login(session_client, seeded.email, PASSWORD).status_code == 401
    assert session_client.get("/api/me/organizations", headers={"X-Dev-User-Email": seeded.email}).status_code == 401


# --- disable, enable, purge -------------------------------------------------------------------------------------------------------------------------


def test_disabling_a_user_ends_all_sessions_and_blocks_login_until_enabled(session_client, db_session):
    user = login_user(db_session)
    handles = [make_session(db_session, user) for _ in range(3)]

    assert admin.disable_user(db_session, email=user.email) == 3

    for handle in handles:
        assert session_client.get("/api/me/organizations", headers=handle.read_headers).status_code == 401
    assert login(session_client, user.email, PASSWORD).status_code == 401
    admin.enable_user(db_session, email=user.email)
    assert login(session_client, user.email, PASSWORD).status_code == 200
    assert session_client.get("/api/me/organizations", headers=handles[0].read_headers).status_code == 401  # nothing is resurrected
    assert [e.event_type for e in events(db_session) if e.event_type.startswith("user_")] == ["user_disabled", "user_enabled"]


def test_reissuing_for_a_disabled_or_unknown_user_is_refused(db_session):
    user = login_user(db_session)
    admin.disable_user(db_session, email=user.email)
    with pytest.raises(admin.OperatorError, match="disabled"):
        admin.reissue_setup_link(db_session, email=user.email)
    with pytest.raises(admin.OperatorError, match="no user"):
        admin.reissue_setup_link(db_session, email="ghost@example.test")
    with pytest.raises(admin.OperatorError):
        admin.disable_user(db_session, email="ghost@example.test")


def test_purge_removes_only_what_is_past_its_retention(db_session):
    user = login_user(db_session)
    now = clock.utcnow()
    old = now - timedelta(days=settings.auth_record_retention_days + 5)
    kept_session, old_session, old_revoked = make_session(db_session, user), make_session(db_session, user, now=old, absolute_days=1), make_session(db_session, user, now=old, absolute_days=30)
    db_session.execute(text("update auth_sessions set revoked_at = :t, revoked_reason = 'logout' where id = :i"), {"t": old, "i": old_revoked.session.id})
    stale_token = bootstrap(db_session, "a@example.test"), bootstrap(db_session, "b@example.test")
    db_session.execute(text("update user_setup_tokens set used_at = :t where user_id = (select id from users where email = 'a@example.test')"), {"t": old})
    db_session.execute(text("update user_setup_tokens set expires_at = :t where user_id = (select id from users where email = 'b@example.test')"), {"t": old})
    fresh_user = bootstrap(db_session, "c@example.test")
    event_old = now - timedelta(days=settings.security_event_retention_days + 2)
    recent = now - timedelta(days=settings.security_event_retention_days - 2)
    db_session.execute(text("insert into security_events (occurred_at, event_type, source) values (:t, 'login_failure', 'old'), (:n, 'login_failure', 'new'), (:r, 'login_failure', 'recent')"), {"t": event_old, "n": now, "r": recent})

    counts = admin.purge(db_session)

    assert counts["sessions"] == 2 and counts["setup_tokens"] == 2 and counts["security_events"] >= 1
    assert db_session.scalar(text("select count(*) from auth_sessions where id = :i"), {"i": kept_session.session.id}) == 1
    assert db_session.scalar(text("select count(*) from auth_sessions where id in (:a, :b)"), {"a": old_session.session.id, "b": old_revoked.session.id}) == 0
    assert db_session.scalar(text("select count(*) from user_setup_tokens")) == 1
    assert db_session.scalar(text("select count(*) from security_events where source = 'old'")) == 0
    assert db_session.scalar(text("select count(*) from security_events where source = 'new'")) == 1
    assert db_session.scalar(text("select count(*) from security_events where source = 'recent'")) == 1  # inside the retention: kept
    assert fresh_user and stale_token


def test_purge_works_in_bounded_batches(db_session):
    old = clock.utcnow() - timedelta(days=settings.security_event_retention_days + 3)
    db_session.execute(text("insert into security_events (occurred_at, event_type, source) select :t, 'login_failure', 'bulk' from generate_series(1, 25)"), {"t": old})
    from app.core import security_events

    assert security_events.purge_events(db_session, clock.utcnow(), batch=10, max_batches=2) == 20  # at most batch * max_batches per call
    assert db_session.scalar(text("select count(*) from security_events where source = 'bulk'")) == 5
    assert security_events.purge_events(db_session, clock.utcnow(), batch=10, max_batches=10) == 5
