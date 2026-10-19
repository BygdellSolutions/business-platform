"""Emergency global session revocation: `admin revoke-all-sessions`.

After a restore from a backup, or a suspected leak, no restored or stolen session may stay valid. One atomic statement ends
every active session; people sign in again with the same passwords; the log records one accurate event with a count only."""

from contextlib import nullcontext

import pytest
from sqlalchemy import select

from app.core import clock
from app.models import AuthSession
from app.scripts import admin
from tests.auth_support import PASSWORD, active_sessions, events, login, login_user, make_session

pytestmark = pytest.mark.usefixtures("session_mode")

ME = "/api/me/organizations"


def test_every_active_session_of_every_user_ends_and_the_count_is_returned(db_session):
    first, second = login_user(db_session), login_user(db_session)
    make_session(db_session, first), make_session(db_session, first), make_session(db_session, second)

    assert admin.revoke_all_sessions(db_session) == 3
    assert active_sessions(db_session, first) == [] and active_sessions(db_session, second) == []
    reasons = set(db_session.scalars(select(AuthSession.revoked_reason).where(AuthSession.user_id.in_([first.id, second.id]))))
    assert reasons == {"operator"}


def test_an_ended_session_no_longer_authenticates(session_client, db_session):
    user = login_user(db_session)
    handle = make_session(db_session, user)
    assert session_client.get(ME, headers=handle.read_headers).status_code == 200

    admin.revoke_all_sessions(db_session)

    assert session_client.get(ME, headers=handle.read_headers).status_code == 401


def test_a_session_that_had_already_ended_keeps_its_own_reason(db_session):
    user = login_user(db_session)
    earlier = make_session(db_session, user)
    earlier.session.revoked_at, earlier.session.revoked_reason = clock.utcnow(), "logout"
    make_session(db_session, user)

    assert admin.revoke_all_sessions(db_session) == 1  # only the active one
    db_session.refresh(earlier.session)
    assert earlier.session.revoked_reason == "logout"


def test_nobody_is_disabled_and_the_same_password_signs_in_again(session_client, db_session):
    user = login_user(db_session)
    make_session(db_session, user)

    admin.revoke_all_sessions(db_session)

    assert user.is_active is True
    assert login(session_client, user.email, PASSWORD).status_code == 200


def test_one_accurate_event_is_recorded_with_a_count_and_no_names_or_tokens(db_session):
    user = login_user(db_session)
    handle = make_session(db_session, user)

    admin.revoke_all_sessions(db_session)

    [event] = events(db_session, "sessions_revoked_by_operator")
    assert event.detail == "count=1" and event.actor_user_id is None and event.source is None
    assert user.email not in str(event.detail) and handle.token not in str(event.detail)


def test_with_no_sessions_it_still_succeeds_and_says_zero(db_session):
    assert admin.revoke_all_sessions(db_session) == 0
    assert events(db_session, "sessions_revoked_by_operator")[0].detail == "count=0"


def test_the_command_refuses_to_run_without_the_explicit_confirmation(db_session, monkeypatch, capsys):
    user = login_user(db_session)
    make_session(db_session, user)
    monkeypatch.setattr(admin, "SessionLocal", lambda: nullcontext(db_session))

    assert admin.main(["revoke-all-sessions"]) == 1
    assert "--yes-end-all-sessions" in capsys.readouterr().err
    assert len(active_sessions(db_session, user)) == 1  # nothing ended

    assert admin.main(["revoke-all-sessions", "--yes-end-all-sessions"]) == 0
    assert active_sessions(db_session, user) == []
    assert "1 session(s) ended" in capsys.readouterr().out
