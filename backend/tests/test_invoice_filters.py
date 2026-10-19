"""The invoice list's filters are defined once (app/modules/invoicing/filters.py): the screen reads its options from the API, and the
status filter means what the status badge says.
"""

import io
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import rate_limit
from app.core.config import settings
from app.modules.invoicing import filters
from tests.invoicing_support import INVOICES, Two, completed, draft_invoice, issue

TODAY = date.today().isoformat()


@pytest.fixture(autouse=True)
def fresh_limits():
    rate_limit.reset()
    yield
    rate_limit.reset()


def ids(client: TestClient, headers, **params) -> set[str]:
    response = client.get(INVOICES, params=params, headers=headers)
    assert response.status_code == 200, response.text
    return {row["id"] for row in response.json()}


def make_issued(db: Session, client: TestClient, two: Two) -> dict:
    return issue(client, two.a, draft_invoice(client, two.a, completed(db, two.a_org, two.a_customer)))


def pay(client: TestClient, headers, invoice: dict, amount: str | None = None) -> None:
    body = {"amount": amount or invoice["gross_amount"], "paid_on": TODAY, "method": "bank_transfer"}
    assert client.post(f"{INVOICES}/{invoice['id']}/payments", json=body, headers=headers).status_code == 201


class Provider:
    def __init__(self, monkeypatch):
        from pydantic import SecretStr

        monkeypatch.setattr(settings, "email_provider", "cloudflare")
        monkeypatch.setattr(settings, "cloudflare_account_id", "acct")
        monkeypatch.setattr(settings, "email_api_token", SecretStr("t"))
        monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=None: io.BytesIO(json.dumps({"success": True, "result": {"delivered": ["x"]}}).encode()))


# --- the options come from the definition -------------------------------------------------------------------------------------------


def test_the_options_endpoint_lists_every_filter_value_in_order_with_its_words(client: TestClient, db_session: Session):
    two = Two(db_session)
    body = client.get(f"{INVOICES}/filter-options", headers=two.a).json()
    assert [o["value"] for o in body["state"]] == ["draft", "issued", "unpaid", "partially_paid", "paid", "credited", "partly_credited"]
    assert {o["value"]: o["label"] for o in body["state"]}["partially_paid"] == "Partially paid"
    assert [o["value"] for o in body["sent"]] == ["sent", "not_sent"]
    assert [o["value"] for o in body["payment"]] == ["open", "overdue", "not_yet_due", "refund_due"]
    assert [o["value"] for o in body["returns"]] == ["open", "follow_up_due"]


def test_every_value_of_every_filter_has_a_label_and_every_label_a_value():
    for name, values in filters.VALUES.items():
        assert set(values) == set(filters.LABELS[name]), name
        assert len(set(values)) == len(values), name


def test_an_unknown_value_is_refused(client: TestClient, db_session: Session):
    two = Two(db_session)
    for params in ({"state": "old_status"}, {"sent": "maybe"}, {"payment": "gone"}):
        assert client.get(INVOICES, params=params, headers=two.a).status_code == 422


def test_the_options_belong_to_nobody_but_need_a_signed_in_member(client: TestClient):
    assert client.get(f"{INVOICES}/filter-options").status_code in (401, 403, 404)


# --- state means what the badge says ------------------------------------------------------------------------------------------------


def test_each_state_finds_exactly_the_invoices_the_badge_names(client: TestClient, db_session: Session):
    two = Two(db_session)
    draft = draft_invoice(client, two.a, completed(db_session, two.a_org, two.a_customer))
    unpaid = make_issued(db_session, client, two)
    partly = make_issued(db_session, client, two)
    pay(client, two.a, partly, "10.00")
    paid = make_issued(db_session, client, two)
    pay(client, two.a, paid)

    assert ids(client, two.a, state="draft") == {draft["id"]}
    assert ids(client, two.a, state="unpaid") == {unpaid["id"]}
    assert ids(client, two.a, state="partially_paid") == {partly["id"]}
    assert ids(client, two.a, state="paid") == {paid["id"]}
    assert ids(client, two.a, state="issued") == {unpaid["id"], partly["id"], paid["id"]}  # everything but the draft
    # and the badge on each row says the same word
    rows = {row["id"]: row for row in client.get(INVOICES, headers=two.a).json()}
    assert [rows[i]["payment_status"] for i in (unpaid["id"], partly["id"], paid["id"])] == ["unpaid", "partially_paid", "paid"]


def test_a_fully_credited_invoice_is_credited_only_never_paid(client: TestClient, db_session: Session):
    two = Two(db_session)
    invoice = make_issued(db_session, client, two)
    lines = [{"invoice_line_id": line["id"], "quantity": line["quantity"]} for line in invoice["lines"]]
    credit = client.post(f"{INVOICES}/{invoice['id']}/credit-notes", json={"lines": lines, "reason": "all of it"}, headers=two.a)
    assert credit.status_code == 201, credit.text

    assert ids(client, two.a, state="credited") == {invoice["id"]}
    assert ids(client, two.a, state="paid") == set() and ids(client, two.a, state="unpaid") == set()  # the badge says Credited, so no other state lists it
    assert ids(client, two.a, state="partly_credited") == set()


def test_another_organizations_invoices_are_never_found(client: TestClient, db_session: Session):
    two = Two(db_session)
    mine = make_issued(db_session, client, two)
    theirs = issue(client, two.b, draft_invoice(client, two.b, completed(db_session, two.b_org, two.b_customer)))
    assert ids(client, two.a, state="unpaid") == {mine["id"]} and ids(client, two.b, state="unpaid") == {theirs["id"]}


# --- sent ----------------------------------------------------------------------------------------------------------------------------


def test_sent_and_not_sent_split_the_invoices_that_are_waiting_to_be_settled(client: TestClient, db_session: Session, monkeypatch):
    two = Two(db_session)
    waiting = make_issued(db_session, client, two)
    emailed = make_issued(db_session, client, two)
    paid_and_emailed = make_issued(db_session, client, two)
    draft = draft_invoice(client, two.a, completed(db_session, two.a_org, two.a_customer))
    Provider(monkeypatch)
    for invoice in (emailed, paid_and_emailed):
        assert client.post(f"{INVOICES}/{invoice['id']}/email", json={"to": "kund@example.test"}, headers=two.a).status_code == 201
    pay(client, two.a, paid_and_emailed)

    assert ids(client, two.a, sent="sent") == {emailed["id"]}  # the paid one drops "Sent", as its badge does
    assert ids(client, two.a, sent="not_sent") == {waiting["id"]}  # never a draft, never a paid invoice
    assert draft["id"] not in ids(client, two.a, sent="sent") | ids(client, two.a, sent="not_sent")
    assert ids(client, two.a, sent="sent", state="unpaid") == {emailed["id"]}  # the filters combine


def test_a_failed_email_does_not_make_an_invoice_sent(client: TestClient, db_session: Session):
    two = Two(db_session)
    invoice = make_issued(db_session, client, two)
    client.post(f"{INVOICES}/{invoice['id']}/email", json={"to": "kund@example.test"}, headers=two.a)  # no provider: failed
    assert ids(client, two.a, sent="sent") == set() and ids(client, two.a, sent="not_sent") == {invoice["id"]}


# --- the old words keep working -----------------------------------------------------------------------------------------------------


def test_the_older_status_and_payment_words_still_work_for_saved_links(client: TestClient, db_session: Session):
    two = Two(db_session)
    draft = draft_invoice(client, two.a, completed(db_session, two.a_org, two.a_customer))
    unpaid = make_issued(db_session, client, two)
    assert ids(client, two.a, status="draft") == {draft["id"]}
    assert ids(client, two.a, status="issued") == {unpaid["id"]}
    assert ids(client, two.a, payment="unpaid") == {unpaid["id"]}
    assert ids(client, two.a, payment="open") == {unpaid["id"]}
