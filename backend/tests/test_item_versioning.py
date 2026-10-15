"""Changing an item (its price included) needs the version the caller last saw (If-Match), like Sales records do."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Role
from tests.factories import add_member, make_item, make_org, make_user


def _item(client: TestClient, headers) -> dict:
    return client.post("/api/items", json={"type": "service", "name": "Massage", "unit": "session", "price_ex_vat": "850", "vat_rate": "25"}, headers=headers).json()


def _patch(client: TestClient, item: dict, headers, body: dict, token: str | None):
    return client.patch(f"/api/items/{item['id']}", json=body, headers={**headers, **({"If-Match": token} if token is not None else {})})


def test_a_change_without_the_version_is_refused_and_changes_nothing(client, member):
    _, headers = member
    item = _item(client, headers)
    # the test client adds a fresh version unless one is given; a bare header-less request is built by hand
    bare = TestClient(client.app).patch(f"/api/items/{item['id']}", json={"price_ex_vat": "1"}, headers=headers)
    assert bare.status_code == 428
    assert client.get(f"/api/items/{item['id']}", headers=headers).json()["price_ex_vat"] == "850.00"


def test_two_tabs_the_second_save_of_a_price_is_refused(client, member):
    _, headers = member
    item = _item(client, headers)
    assert item["version"] == 1
    first = _patch(client, item, headers, {"price_ex_vat": "900"}, '"1"')
    assert first.status_code == 200 and first.json()["version"] == 2
    stale = _patch(client, item, headers, {"price_ex_vat": "700"}, '"1"')
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_record" and stale.json()["detail"]["current_version"] == 2
    assert client.get(f"/api/items/{item['id']}", headers=headers).json()["price_ex_vat"] == "900.00"  # the stale save changed nothing


def test_a_save_that_changes_nothing_does_not_move_the_version(client, member):
    _, headers = member
    item = _item(client, headers)
    same = _patch(client, item, headers, {"price_ex_vat": "850", "name": "Massage"}, '"1"')
    assert same.status_code == 200 and same.json()["version"] == 1


def test_another_organizations_item_is_not_found_whatever_the_version(client, member, db_session: Session):
    _, headers = member
    other_org, other_user = make_org(db_session, "Other"), make_user(db_session)
    add_member(db_session, other_org, other_user, Role.OWNER)
    foreign = make_item(db_session, other_org)
    for token in ('"1"', '"99"', None):
        response = client.patch(f"/api/items/{foreign.id}", json={"price_ex_vat": "1"}, headers={**headers, **({"If-Match": token} if token else {})})
        assert response.status_code == 404  # never 409/428: the version of a foreign record is not revealed
