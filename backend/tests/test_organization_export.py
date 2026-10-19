"""A complete copy of one organization's data (a ZIP), the rule that deleting needs a copy or an explicit "no copy", and the budget.

The export reads from its own snapshot session in production; these tests hand it the rollback-only test session instead, so the
whole code path (queries, archive, marker) runs against data that is never committed."""

import io
import json
import zipfile
from contextlib import nullcontext
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core import clock, organization_export
from app.core.config import settings
from app.core.organization_deletion import KEPT
from app.models import Organization, Role
from tests.auth_support import OTHER_PASSWORD, PASSWORD
from tests.factories import make_customer, make_org, make_transaction
from tests.test_danger_zone import _fill, _issued_invoice_with_pdf, _person, team  # noqa: F401  (team is a fixture)

pytestmark = pytest.mark.usefixtures("session_mode")

EXPORT = "/api/organization/export"
DELETE = "/api/organization/delete"
NAME = "Umeå Häst & Rehab"


@pytest.fixture(autouse=True)
def snapshot_is_the_test_session(db_session, monkeypatch):
    monkeypatch.setattr(organization_export, "_open_session", lambda: nullcontext(db_session))
    monkeypatch.setattr(organization_export, "_begin_snapshot", lambda db: None)  # the test's own transaction is already open
    monkeypatch.setattr(organization_export, "_end_snapshot", lambda db: None)  # ... and must not be rolled back


def download(team, headers=None, password=PASSWORD):
    return team.client.post(EXPORT, json={"password": password}, headers=headers or team.owner.h)


def archive(response) -> zipfile.ZipFile:
    assert response.status_code == 200, response.text
    return zipfile.ZipFile(io.BytesIO(response.content))


def rows(zf: zipfile.ZipFile, name: str) -> list[dict]:
    return [json.loads(line) for line in zf.read(name).decode("utf-8").splitlines() if line]


# --- what is in it -------------------------------------------------------------------------------------------------------------------------


def test_the_owner_gets_a_valid_archive_with_every_kind_of_data_and_the_stored_pdf(team, db_session: Session):
    _fill(db_session, team.org)
    invoice_id = _issued_invoice_with_pdf(team.client, db_session, team.org, team.owner.h)

    response = download(team)
    zf = archive(response)

    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == f'attachment; filename="export-umea-hast-rehab-{clock.utcnow().date().isoformat()}.zip"'
    assert response.headers["cache-control"] == "no-store"
    assert zf.testzip() is None
    names = set(zf.namelist())
    assert {"README.txt", "manifest.json", "members.jsonl", "data/customers.jsonl", "data/invoices.jsonl", "data/horses.jsonl", "data/transactions.jsonl"} <= names
    in_database = db_session.scalar(text("SELECT count(*) FROM customers WHERE organization_id = :o"), {"o": team.org.id})
    assert in_database >= 1 and len(rows(zf, "data/customers.jsonl")) == in_database  # every row of the organization, no more, no fewer
    assert len(rows(zf, "data/items.jsonl")) == 1 and rows(zf, "data/invoices.jsonl")[0]["id"] == invoice_id
    stored = db_session.scalar(text("SELECT content FROM invoice_pdfs WHERE invoice_id = :i"), {"i": invoice_id})
    pdfs = [name for name in names if name.startswith("invoices/") and name.endswith(".pdf")]
    assert len(pdfs) == 1 and zf.read(pdfs[0]) == bytes(stored) and zf.read(pdfs[0]).startswith(b"%PDF-")  # the very bytes that were frozen
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["organization_id"] == str(team.org.id) and manifest["rows"]["customers"] == in_database and manifest["rows"]["invoices/pdf"] == 1


def _issued_invoice_nobody_opened(client, db: Session, org: Organization, headers) -> str:
    """Issued, but its PDF was never downloaded, so no PDF is stored yet (a PDF is frozen on its first download)."""
    tx = make_transaction(db, org, status="completed")
    invoice = client.post("/api/invoices", json={"transaction_ids": [str(tx.id)]}, headers=headers).json()
    assert client.post(f"/api/invoices/{invoice['id']}/issue", headers={**headers, "If-Match": f'"{invoice["version"]}"'}).status_code == 200
    return invoice["id"]


def test_an_invoice_nobody_ever_opened_still_gets_its_pdf_in_the_copy(team, db_session: Session):
    unopened = _issued_invoice_nobody_opened(team.client, db_session, team.org, team.owner.h)
    assert db_session.scalar(text("SELECT count(*) FROM invoice_pdfs WHERE invoice_id = :i"), {"i": unopened}) == 0  # control

    zf = archive(download(team))

    pdfs = [name for name in zf.namelist() if name.startswith("invoices/") and name.endswith(".pdf")]
    assert len(pdfs) == 1 and zf.read(pdfs[0]).startswith(b"%PDF-")
    assert json.loads(zf.read("manifest.json"))["rows"]["invoices/without_stored_pdf"] == 0


def test_when_more_documents_need_rendering_than_one_copy_renders_the_copy_says_how_many_are_missing(team, db_session: Session, monkeypatch):
    monkeypatch.setattr(settings, "export_render_limit", 1)
    for _ in range(3):
        _issued_invoice_nobody_opened(team.client, db_session, team.org, team.owner.h)

    manifest = json.loads(archive(download(team)).read("manifest.json"))

    assert manifest["rows"]["invoices/pdf"] == 1 and manifest["rows"]["invoices/without_stored_pdf"] == 2  # honest about what is not in it


def test_members_are_named_without_any_credential(team, db_session: Session):
    zf = archive(download(team))

    members = rows(zf, "members.jsonl")

    assert sorted(m["role"] for m in members) == ["admin", "employee", "owner"]
    assert all(set(m) == {"email", "name", "role", "member_since"} for m in members)


def test_no_secret_leaves_in_any_file(team, db_session: Session):
    _fill(db_session, team.org)
    db_session.execute(
        text("INSERT INTO organization_invitations (organization_id, email, role, token_hash, expires_at, created_by) VALUES (:o, 'new@example.test', 'viewer', :h, now() + interval '7 days', :u)"),
        {"o": team.org.id, "h": "d" * 64, "u": team.owner.user.id},
    )
    _issued_invoice_with_pdf(team.client, db_session, team.org, team.owner.h)  # so that the PDF table has a row to look at
    zf = archive(download(team))

    everything = b"".join(zf.read(name) for name in zf.namelist() if name.startswith("data/") or name == "members.jsonl")  # the data, not the README

    assert b"token_hash" not in everything and b"dddddddddddddddd" not in everything  # an invitation is there, its credential hash is not
    assert len(rows(zf, "data/organization_invitations.jsonl")) == 1
    assert b"$argon2" not in everything and b"password_hash" not in everything  # no credential of anyone
    pdf_rows = rows(zf, "data/invoice_pdfs.jsonl")
    assert len(pdf_rows) == 1 and "content" not in pdf_rows[0] and pdf_rows[0]["byte_size"] > 0  # control: the row is there; its bytes are a file, not JSON


# --- whose data -----------------------------------------------------------------------------------------------------------------------


def test_a_horses_documents_are_in_the_copy_as_files_and_never_as_text_in_the_data(team, db_session: Session):
    from app.modules.equine.models import HorseDocument
    from tests.factories import make_horse

    pdf = b"%PDF-1.4 vet report only in the first organization"
    horse = make_horse(db_session, team.org, "Kalle", owner=make_customer(db_session, team.org, "Anna Andersson"))
    db_session.add(HorseDocument(organization_id=team.org.id, horse_id=horse.id, filename="Vet report.pdf", content_type="application/pdf", byte_size=len(pdf), sha256="x", content=pdf))
    other = make_org(db_session, "Another Stable")
    other_horse = make_horse(db_session, other, "Storm", owner=make_customer(db_session, other, "Bo Berg"))
    db_session.add(HorseDocument(organization_id=other.id, horse_id=other_horse.id, filename="Secret.pdf", content_type="application/pdf", byte_size=3, sha256="y", content=b"%PD"))
    db_session.flush()

    zf = archive(download(team))

    files = [name for name in zf.namelist() if name.startswith("horse-documents/")]
    assert len(files) == 1 and files[0].endswith("-Vet-report.pdf") and zf.read(files[0]) == pdf
    listed = rows(zf, "data/horse_documents.jsonl")
    assert [row["filename"] for row in listed] == ["Vet report.pdf"] and all("content" not in row for row in listed)
    assert json.loads(zf.read("manifest.json"))["rows"]["horse-documents/files"] == 1
    assert b"Secret.pdf" not in b"".join(zf.read(name) for name in zf.namelist())  # nothing of the other organization


def test_another_organizations_data_never_appears_even_with_the_same_names(team, db_session: Session):
    other = make_org(db_session, NAME)  # the very same organization name, and a customer with the very same name
    _fill(db_session, team.org)
    _fill(db_session, other)
    other_owner = _person(db_session, other, Role.OWNER, "Other Owner")
    other_invoice = _issued_invoice_with_pdf(team.client, db_session, other, other_owner.h)
    own_invoice = _issued_invoice_with_pdf(team.client, db_session, team.org, team.owner.h)
    foreign_customer = make_customer(db_session, other, name="Only In The Other Organization")

    zf = archive(download(team))

    organization_ids = {row["organization_id"] for name in zf.namelist() if name.startswith("data/") for row in rows(zf, name) if "organization_id" in row}
    assert organization_ids == {str(team.org.id)}
    text_of_all = b"".join(zf.read(name) for name in zf.namelist() if not name.endswith(".pdf")).decode("utf-8")
    assert str(foreign_customer.id) not in text_of_all and "Only In The Other Organization" not in text_of_all
    assert [row["id"] for row in rows(zf, "data/invoices.jsonl")] == [own_invoice]
    assert other_invoice not in text_of_all and "other.owner" not in text_of_all.lower()
    assert len([n for n in zf.namelist() if n.startswith("invoices/")]) == 1
    assert [m["email"] for m in rows(zf, "members.jsonl")] and all(other_owner.user.email != m["email"] for m in rows(zf, "members.jsonl"))


def test_every_tenant_table_is_exported_or_deliberately_left_out(db_session: Session):
    with_org = set(db_session.scalars(text("SELECT table_name FROM information_schema.columns WHERE column_name = 'organization_id' AND table_schema = 'public'")))

    assert with_org == set(organization_export.EXPORTED_TABLES) | set(organization_export.LEFT_OUT) | set(KEPT)


# --- who may ask, and how often -------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["admin", "employee"])
def test_only_the_owner_can_download_everything(team, who):
    response = download(team, headers=getattr(team, who).h)

    assert response.status_code == 403 and response.headers["content-type"].startswith("application/json")


def test_the_password_is_asked_for_again(team):
    response = download(team, password=OTHER_PASSWORD)

    assert response.status_code == 403 and response.json()["detail"]["code"] == "reauthentication_failed"


def test_a_few_copies_per_hour_per_organization(team, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_export_per_hour", 2)

    assert [download(team).status_code for _ in range(3)] == [200, 200, 429]


# --- "a copy was made" ----------------------------------------------------------------------------------------------------------------------


def last_export(db: Session, org: Organization):
    db.expire_all()
    return db.scalar(select(Organization.last_export_at).where(Organization.id == org.id))


def test_a_finished_download_marks_the_organization_and_an_abandoned_one_does_not(team, db_session: Session):
    abandoned = organization_export.stream_export(organization_id=team.org.id, organization_name=NAME, now=clock.utcnow())
    next(abandoned)
    abandoned.close()  # the browser went away part-way
    assert last_export(db_session, team.org) is None

    assert download(team).status_code == 200

    assert last_export(db_session, team.org) is not None


# --- deleting needs a copy, or an explicit "no copy" --------------------------------------------------------------------------------------------


def delete(team, **extra):
    return team.client.post(DELETE, json={"confirm_name": NAME, "password": PASSWORD, **extra}, headers=team.owner.h)


def test_deleting_without_a_copy_or_a_statement_that_none_is_wanted_is_refused(team, db_session: Session):
    refused = delete(team)

    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "export_required"
    assert db_session.get(Organization, team.org.id) is not None  # nothing was deleted


def test_a_copy_from_the_last_day_lets_the_deletion_through(team, db_session: Session):
    assert download(team).status_code == 200

    assert delete(team).status_code == 204
    db_session.expire_all()
    assert db_session.get(Organization, team.org.id) is None


def test_a_copy_older_than_a_day_does_not(team, db_session: Session):
    assert download(team).status_code == 200
    db_session.execute(text("UPDATE organizations SET last_export_at = :t WHERE id = :o"), {"t": clock.utcnow() - timedelta(hours=25), "o": team.org.id})

    assert delete(team).status_code == 409


def test_saying_no_copy_is_wanted_works_without_one(team, db_session: Session):
    assert delete(team, without_copy=True).status_code == 204


def test_the_copy_requirement_is_checked_after_the_password_not_before(team):
    wrong = team.client.post(DELETE, json={"confirm_name": NAME, "password": OTHER_PASSWORD}, headers=team.owner.h)

    assert wrong.status_code == 403 and wrong.json()["detail"]["code"] == "reauthentication_failed"  # no hint about the copy rule to a stranger


def test_the_archive_name_is_plain_ascii():
    now = clock.utcnow()
    assert organization_export.archive_name("Umeå Häst & Rehab", now).startswith("export-umea-hast-rehab-")
    assert organization_export.archive_name("日本語", now).startswith("export-organization-")
    assert len(organization_export.archive_name("x" * 200, now)) < 70
