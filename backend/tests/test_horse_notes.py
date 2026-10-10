"""Notes on a horse: a log of what was seen, done or agreed, with who wrote it and when.

Any member reads them; record writers add, change and delete them; every step is in the horse's history; another
organization's horse (or note) is a 404; the notes go with the horse.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Role
from app.modules.equine.models import HorseNote
from tests.factories import add_member, make_customer, make_horse, make_org, make_user


def _world(db: Session, role: Role = Role.OWNER):
    org = make_org(db)
    user = make_user(db, name="Tina Therapist")
    add_member(db, org, user, role)
    return org, {"X-Dev-User-Email": user.email}


def _horse(db: Session, org):
    return make_horse(db, org, "Kalle", owner=make_customer(db, org, "Anna Andersson"))


def test_notes_are_added_listed_newest_first_with_who_and_when(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    horse = _horse(db_session, org)

    first = client.post(f"/api/horses/{horse.id}/notes", json={"body": "Stiff left shoulder."}, headers=owner)
    second = client.post(f"/api/horses/{horse.id}/notes", json={"body": "  Better after massage.  "}, headers=owner)

    assert first.status_code == 201 and second.json()["body"] == "Better after massage."
    notes = client.get(f"/api/horses/{horse.id}/notes", headers=owner).json()
    assert {n["body"] for n in notes} == {"Stiff left shoulder.", "Better after massage."}
    assert all(n["created_by_name"] == "Tina Therapist" for n in notes)


def test_a_note_is_changed_and_deleted_and_the_horse_history_shows_both(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    horse = _horse(db_session, org)
    note = client.post(f"/api/horses/{horse.id}/notes", json={"body": "First draft"}, headers=owner).json()

    changed = client.patch(f"/api/horses/{horse.id}/notes/{note['id']}", json={"body": "Corrected"}, headers=owner)
    deleted = client.delete(f"/api/horses/{horse.id}/notes/{note['id']}", headers=owner)

    assert changed.json()["body"] == "Corrected" and deleted.status_code == 204
    assert client.get(f"/api/horses/{horse.id}/notes", headers=owner).json() == []
    events = client.get("/api/history", params={"entity_type": "horse", "entity_id": str(horse.id)}, headers=owner).json()["events"]
    note_events = [e for e in events if e["entity_type"] == "horse_note"]
    assert {e["action"] for e in note_events} == {"created", "updated", "deleted"}
    assert any(e["changes"].get("body") == {"from": "First draft", "to": "Corrected"} for e in note_events)


@pytest.mark.parametrize("body", ["", "   ", "x" * 5001])
def test_an_empty_or_huge_note_is_refused(client: TestClient, db_session: Session, body):
    org, owner = _world(db_session)
    horse = _horse(db_session, org)
    assert client.post(f"/api/horses/{horse.id}/notes", json={"body": body}, headers=owner).status_code == 422


def test_a_viewer_reads_notes_but_cannot_write_them(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    viewer = make_user(db_session)
    add_member(db_session, org, viewer, Role.VIEWER)
    headers = {"X-Dev-User-Email": viewer.email}
    horse = _horse(db_session, org)
    note = client.post(f"/api/horses/{horse.id}/notes", json={"body": "Owner's note"}, headers=owner).json()

    assert len(client.get(f"/api/horses/{horse.id}/notes", headers=headers).json()) == 1
    assert client.post(f"/api/horses/{horse.id}/notes", json={"body": "x"}, headers=headers).status_code == 403
    assert client.patch(f"/api/horses/{horse.id}/notes/{note['id']}", json={"body": "x"}, headers=headers).status_code == 403
    assert client.delete(f"/api/horses/{horse.id}/notes/{note['id']}", headers=headers).status_code == 403


def test_another_organizations_horse_and_notes_are_not_found(client: TestClient, db_session: Session):
    _, owner_a = _world(db_session)
    org_b, owner_b = _world(db_session)
    horse_b = _horse(db_session, org_b)
    note_b = client.post(f"/api/horses/{horse_b.id}/notes", json={"body": "B's note"}, headers=owner_b).json()

    assert client.get(f"/api/horses/{horse_b.id}/notes", headers=owner_a).status_code == 404
    assert client.post(f"/api/horses/{horse_b.id}/notes", json={"body": "x"}, headers=owner_a).status_code == 404
    assert client.patch(f"/api/horses/{horse_b.id}/notes/{note_b['id']}", json={"body": "x"}, headers=owner_a).status_code == 404
    assert client.delete(f"/api/horses/{horse_b.id}/notes/{note_b['id']}", headers=owner_a).status_code == 404
    assert client.get(f"/api/horses/{horse_b.id}/notes", headers=owner_b).json()[0]["body"] == "B's note"


def test_a_note_of_one_horse_cannot_be_reached_through_another(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    kalle, other = _horse(db_session, org), make_horse(db_session, org, "Other", owner=make_customer(db_session, org, "Bo"))
    note = client.post(f"/api/horses/{kalle.id}/notes", json={"body": "Kalle's"}, headers=owner).json()
    assert client.delete(f"/api/horses/{other.id}/notes/{note['id']}", headers=owner).status_code == 404


def test_the_notes_go_with_the_horse(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    horse = _horse(db_session, org)
    client.post(f"/api/horses/{horse.id}/notes", json={"body": "Note"}, headers=owner)

    assert client.delete(f"/api/horses/{horse.id}", headers=owner).status_code == 204
    db_session.expire_all()
    assert db_session.scalar(select(HorseNote).where(HorseNote.horse_id == horse.id)) is None
