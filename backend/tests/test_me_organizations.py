"""GET /api/me/organizations: the current user's own memberships, and nothing else."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Role
from tests.factories import add_member, make_org, make_user

URL = "/api/me/organizations"


def headers(user, org=None):
    result = {"X-Dev-User-Email": user.email}
    if org is not None:
        result["X-Organization-Id"] = str(org.id)
    return result


def test_lists_every_membership_with_the_role_in_that_organization(client: TestClient, db_session: Session):
    user = make_user(db_session)
    a, b, c = make_org(db_session, "Alpha"), make_org(db_session, "Charlie"), make_org(db_session, "Bravo")
    add_member(db_session, a, user, Role.OWNER)
    add_member(db_session, b, user, Role.VIEWER)
    add_member(db_session, c, user, Role.ADMIN)

    response = client.get(URL, headers=headers(user))

    assert response.status_code == 200
    assert response.json() == [  # ordered by name
        {"id": str(a.id), "name": "Alpha", "role": "owner"},
        {"id": str(c.id), "name": "Bravo", "role": "admin"},
        {"id": str(b.id), "name": "Charlie", "role": "viewer"},
    ]


def test_needs_no_active_organization_even_with_several_memberships(client: TestClient, db_session: Session):
    user = make_user(db_session)
    add_member(db_session, make_org(db_session, "A"), user)
    add_member(db_session, make_org(db_session, "B"), user)

    assert client.get(URL, headers=headers(user)).status_code == 200  # /api/me would answer 400 here


def test_an_organization_selector_header_is_ignored_here(client: TestClient, db_session: Session):
    user = make_user(db_session)
    mine = make_org(db_session, "Mine")
    add_member(db_session, mine, user)

    for selector in (str(uuid.uuid4()), "not-a-uuid", str(make_org(db_session, "Foreign").id)):
        response = client.get(URL, headers={**headers(user), "X-Organization-Id": selector})
        assert response.status_code == 200
        assert [o["id"] for o in response.json()] == [str(mine.id)]


def test_never_lists_organizations_the_user_is_not_a_member_of(client: TestClient, db_session: Session):
    me, other = make_user(db_session), make_user(db_session)
    mine, theirs = make_org(db_session, "Same Name"), make_org(db_session, "Same Name")  # identical-looking
    add_member(db_session, mine, me, Role.EMPLOYEE)
    add_member(db_session, theirs, other, Role.OWNER)

    body = client.get(URL, headers=headers(me)).json()

    assert [o["id"] for o in body] == [str(mine.id)]
    assert body[0]["role"] == "employee"  # my role, not the other user's
    assert str(theirs.id) not in str(body)


def test_exposes_only_id_name_and_role(client: TestClient, db_session: Session):
    user = make_user(db_session)
    add_member(db_session, make_org(db_session, "Only"), user)

    assert set(client.get(URL, headers=headers(user)).json()[0]) == {"id", "name", "role"}


def test_a_user_without_memberships_gets_an_empty_list(client: TestClient, db_session: Session):
    assert client.get(URL, headers=headers(make_user(db_session))).json() == []


def test_requires_an_authenticated_active_user(client: TestClient, db_session: Session):
    inactive = make_user(db_session, is_active=False)
    add_member(db_session, make_org(db_session, "Org"), inactive)

    assert client.get(URL).status_code == 401
    assert client.get(URL, headers={"X-Dev-User-Email": "nobody@tests.invalid"}).status_code == 401
    assert client.get(URL, headers=headers(inactive)).status_code == 401


@pytest.mark.parametrize("method", ["post", "patch", "delete", "put"])
def test_is_read_only(client: TestClient, db_session: Session, method):
    user = make_user(db_session)
    assert getattr(client, method)(URL, headers=headers(user)).status_code == 405
