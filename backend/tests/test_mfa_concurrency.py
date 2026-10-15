"""The second factor under real concurrency: committed data, separate connections, requests started at the same instant.

A code (or a challenge, or a recovery code) must be spendable exactly once however many requests present it together."""

from datetime import timedelta

import pytest
from pydantic import SecretStr

from app.core import mfa, passwords
from app.core.config import settings
from app.core.db import SessionLocal
from app.core import mfa_service
from app.models.auth import UserMfa
from tests.auth_support import BFF_SECRET, PASSWORD, CommittedUser, build_committed_user, purge_committed
from tests.invoicing_support import Request, race, scalar

pytestmark = pytest.mark.usefixtures("session_mode")


@pytest.fixture(autouse=True)
def committed(session_mode, monkeypatch):
    monkeypatch.setattr(settings, "trust_client_ip_header", True)
    monkeypatch.setattr(settings, "bff_internal_secret", SecretStr(BFF_SECRET))
    monkeypatch.setattr(settings, "argon2_max_concurrent", 8)
    monkeypatch.setattr(settings, "argon2_max_waiting", 200)
    monkeypatch.setattr(settings, "argon2_wait_seconds", 20.0)
    passwords.configure_admission_from_settings()
    created: list[CommittedUser] = []
    yield created
    purge_committed(created)


def enrolled_user(created) -> tuple[CommittedUser, list[str]]:
    user = build_committed_user()
    created.append(user)
    now = mfa_now() - timedelta(seconds=120)  # enrolled in the past, so the current time step is still unspent
    with SessionLocal() as db:
        from app.models import User

        person = db.get(User, user.id)
        mfa_service.begin_enrollment(db, person, now)
        row = db.get(UserMfa, user.id)
        codes = mfa_service.confirm_enrollment(db, person, mfa.hotp(mfa.derive_secret(row.seed), mfa.step_of(now)), now, user.source)
    return user, codes


def mfa_now():
    from app.core import clock

    return clock.utcnow()


def current_code(user: CommittedUser) -> str:
    with SessionLocal() as db:
        row = db.get(UserMfa, user.id)
        return mfa.hotp(mfa.derive_secret(row.seed), mfa.step_of(mfa_now()))


def first_step(user: CommittedUser) -> str:
    response = Request({"X-Client-Ip": user.source}, "post", "/api/auth/login", json={"email": user.email, "password": PASSWORD}).result()
    assert response.status_code == 200 and response.json()["mfa_required"] is True, response.text
    return response.json()["challenge"]


def second_step(user: CommittedUser, challenge: str, code: str):
    return Request({"X-Client-Ip": user.source}, "post", "/api/auth/login/mfa", json={"challenge": challenge, "code": code}).result()


def sessions_of(user: CommittedUser) -> int:
    return scalar("select count(*) from auth_sessions where user_id = :u and revoked_at is null", u=user.id)


def test_one_challenge_and_one_code_presented_together_open_exactly_one_session(committed):
    user, _ = enrolled_user(committed)
    challenge, code = first_step(user), current_code(user)

    responses = race(*[lambda: second_step(user, challenge, code) for _ in range(8)])

    assert sorted(r.status_code for r in responses) == [200] + [401] * 7
    assert sessions_of(user) == 1


def test_one_code_presented_through_many_challenges_opens_exactly_one_session(committed):
    user, _ = enrolled_user(committed)
    challenges = [first_step(user) for _ in range(6)]
    code = current_code(user)

    responses = race(*[(lambda c=c: second_step(user, c, code)) for c in challenges])

    assert sorted(r.status_code for r in responses) == [200] + [401] * 5  # a code works once, whichever challenge carries it
    assert sessions_of(user) == 1


def test_one_recovery_code_presented_together_works_exactly_once(committed):
    user, codes = enrolled_user(committed)
    challenges = [first_step(user) for _ in range(6)]

    responses = race(*[(lambda c=c: second_step(user, c, codes[0])) for c in challenges])

    assert sorted(r.status_code for r in responses) == [200] + [401] * 5
    assert scalar("select count(*) from user_recovery_codes where user_id = :u and used_at is not null", u=user.id) == 1
    assert sessions_of(user) == 1
