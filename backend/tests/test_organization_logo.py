"""The organization logo: owner-only upload with real image validation, tenant isolation, and printing on the PDFs.

An issued invoice keeps the logo version it was issued with. Logos in these tests are solid colours, so the picture that
was printed can be told from the PDF's own image.
"""

import base64
import io
import uuid

import pytest
from PIL import Image
from sqlalchemy import select

from app.core import logo_image
from app.models import AuditEvent, OrganizationLogo, Role
from app.modules.invoicing.models import InvoicePdf
from app.modules.invoicing.pdf import render
from app.modules.invoicing.pdf.document import source_sha256
from tests.invoicing_support import Two, completed, draft_invoice, issue, member_of
from tests.pdf_support import document, reader

LOGO = "/api/organization/logo"
RED, BLUE = (200, 0, 0), (0, 0, 200)


def picture(color=RED, size=(300, 100), fmt="PNG", mode="RGB") -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, color if mode == "RGB" else (*color, 128)).save(out, format=fmt)
    return out.getvalue()


def b64(raw: bytes) -> dict:
    return {"data_base64": base64.b64encode(raw).decode()}


def printed_color(pdf: bytes) -> tuple[int, int, int]:
    (image,) = reader(pdf).pages[0].images
    return image.image.convert("RGB").getpixel((1, 1))


def pdf_of(client, headers, invoice) -> bytes:
    response = client.get(f"/api/invoices/{invoice['id']}/pdf", headers=headers)
    assert response.status_code == 200
    return response.content


# --- upload, read, replace, remove ---------------------------------------------------------------------------------------


def test_an_owner_uploads_replaces_and_removes_the_logo(client, sales):
    assert client.get(LOGO, headers=sales.headers).status_code == 404
    assert client.get("/api/organization", headers=sales.headers).json()["has_logo"] is False

    uploaded = client.post(LOGO, json=b64(picture(RED)), headers=sales.headers)
    assert uploaded.status_code == 200 and uploaded.json()["has_logo"] is True
    read = client.get(LOGO, headers=sales.headers).json()
    assert (read["width"], read["height"]) == (300, 100)
    assert Image.open(io.BytesIO(base64.b64decode(read["data_base64"]))).format == "PNG"

    client.post(LOGO, json=b64(picture(BLUE, (50, 50))), headers=sales.headers)
    assert client.get(LOGO, headers=sales.headers).json()["width"] == 50

    assert client.delete(LOGO, headers=sales.headers).status_code == 204
    assert client.get(LOGO, headers=sales.headers).status_code == 404
    assert client.get("/api/organization", headers=sales.headers).json()["has_logo"] is False


def test_replacing_and_removing_are_audited_and_keep_every_version(client, db_session, sales):
    client.post(LOGO, json=b64(picture(RED)), headers=sales.headers)
    client.post(LOGO, json=b64(picture(BLUE)), headers=sales.headers)
    client.delete(LOGO, headers=sales.headers)

    assert len(list(db_session.scalars(select(OrganizationLogo).where(OrganizationLogo.organization_id == sales.org.id)))) == 2
    actions = list(db_session.scalars(select(AuditEvent.action).where(AuditEvent.organization_id == sales.org.id, AuditEvent.entity_type == "organization")))
    assert actions.count("logo_changed") == 2 and actions.count("logo_removed") == 1


@pytest.mark.parametrize("role", [Role.ADMIN, Role.ACCOUNTANT, Role.EMPLOYEE, Role.VIEWER])
def test_only_the_owner_changes_the_logo_but_every_member_reads_it(client, db_session, sales, role):
    client.post(LOGO, json=b64(picture()), headers=sales.headers)
    headers = member_of(db_session, sales.org, role)

    assert client.post(LOGO, json=b64(picture(BLUE)), headers=headers).status_code == 403
    assert client.delete(LOGO, headers=headers).status_code == 403
    assert client.get(LOGO, headers=headers).status_code == 200
    assert client.get(LOGO, headers=sales.headers).json()["width"] == 300  # unchanged


# --- validation ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not an image at all",
        b"<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'/>",
        picture(fmt="GIF"),
        picture(fmt="BMP"),
        picture()[:60],  # cut off
        picture(size=(4001, 10)),
    ],
    ids=["empty", "text", "svg", "gif", "bmp", "truncated", "too-wide"],
)
def test_anything_but_a_readable_png_or_jpeg_within_the_limits_is_refused(client, sales, raw):
    response = client.post(LOGO, json=b64(raw), headers=sales.headers)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "invalid_logo"
    assert client.get(LOGO, headers=sales.headers).status_code == 404


def test_a_file_over_the_byte_limit_is_refused(client, sales):
    noise = Image.frombytes("RGB", (900, 900), bytes(range(256)) * 2430 + bytes(900 * 900 * 3 - 256 * 2430))
    out = io.BytesIO()
    noise.save(out, format="BMP")  # large, but also not an accepted format: the size check comes first
    assert len(out.getvalue()) > logo_image.MAX_UPLOAD_BYTES
    assert client.post(LOGO, json=b64(out.getvalue()), headers=sales.headers).status_code == 422


def test_invalid_base64_is_refused(client, sales):
    assert client.post(LOGO, json={"data_base64": "***"}, headers=sales.headers).status_code == 422


def test_a_jpeg_is_stored_as_a_png_without_its_metadata_and_a_big_picture_is_scaled_down():
    source = Image.new("RGB", (2400, 1200), RED)
    exif = Image.Exif()
    exif[0x010E] = "secret description"
    out = io.BytesIO()
    source.save(out, format="JPEG", exif=exif.tobytes())
    assert b"secret description" in out.getvalue()

    stored = logo_image.normalize(out.getvalue())
    assert (stored.width, stored.height) == (1000, 500)
    assert stored.content.startswith(b"\x89PNG") and b"secret description" not in stored.content
    assert not Image.open(io.BytesIO(stored.content)).getexif()


def test_transparency_is_kept():
    stored = logo_image.normalize(picture(mode="RGBA", size=(40, 40)))
    assert Image.open(io.BytesIO(stored.content)).mode == "RGBA"


# --- tenant isolation ----------------------------------------------------------------------------------------------------


def test_one_organization_never_sees_anothers_logo(client, db_session):
    two = Two(db_session)
    client.post(LOGO, json=b64(picture(RED)), headers=two.a)

    assert client.get(LOGO, headers=two.a).status_code == 200
    assert client.get(LOGO, headers=two.b).status_code == 404
    assert client.get("/api/organization", headers=two.b).json()["has_logo"] is False
    assert client.delete(LOGO, headers=two.b).status_code == 204  # removing "its" nothing leaves A untouched
    assert client.get(LOGO, headers=two.a).status_code == 200


def test_a_snapshot_pointing_at_another_organizations_logo_prints_none(client, db_session):
    from app.modules.invoicing import snapshots

    two = Two(db_session)
    client.post(LOGO, json=b64(picture(RED)), headers=two.a)
    row = db_session.scalar(select(OrganizationLogo).where(OrganizationLogo.organization_id == two.a_org.id))
    snapshot = {"logo_id": str(row.id), "logo_sha256": row.sha256}

    assert snapshots.logo_bytes(db_session, two.a_org.id, snapshot) == row.content
    assert snapshots.logo_bytes(db_session, two.b_org.id, snapshot) is None


def test_a_logo_whose_content_no_longer_matches_its_recorded_hash_prints_none(client, db_session, sales):
    from app.modules.invoicing import snapshots

    client.post(LOGO, json=b64(picture(RED)), headers=sales.headers)
    row = db_session.scalar(select(OrganizationLogo).where(OrganizationLogo.organization_id == sales.org.id))
    assert snapshots.logo_bytes(db_session, sales.org.id, {"logo_id": str(row.id), "logo_sha256": "0" * 64}) is None


# --- on the PDF ----------------------------------------------------------------------------------------------------------


def test_an_invoice_issued_with_a_logo_prints_that_logo_even_after_it_is_replaced_or_removed(client, db_session, sales):
    client.post(LOGO, json=b64(picture(RED)), headers=sales.headers)
    issued = issue(client, sales.headers, draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing)))
    assert issued["issuer_snapshot"]["logo_id"] and issued["issuer_snapshot"]["logo_sha256"]

    client.post(LOGO, json=b64(picture(BLUE)), headers=sales.headers)  # replaced before the FIRST download
    assert printed_color(pdf_of(client, sales.headers, issued)) == RED

    client.delete(LOGO, headers=sales.headers)
    assert printed_color(pdf_of(client, sales.headers, issued)) == RED


def test_an_invoice_issued_without_a_logo_stays_without_one(client, db_session, sales):
    issued = issue(client, sales.headers, draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing)))
    assert issued["issuer_snapshot"]["logo_id"] is None

    client.post(LOGO, json=b64(picture(RED)), headers=sales.headers)
    assert len(reader(pdf_of(client, sales.headers, issued)).pages[0].images) == 0


def test_a_later_template_version_still_prints_the_recorded_logo(client, db_session, sales, monkeypatch):
    client.post(LOGO, json=b64(picture(RED)), headers=sales.headers)
    issued = issue(client, sales.headers, draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing)))
    pdf_of(client, sales.headers, issued)
    client.post(LOGO, json=b64(picture(BLUE)), headers=sales.headers)

    monkeypatch.setattr(render, "TEMPLATE_VERSION", render.TEMPLATE_VERSION + 1)  # the next download renders a NEW artifact
    assert printed_color(pdf_of(client, sales.headers, issued)) == RED
    rows = db_session.scalars(select(InvoicePdf).where(InvoicePdf.invoice_id == uuid.UUID(issued["id"]))).all()
    assert len(rows) == 2


# --- layout --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("size", [(2000, 100), (100, 2000), (40, 40)], ids=["wide", "tall", "tiny"])
def test_any_proportions_fit_the_header_box_without_changing_shape(size):
    png = logo_image.normalize(picture(size=size, mode="RGB")).content
    drawn = render._logo_flowable(png)
    assert drawn.drawWidth <= render.LOGO_MAX_WIDTH + 0.01 and drawn.drawHeight <= render.LOGO_MAX_HEIGHT + 0.01
    assert drawn.drawWidth / drawn.drawHeight == pytest.approx(size[0] / size[1], rel=0.01)
    pdf = render.render_pdf(document().model_copy(update={"logo": png}))
    assert len(reader(pdf).pages[0].images) == 1


def test_the_logo_is_in_the_hash_by_its_checksum_and_the_text_is_still_printed():
    import hashlib

    png = logo_image.normalize(picture(size=(200, 80))).content
    plain = document()
    with_logo = plain.model_copy(update={"logo": png, "logo_sha256": hashlib.sha256(png).hexdigest()})
    assert source_sha256(with_logo) != source_sha256(plain)
    assert "Fredrik Horse Therapy AB" in "".join(page.extract_text() for page in reader(render.render_pdf(with_logo)).pages)


# --- the changes are COMMITTED, not only visible to the request's own transaction ----------------------------------------------
# The other tests share one rollback-only transaction, so a handler that forgot to commit still passed them (and the screen said
# "Logo saved" while nothing was kept). These use the real application with a real database, and look from a second session.


@pytest.fixture
def committed(dev_auth):
    from tests.invoicing_support import build_committed_world, purge_organization

    world = build_committed_world("Logo")
    yield world
    purge_organization(world.org_id, [world.user_id])


def test_an_uploaded_logo_is_kept_after_the_request_ends(committed):
    from fastapi.testclient import TestClient

    from app.core.db import SessionLocal
    from app.main import app
    from app.models import Organization

    sent = TestClient(app).post(LOGO, json={"data_base64": base64.b64encode(picture()).decode()}, headers=committed.headers)
    assert sent.status_code == 200, sent.text

    with SessionLocal() as other_session:  # a fresh transaction: only committed data is visible here
        organization = other_session.get(Organization, committed.org_id)
        assert organization.logo_id is not None
        stored = other_session.scalars(select(OrganizationLogo).where(OrganizationLogo.organization_id == committed.org_id)).all()
        assert [row.id for row in stored] == [organization.logo_id]
        events = other_session.scalars(select(AuditEvent).where(AuditEvent.organization_id == committed.org_id, AuditEvent.action == "logo_changed")).all()
        assert len(events) == 1


def test_removing_a_logo_is_kept_too_and_the_version_stays(committed):
    from fastapi.testclient import TestClient

    from app.core.db import SessionLocal
    from app.main import app
    from app.models import Organization

    http = TestClient(app)
    http.post(LOGO, json={"data_base64": base64.b64encode(picture()).decode()}, headers=committed.headers)
    assert http.delete(LOGO, headers=committed.headers).status_code == 204

    with SessionLocal() as other_session:
        assert other_session.get(Organization, committed.org_id).logo_id is None
        assert len(other_session.scalars(select(OrganizationLogo).where(OrganizationLogo.organization_id == committed.org_id)).all()) == 1
