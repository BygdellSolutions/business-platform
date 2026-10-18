"""The receipt of an order paid at the counter is made from the organization as it is NOW (it is not frozen like an invoice): the current
logo and the current seller details are on it, and a logo that was replaced or removed shows on the next receipt downloaded.
"""

import base64
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from app.models import Role
from tests.factories import make_customer, make_org
from tests.invoicing_support import member_of
from tests.pdf_support import pdf_text, reader
from tests.test_counter_sales import _draft, _pay

LOGO = "/api/organization/logo"
RED, BLUE = (200, 0, 0), (0, 0, 200)


def picture(color) -> str:
    out = io.BytesIO()
    Image.new("RGB", (300, 100), color).save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode()


@pytest.fixture
def world(db_session: Session):
    org = make_org(db_session)
    return org, member_of(db_session, org, Role.OWNER), make_customer(db_session, org, "Anna")


def receipt(client: TestClient, headers, paid: dict) -> bytes:
    response = client.get(f"/api/invoices/receipts/{paid['id']}/pdf", headers=headers)
    assert response.status_code == 200, response.text
    return response.content


def printed_color(pdf: bytes):
    (image,) = reader(pdf).pages[0].images
    return image.image.convert("RGB").getpixel((1, 1))


def test_a_receipt_prints_the_logo_and_follows_a_replaced_or_removed_one(client: TestClient, db_session: Session, world):
    org, owner, anna = world
    paid = _pay(client, owner, _draft(client, owner, anna.id)).json()
    assert len(reader(receipt(client, owner, paid)).pages[0].images) == 0  # no logo yet

    assert client.post(LOGO, json={"data_base64": picture(RED)}, headers=owner).status_code == 200
    assert printed_color(receipt(client, owner, paid)) == RED  # the logo is on the receipt

    assert client.post(LOGO, json={"data_base64": picture(BLUE)}, headers=owner).status_code == 200
    assert printed_color(receipt(client, owner, paid)) == BLUE  # a receipt is made live: the new logo, also on an older receipt

    assert client.delete(LOGO, headers=owner).status_code == 204
    assert len(reader(receipt(client, owner, paid)).pages[0].images) == 0


def test_a_receipt_prints_the_sellers_details_as_they_are_now(client: TestClient, db_session: Session, world):
    org, owner, anna = world
    paid = _pay(client, owner, _draft(client, owner, anna.id), "card").json()
    before = pdf_text(receipt(client, owner, paid))
    assert "Receipt" in before and "Anna" in before and "Paid by card" in before

    changed = client.patch(
        "/api/organization",
        json={"legal_name": "Kalle Hästterapi AB", "email": "kontor@kalle.example", "phone": "090-123 45", "website": "www.kalle.example", "city": "Umeå", "address_line1": "Storgatan 1"},
        headers=owner,
    )
    assert changed.status_code == 200, changed.text

    after = pdf_text(receipt(client, owner, paid))
    for text in ("Kalle Hästterapi AB", "kontor@kalle.example", "090-123 45", "www.kalle.example", "Storgatan 1", "Umeå"):
        assert text in after, text
    assert "Kalle Hästterapi AB" not in before


def test_another_organizations_logo_never_appears_on_my_receipt(client: TestClient, db_session: Session, world):
    org, owner, anna = world
    other = member_of(db_session, make_org(db_session, "Other"), Role.OWNER)
    assert client.post(LOGO, json={"data_base64": picture(BLUE)}, headers=other).status_code == 200
    paid = _pay(client, owner, _draft(client, owner, anna.id)).json()
    assert len(reader(receipt(client, owner, paid)).pages[0].images) == 0
