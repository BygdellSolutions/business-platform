"""A person's own sessions: list them, end one, end the others, end all. Only ever their own."""

import uuid

import pytest

from tests.auth_support import active_sessions, events, login_user, make_session

pytestmark = pytest.mark.usefixtures("session_mode")

LIST = "/api/auth/sessions"
ME = "/api/me/organizations"


def listed(client, handle):
    response = client.get(LIST, headers=handle.read_headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_list_shows_only_my_usable_sessions_and_marks_this_one(session_client, db_session):
    me, someone = login_user(db_session), login_user(db_session)
    here, other = make_session(db_session, me), make_session(db_session, me)
    ended = make_session(db_session, me)
    session_client.post("/api/auth/logout", headers=ended.headers)  # one of mine that is already over
    foreign = make_session(db_session, someone)

    body = listed(session_client, here)

    ids = {row["id"] for row in body}
    assert ids == {str(here.session.id), str(other.session.id)}
    assert str(foreign.session.id) not in ids and str(ended.session.id) not in ids
    assert [row["current"] for row in body if row["id"] == str(here.session.id)] == [True]
    assert [row["current"] for row in body if row["id"] == str(other.session.id)] == [False]


def test_the_list_never_carries_a_token_a_hash_or_an_address(session_client, db_session):
    user = login_user(db_session)
    handle = make_session(db_session, user)

    [row] = listed(session_client, handle)

    assert set(row) == {"id", "created_at", "last_used_at", "user_agent", "current"}
    text = str(row)
    assert handle.token not in text and handle.csrf not in text


def test_ending_one_session_ends_only_that_one(session_client, db_session):
    user = login_user(db_session)
    here, other, third = make_session(db_session, user), make_session(db_session, user), make_session(db_session, user)

    response = session_client.delete(f"{LIST}/{other.session.id}", headers=here.headers)

    assert response.status_code == 204
    assert session_client.get(ME, headers=other.read_headers).status_code == 401
    assert session_client.get(ME, headers=here.read_headers).status_code == 200
    assert session_client.get(ME, headers=third.read_headers).status_code == 200


def test_someone_elses_ended_and_unknown_sessions_are_the_same_404(session_client, db_session):
    me, someone = login_user(db_session), login_user(db_session)
    here = make_session(db_session, me)
    foreign = make_session(db_session, someone)
    gone = make_session(db_session, me)
    session_client.delete(f"{LIST}/{gone.session.id}", headers=here.headers)

    answers = [session_client.delete(f"{LIST}/{target}", headers=here.headers).status_code for target in (foreign.session.id, gone.session.id, uuid.uuid4())]

    assert answers == [404, 404, 404]
    assert session_client.get(ME, headers=foreign.read_headers).status_code == 200  # untouched
    assert len(active_sessions(db_session, someone)) == 1


def test_ending_the_other_sessions_keeps_this_one(session_client, db_session):
    user = login_user(db_session)
    here, a, b = make_session(db_session, user), make_session(db_session, user), make_session(db_session, user)

    response = session_client.post(f"{LIST}/end-others", headers=here.headers)

    assert response.status_code == 200 and response.json() == {"ended": 2}
    assert session_client.get(ME, headers=here.read_headers).status_code == 200
    assert session_client.get(ME, headers=a.read_headers).status_code == 401 and session_client.get(ME, headers=b.read_headers).status_code == 401


def test_signing_out_everywhere_ends_this_session_too(session_client, db_session):
    user = login_user(db_session)
    here, other = make_session(db_session, user), make_session(db_session, user)
    bystander = make_session(db_session, login_user(db_session))

    response = session_client.post(f"{LIST}/end-all", headers=here.headers)

    assert response.status_code == 200 and response.json() == {"ended": 2}
    assert session_client.get(ME, headers=here.read_headers).status_code == 401 and session_client.get(ME, headers=other.read_headers).status_code == 401
    assert session_client.get(ME, headers=bystander.read_headers).status_code == 200  # nobody else is affected


def test_each_ending_is_recorded_without_names_or_tokens(session_client, db_session):
    user = login_user(db_session)
    here, other = make_session(db_session, user), make_session(db_session, user)

    session_client.delete(f"{LIST}/{other.session.id}", headers=here.headers)
    session_client.post(f"{LIST}/end-others", headers=here.headers)
    session_client.post(f"{LIST}/end-all", headers=here.headers)

    assert [e.detail for e in events(db_session, "logout")] == ["one", "others=0", "all=1"]
    assert all(e.actor_user_id == user.id for e in events(db_session, "logout"))


def test_a_mutation_needs_the_csrf_token_and_a_session(session_client, db_session):
    user = login_user(db_session)
    here, other = make_session(db_session, user), make_session(db_session, user)

    for call in (
        lambda h: session_client.delete(f"{LIST}/{other.session.id}", headers=h),
        lambda h: session_client.post(f"{LIST}/end-others", headers=h),
        lambda h: session_client.post(f"{LIST}/end-all", headers=h),
    ):
        assert call(here.read_headers).status_code == 403  # no CSRF token
        assert call({}).status_code == 401  # no session
    assert len(active_sessions(db_session, user)) == 2
    assert session_client.get(LIST).status_code == 401
