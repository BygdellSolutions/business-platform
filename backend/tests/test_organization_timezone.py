"""The organization's time zone decides what "today" is for date defaults.

At 22:30 UTC on 7 October it is already 8 October in Stockholm. A transaction or invoice created then without a
date must get the organization's date. An organization that never set a zone keeps UTC (nothing is assumed).
"""

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.core.org_time import is_known_zone, today_in
from app.models import Organization, Role
from tests.factories import add_member, make_customer, make_org, make_transaction, make_user
from tests.test_migration_currency import _columns, _execute, _scalar, _upgrade, _downgrade, scratch_url  # noqa: F401

URL = "/api/organization"
LATE_EVENING_UTC = datetime(2026, 10, 7, 22, 30, tzinfo=timezone.utc)  # 00:30 on 8 October in Stockholm (CEST)


@pytest.fixture
def late_evening(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "utcnow", lambda: LATE_EVENING_UTC)


def _member(db: Session, org: Organization, role: Role = Role.OWNER) -> dict[str, str]:
    user = make_user(db)
    add_member(db, org, user, role)
    return {"X-Dev-User-Email": user.email}


# --- the rule itself ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "zone,moment,expected",
    [
        ("Europe/Stockholm", LATE_EVENING_UTC, date(2026, 10, 8)),
        ("UTC", LATE_EVENING_UTC, date(2026, 10, 7)),
        (None, LATE_EVENING_UTC, date(2026, 10, 7)),  # no zone configured: UTC, as before
        ("America/Los_Angeles", datetime(2026, 10, 8, 5, 0, tzinfo=timezone.utc), date(2026, 10, 7)),
        # Daylight saving starts at 01:00 UTC on 29 March 2026 in Stockholm: 00:30 UTC is 01:30 local, same day.
        ("Europe/Stockholm", datetime(2026, 3, 29, 0, 30, tzinfo=timezone.utc), date(2026, 3, 29)),
        ("Europe/Stockholm", datetime(2026, 3, 28, 22, 59, tzinfo=timezone.utc), date(2026, 3, 28)),
        ("Europe/Stockholm", datetime(2026, 3, 28, 23, 0, tzinfo=timezone.utc), date(2026, 3, 29)),
    ],
)
def test_today_is_the_calendar_date_in_the_zone(zone, moment, expected):
    assert today_in(zone, moment) == expected


def test_today_uses_the_clock_seam(late_evening):
    assert today_in("Europe/Stockholm") == date(2026, 10, 8)


@pytest.mark.parametrize("name,known", [("Europe/Stockholm", True), ("UTC", True), ("Mars/Olympus", False), ("../etc/passwd", False), ("", False)])
def test_only_real_zone_names_are_known(name, known):
    assert is_known_zone(name) is known


# --- settings API -------------------------------------------------------------------------------------------------


def test_an_owner_sets_reads_and_clears_the_time_zone(client: TestClient, db_session: Session):
    org = make_org(db_session)
    headers = _member(db_session, org)

    assert client.get(URL, headers=headers).json()["timezone"] is None
    saved = client.patch(URL, json={"timezone": "Europe/Stockholm"}, headers=headers)
    assert saved.status_code == 200 and saved.json()["timezone"] == "Europe/Stockholm"
    assert client.get(URL, headers=headers).json()["timezone"] == "Europe/Stockholm"

    cleared = client.patch(URL, json={"timezone": None}, headers=headers)
    assert cleared.status_code == 200 and cleared.json()["timezone"] is None


@pytest.mark.parametrize("bad", ["Mars/Olympus", "europe/stockholm", "Europe/Stockholm; DROP TABLE", "x" * 65, ""])
def test_an_unknown_zone_is_refused_and_nothing_changes(client: TestClient, db_session: Session, bad: str):
    org = make_org(db_session, timezone="UTC")
    headers = _member(db_session, org)

    response = client.patch(URL, json={"timezone": bad}, headers=headers)

    assert response.status_code == 422
    assert "timezone" in response.json()["detail"][0]["loc"]
    db_session.refresh(org)
    assert org.timezone == "UTC"


@pytest.mark.parametrize("role", [Role.ACCOUNTANT, Role.EMPLOYEE, Role.VIEWER])
def test_only_owners_and_admins_change_the_time_zone(client: TestClient, db_session: Session, role: Role):
    org = make_org(db_session)
    response = client.patch(URL, json={"timezone": "Europe/Stockholm"}, headers=_member(db_session, org, role))
    assert response.status_code == 403
    db_session.refresh(org)
    assert org.timezone is None


def test_the_settings_say_what_today_is_for_the_organization(client: TestClient, db_session: Session, late_evening):
    stockholm = make_org(db_session, "Stockholm", timezone="Europe/Stockholm")
    unset = make_org(db_session, "Unset")

    assert client.get(URL, headers=_member(db_session, stockholm, Role.VIEWER)).json()["today"] == "2026-10-08"
    assert client.get(URL, headers=_member(db_session, unset, Role.VIEWER)).json()["today"] == "2026-10-07"


def test_the_database_refuses_a_malformed_zone(db_session: Session):
    org = make_org(db_session)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.execute(text("UPDATE organizations SET timezone = 'bad zone!' WHERE id = :id"), {"id": org.id})


# --- date defaults --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("zone,expected", [("Europe/Stockholm", "2026-10-08"), (None, "2026-10-07")])
def test_a_new_transaction_without_a_date_gets_the_organizations_today(
    client: TestClient, db_session: Session, late_evening, zone, expected
):
    org = make_org(db_session, timezone=zone)
    customer = make_customer(db_session, org)

    response = client.post("/api/transactions", json={"billing_customer_id": str(customer.id)}, headers=_member(db_session, org))

    assert response.status_code == 201, response.text
    assert response.json()["transaction_date"] == expected


def test_a_given_transaction_date_is_kept_whatever_the_zone(client: TestClient, db_session: Session, late_evening):
    org = make_org(db_session, timezone="Pacific/Kiritimati")  # UTC+14
    customer = make_customer(db_session, org)
    body = {"billing_customer_id": str(customer.id), "transaction_date": "2026-01-01"}

    assert client.post("/api/transactions", json=body, headers=_member(db_session, org)).json()["transaction_date"] == "2026-01-01"


@pytest.mark.parametrize("zone,expected", [("Europe/Stockholm", "2026-10-08"), (None, "2026-10-07")])
def test_a_new_invoice_without_a_date_gets_the_organizations_today(
    client: TestClient, db_session: Session, late_evening, zone, expected
):
    org = make_org(db_session, timezone=zone)
    tx = make_transaction(db_session, org, status="completed")

    response = client.post("/api/invoices", json={"transaction_ids": [str(tx.id)]}, headers=_member(db_session, org))

    assert response.status_code == 201, response.text
    assert response.json()["invoice_date"] == expected


# --- migration ------------------------------------------------------------------------------------------------------

BEFORE = "e29c5d7a3b48"


def test_the_migration_adds_the_column_and_assumes_no_zone(scratch_url: str):  # noqa: F811
    _upgrade(scratch_url, BEFORE)
    _execute(scratch_url, "INSERT INTO organizations (name, default_currency) VALUES ('Existing', 'SEK')")

    _upgrade(scratch_url, "head")
    assert "timezone" in _columns(scratch_url, "organizations")
    assert _scalar(scratch_url, "SELECT count(*) FROM organizations WHERE timezone IS NOT NULL") == 0

    _downgrade(scratch_url, BEFORE)
    assert "timezone" not in _columns(scratch_url, "organizations")
    assert _scalar(scratch_url, "SELECT count(*) FROM organizations WHERE name = 'Existing'") == 1
