"""GET /api/customers?active=: filter by the active flag, always inside the organization."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.factories import make_customer, make_org


def names(client, headers, **params):
    response = client.get("/api/customers", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return [c["name"] for c in response.json()]


@pytest.fixture
def mixed(db_session: Session, member):
    org, headers = member
    make_customer(db_session, org, "Active Anna", email="anna@example.test")
    make_customer(db_session, org, "Inactive Bo", email="bo@example.test", active=False)
    make_customer(db_session, org, "Active Cleo", email=None)
    return org, headers


def test_without_the_filter_everyone_is_listed(client: TestClient, mixed):
    assert names(client, mixed[1]) == ["Active Anna", "Active Cleo", "Inactive Bo"]


def test_active_true_and_false(client: TestClient, mixed):
    assert names(client, mixed[1], active="true") == ["Active Anna", "Active Cleo"]
    assert names(client, mixed[1], active="false") == ["Inactive Bo"]


def test_combines_with_search_and_pagination(client: TestClient, mixed):
    assert names(client, mixed[1], active="true", q="cleo") == ["Active Cleo"]
    assert names(client, mixed[1], active="false", q="cleo") == []
    assert names(client, mixed[1], active="true", limit=1, offset=1) == ["Active Cleo"]


@pytest.mark.parametrize("bad", ["maybe", "2", ""])
def test_rejects_non_boolean_values(client: TestClient, mixed, bad):
    assert client.get("/api/customers", params={"active": bad}, headers=mixed[1]).status_code == 422


def test_never_returns_another_organizations_customers(client: TestClient, db_session: Session, mixed):
    other = make_org(db_session, "Other")
    make_customer(db_session, other, "Active Anna", email="anna@example.test")  # identical-looking
    make_customer(db_session, other, "Inactive Bo", active=False)

    assert names(client, mixed[1], active="true") == ["Active Anna", "Active Cleo"]
    assert names(client, mixed[1], active="false") == ["Inactive Bo"]
