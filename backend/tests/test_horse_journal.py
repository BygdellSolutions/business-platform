"""The horse journal (J1): entries per visit, written as drafts and finalized, with explicit journal permissions.

Covers who may read and write (owner/admin/employee yes; accountant/viewer never), that an employee changes only their own
drafts, conflict detection on drafts, finalization, the bounds on text and drawing marks, the list, and tenant isolation.
"""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.models import Role
from tests.factories import add_member, make_customer, make_horse, make_org, make_user

MARK = {"id": "11111111-1111-4111-8111-111111111111", "number": 1, "side": "left", "tool": "arrow", "points": [[0.25, 0.5], [0.75, 0.5]]}


IDS: dict[str, uuid.UUID] = {}  # the user id behind each set of headers, by email


def _person(db: Session, org, role: Role, name: str):
    user = make_user(db, name=name)
    add_member(db, org, user, role)
    IDS[user.email] = user.id
    return {"X-Dev-User-Email": user.email}


def _id(headers) -> str:
    return str(IDS[headers["X-Dev-User-Email"]])


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
    assert entry["practitioner_user_id"] == _id(people[role])  # the author, unless someone else is chosen
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
    marks = [MARK, {"id": str(uuid.uuid4()), "number": 2, "side": "right", "tool": "x", "points": [[0.1, 0.9]]}, {"id": str(uuid.uuid4()), "number": 3, "side": "right", "tool": "o", "points": [[0, 1]]}]
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
    stated = _create(client, horse, people["owner"], visit_at="2026-10-15T14:30:00+02:00")
    assert stated["visit_at"].startswith("2026-10-15T12:30:00")
    assert _create(client, horse, people["owner"])["visit_at"]


# --- marks carry numbers so the text can refer to them ----------------------------------------------------------------------------


def _plain(tool: str = "x", **more) -> dict:
    return {"id": str(uuid.uuid4()), "side": "left", "tool": tool, "points": [[0.5, 0.5]], **more}


def test_marks_without_numbers_are_numbered_in_order_and_given_numbers_are_kept(client: TestClient, world):
    _, horse, people = world
    entry = _create(client, horse, people["owner"], marks=[_plain(), _plain(), _plain()])
    assert [m["number"] for m in entry["marks"]] == [1, 2, 3]
    kept = _create(client, horse, people["owner"], marks=[_plain(number=7), _plain(), _plain(number=2)])
    assert [m["number"] for m in kept["marks"]] == [7, 8, 2]  # the new one goes after the highest, whatever was deleted before


def test_a_number_stays_with_its_mark_when_another_is_deleted_and_is_never_shared(client: TestClient, world):
    _, horse, people = world
    first, second, third = _plain(number=1), _plain(number=2), _plain(number=3)
    entry = _create(client, horse, people["owner"], marks=[first, second, third])
    after = client.patch(_url(horse, entry), json={"marks": [first, third]}, headers={**people["owner"], **_if_match(entry)}).json()
    assert [m["number"] for m in after["marks"]] == [1, 3]  # mark 3 is still mark 3
    assert client.post(_url(horse), json={"marks": [_plain(number=2), _plain(number=2)]}, headers=people["owner"]).status_code == 422
    for bad in (0, -1, 10_000, "two"):
        assert client.post(_url(horse), json={"marks": [_plain(number=bad)]}, headers=people["owner"]).status_code == 422


def test_a_deleted_marks_number_is_never_given_to_another_mark(client: TestClient, world):
    """Draw 1-3, delete 3, add one: it is 4, not 3 (a note saying "see mark 3" must never point at a different mark)."""
    _, horse, people = world
    owner = people["owner"]
    first, second, third = _plain(number=1), _plain(number=2), _plain(number=3)
    entry = _create(client, horse, owner, marks=[first, second, third])
    assert entry["next_mark_number"] == 4

    after_delete = client.patch(_url(horse, entry), json={"marks": [first, second]}, headers={**owner, **_if_match(entry)}).json()
    assert [m["number"] for m in after_delete["marks"]] == [1, 2] and after_delete["next_mark_number"] == 4  # the counter did not go back

    added = client.patch(_url(horse, after_delete), json={"marks": [first, second, _plain()]}, headers={**owner, **_if_match(after_delete)}).json()
    assert [m["number"] for m in added["marks"]] == [1, 2, 4] and added["next_mark_number"] == 5

    emptied = client.patch(_url(horse, added), json={"marks": []}, headers={**owner, **_if_match(added)}).json()
    assert emptied["marks"] == [] and emptied["next_mark_number"] == 5  # even with no marks left
    again = client.patch(_url(horse, emptied), json={"marks": [_plain()]}, headers={**owner, **_if_match(emptied)}).json()
    assert [m["number"] for m in again["marks"]] == [5]


def test_the_counter_follows_numbers_the_browser_hands_out_and_a_save_without_marks_leaves_it(client: TestClient, world):
    _, horse, people = world
    owner = people["owner"]
    entry = _create(client, horse, owner, marks=[_plain(number=9)])
    assert entry["next_mark_number"] == 10
    texted = client.patch(_url(horse, entry), json={"body": "text only"}, headers={**owner, **_if_match(entry)}).json()
    assert texted["next_mark_number"] == 10
    unchanged = client.patch(_url(horse, texted), json={"marks": texted["marks"]}, headers={**owner, **_if_match(texted)}).json()
    assert unchanged["version"] == texted["version"]  # the same marks again change nothing, not even the version
    assert _create(client, horse, owner)["next_mark_number"] == 1  # an entry without marks starts at 1


# --- the practitioner is a member chosen from a list -----------------------------------------------------------------------------


def test_the_practitioner_is_a_member_of_the_organization_chosen_by_id(client: TestClient, world):
    _, horse, people = world
    entry = _create(client, horse, people["owner"], practitioner_user_id=_id(people["employee"]))
    assert (entry["practitioner_user_id"], entry["practitioner_name"]) == (_id(people["employee"]), "Employee Person")
    changed = client.patch(_url(horse, entry), json={"practitioner_user_id": _id(people["admin"])}, headers={**people["owner"], **_if_match(entry)})
    assert changed.status_code == 200 and (changed.json()["practitioner_user_id"], changed.json()["practitioner_name"]) == (_id(people["admin"]), "Admin Person")


def test_someone_who_is_not_a_member_cannot_be_chosen_and_the_free_text_name_is_gone(client: TestClient, db_session: Session, world):
    _, horse, people = world
    other_org = make_org(db_session, "Other")
    stranger = _person(db_session, other_org, Role.OWNER, "Stranger")
    answers = {
        client.post(_url(horse), json={"practitioner_user_id": uid}, headers=people["owner"]).text
        for uid in (_id(stranger), str(uuid.uuid4()))  # a user of another organization and a made-up id look the same
    }
    assert len(answers) == 1 and "Choose a member of this organization" in answers.pop()
    entry = _create(client, horse, people["owner"])
    assert client.patch(_url(horse, entry), json={"practitioner_user_id": _id(stranger)}, headers={**people["owner"], **_if_match(entry)}).status_code == 422
    assert client.post(_url(horse), json={"practitioner_name": "Typed by hand"}, headers=people["owner"]).status_code == 422


def test_only_people_who_work_with_the_journal_can_be_the_practitioner_and_the_list_says_who(client: TestClient, world):
    _, horse, people = world
    for role in ("accountant", "viewer"):  # they have no journal, so they cannot be the one who did the work either
        assert client.post(_url(horse), json={"practitioner_user_id": _id(people[role])}, headers=people["owner"]).status_code == 422
    listed = client.get("/api/members/people", params={"journal": "true"}, headers=people["employee"]).json()
    assert sorted(p["name"] for p in listed) == ["Admin Person", "Employee Person", "Other Employee", "Owner Person"]
    everyone = client.get("/api/members/people", headers=people["employee"]).json()
    assert len(everyone) == 6  # without the filter (used for services on orders) every member is listed


def test_an_old_entry_keeps_the_name_the_practitioner_had(client: TestClient, db_session: Session, world):
    from sqlalchemy import text

    _, horse, people = world
    entry = _create(client, horse, people["owner"], practitioner_user_id=_id(people["employee"]))
    db_session.execute(text("UPDATE users SET name = 'Renamed Later' WHERE id = :i"), {"i": _id(people["employee"])})
    assert client.get(_url(horse, entry), headers=people["owner"]).json()["practitioner_name"] == "Employee Person"


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


def test_the_backfill_gives_old_entries_their_author_as_practitioner_and_leaves_the_freeze_in_place(client: TestClient, db_session: Session, world):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    _, horse, people = world
    mine = _create(client, horse, people["employee"], body="Draft by the employee")
    final = _finalized(client, horse, people["owner"], body="Final by the owner")
    renamed = _create(client, horse, people["admin"], body="Written under another name")
    # make them look like entries from before the list existed
    db_session.execute(text("ALTER TABLE horse_journal_entries DISABLE TRIGGER trg_horse_journal_entries_final_immutable"))
    db_session.execute(text("UPDATE horse_journal_entries SET practitioner_user_id = NULL WHERE id IN (:a, :b, :c)"), {"a": mine["id"], "b": final["id"], "c": renamed["id"]})
    db_session.execute(text("UPDATE horse_journal_entries SET practitioner_name = 'Someone Else' WHERE id = :c"), {"c": renamed["id"]})
    db_session.execute(text("ALTER TABLE horse_journal_entries ENABLE TRIGGER trg_horse_journal_entries_final_immutable"))

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "b8d0f2a4c6e7_journal_practitioner_backfill.py"
    spec = importlib.util.spec_from_file_location("backfill", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(db_session.connection())):
        module.upgrade()

    ids = {row[0]: row[1] for row in db_session.execute(text("SELECT id::text, practitioner_user_id::text FROM horse_journal_entries"))}
    assert ids[mine["id"]] == _id(people["employee"]) and ids[final["id"]] == _id(people["owner"])  # the author, where the name says so
    assert ids[renamed["id"]] is None  # a name that is not the author's stays a name
    nested = db_session.begin_nested()
    with pytest.raises(DBAPIError, match="finalized journal entry"):  # the freeze is back on
        db_session.execute(text("UPDATE horse_journal_entries SET body = 'x' WHERE id = :i"), {"i": final["id"]})
    nested.rollback()


# --- deleting a draft (and only a draft) ----------------------------------------------------------------------------------------------


def test_a_draft_can_be_deleted_by_its_author_an_admin_or_an_owner(client: TestClient, world):
    _, horse, people = world
    for who, deleter in (("employee", "employee"), ("employee", "admin"), ("employee2", "owner")):
        entry = _create(client, horse, people[who], body="A draft to throw away", marks=[MARK])
        gone = client.delete(_url(horse, entry), headers={**people[deleter], **_if_match(entry)})
        assert gone.status_code == 204, (who, deleter, gone.text)
        assert client.get(_url(horse, entry), headers=people["owner"]).status_code == 404
    assert client.get(_url(horse), headers=people["owner"]).json() == []


def test_a_finalized_entry_can_never_be_deleted_not_even_by_an_owner(client: TestClient, db_session: Session, world):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    _, horse, people = world
    final = _finalized(client, horse, people["owner"], body="Frozen")
    refused = client.delete(_url(horse, final), headers={**people["owner"], **_if_match(final)})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "journal_entry_final"
    assert client.get(_url(horse, final), headers=people["owner"]).json()["body"] == "Frozen"
    nested = db_session.begin_nested()  # and the database itself refuses it as well
    with pytest.raises(DBAPIError, match="finalized journal entry"):
        db_session.execute(text("DELETE FROM horse_journal_entries WHERE id = :i"), {"i": final["id"]})
    nested.rollback()


def test_deleting_a_draft_needs_the_right_person_and_the_current_version(client: TestClient, world):
    _, horse, people = world
    entry = _create(client, horse, people["employee"], body="Mine")
    assert client.delete(_url(horse, entry), headers={**people["employee2"], **_if_match(entry)}).status_code == 403  # someone else's draft
    for role in ("accountant", "viewer"):
        assert client.delete(_url(horse, entry), headers={**people[role], **_if_match(entry)}).status_code == 403
    assert TestClient(client.app).delete(_url(horse, entry), headers=people["employee"]).status_code == 428  # no version
    client.patch(_url(horse, entry), json={"body": "Changed meanwhile"}, headers={**people["owner"], **_if_match(entry)})
    stale = client.delete(_url(horse, entry), headers={**people["employee"], **_if_match(entry)})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_record"
    assert client.get(_url(horse, entry), headers=people["owner"]).status_code == 200  # nothing was deleted by any of these


def test_a_foreign_draft_cannot_be_deleted_and_the_history_notes_a_deletion_without_the_text(client: TestClient, db_session: Session, world):
    _, horse, people = world
    other_org = make_org(db_session, "Other")
    other_horse = make_horse(db_session, other_org, "Kalle", owner=make_customer(db_session, other_org, "Anna Andersson"))
    other_owner = _person(db_session, other_org, Role.OWNER, "Their Owner")
    theirs = _create(client, other_horse, other_owner, body="Theirs")
    assert client.delete(_url(horse, theirs), headers={**people["owner"], **_if_match(theirs)}).status_code == 404
    assert client.get(_url(other_horse, theirs), headers=other_owner).status_code == 200

    mine = _create(client, horse, people["owner"], body="Private words")
    client.delete(_url(horse, mine), headers={**people["owner"], **_if_match(mine)})
    events = client.get("/api/history", params={"entity_type": "horse", "entity_id": str(horse.id)}, headers=people["owner"]).json()["events"]
    journal = [e for e in events if e["entity_type"] == "horse_journal_entry"]
    assert {e["action"] for e in journal} == {"created", "deleted"} and "Private words" not in str(journal)


# --- the entries are numbered and the list can be sorted ---------------------------------------------------------------------


def test_entries_have_numbers_per_organization_and_the_list_sorts_by_its_columns(client: TestClient, world):
    _, horse, people = world
    a = _create(client, horse, people["owner"], visit_at="2026-10-01T10:00:00+00:00", body="b", marks=[MARK])
    b = _create(client, horse, people["employee"], visit_at="2026-10-03T10:00:00+00:00", body="a")
    c = _create(client, horse, people["admin"], visit_at="2026-10-02T10:00:00+00:00", body="c")
    assert [a["number"], b["number"], c["number"]] == [1, 2, 3]

    def order(**params) -> list[int]:
        return [row["number"] for row in client.get(_url(horse), params=params, headers=people["owner"]).json()]

    assert order() == [2, 3, 1]  # newest visit first
    assert order(sort="number") == [1, 2, 3] and order(sort="number", dir="desc") == [3, 2, 1]
    assert order(sort="visit") == [1, 3, 2]
    assert order(sort="by") == [3, 2, 1]  # Admin, Employee, Owner
    assert order(sort="marks", dir="desc")[0] == 1  # the one with a mark first
    client.patch(_url(horse, b), json={"follow_up_on": "2026-11-20"}, headers={**people["owner"], **_if_match(b)})
    assert order(sort="follow_up")[0] == 2 and order(sort="follow_up", dir="desc")[0] == 2  # entries without a follow-up date come last either way
    assert client.get(_url(horse), params={"sort": "preview"}, headers=people["owner"]).status_code == 422  # a column the list does not offer


# --- a long history (J5): every list is bounded, whatever the size of the history ----------------------------------------


def _bulk_entries(db: Session, org, horse, count: int, *, by: str = "Tina Therapist") -> None:
    """`count` finished-looking drafts for one horse, one visit a day backwards from today, made in a single statement (the database numbers them)."""
    db.execute(
        text(
            "INSERT INTO horse_journal_entries (organization_id, horse_id, visit_at, practitioner_name, body, status, marks, version, diagram_version, next_mark_number) "
            "SELECT :org, :horse, now() - make_interval(days => g), :by, 'Visit ' || g || ' ' || repeat('treated the shoulder. ', 20), 'draft', '[]'::jsonb, 1, 2, 1 "
            "FROM generate_series(1, :count) AS g"
        ),
        {"org": org.id, "horse": horse.id, "by": by, "count": count},
    )
    db.flush()


def _statements_during(db: Session, call) -> tuple[list[str], object]:
    seen: list[str] = []

    def record(conn, cursor, statement, *rest):
        seen.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        result = call()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return seen, result


def test_a_long_history_is_listed_in_bounded_pages_with_a_constant_number_of_queries(client: TestClient, db_session: Session, world):
    org, horse, people = world
    owner = people["owner"]
    _bulk_entries(db_session, org, horse, 5)
    small, _ = _statements_during(db_session, lambda: client.get(_url(horse), params={"limit": 25}, headers=owner))

    _bulk_entries(db_session, org, horse, 3000)
    big, response = _statements_during(db_session, lambda: client.get(_url(horse), params={"limit": 25}, headers=owner))

    rows = response.json()
    assert response.status_code == 200 and len(rows) == 25  # a page, not the history
    assert all("body" not in row for row in rows)  # a summary row: the text is fetched by opening an entry
    assert len(big) == len(small), (len(small), len(big))  # no query per entry: the same few statements for 5 entries and for 3,005
    assert [row["visit_at"] for row in rows] == sorted((row["visit_at"] for row in rows), reverse=True)  # newest first
    assert client.get(_url(horse), params={"limit": 201}, headers=owner).status_code == 422  # a page can never be asked to be the history
    default = client.get(_url(horse), headers=owner).json()
    assert len(default) <= 50


def test_pages_cover_a_long_history_exactly_once_and_the_date_range_and_search_stay_bounded(client: TestClient, db_session: Session, world):
    org, horse, people = world
    owner = people["owner"]
    _bulk_entries(db_session, org, horse, 600)

    seen: list[str] = []
    for offset in range(0, 650, 200):
        seen += [row["id"] for row in client.get(_url(horse), params={"limit": 200, "offset": offset}, headers=owner).json()]
    assert len(seen) == 600 and len(set(seen)) == 600  # every entry on exactly one page, none twice, none missing

    today = db_session.scalar(text("SELECT (now() - interval '10 days')::date"))
    week = client.get(_url(horse), params={"date_from": str(today - timedelta(days=3)), "date_to": str(today), "limit": 200}, headers=owner).json()
    assert 4 <= len(week) <= 5  # only the visits of those days
    found = client.get(_url(horse), params={"q": "Visit 17 ", "limit": 200}, headers=owner).json()
    assert 1 <= len(found) <= 200


def test_a_long_history_of_one_horse_is_invisible_and_free_for_another(client: TestClient, db_session: Session, world):
    org, horse, people = world
    owner = people["owner"]
    other_horse = make_horse(db_session, org, "Storm", owner=make_customer(db_session, org, "Bo Berg"))
    _bulk_entries(db_session, org, horse, 1500)
    _bulk_entries(db_session, org, other_horse, 3, by="Someone Else")

    listed = client.get(_url(other_horse), params={"limit": 200}, headers=owner).json()
    assert len(listed) == 3 and {row["practitioner_name"] for row in listed} == {"Someone Else"}  # not the neighbour's 1,500


def test_the_list_uses_the_index_for_the_default_order(db_session: Session, world):
    org, horse, _ = world
    _bulk_entries(db_session, org, horse, 5000)
    db_session.execute(text("ANALYZE horse_journal_entries"))
    plan = "\n".join(
        row[0]
        for row in db_session.execute(
            text(
                "EXPLAIN SELECT id FROM horse_journal_entries WHERE organization_id = :org AND horse_id = :horse "
                "ORDER BY visit_at DESC, number DESC LIMIT 25"
            ),
            {"org": org.id, "horse": horse.id},
        )
    )
    assert "ix_horse_journal_entries_horse_visit" in plan, plan  # not a scan of the whole table, and no sort of every entry
    assert "Sort" not in plan.split("Index")[0] or "Incremental Sort" in plan, plan
