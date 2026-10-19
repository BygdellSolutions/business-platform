"""The common-password check on every path that sets a NEW password, and its promise never to touch a login."""

import pytest
from sqlalchemy import select

from app.core import common_passwords, passwords
from app.models import User
from tests.auth_support import PASSWORD, active_sessions, login, login_user, make_session
from tests.test_auth_setup import NEW_PASSWORD, bootstrap, redeem

pytestmark = pytest.mark.usefixtures("session_mode")

COMMON = "Please choose a password that is less common."
MESSAGE = "This password is too common. Choose another."


def test_the_list_is_the_shipped_one_and_loads_without_a_network_call():
    assert len(common_passwords._load()) > 90_000


@pytest.mark.parametrize(
    "password",
    [
        "qwerty123456789",  # whole entry or its stem
        "Password123456",  # a common word with digits behind it: the 12-character minimum alone would accept it
        "PASSWORD1234!!",  # case and trailing symbols do not hide it
        "  iloveyou123456  ",  # surrounding spaces do not hide it
        "q1w2e3r4t5y6",  # a long entry on the list as a whole
    ],
)
def test_common_passwords_are_refused_with_one_fixed_message(password):
    assert common_passwords.is_common(password)
    assert passwords.policy_problem(password) == MESSAGE


@pytest.mark.parametrize(
    "password",
    [
        "correct horse battery staple",
        "a freshly chosen long passphrase",
        "my-secure-passphrase-password",  # merely ENDS in a common word: the whole stem is not on the list
        "86420246801357913579",  # digits only: a stem shorter than four letters is never judged
        "ab" * 6,
        "ünïcödé pässwörd ✓ 日本語パスワード",
    ],
)
def test_ordinary_passwords_are_not_refused(password):
    assert not common_passwords.is_common(password)
    assert passwords.policy_problem(password) is None


def test_the_message_never_says_which_check_matched():
    assert passwords.policy_problem("Password123456") == passwords.policy_problem("qwerty123456789") == MESSAGE


def test_changing_to_a_common_password_is_refused_and_nothing_changes(session_client, db_session):
    user = login_user(db_session)
    handle = make_session(db_session, user)
    other = make_session(db_session, user)

    response = session_client.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "Password123456"}, headers=handle.headers)

    assert response.status_code == 422 and MESSAGE in response.text
    assert len(active_sessions(db_session, user)) == 2  # the other session was not ended
    assert login(session_client, user.email, PASSWORD).status_code == 200  # the old password still works
    assert other.session.revoked_at is None


def test_a_setup_link_with_a_common_password_is_refused_and_stays_usable(session_client, db_session):
    token = bootstrap(db_session)

    refused = redeem(session_client, token, "Password123456")
    assert refused.status_code in (400, 422) and "too common" in refused.text.lower()

    assert redeem(session_client, token, NEW_PASSWORD).status_code == 200  # the link was not consumed


def test_a_login_with_a_password_that_is_now_on_the_list_still_works(session_client, db_session):
    """The list judges NEW passwords only: an existing account keeps signing in."""
    user = login_user(db_session, "Password123456")

    assert login(session_client, user.email, "Password123456").status_code == 200
    assert db_session.scalar(select(User).where(User.id == user.id)) is not None
