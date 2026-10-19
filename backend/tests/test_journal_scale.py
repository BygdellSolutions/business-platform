"""A horse with a long journal: the list stays bounded and fast however many entries (and however long their text) there are."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.models import Role
from tests.factories import add_member, make_customer, make_horse, make_org, make_user

ENTRIES = 3000


@pytest.fixture
def history(db_session: Session):
    org = make_org(db_session)
    owner_customer = make_customer(db_session, org, "Anna Andersson")
    kalle, storm = make_horse(db_session, org, "Kalle", owner=owner_customer), make_horse(db_session, org, "Storm", owner=owner_customer)
    user = make_user(db_session)
    add_member(db_session, org, user, Role.OWNER)
    other = make_org(db_session, "Other")
    other_horse = make_horse(db_session, other, "Kalle", owner=make_customer(db_session, other, "Anna Andersson"))
    for target_org, horse in ((org, kalle), (org, storm), (other, other_horse)):  # the same amount of history for every horse and organization
        db_session.execute(
            text(
                """
                INSERT INTO horse_journal_entries (organization_id, horse_id, visit_at, practitioner_name, body, status)
                SELECT :o, :h, TIMESTAMPTZ '2020-01-01 10:00+00' + (n || ' hours')::interval, 'Tina', repeat('long text ', 600) || n, 'draft'
                FROM generate_series(1, :n) AS n
                """
            ),
            {"o": target_org.id, "h": horse.id, "n": ENTRIES},
        )
    db_session.execute(text("ANALYZE horse_journal_entries"))
    return org, kalle, {"X-Dev-User-Email": user.email}


def _count(db: Session, call):
    counted: list[str] = []
    listener = lambda conn, cursor, statement, parameters, context, many: counted.append(statement)  # noqa: E731
    engine = db.get_bind().engine
    event.listen(engine, "before_cursor_execute", listener)
    try:
        result = call()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    return result, counted


def test_a_page_of_a_long_journal_is_a_few_statements_and_a_small_answer(client: TestClient, db_session: Session, history):
    _, horse, owner = history
    response, statements = _count(db_session, lambda: client.get(f"/api/horses/{horse.id}/journal", params={"limit": 25}, headers=owner))
    rows = response.json()
    assert response.status_code == 200 and len(rows) == 25
    assert rows[0]["number"] > rows[-1]["number"] or rows[0]["visit_at"] > rows[-1]["visit_at"]  # newest visit first
    assert len(statements) <= 8  # not one per entry
    assert len(response.content) < 25_000  # 6,000-character texts (150 KB if the bodies were sent), yet each row carries only its preview
    assert all(len(row["preview"]) <= 160 and "body" not in row for row in rows)


def test_the_page_is_read_through_the_index_not_by_scanning_the_table(db_session: Session, history):
    org, horse, _ = history
    plan = db_session.execute(
        text(
            "EXPLAIN (FORMAT JSON) SELECT id FROM horse_journal_entries WHERE organization_id = :o AND horse_id = :h "
            "ORDER BY visit_at DESC, number DESC LIMIT 25"
        ),
        {"o": org.id, "h": horse.id},
    ).scalar()
    assert "Seq Scan" not in json.dumps(plan)  # 9,000 entries of three horses: the page is found through ix_horse_journal_entries_horse_visit


def test_later_pages_and_the_drafts_overview_stay_bounded_too(client: TestClient, db_session: Session, history):
    _, horse, owner = history
    deep, statements = _count(db_session, lambda: client.get(f"/api/horses/{horse.id}/journal", params={"limit": 25, "offset": 2900}, headers=owner))
    assert len(deep.json()) == 25 and len(statements) <= 8
    drafts, statements = _count(db_session, lambda: client.get("/api/journal/drafts", params={"limit": 25}, headers=owner))
    assert len(drafts.json()) == 25 and len(statements) <= 8 and len(drafts.content) < 30_000
    summary, statements = _count(db_session, lambda: client.get("/api/journal/summary", headers=owner))
    assert summary.json()["drafts"] == 2 * ENTRIES and len(statements) <= 4  # every draft of the organization, in one count
