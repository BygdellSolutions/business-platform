"""Documents on a horse: PDFs and pictures kept with it.

Any member reads and opens them; record writers upload and delete; every step is in the horse's history (name and size, never the
content); another organization's horse or document is a 404 (also by reference); the file must be what its type says; and a
listing never loads the bytes.
"""

import base64
import hashlib
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.models import Role
from app.modules.equine.documents import MAX_BASE64_CHARS, clean_filename
from app.modules.equine.models import DOCUMENT_MAX_BYTES, DOCUMENT_MAX_PER_HORSE, HorseDocument
from tests.factories import add_member, make_customer, make_horse, make_org, make_user

PDF = b"%PDF-1.4\n% a tiny test document\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 40


def _world(db: Session, role: Role = Role.OWNER):
    org = make_org(db)
    user = make_user(db, name="Tina Therapist")
    add_member(db, org, user, role)
    return org, {"X-Dev-User-Email": user.email}


def _horse(db: Session, org):
    return make_horse(db, org, "Kalle", owner=make_customer(db, org, "Anna Andersson"))


def _body(content: bytes = PDF, *, name: str = "Vet report.pdf", kind: str = "application/pdf") -> dict:
    return {"filename": name, "content_type": kind, "data_base64": base64.b64encode(content).decode("ascii")}


def _upload(client: TestClient, headers, horse, **kwargs):
    return client.post(f"/api/horses/{horse.id}/documents", json=_body(**kwargs), headers=headers)


def test_a_document_is_uploaded_listed_opened_and_deleted(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    horse = _horse(db_session, org)

    made = _upload(client, owner, horse)
    assert made.status_code == 201, made.text
    document = made.json()
    assert document["filename"] == "Vet report.pdf" and document["byte_size"] == len(PDF) and document["created_by_name"] == "Tina Therapist"
    assert "content" not in document and "data_base64" not in document

    listed = client.get(f"/api/horses/{horse.id}/documents", headers=owner).json()
    assert [d["id"] for d in listed] == [document["id"]]

    opened = client.get(f"/api/horses/{horse.id}/documents/{document['id']}/file", headers=owner)
    assert opened.status_code == 200 and opened.content == PDF
    assert opened.headers["content-type"] == "application/pdf"
    assert opened.headers["content-disposition"].startswith("inline;")
    assert opened.headers["x-content-type-options"] == "nosniff"
    assert opened.headers["etag"] == f'"{hashlib.sha256(PDF).hexdigest()}"'

    assert client.delete(f"/api/horses/{horse.id}/documents/{document['id']}", headers=owner).status_code == 204
    assert client.get(f"/api/horses/{horse.id}/documents", headers=owner).json() == []
    assert client.get(f"/api/horses/{horse.id}/documents/{document['id']}/file", headers=owner).status_code == 404


@pytest.mark.parametrize(("content", "kind"), [(PDF, "application/pdf"), (PNG, "image/png"), (JPEG, "image/jpeg"), (WEBP, "image/webp")])
def test_the_four_types_are_accepted_and_pictures_are_sandboxed(client: TestClient, db_session: Session, content, kind):
    org, owner = _world(db_session)
    horse = _horse(db_session, org)
    document = _upload(client, owner, horse, content=content, kind=kind, name="file").json()

    opened = client.get(f"/api/horses/{horse.id}/documents/{document['id']}/file", headers=owner)
    assert opened.content == content and opened.headers["content-type"] == kind
    assert ("content-security-policy" in opened.headers) == (kind != "application/pdf")


@pytest.mark.parametrize(
    ("content", "kind", "code"),
    [
        (b"<html><script>alert(1)</script></html>", "text/html", "unsupported_document_type"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml", "unsupported_document_type"),
        (b"MZ\x90\x00", "application/pdf", "document_type_mismatch"),  # an executable that says it is a PDF
        (PDF, "image/png", "document_type_mismatch"),
        (b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 20, "image/webp", "document_type_mismatch"),
    ],
)
def test_a_file_that_is_not_what_it_says_is_refused(client: TestClient, db_session: Session, content, kind, code):
    org, owner = _world(db_session)
    horse = _horse(db_session, org)
    refused = _upload(client, owner, horse, content=content, kind=kind)
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == code
    assert client.get(f"/api/horses/{horse.id}/documents", headers=owner).json() == []


def test_an_empty_file_is_refused(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    horse = _horse(db_session, org)
    assert _upload(client, owner, horse, content=b"").status_code == 422


def test_a_file_over_ten_megabytes_and_bad_base64_are_refused(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    horse = _horse(db_session, org)
    one_over = _upload(client, owner, horse, content=b"%PDF-" + b"0" * (DOCUMENT_MAX_BYTES - 4))
    assert one_over.status_code == 422 and one_over.json()["detail"]["code"] == "document_too_large"
    far_over = _upload(client, owner, horse, content=b"%PDF-" + b"0" * (DOCUMENT_MAX_BYTES + 100))
    assert far_over.status_code == 422  # stopped by the schema's length limit before it is decoded
    exactly = _upload(client, owner, horse, content=b"%PDF-" + b"0" * (DOCUMENT_MAX_BYTES - 5))
    assert exactly.status_code == 201  # the limit itself is allowed
    bad = client.post(f"/api/horses/{horse.id}/documents", json={**_body(), "data_base64": "not base64 !!"}, headers=owner)
    assert bad.status_code == 422
    huge = client.post(f"/api/horses/{horse.id}/documents", json={**_body(), "data_base64": "A" * (MAX_BASE64_CHARS + 4)}, headers=owner)
    assert huge.status_code == 422  # refused by the schema before anything is decoded


def test_the_name_is_cleaned_and_unknown_fields_are_refused(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    horse = _horse(db_session, org)
    assert _upload(client, owner, horse, name="..\\..\\etc/passwd\x00.pdf").json()["filename"] == "passwd.pdf"
    assert clean_filename("C:\\Users\\x\\Röntgen 1.pdf") == "Röntgen 1.pdf"
    assert clean_filename("  ..  ") == "document"
    assert len(clean_filename("a" * 300 + ".pdf")) == 200
    extra = client.post(f"/api/horses/{horse.id}/documents", json={**_body(), "organization_id": "x"}, headers=owner)
    assert extra.status_code == 422


def test_a_non_ascii_name_is_offered_safely(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    horse = _horse(db_session, org)
    document = _upload(client, owner, horse, name='Röntgen "vänster".pdf').json()
    header = client.get(f"/api/horses/{horse.id}/documents/{document['id']}/file", headers=owner).headers["content-disposition"]
    assert 'filename="Rontgen _vanster_.pdf"' in header  # the plain fallback: accents folded, quotes replaced
    assert "filename*=UTF-8''R%C3%B6ntgen%20%22v%C3%A4nster%22.pdf" in header
    assert "\n" not in header and "\r" not in header


def test_the_history_names_the_file_but_never_holds_its_content(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    horse = _horse(db_session, org)
    document = _upload(client, owner, horse).json()
    client.delete(f"/api/horses/{horse.id}/documents/{document['id']}", headers=owner)

    events = client.get("/api/history", params={"entity_type": "horse", "entity_id": str(horse.id)}, headers=owner).json()["events"]
    mine = [e for e in events if e["entity_type"] == "horse_document"]
    assert {e["action"] for e in mine} == {"created", "deleted"}
    assert any(e["changes"].get("filename", {}).get("to") == "Vet report.pdf" for e in mine)
    assert "JVBER" not in str(events) and "%PDF" not in str(events)  # neither base64 nor raw content


def test_a_viewer_reads_and_opens_but_cannot_upload_or_delete(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    viewer = make_user(db_session)
    add_member(db_session, org, viewer, Role.VIEWER)
    horse = _horse(db_session, org)
    document = _upload(client, owner, horse).json()

    headers = {"X-Dev-User-Email": viewer.email}
    assert len(client.get(f"/api/horses/{horse.id}/documents", headers=headers).json()) == 1
    assert client.get(f"/api/horses/{horse.id}/documents/{document['id']}/file", headers=headers).status_code == 200
    assert _upload(client, headers, horse).status_code == 403
    assert client.delete(f"/api/horses/{horse.id}/documents/{document['id']}", headers=headers).status_code == 403
    assert len(client.get(f"/api/horses/{horse.id}/documents", headers=owner).json()) == 1


@pytest.mark.parametrize("role", [Role.ADMIN, Role.ACCOUNTANT, Role.EMPLOYEE])
def test_whoever_may_edit_records_uploads_and_deletes(client: TestClient, db_session: Session, role):
    org, _ = _world(db_session)
    member = make_user(db_session)
    add_member(db_session, org, member, role)
    headers = {"X-Dev-User-Email": member.email}  # the same people who may add and change a horse's notes
    horse = _horse(db_session, org)
    document = _upload(client, headers, horse)
    assert document.status_code == 201
    assert client.delete(f"/api/horses/{horse.id}/documents/{document.json()['id']}", headers=headers).status_code == 204


def test_another_organizations_horse_and_documents_are_not_found(client: TestClient, db_session: Session):
    org_a, owner_a = _world(db_session)
    org_b, owner_b = _world(db_session)
    horse_a, horse_b = _horse(db_session, org_a), _horse(db_session, org_b)
    mine = _upload(client, owner_a, horse_a).json()
    theirs = _upload(client, owner_b, horse_b).json()

    # B cannot list, upload to, open or delete A's horse or document
    assert client.get(f"/api/horses/{horse_a.id}/documents", headers=owner_b).status_code == 404
    assert _upload(client, owner_b, horse_a).status_code == 404
    assert client.get(f"/api/horses/{horse_a.id}/documents/{mine['id']}/file", headers=owner_b).status_code == 404
    assert client.delete(f"/api/horses/{horse_a.id}/documents/{mine['id']}", headers=owner_b).status_code == 404
    # a valid id of B's own document under A's horse (or the reverse) is not found either
    assert client.get(f"/api/horses/{horse_b.id}/documents/{mine['id']}/file", headers=owner_b).status_code == 404
    assert client.get(f"/api/horses/{horse_a.id}/documents/{theirs['id']}/file", headers=owner_a).status_code == 404
    assert client.delete(f"/api/horses/{horse_a.id}/documents/{theirs['id']}", headers=owner_a).status_code == 404
    # and nothing of A's changed
    assert len(client.get(f"/api/horses/{horse_a.id}/documents", headers=owner_a).json()) == 1


def test_the_database_refuses_a_document_for_another_organizations_horse(db_session: Session):
    org_a, _ = _world(db_session)
    org_b, _ = _world(db_session)
    horse_b = _horse(db_session, org_b)
    with pytest.raises(Exception, match="fk_horse_documents_horse"):
        db_session.execute(
            text(
                "INSERT INTO horse_documents (organization_id, horse_id, filename, content_type, byte_size, sha256, content) "
                "VALUES (:org, :horse, 'x.pdf', 'application/pdf', 1, 'x', '\\x25')"
            ),
            {"org": org_a.id, "horse": horse_b.id},
        )


def test_the_database_checks_size_type_and_name(db_session: Session):
    org, _ = _world(db_session)
    horse = _horse(db_session, org)
    insert = text(
        "INSERT INTO horse_documents (organization_id, horse_id, filename, content_type, byte_size, sha256, content) "
        "VALUES (:org, :horse, :name, :kind, :size, 'x', '\\x2525')"
    )
    base = {"org": org.id, "horse": horse.id, "name": "x.pdf", "kind": "application/pdf", "size": 2}
    for override, constraint in (
        ({"size": 3}, "ck_horse_documents_size_matches_content"),
        ({"kind": "text/html"}, "ck_horse_documents_content_type"),
        ({"name": "   "}, "ck_horse_documents_filename_not_blank"),
    ):
        with pytest.raises(Exception, match=constraint):
            with db_session.begin_nested():
                db_session.execute(insert, {**base, **override})


def test_listing_does_not_load_the_bytes(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    horse = _horse(db_session, org)
    _upload(client, owner, horse)
    statements: list[str] = []

    def record(conn, cursor, statement, *rest):
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert len(client.get(f"/api/horses/{horse.id}/documents", headers=owner).json()) == 1
    finally:
        event.remove(engine, "before_cursor_execute", record)
    selects = [s for s in statements if "FROM horse_documents" in s and s.lstrip().upper().startswith("SELECT") and "count(" not in s.lower()]
    assert selects, "the control: the listing must be seen reading horse_documents"
    assert not any(re.search(r"horse_documents\.content(?!_)", s) for s in selects), selects  # content_type is fine, content is not


def test_a_horse_has_at_most_one_hundred_documents(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    horse = _horse(db_session, org)
    for _ in range(DOCUMENT_MAX_PER_HORSE):
        db_session.add(
            HorseDocument(
                organization_id=org.id, horse_id=horse.id, filename="f.pdf", content_type="application/pdf", byte_size=len(PDF), sha256="x", content=PDF
            )
        )
    db_session.flush()
    refused = _upload(client, owner, horse)
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "too_many_documents"
    assert db_session.scalar(select(HorseDocument.id).where(HorseDocument.organization_id == org.id).limit(1)) is not None
