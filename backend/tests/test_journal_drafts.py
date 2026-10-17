"""The drafts overview (dashboard to-do): the entries not finalized yet that this person can finish, across all horses."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Role
from tests.factories import add_member, make_customer, make_horse, make_org, make_user


def _person(db: Session, org, role: Role, name: str):
    user = make_user(db, name=name)
    add_member(db, org, user, role)
    return {"X-Dev-User-Email": user.email}


@pytest.fixture
def world(db_session: Session):
    org = make_org(db_session)
    owner_customer = make_customer(db_session, org, "Anna Andersson")
    kalle, storm = make_horse(db_session, org, "Kalle", owner=owner_customer), make_horse(db_session, org, "Storm", owner=owner_customer)
    people = {name: _person(db_session, org, role, name.title()) for name, role in (("owner", Role.OWNER), ("admin", Role.ADMIN), ("employee", Role.EMPLOYEE), ("employee2", Role.EMPLOYEE), ("accountant", Role.ACCOUNTANT), ("viewer", Role.VIEWER))}
    return org, kalle, storm, people


def _draft(client, horse, headers, **body):
    response = client.post(f"/api/horses/{horse.id}/journal", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _count(client, headers) -> int:
    return client.get("/api/journal/summary", headers=headers).json()["drafts"]


def test_an_employee_sees_their_own_drafts_and_an_owner_or_admin_every_draft(client: TestClient, world):
    _, kalle, storm, people = world
    mine = _draft(client, kalle, people["employee"], visit_at="2026-10-01T10:00:00+00:00")
    _draft(client, storm, people["employee2"], visit_at="2026-10-02T10:00:00+00:00")
    final = _draft(client, kalle, people["owner"], body="Done")
    client.post(f"/api/horses/{kalle.id}/journal/{final['id']}/finalize", headers={**people["owner"], "If-Match": f'"{final["version"]}"'})

    assert _count(client, people["employee"]) == 1 and _count(client, people["employee2"]) == 1
    assert _count(client, people["owner"]) == 2 and _count(client, people["admin"]) == 2  # a finalized entry is no longer a to-do
    rows = client.get("/api/journal/drafts", headers=people["employee"]).json()
    assert [(r["id"], r["horse_name"], r["status"]) for r in rows] == [(mine["id"], "Kalle", "draft")]
    assert [r["horse_name"] for r in client.get("/api/journal/drafts", headers=people["owner"]).json()] == ["Kalle", "Storm"]  # oldest visit first


def test_finalizing_or_deleting_a_draft_takes_it_off_the_list(client: Session, world):
    _, kalle, _, people = world
    one, two = _draft(client, kalle, people["owner"], body="a"), _draft(client, kalle, people["owner"], body="b")
    assert _count(client, people["owner"]) == 2
    client.delete(f"/api/horses/{kalle.id}/journal/{one['id']}", headers={**people["owner"], "If-Match": f'"{one["version"]}"'})
    assert _count(client, people["owner"]) == 1
    client.post(f"/api/horses/{kalle.id}/journal/{two['id']}/finalize", headers={**people["owner"], "If-Match": f'"{two["version"]}"'})
    assert _count(client, people["owner"]) == 0 and client.get("/api/journal/drafts", headers=people["owner"]).json() == []


@pytest.mark.parametrize("role", ["accountant", "viewer"])
def test_accountant_and_viewer_have_no_journal_overview_either(client: TestClient, world, role):
    _, _, _, people = world
    assert client.get("/api/journal/summary", headers=people[role]).status_code == 403
    assert client.get("/api/journal/drafts", headers=people[role]).status_code == 403


def test_another_organizations_drafts_are_never_counted_or_listed(client: TestClient, db_session: Session, world):
    _, kalle, _, people = world
    other = make_org(db_session, "Other")
    their_horse = make_horse(db_session, other, "Kalle", owner=make_customer(db_session, other, "Anna Andersson"))
    their_owner = _person(db_session, other, Role.OWNER, "Their Owner")
    _draft(client, their_horse, their_owner, body="Theirs")
    _draft(client, kalle, people["owner"], body="Ours")
    assert _count(client, people["owner"]) == 1 and _count(client, their_owner) == 1
    assert [r["preview"] for r in client.get("/api/journal/drafts", headers=people["owner"]).json()] == ["Ours"]


def test_the_list_pages_and_sorts(client: TestClient, world):
    _, kalle, storm, people = world
    for index in range(3):
        _draft(client, kalle if index % 2 == 0 else storm, people["owner"], visit_at=f"2026-10-0{index + 1}T10:00:00+00:00")
    assert len(client.get("/api/journal/drafts", params={"limit": 2}, headers=people["owner"]).json()) == 2
    assert [r["number"] for r in client.get("/api/journal/drafts", params={"sort": "number", "dir": "desc"}, headers=people["owner"]).json()] == [3, 2, 1]
    assert [r["horse_name"] for r in client.get("/api/journal/drafts", params={"sort": "horse"}, headers=people["owner"]).json()] == ["Kalle", "Kalle", "Storm"]
    assert client.get("/api/journal/drafts", params={"sort": "preview"}, headers=people["owner"]).status_code == 422
