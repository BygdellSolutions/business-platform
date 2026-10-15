"""The horse journal (J1): entries per visit, written as drafts and finalized, with explicit journal permissions.

Covers who may read and write (owner/admin/employee yes; accountant/viewer never), that an employee changes only their own
drafts, conflict detection on drafts, finalization, the bounds on text and drawing marks, the list, and tenant isolation.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Role
from tests.factories import add_member, make_customer, make_horse, make_org, make_user

MARK = {"id": "11111111-1111-4111-8111-111111111111", "side": "left", "tool": "arrow", "points": [[0.25, 0.5], [0.75, 0.5]]}


def _person(db: Session, org, role: Role, name: str):
    user = make_user(db, name=name)
    add_member(db, org, user, role)
    return {"X-Dev-User-Email": user.email}


@pytest.fixture
def world(db_session: Session):
    org = make_org(db_session)
    horse = make_horse(db_session, org, "Kalle", owner=make_customer(db_session, org, "Anna Andersson"))
    people = {role.value: _person(db_session, org, role, f"{role.value.title()} Person") for role in Role}
    people["employee2"] = _person(db_session, org, Role.EMPLOYEE, "Other Employee")
    return org, horse, people


def _create(client: TestClient, horse, headers, **body):
    response = client.post(f"/api/horses/{horse.id}/journal", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _url(horse, entry=None, tail=""):
    return f"/api/horses/{horse.id}/journal" + (f"/{entry['id']}" if entry else "") + tail


def _if_match(entry):
    return {"If-Match": f'"{entry["version"]}"'}


# --- who may use it ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["owner", "admin", "employee"])
def test_owner_admin_and_employee_read_and_write(client: TestClient, world, role):
    _, horse, people = world
    entry = _create(client, horse, people[role], body="Stiff left shoulder.", marks=[MARK])
    assert entry["status"] == "draft" and entry["practitioner_name"] == f"{role.title()} Person" and entry["version"] == 1
    assert entry["diagram_version"] == 2  # new entries are drawn on the newest illustration
    assert client.get(_url(horse, entry), headers=people["owner"]).json()["body"] == "Stiff left shoulder."
    assert [row["id"] for row in client.get(_url(horse), headers=people[role]).json()] == [entry["id"]]


@pytest.mark.parametrize("role", ["accountant", "viewer"])
def test_accountant_and_viewer_have_no_access_at_all(client: TestClient, world, role):
    _, horse, people = world
    entry = _create(client, horse, people["owner"], body="Private")
    outsider = people[role]
    assert client.get(_url(horse), headers=outsider).status_code == 403
    assert client.get(_url(horse, entry), headers=outsider).status_code == 403
    assert client.post(_url(horse), json={"body": "x"}, headers=outsider).status_code == 403
    assert client.patch(_url(horse, entry), json={"body": "x"}, headers={**outsider, **_if_match(entry)}).status_code == 403
    assert client.post(_url(horse, entry, "/finalize"), headers={**outsider, **_if_match(entry)}).status_code == 403
    # the role is judged before anything is looked up: a made-up id is the same 403
    assert client.get(f"/api/horses/{uuid.uuid4()}/journal", headers=outsider).status_code == 403


def test_an_employee_changes_only_their_own_drafts_but_reads_all(client: TestClient, world):
    _, horse, people = world
    mine = _create(client, horse, people["employee"], body="Mine")
    theirs = _create(client, horse, people["employee2"], body="Theirs")

    assert client.get(_url(horse, theirs), headers=people["employee"]).json()["can_change"] is False
    assert client.patch(_url(horse, theirs), json={"body": "Hijack"}, headers={**people["employee"], **_if_match(theirs)}).status_code == 403
    assert client.post(_url(horse, theirs, "/finalize"), headers={**people["employee"], **_if_match(theirs)}).status_code == 403
    assert client.get(_url(horse, mine), headers=people["employee"]).json()["can_change"] is True
    assert client.patch(_url(horse, mine), json={"body": "Edited"}, headers={**people["employee"], **_if_match(mine)}).status_code == 200
    # an admin or an owner may change any draft
    assert client.patch(_url(horse, theirs), json={"body": "By admin"}, headers={**people["admin"], **_if_match(theirs)}).status_code == 200
    assert client.get(_url(horse, theirs), headers=people["employee2"]).json()["body"] == "By admin"


# --- drafts, conflicts and finalization ---------------------------------------------------------------------------


def test_a_draft_needs_the_version_it_is_based_on(client: TestClient, world):
    _, horse, people = world
    entry = _create(client, horse, people["owner"], body="v1")
    bare = TestClient(client.app).patch(_url(horse, entry), json={"body": "x"}, headers=people["owner"])
    assert bare.status_code == 428

    first = client.patch(_url(horse, entry), json={"body": "v2"}, headers={**people["owner"], **_if_match(entry)})
    assert first.status_code == 200 and first.json()["version"] == 2
    stale = client.patch(_url(horse, entry), json={"body": "lost update"}, headers={**people["owner"], **_if_match(entry)})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_record" and stale.json()["detail"]["current_version"] == 2
    assert client.get(_url(horse, entry), headers=people["owner"]).json()["body"] == "v2"
    same = client.patch(_url(horse, entry), json={"body": "v2"}, headers={**people["owner"], **{"If-Match": '"2"'}})
    assert same.json()["version"] == 2  # a save that changes nothing moves no version


def test_finalizing_freezes_the_entry(client: TestClient, world):
    _, horse, people = world
    entry = _create(client, horse, people["employee"], body="Treated the poll.", marks=[MARK])
    final = client.post(_url(horse, entry, "/finalize"), headers={**people["employee"], **_if_match(entry)})
    assert final.status_code == 200, final.text
    body = final.json()
    assert body["status"] == "final" and body["finalized_at"] and body["finalized_by_name"] == "Employee Person" and body["can_change"] is False
    after = {**people["owner"], "If-Match": f'"{body["version"]}"'}
    changed = client.patch(_url(horse, entry), json={"body": "Rewritten"}, headers=after)
    assert changed.status_code == 409 and changed.json()["detail"]["code"] == "journal_entry_final"
    assert client.post(_url(horse, entry, "/finalize"), headers=after).status_code == 409
    again = client.get(_url(horse, entry), headers=people["owner"]).json()
    assert (again["body"], again["marks"]) == ("Treated the poll.", [MARK])


def test_an_empty_entry_cannot_be_finalized(client: TestClient, world):
    _, horse, people = world
    entry = _create(client, horse, people["owner"])
    assert client.post(_url(horse, entry, "/finalize"), headers={**people["owner"], **_if_match(entry)}).status_code == 422
    assert client.get(_url(horse, entry), headers=people["owner"]).json()["status"] == "draft"


# --- bounds on text and marks ---------------------------------------------------------------------------------------


def test_marks_round_trip_exactly(client: TestClient, world):
    _, horse, people = world
    marks = [MARK, {"id": str(uuid.uuid4()), "side": "right", "tool": "x", "points": [[0.1, 0.9]]}, {"id": str(uuid.uuid4()), "side": "right", "tool": "o", "points": [[0, 1]]}]
    entry = _create(client, horse, people["owner"], marks=marks)
    assert client.get(_url(horse, entry), headers=people["owner"]).json()["marks"] == marks
    replaced = client.patch(_url(horse, entry), json={"marks": [MARK]}, headers={**people["owner"], **_if_match(entry)})
    assert replaced.json()["marks"] == [MARK]


@pytest.mark.parametrize(
    "bad",
    [
        {**MARK, "tool": "freehand"},
        {**MARK, "side": "front"},
        {**MARK, "points": [[0.1, 0.1]]},  # an arrow has two points
        {**MARK, "tool": "x", "points": [[0.1, 0.1], [0.2, 0.2]]},  # an x has one
        {**MARK, "points": [[1.5, 0.5], [0.2, 0.2]]},
        {**MARK, "points": [[-0.1, 0.5], [0.2, 0.2]]},
        {**MARK, "points": [[0.1, 0.5, 0.3], [0.2, 0.2]]},
        {**MARK, "color": "red"},
        {**MARK, "id": "not-a-uuid"},
        {"side": "left", "tool": "x", "points": [[0.1, 0.1]]},
    ],
)
def test_a_malformed_mark_is_refused(client: TestClient, world, bad):
    _, horse, people = world
    assert client.post(_url(horse), json={"marks": [bad]}, headers=people["owner"]).status_code == 422


def test_marks_and_text_have_size_limits(client: TestClient, world):
    _, horse, people = world
    many = [{"id": str(uuid.uuid4()), "side": "left", "tool": "x", "points": [[0.5, 0.5]]} for _ in range(201)]
    assert client.post(_url(horse), json={"marks": many}, headers=people["owner"]).status_code == 422
    assert client.post(_url(horse), json={"marks": many[:200]}, headers=people["owner"]).status_code == 201
    assert client.post(_url(horse), json={"marks": [MARK, MARK]}, headers=people["owner"]).status_code == 422  # a duplicate id
    assert client.post(_url(horse), json={"body": "x" * 20_001}, headers=people["owner"]).status_code == 422
    assert client.post(_url(horse), json={"body": "x" * 20_000}, headers=people["owner"]).status_code == 201


def test_the_visit_time_must_say_its_time_zone_and_defaults_to_now(client: TestClient, world):
    _, horse, people = world
    assert client.post(_url(horse), json={"visit_at": "2026-10-15T14:30:00"}, headers=people["owner"]).status_code == 422
    stated = _create(client, horse, people["owner"], visit_at="2026-10-15T14:30:00+02:00", practitioner_name="  Tina  ")
    assert stated["visit_at"].startswith("2026-10-15T12:30:00") and stated["practitioner_name"] == "Tina"
    assert _create(client, horse, people["owner"])["visit_at"]


# --- the list -----------------------------------------------------------------------------------------------------------


def test_the_list_is_newest_visit_first_with_summaries_pages_and_a_date_filter(client: TestClient, world):
    _, horse, people = world
    old = _create(client, horse, people["owner"], visit_at="2026-09-01T10:00:00+00:00", body="Old visit with some text", marks=[MARK])
    new = _create(client, horse, people["owner"], visit_at="2026-10-10T10:00:00+00:00", body="New visit")
    rows = client.get(_url(horse), headers=people["owner"]).json()
    assert [row["id"] for row in rows] == [new["id"], old["id"]]
    assert rows[1]["mark_count"] == 1 and rows[1]["preview"] == "Old visit with some text" and "body" not in rows[1] and "marks" not in rows[1]
    assert [row["id"] for row in client.get(_url(horse), params={"limit": 1, "offset": 1}, headers=people["owner"]).json()] == [old["id"]]
    assert [row["id"] for row in client.get(_url(horse), params={"date_from": "2026-10-01"}, headers=people["owner"]).json()] == [new["id"]]
    assert [row["id"] for row in client.get(_url(horse), params={"date_to": "2026-09-01"}, headers=people["owner"]).json()] == [old["id"]]


# --- isolation ---------------------------------------------------------------------------------------------------------


def test_another_organizations_horse_and_entries_do_not_exist_for_us(client: TestClient, db_session: Session, world):
    _, horse, people = world
    other_org = make_org(db_session, "Other")
    other_horse = make_horse(db_session, other_org, "Kalle", owner=make_customer(db_session, other_org, "Anna Andersson"))
    other_owner = _person(db_session, other_org, Role.OWNER, "Their Owner")
    theirs = _create(client, other_horse, other_owner, body="Secret")

    assert client.get(_url(other_horse), headers=people["owner"]).status_code == 404
    assert client.post(_url(other_horse), json={"body": "x"}, headers=people["owner"]).status_code == 404
    assert client.get(_url(other_horse, theirs), headers=people["owner"]).status_code == 404
    assert client.get(_url(horse, theirs), headers=people["owner"]).status_code == 404  # right horse path, foreign entry
    assert client.patch(_url(horse, theirs), json={"body": "x"}, headers={**people["owner"], **_if_match(theirs)}).status_code == 404
    assert client.post(_url(horse, theirs, "/finalize"), headers={**people["owner"], **_if_match(theirs)}).status_code == 404
    # and an entry of one of OUR horses is not reachable through another horse
    sibling = make_horse(db_session, world[0], "Storm", owner=make_customer(db_session, world[0], "Erik"))
    mine = _create(client, horse, people["owner"], body="Mine")
    assert client.get(_url(sibling, mine), headers=people["owner"]).status_code == 404
    assert client.get(_url(other_horse, theirs), headers=other_owner).json()["body"] == "Secret"


def test_the_horse_history_records_the_steps_but_not_the_journal_text(client: TestClient, world):
    _, horse, people = world
    entry = _create(client, horse, people["owner"], body="Very private observation", marks=[MARK])
    client.post(_url(horse, entry, "/finalize"), headers={**people["owner"], **_if_match(entry)})
    events = client.get("/api/history", params={"entity_type": "horse", "entity_id": str(horse.id)}, headers=people["owner"]).json()["events"]
    journal = [e for e in events if e["entity_type"] == "horse_journal_entry"]
    assert {e["action"] for e in journal} == {"created", "updated"}
    assert "Very private observation" not in str(journal) and "marks" not in str(journal)
    assert any(e["changes"].get("status") == {"from": "draft", "to": "final"} for e in journal)


# --- J3: a finalized entry is frozen by the database; corrections are dated additions --------------------------------------


def _finalized(client: TestClient, horse, headers, **body):
    entry = _create(client, horse, headers, **body)
    return client.post(_url(horse, entry, "/finalize"), headers={**headers, **_if_match(entry)}).json()


def test_the_database_itself_refuses_to_change_or_delete_a_finalized_entry(client: TestClient, db_session: Session, world):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    _, horse, people = world
    draft = _create(client, horse, people["owner"], body="Draft")
    final = _finalized(client, horse, people["owner"], body="Final")

    db_session.execute(text("UPDATE horse_journal_entries SET body = 'edited draft' WHERE id = :i"), {"i": draft["id"]})  # a draft may change
    for statement in ("UPDATE horse_journal_entries SET body = 'rewritten' WHERE id = :i", "UPDATE horse_journal_entries SET status = 'draft', finalized_at = NULL, finalized_by = NULL WHERE id = :i", "DELETE FROM horse_journal_entries WHERE id = :i"):
        nested = db_session.begin_nested()
        with pytest.raises(DBAPIError, match="finalized journal entry"):
            db_session.execute(text(statement), {"i": final["id"]})
        nested.rollback()


def test_a_correction_is_added_to_a_finalized_entry_by_its_author_an_admin_or_an_owner(client: TestClient, world):
    _, horse, people = world
    final = _finalized(client, horse, people["employee"], body="Original text", marks=[MARK])

    by_author = client.post(_url(horse, final, "/corrections"), json={"body": "  Left, not right, shoulder.  "}, headers=people["employee"])
    assert by_author.status_code == 201, by_author.text
    by_admin = client.post(_url(horse, final, "/corrections"), json={"body": "Follow-up done."}, headers=people["admin"])
    assert by_admin.status_code == 201

    entry = client.get(_url(horse, final), headers=people["employee2"]).json()
    assert [c["body"] for c in entry["corrections"]] == ["Left, not right, shoulder.", "Follow-up done."]
    assert [c["created_by_name"] for c in entry["corrections"]] == ["Employee Person", "Admin Person"]
    assert (entry["body"], entry["marks"], entry["version"]) == ("Original text", [MARK], final["version"])  # the entry itself is untouched
    assert entry["can_correct"] is False  # employee2 did not write it
    assert client.get(_url(horse, final), headers=people["employee"]).json()["can_correct"] is True


def test_only_the_right_people_can_correct_and_only_a_finalized_entry(client: TestClient, world):
    _, horse, people = world
    final = _finalized(client, horse, people["employee"], body="Original")
    draft = _create(client, horse, people["employee"], body="Draft")

    assert client.post(_url(horse, final, "/corrections"), json={"body": "x"}, headers=people["employee2"]).status_code == 403
    for role in ("accountant", "viewer"):
        assert client.post(_url(horse, final, "/corrections"), json={"body": "x"}, headers=people[role]).status_code == 403
    refused = client.post(_url(horse, draft, "/corrections"), json={"body": "x"}, headers=people["employee"])
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "journal_entry_not_final"
    for bad in ("", "   ", "x" * 5001):
        assert client.post(_url(horse, final, "/corrections"), json={"body": bad}, headers=people["owner"]).status_code == 422
    assert client.get(_url(horse, final), headers=people["owner"]).json()["corrections"] == []


def test_a_correction_cannot_be_changed_or_deleted_even_directly_in_the_database(client: TestClient, db_session: Session, world):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    _, horse, people = world
    final = _finalized(client, horse, people["owner"], body="Original")
    client.post(_url(horse, final, "/corrections"), json={"body": "Added later"}, headers=people["owner"])
    for statement in ("UPDATE horse_journal_corrections SET body = 'edited' WHERE entry_id = :i", "DELETE FROM horse_journal_corrections WHERE entry_id = :i"):
        nested = db_session.begin_nested()
        with pytest.raises(DBAPIError, match="journal correction"):
            db_session.execute(text(statement), {"i": final["id"]})
        nested.rollback()


def test_a_foreign_entry_cannot_be_corrected(client: TestClient, db_session: Session, world):
    _, horse, people = world
    other_org = make_org(db_session, "Other")
    other_horse = make_horse(db_session, other_org, "Kalle", owner=make_customer(db_session, other_org, "Anna Andersson"))
    other_owner = _person(db_session, other_org, Role.OWNER, "Their Owner")
    theirs = _finalized(client, other_horse, other_owner, body="Secret")
    assert client.post(_url(horse, theirs, "/corrections"), json={"body": "x"}, headers=people["owner"]).status_code == 404
    assert client.post(_url(other_horse, theirs, "/corrections"), json={"body": "x"}, headers=people["owner"]).status_code == 404


def test_deleting_the_organization_removes_its_journals_even_the_finalized_ones(client: TestClient, db_session: Session, world):
    from datetime import datetime, timezone

    from sqlalchemy import text

    from app.core.organization_deletion import delete_organization
    from tests.factories import make_user as _make_user  # noqa: F401

    org, horse, people = world
    final = _finalized(client, horse, people["owner"], body="Original")
    client.post(_url(horse, final, "/corrections"), json={"body": "Later"}, headers=people["owner"])
    owner_id = db_session.scalar(text("SELECT u.id FROM users u JOIN organization_users m ON m.user_id = u.id WHERE m.organization_id = :o AND m.role = 'owner'"), {"o": org.id})

    delete_organization(db_session, organization_id=org.id, actor_user_id=owner_id, confirm_name=org.name, now=datetime.now(timezone.utc), source=None)

    for table in ("horse_journal_entries", "horse_journal_corrections"):
        assert db_session.scalar(text(f"SELECT count(*) FROM {table} WHERE organization_id = :o"), {"o": org.id}) == 0
