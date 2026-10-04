"""GET /api/invoices/{id}/pdf: access, headers, the frozen artifact, refusals and the stored row's protection."""

import hashlib
import re
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.models import CustomerType, Role
from app.modules.custom_fields.models import CustomFieldOption
from app.modules.invoicing.models import InvoicePdf
from app.modules.invoicing.pdf import document as document_module
from app.modules.invoicing.pdf import render
from app.modules.invoicing.pdf.build import document_from_invoice
from app.modules.invoicing.pdf.document import source_sha256
from app.modules.invoicing.pdf.format import NBSP, money
from app.modules.sales.models import TransactionLine
from tests.factories import make_customer, make_definition, make_item, make_value
from tests.invoicing_support import INVOICES, Statements, Two, completed, draft_invoice, issue, member_of
from tests.pdf_support import pdf_text, running_text, squeezed


def pdf_url(invoice) -> str:
    return f"{INVOICES}/{invoice['id'] if isinstance(invoice, dict) else invoice}/pdf"


def stored(db, invoice) -> list[InvoicePdf]:
    return list(db.scalars(select(InvoicePdf).where(InvoicePdf.invoice_id == uuid.UUID(invoice["id"]))))


@pytest.fixture
def world(client, db_session, sales):
    """An issued invoice built from rich live data, with custom fields at both snapshot levels."""
    org = sales.org
    org.legal_name, org.address_line1, org.postal_code, org.city, org.vat_number = "Solo AB", "Storgatan 1", "903 26", "Umeå", "SE556000000101"
    customer = make_customer(db_session, org, "Umeå HK", CustomerType.COMPANY, "hk@example.test", "070-1", city="Umeå", country_code="SE", vat_number="SE1")
    item = make_item(db_session, org, "Horse massage")
    owner = make_customer(db_session, org, "Anna Andersson")
    tx = completed(db_session, org, customer, lines=[{"item": item, "description": "Horse massage"}, {"description": "Travel", "unit_price_ex_vat": "2.50"}])
    line = db_session.scalar(select(TransactionLine).where(TransactionLine.transaction_id == tx.id, TransactionLine.position == 1))
    reference = make_definition(db_session, org, key="owner", label="Owner", field_type="reference", reference_source="customer", show_on_invoice=True)
    kind = make_definition(db_session, org, key="kind", label="Kind", field_type="select", show_on_invoice=True, options=["Therapy", "Check"])
    header = make_definition(db_session, org, entity_type="transaction", key="po", label="PO", field_type="text", show_on_invoice=True)
    option = db_session.scalar(select(CustomFieldOption).where(CustomFieldOption.definition_id == kind.id, CustomFieldOption.label == "Therapy"))
    make_value(db_session, org, reference, line.id, value_reference_id=owner.id)
    make_value(db_session, org, kind, line.id, value_option_id=option.id)
    make_value(db_session, org, header, tx.id, value_text="PO-17")
    document = issue(client, sales.headers, draft_invoice(client, sales.headers, tx))
    return type("W", (), dict(org=org, customer=customer, item=item, owner=owner, tx=tx, line=line, document=document, headers=sales.headers, db=db_session, sales=sales))


# --- the response ---------------------------------------------------------------------------------------------------------------------


def test_an_issued_invoice_downloads_as_a_pdf_attachment_with_the_required_headers(client, world):
    response = client.get(pdf_url(world.document), headers=world.headers)
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
    headers = response.headers
    assert headers["content-type"] == "application/pdf"
    assert headers["content-disposition"] == f'attachment; filename="invoice-{world.document["number_text"]}.pdf"'
    assert headers["content-length"] == str(len(response.content))
    assert headers["etag"] == f'"{hashlib.sha256(response.content).hexdigest()}"'
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["cache-control"] == "private, no-store"


def test_the_pdf_prints_the_stored_invoice(client, world):
    text_ = pdf_text(client.get(pdf_url(world.document), headers=world.headers).content)
    for expected in ("Solo AB", "Umeå HK", "Horse massage", "Travel", "PO: PO-17", "Owner: Anna Andersson", "Kind: Therapy", "Invoice " + world.document["number_text"]):
        assert expected in text_, expected
    assert world.document["gross_amount"].replace(".", ".") in text_.replace("\u00a0", "").replace(" ", "")


def test_the_first_download_stores_one_artifact_with_its_provenance(client, world):
    assert stored(world.db, world.document) == []
    body = client.get(pdf_url(world.document), headers=world.headers).content
    (row,) = stored(world.db, world.document)
    assert row.content == body and row.byte_size == len(body) and row.sha256 == hashlib.sha256(body).hexdigest()
    assert row.organization_id == world.org.id and str(row.invoice_id) == world.document["id"]
    assert row.renderer == render.renderer_identity() and row.template_version == render.TEMPLATE_VERSION
    read = client.get(f"{INVOICES}/{world.document['id']}", headers=world.headers).json()
    assert row.source_sha256 == source_sha256(document_from_invoice(read))


def test_later_downloads_return_exactly_the_stored_bytes_and_never_render_again(client, world, monkeypatch):
    first = client.get(pdf_url(world.document), headers=world.headers)

    def forbidden(*args, **kwargs):
        raise AssertionError("an existing artifact must never be rendered again")

    monkeypatch.setattr(render, "render_pdf", forbidden)
    for _ in range(3):
        again = client.get(pdf_url(world.document), headers=world.headers)
        assert again.status_code == 200 and again.content == first.content and again.headers["etag"] == first.headers["etag"]
    assert len(stored(world.db, world.document)) == 1


def test_a_new_template_or_renderer_never_changes_an_artifact_that_exists(client, world, monkeypatch):
    first = client.get(pdf_url(world.document), headers=world.headers).content
    monkeypatch.setattr(render, "TEMPLATE_VERSION", 2)
    monkeypatch.setattr(render, "renderer_identity", lambda: "reportlab 99; other fonts")
    assert client.get(pdf_url(world.document), headers=world.headers).content == first
    (row,) = stored(world.db, world.document)
    assert row.template_version == 1 and row.renderer.startswith("reportlab 5")  # provenance of what was served


def test_the_download_reads_no_live_table_neither_the_first_time_nor_later(client, world):
    with Statements() as first:
        assert client.get(pdf_url(world.document), headers=world.headers).status_code == 200
    with Statements() as later:
        assert client.get(pdf_url(world.document), headers=world.headers).status_code == 200
    assert first.touching_live_tables() == [] and later.touching_live_tables() == []
    assert "invoice_pdfs" in " ".join(first.sql).lower()  # and the recorder is not blind


def test_a_download_leaves_no_transaction_open_while_rendering(client, world, monkeypatch):
    """The session has committed its read before ReportLab runs: no lock and no transaction span the render."""
    real = render.render_pdf
    seen = {}

    def spy(document):
        seen["in_transaction"] = world.db.in_transaction()
        return real(document)

    monkeypatch.setattr(render, "render_pdf", spy)
    assert client.get(pdf_url(world.document), headers=world.headers).status_code == 200
    assert seen["in_transaction"] is False


# --- history: the PDF is of the issued document ------------------------------------------------------------------------------------


def test_the_first_download_after_every_live_record_changed_still_prints_the_issued_document(client, world):
    w = world
    w.customer.name, w.customer.city = "Renamed Club", "Luleå"
    w.org.name, w.org.legal_name, w.org.address_line1 = "Renamed Org", "Other AB", "Elsewhere 9"
    w.item.name, w.item.price_ex_vat = "Renamed item", 1
    w.owner.name = "Renamed Owner"
    w.db.flush()
    w.db.execute(text("set local session_replication_role = replica"))
    w.db.execute(text("update custom_field_values set value_text = 'changed' where organization_id = :o and value_text is not null"), {"o": w.org.id})
    w.db.execute(text("update custom_field_definitions set label = 'Renamed', enabled = false where organization_id = :o"), {"o": w.org.id})
    w.db.execute(text("update transaction_lines set description = 'Tampered' where organization_id = :o"), {"o": w.org.id})
    w.db.execute(text("delete from items where organization_id = :o"), {"o": w.org.id})

    body = pdf_text(client.get(pdf_url(w.document), headers=w.headers).content)
    for expected in ("Solo AB", "Umeå HK", "Horse massage", "PO: PO-17", "Owner: Anna Andersson"):
        assert expected in body, expected
    for changed in ("Renamed", "Other AB", "Elsewhere", "Tampered", "changed", "Luleå"):
        assert changed not in body, changed


def drop_checks(db, *tables: str) -> None:
    for table in tables:
        for (name,) in db.execute(text("select conname from pg_constraint where conrelid = cast(:t as regclass) and contype = 'c'"), {"t": table}):
            db.execute(text(f'alter table {table} drop constraint "{name}"'))


def test_the_pdf_prints_the_stored_figures_not_the_ones_it_could_calculate(client, world):
    """Stored figures edited to disagree with each other (as raw SQL, with the immutability triggers off): the PDF
    shows the stored ones, because it calculates nothing."""
    w = world
    w.db.execute(text("set local session_replication_role = replica"))
    drop_checks(w.db, "invoice_lines", "invoices")  # the table checks would refuse figures that disagree; the transaction is rolled back
    w.db.execute(text("update invoice_lines set net_amount = 1.11, vat_amount = 2.22, gross_amount = 3.33 where invoice_id = :i and position = 1"), {"i": w.document["id"]})
    w.db.execute(text("update invoices set net_amount = 4.44, vat_amount = 5.55, gross_amount = 6.66 where id = :i"), {"i": w.document["id"]})
    body = pdf_text(client.get(pdf_url(w.document), headers=w.headers).content)
    for stored_figure in ("1.11", "2.22", "3.33", "4.44", "5.55", "6.66"):
        assert stored_figure in body, stored_figure
    original_total = money(world.document["gross_amount"]).replace(NBSP, " ")
    assert original_total not in body  # the invoice total as originally stored is gone: the edited stored total is what is printed


# --- access ----------------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(Role))
def test_every_member_who_can_read_the_invoice_can_download_it(client, db_session, world, role):
    headers = member_of(db_session, world.org, role)
    response = client.get(pdf_url(world.document), headers=headers)
    assert response.status_code == 200 and response.content.startswith(b"%PDF-"), role


def test_a_reader_may_trigger_the_first_generation(client, db_session, world):
    viewer = member_of(db_session, world.org, Role.VIEWER)
    assert stored(db_session, world.document) == []
    assert client.get(pdf_url(world.document), headers=viewer).status_code == 200
    assert len(stored(db_session, world.document)) == 1


def test_a_request_without_identity_is_refused(raw_client, world):
    response = raw_client.get(pdf_url(world.document))
    assert response.status_code in (401, 403)


def test_a_foreign_invoice_and_a_random_id_are_the_same_404_and_nothing_is_stored(client, db_session):
    two = Two(db_session)
    foreign = issue(client, two.b, draft_invoice(client, two.b, two.b_tx))
    own = issue(client, two.a, draft_invoice(client, two.a, two.a_tx))
    cross = client.get(pdf_url(foreign), headers=two.a)
    random = client.get(pdf_url(uuid.uuid4()), headers=two.a)
    assert cross.status_code == random.status_code == 404
    assert cross.json() == random.json() and cross.headers["content-type"] == random.headers["content-type"] == "application/json"
    assert b"%PDF" not in cross.content
    assert stored(db_session, foreign) == []  # the foreign attempt did not even create the other tenant's artifact
    # the owner of each can download its own, and the two are different documents
    a, b = client.get(pdf_url(own), headers=two.a), client.get(pdf_url(foreign), headers=two.b)
    assert a.status_code == b.status_code == 200 and a.content != b.content
    assert {row.organization_id for row in stored(db_session, own)} == {two.a_org.id}
    assert {row.organization_id for row in stored(db_session, foreign)} == {two.b_org.id}


def test_a_foreign_draft_is_the_same_404_as_a_random_id_not_a_409(client, db_session):
    """A 409 `invoice_not_issued` would reveal that another organization owns a draft with that id."""
    two = Two(db_session)
    foreign_draft = draft_invoice(client, two.b, two.b_tx)
    cross = client.get(pdf_url(foreign_draft), headers=two.a)
    random = client.get(pdf_url(uuid.uuid4()), headers=two.a)
    assert cross.status_code == random.status_code == 404
    assert cross.json() == random.json()
    assert stored(db_session, foreign_draft) == []


def test_a_foreign_member_cannot_read_a_stored_artifact_either(client, db_session):
    two = Two(db_session)
    foreign = issue(client, two.b, draft_invoice(client, two.b, two.b_tx))
    assert client.get(pdf_url(foreign), headers=two.b).status_code == 200  # stored now
    assert client.get(pdf_url(foreign), headers=two.a).status_code == 404


def test_a_draft_has_no_pdf_and_nothing_is_generated_or_stored(client, db_session, sales):
    draft = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing))
    response = client.get(pdf_url(draft), headers=sales.headers)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "invoice_not_issued"
    assert "application/pdf" not in response.headers["content-type"]
    assert stored(db_session, draft) == []
    assert db_session.scalar(text("select count(*) from invoice_pdfs")) == 0


def test_a_malformed_id_is_a_validation_error(client, world):
    assert client.get(f"{INVOICES}/not-a-uuid/pdf", headers=world.headers).status_code == 422


# --- the filename ------------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "number_text",
    ['A/../B"; x=y\r\nZ', "../../etc/passwd", "INV 2026/10 Å", "x" * 64, "////", "Ünï", 'a"b', "a\\b", "..", "-.-", "ok-1_2.3"],
)
def test_the_filename_is_sanitized_independently_of_the_invoice_content(client, world, number_text):
    world.db.execute(text("set local session_replication_role = replica"))
    world.db.execute(text("update invoices set number_text = :n where id = :i"), {"n": number_text, "i": world.document["id"]})
    response = client.get(pdf_url(world.document), headers=world.headers)
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert re.fullmatch(r'attachment; filename="(invoice(-[A-Za-z0-9._-]{1,60})?\.pdf)"', disposition), disposition
    assert len(disposition) < 100 and not any(char in disposition for char in ("/", "\\", "\r", "\n", "..\\"))
    assert response.content.startswith(b"%PDF-")


def test_the_filename_never_contains_anything_but_the_number(client, world):
    disposition = client.get(pdf_url(world.document), headers=world.headers).headers["content-disposition"]
    for private in ("Umeå", "Solo", "Horse", "hk@example", "Anna"):
        assert private not in disposition


# --- refusals ----------------------------------------------------------------------------------------------------------------------------


def test_text_the_renderer_cannot_draw_gives_a_clear_422_and_stores_nothing(client, db_session, sales):
    transaction = completed(db_session, sales.org, sales.billing, lines=[{"description": "مرحبا 😀"}])
    invoice = issue(client, sales.headers, draft_invoice(client, sales.headers, transaction))
    for _ in range(2):  # repeatable: nothing was stored, so nothing is stuck
        response = client.get(pdf_url(invoice), headers=sales.headers)
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "unsupported_characters" and "renderer" in detail["message"]
        assert detail["total"] >= 2 and {item["character"] for item in detail["characters"]} >= {"U+0645", "U+1F600"}
        assert all(item["reason"] for item in detail["characters"])
        assert b"%PDF" not in response.content
    assert stored(db_session, invoice) == []
    assert client.get(f"{INVOICES}/{invoice['id']}", headers=sales.headers).status_code == 200  # the invoice itself is untouched


def test_an_invoice_beyond_the_input_bounds_gives_a_422_and_stores_nothing(client, db_session, sales, monkeypatch):
    transaction = completed(db_session, sales.org, sales.billing, lines=[{"description": "One"}, {"description": "Two"}])
    invoice = issue(client, sales.headers, draft_invoice(client, sales.headers, transaction))
    monkeypatch.setattr(document_module, "MAX_LINES", 1)
    response = client.get(pdf_url(invoice), headers=sales.headers)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "document_too_large"
    assert stored(db_session, invoice) == []
    monkeypatch.undo()
    assert client.get(pdf_url(invoice), headers=sales.headers).status_code == 200  # a later, successful download is possible


def test_hostile_stored_text_is_printed_literally_through_the_whole_stack(client, db_session, sales):
    hostile = '<img src="file:///C:/Windows/win.ini"/><b>x</b> &amp; <a href="http://evil.example">l</a>'
    transaction = completed(db_session, sales.org, sales.billing, lines=[{"description": hostile}])
    invoice = issue(client, sales.headers, draft_invoice(client, sales.headers, transaction, description=hostile))
    body = client.get(pdf_url(invoice), headers=sales.headers).content
    assert squeezed(hostile) in running_text(body)
    for forbidden in (b"/URI", b"/JavaScript", b"/Launch", b"/EmbeddedFile"):
        assert forbidden not in body


def test_swedish_text_survives_the_whole_stack(client, db_session, sales):
    transaction = completed(db_session, sales.org, sales.billing, lines=[{"description": "Hästmassage åäö ÅÄÖ"}])
    invoice = issue(client, sales.headers, draft_invoice(client, sales.headers, transaction, description="Sjukgymnastik för Östen"))
    body = pdf_text(client.get(pdf_url(invoice), headers=sales.headers).content)
    assert "Hästmassage åäö ÅÄÖ" in body and "Sjukgymnastik för Östen" in body


# --- the stored row cannot be changed, even by SQL ---------------------------------------------------------------------------------


def refused(db, statement: str, **params) -> str:
    """Run `statement` in a savepoint and return the database's error message ("" if it was accepted)."""
    savepoint = db.begin_nested()
    try:
        db.execute(text(statement), params)
    except DBAPIError as error:
        savepoint.rollback()
        return str(error.orig)
    savepoint.rollback()
    return ""


def test_the_stored_artifact_cannot_be_updated_or_deleted_by_sql(client, world):
    client.get(pdf_url(world.document), headers=world.headers)
    for statement in (
        "update invoice_pdfs set content = content || '\\x00'::bytea where invoice_id = :i",
        "update invoice_pdfs set renderer = 'other' where invoice_id = :i",
        "update invoice_pdfs set template_version = 9 where invoice_id = :i",
        "update invoice_pdfs set source_sha256 = repeat('0', 64) where invoice_id = :i",
        "update invoice_pdfs set updated_at = now() where invoice_id = :i",
        "delete from invoice_pdfs where invoice_id = :i",
    ):
        assert refused(world.db, statement, i=world.document["id"]), statement
    (row,) = stored(world.db, world.document)
    assert row.content.startswith(b"%PDF-")


def test_the_orm_refuses_to_change_or_delete_a_stored_artifact(client, world):
    client.get(pdf_url(world.document), headers=world.headers)
    (row,) = stored(world.db, world.document)
    with pytest.raises(ValueError, match="frozen"):
        with world.db.begin_nested():
            row.renderer = "other"
            world.db.flush()
    world.db.expire_all()
    (row,) = stored(world.db, world.document)
    with pytest.raises(ValueError, match="frozen"):
        with world.db.begin_nested():
            world.db.delete(row)
            world.db.flush()
    world.db.expire_all()
    assert len(stored(world.db, world.document)) == 1 and stored(world.db, world.document)[0].renderer != "other"


def test_the_artifact_must_belong_to_an_issued_invoice_in_the_same_organization(client, db_session, world, sales):
    insert = (
        "insert into invoice_pdfs (organization_id, invoice_id, content, byte_size, sha256, renderer, template_version, source_sha256) "
        "values (:o, :i, :c, :n, :h, 'r', 1, repeat('a', 64))"
    )
    content = b"%PDF-1.4 test"
    params = dict(c=content, n=len(content), h=hashlib.sha256(content).hexdigest())
    draft = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing))
    assert refused(db_session, insert, o=world.org.id, i=draft["id"], **params), "a draft invoice must not get an artifact"
    other_org = Two(db_session).b_org
    assert refused(db_session, insert, o=other_org.id, i=world.document["id"], **params), "another organization's id must not reference this invoice"
    assert refused(db_session, insert, o=world.org.id, i=uuid.uuid4(), **params), "an unknown invoice"
    assert refused(db_session, insert, o=world.org.id, i=world.document["id"], **{**params, "h": "0" * 64}), "a hash that is not the content's"
    assert refused(db_session, insert, o=world.org.id, i=world.document["id"], **{**params, "n": 1}), "a wrong byte size"
    assert refused(db_session, insert, o=world.org.id, i=world.document["id"], **{**params, "c": b"not a pdf", "n": 9, "h": hashlib.sha256(b"not a pdf").hexdigest()}), "content that is not a PDF"


def test_a_valid_artifact_insert_is_accepted_once_and_a_second_for_the_same_invoice_is_refused(client, world):
    insert = (
        "insert into invoice_pdfs (organization_id, invoice_id, content, byte_size, sha256, renderer, template_version, source_sha256) "
        "values (:o, :i, :c, :n, :h, 'r', 1, repeat('a', 64))"
    )
    content = b"%PDF-1.4 test"
    params = dict(o=world.org.id, i=world.document["id"], c=content, n=len(content), h=hashlib.sha256(content).hexdigest())
    assert refused(world.db, insert, **params) == ""  # control: the valid statement the other tests vary is itself accepted
    world.db.execute(text(insert), params)
    assert refused(world.db, insert, **params), "one artifact per invoice"


def test_an_invoice_with_an_artifact_cannot_be_deleted(client, world):
    client.get(pdf_url(world.document), headers=world.headers)
    assert refused(world.db, "delete from invoices where id = :i", i=world.document["id"])
