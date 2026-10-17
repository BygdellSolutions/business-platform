"""Request budgets for signed-in traffic: per user, per organization and for expensive endpoints."""

import pytest

from app.core import metrics, rate_limit
from app.core.config import settings
from app.models import Role
from tests.auth_support import login, login_user, make_session
from tests.factories import add_member, make_org

CUSTOMERS = "/api/customers"
SUMMARY = "/api/invoices/summary"


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    rate_limit.reset()
    metrics.reset()
    now = [1000.0]
    monkeypatch.setattr(rate_limit, "clock", lambda: now[0])
    yield now
    rate_limit.reset()
    metrics.reset()


# --- the window itself ---------------------------------------------------------------------------------------------------------------------------


def test_a_window_allows_the_limit_then_says_how_long_to_wait(fresh_state):
    window = rate_limit.SlidingWindow()
    assert [window.hit("k", 3, 60) for _ in range(3)] == [None, None, None]

    wait = window.hit("k", 3, 60)

    assert wait is not None and 1 <= wait <= 61


def test_a_refused_request_is_not_recorded_so_a_block_ends_on_time(fresh_state):
    window = rate_limit.SlidingWindow()
    for _ in range(3):
        window.hit("k", 3, 60)
    fresh_state[0] += 30  # half way through the window the person keeps retrying
    for _ in range(50):  # hammering while blocked must not extend the block
        assert window.hit("k", 3, 60) is not None

    fresh_state[0] += 31  # 61 seconds after the three real requests

    assert window.hit("k", 3, 60) is None


def test_keys_are_independent_and_the_number_of_keys_is_bounded(fresh_state):
    window = rate_limit.SlidingWindow(max_keys=3)
    for _ in range(2):
        window.hit("a", 2, 60)
    assert window.hit("a", 2, 60) is not None and window.hit("b", 2, 60) is None

    for key in ("c", "d", "e"):
        window.hit(key, 2, 60)

    assert len(window._hits) == 3  # the oldest keys were forgotten: memory stays bounded


# --- through the application ---------------------------------------------------------------------------------------------------------------------


def team(db_session, *emails_roles):
    org = make_org(db_session)
    handles = []
    for _ in emails_roles:
        user = login_user(db_session)
        add_member(db_session, org, user, Role.OWNER)
        handles.append(make_session(db_session, user))
    return org, handles


def get(client, handle, org, path=CUSTOMERS):
    return client.get(path, headers={**handle.read_headers, "X-Organization-Id": str(org.id)})


@pytest.mark.usefixtures("session_mode")
def test_one_person_who_exceeds_their_budget_is_refused_and_others_are_not(session_client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_user_per_minute", 3)
    org, (noisy, calm) = team(db_session, 1, 2)

    assert [get(session_client, noisy, org).status_code for _ in range(3)] == [200, 200, 200]
    refused = get(session_client, noisy, org)

    assert refused.status_code == 429
    assert refused.json() == {"detail": {"code": "rate_limited", "message": "Too many requests. Wait a moment and try again."}}
    assert refused.headers["retry-after"].isdigit() and int(refused.headers["retry-after"]) >= 1
    assert get(session_client, calm, org).status_code == 200  # a colleague in the same organization is unaffected
    assert metrics.snapshot()["counters"]["rate_limited_user"] == 1


@pytest.mark.usefixtures("session_mode")
def test_the_person_can_work_again_when_the_window_has_passed(session_client, db_session, monkeypatch, fresh_state):
    monkeypatch.setattr(settings, "rate_limit_user_per_minute", 2)
    org, (handle,) = team(db_session, 1)
    get(session_client, handle, org), get(session_client, handle, org)
    assert get(session_client, handle, org).status_code == 429

    fresh_state[0] += 61

    assert get(session_client, handle, org).status_code == 200


@pytest.mark.usefixtures("session_mode")
def test_one_organization_cannot_use_up_more_than_its_budget_and_other_organizations_are_unaffected(session_client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_org_per_minute", 4)
    busy_org, (first, second) = team(db_session, 1, 2)
    other_org, (elsewhere,) = team(db_session, 1)

    assert [get(session_client, first, busy_org).status_code for _ in range(2)] == [200, 200]
    assert [get(session_client, second, busy_org).status_code for _ in range(2)] == [200, 200]
    assert get(session_client, first, busy_org).status_code == 429  # the organization's shared budget is gone
    assert get(session_client, second, busy_org).status_code == 429
    assert get(session_client, elsewhere, other_org).status_code == 200
    assert metrics.snapshot()["counters"]["rate_limited_org"] == 2


@pytest.mark.usefixtures("session_mode")
def test_expensive_endpoints_have_a_tighter_budget_that_leaves_ordinary_ones_alone(session_client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_heavy_per_minute", 2)
    org, (handle,) = team(db_session, 1)

    assert [get(session_client, handle, org, SUMMARY).status_code for _ in range(2)] == [200, 200]
    assert get(session_client, handle, org, SUMMARY).status_code == 429
    assert get(session_client, handle, org, CUSTOMERS).status_code == 200  # ordinary requests are still fine
    assert metrics.snapshot()["counters"]["rate_limited_heavy"] == 1


@pytest.mark.usefixtures("session_mode")
def test_a_request_without_a_session_is_a_plain_401_and_uses_no_budget(session_client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_user_per_minute", 1)
    org, _ = team(db_session, 1)

    assert [session_client.get(CUSTOMERS, headers={"X-Organization-Id": str(org.id)}).status_code for _ in range(5)] == [401] * 5


@pytest.mark.usefixtures("session_mode")
def test_security_events_are_counted_for_the_operator(session_client, db_session):
    user = login_user(db_session)

    login(session_client, user.email, "a wrong long password for this test")
    login(session_client, user.email, "a wrong long password for this test")

    assert metrics.snapshot()["counters"]["security_event_login_failure"] == 2
