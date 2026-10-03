"""Optimistic concurrency for Sales, at the API level (app/modules/sales/versioning.py).

These tests run in the rolled-back test session and send EXACTLY the If-Match they mean to
(`raw_client`). The committed, multi-connection scenarios are in
test_sales_versioning_concurrency.py.
"""

import uuid

import pytest
from sqlalchemy import select, text

from app.models import Role
from app.modules.sales.models import Transaction, TransactionLine
from tests.factories import add_member, make_customer, make_item, make_org, make_transaction, make_user

LINE = {"description": "Travel", "unit": "km", "quantity": "1", "unit_price_ex_vat": "2.50", "vat_rate": "25.00"}


def v(n: int) -> dict[str, str]:
    return {"If-Match": f'"{n}"'}


def with_headers(headers: dict[str, str], n: int | None) -> dict[str, str]:
    return {**headers, **({} if n is None else v(n))}


@pytest.fixture
def draft(raw_client, sales):
    """A draft transaction with two ad-hoc lines, as the API returns it."""
    body = {
        "billing_customer_id": str(sales.billing.id),
        "transaction_date": "2026-10-01",
        "lines": [LINE, {**LINE, "description": "Second"}],
    }
    response = raw_client.post("/api/transactions", json=body, headers=sales.headers)
    assert response.status_code == 201, response.text
    return response.json()


def read(raw_client, sales, tx_id):
    response = raw_client.get(f"/api/transactions/{tx_id}", headers=sales.headers)
    assert response.status_code == 200
    return response.json()


# --- the versions are exposed and start at 1 -------------------------------------------------------------------


def test_every_new_record_starts_at_version_1(raw_client, sales, draft):
    assert (draft["version"], draft["header_version"]) == (1, 1)
    assert [line["version"] for line in draft["lines"]] == [1, 1]
    listed = raw_client.get("/api/transactions", headers=sales.headers).json()[0]
    assert (listed["version"], listed["header_version"]) == (1, 1)


# --- the header ------------------------------------------------------------------------------------------------


def test_a_header_edit_with_the_current_version_works_and_moves_both_header_and_document_versions(raw_client, sales, draft):
    response = raw_client.patch(f"/api/transactions/{draft['id']}", json={"transaction_date": "2026-11-02"}, headers=with_headers(sales.headers, 1))

    assert response.status_code == 200
    body = response.json()
    assert (body["transaction_date"], body["header_version"], body["version"]) == ("2026-11-02", 2, 2)
    assert [line["version"] for line in body["lines"]] == [1, 1]  # lines untouched


def test_the_token_may_be_quoted_or_bare(raw_client, sales, draft):
    bare = raw_client.patch(f"/api/transactions/{draft['id']}", json={"transaction_date": "2026-11-02"}, headers={**sales.headers, "If-Match": "1"})
    assert bare.status_code == 200
    quoted = raw_client.patch(f"/api/transactions/{draft['id']}", json={"transaction_date": "2026-11-03"}, headers={**sales.headers, "If-Match": '"2"'})
    assert quoted.status_code == 200


def test_a_stale_header_edit_is_refused_and_changes_nothing(raw_client, sales, draft, db_session):
    newer = raw_client.patch(f"/api/transactions/{draft['id']}", json={"transaction_date": "2026-11-02"}, headers=with_headers(sales.headers, 1))
    assert newer.status_code == 200
    other = make_customer(db_session, sales.org, "Someone Else")

    stale = raw_client.patch(
        f"/api/transactions/{draft['id']}",
        json={"transaction_date": "2030-01-01", "billing_customer_id": str(other.id)},
        headers=with_headers(sales.headers, 1),  # based on the version before the newer edit
    )

    assert stale.status_code == 409
    assert stale.json()["detail"] == {
        "code": "stale_record",
        "message": "This record was changed by someone else since you loaded it; reload it and try again",
        "entity_type": "transaction",
        "entity_id": draft["id"],
        "current_version": 2,
    }
    after = read(raw_client, sales, draft["id"])
    assert after["transaction_date"] == "2026-11-02"  # the newer edit survived
    assert after["billing_customer_id"] == str(sales.billing.id)  # NEITHER field of the stale edit was applied
    assert (after["header_version"], after["version"]) == (2, 2)


def test_a_header_edit_that_changes_nothing_does_not_move_any_version(raw_client, sales, draft):
    for body in ({}, {"transaction_date": draft["transaction_date"]}, {"billing_customer_id": draft["billing_customer_id"]}):
        response = raw_client.patch(f"/api/transactions/{draft['id']}", json=body, headers=with_headers(sales.headers, 1))
        assert response.status_code == 200
    after = read(raw_client, sales, draft["id"])
    assert (after["header_version"], after["version"]) == (1, 1)


def test_a_line_changed_elsewhere_does_not_make_a_header_edit_stale(raw_client, sales, draft):
    line = draft["lines"][0]
    changed = raw_client.patch(f"/api/transactions/{draft['id']}/lines/{line['id']}", json={"quantity": "3"}, headers=with_headers(sales.headers, 1))
    assert changed.status_code == 200

    header = raw_client.patch(f"/api/transactions/{draft['id']}", json={"transaction_date": "2026-12-01"}, headers=with_headers(sales.headers, 1))

    assert header.status_code == 200  # the header token is about the header only


# --- lines -----------------------------------------------------------------------------------------------------


def test_adding_a_line_needs_no_version_but_moves_the_document_version(raw_client, sales, draft):
    added = raw_client.post(f"/api/transactions/{draft['id']}/lines", json=LINE, headers=sales.headers)  # no If-Match
    assert added.status_code == 201
    assert added.json()["version"] == 1
    after = read(raw_client, sales, draft["id"])
    assert (after["version"], after["header_version"]) == (2, 1)


def test_a_line_edit_with_the_current_version_moves_that_line_and_the_document(raw_client, sales, draft):
    first, second = draft["lines"]

    response = raw_client.patch(f"/api/transactions/{draft['id']}/lines/{first['id']}", json={"quantity": "2"}, headers=with_headers(sales.headers, 1))

    assert response.status_code == 200
    assert response.json()["version"] == 2
    after = read(raw_client, sales, draft["id"])
    assert [line["version"] for line in after["lines"]] == [2, 1]  # the other line is not stale
    assert (after["version"], after["header_version"]) == (2, 1)
    ok = raw_client.patch(f"/api/transactions/{draft['id']}/lines/{second['id']}", json={"quantity": "5"}, headers=with_headers(sales.headers, 1))
    assert ok.status_code == 200


def test_a_stale_line_edit_cannot_overwrite_and_changes_nothing(raw_client, sales, draft):
    line = draft["lines"][0]
    first = raw_client.patch(f"/api/transactions/{draft['id']}/lines/{line['id']}", json={"description": "Tab A", "quantity": "2"}, headers=with_headers(sales.headers, 1))
    assert first.status_code == 200
    before = read(raw_client, sales, draft["id"])

    stale = raw_client.patch(
        f"/api/transactions/{draft['id']}/lines/{line['id']}",
        json={"description": "Tab B", "quantity": "9", "unit_price_ex_vat": "99.00"},
        headers=with_headers(sales.headers, 1),
    )

    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert (detail["code"], detail["entity_type"], detail["entity_id"], detail["current_version"]) == ("stale_record", "transaction_line", line["id"], 2)
    after = read(raw_client, sales, draft["id"])
    assert after == before  # not one field, amount, total or version moved
    assert after["lines"][0]["description"] == "Tab A"


def test_a_line_edit_that_changes_nothing_does_not_move_a_version(raw_client, sales, draft):
    line = draft["lines"][0]
    response = raw_client.patch(
        f"/api/transactions/{draft['id']}/lines/{line['id']}",
        json={"description": line["description"], "quantity": line["quantity"]},
        headers=with_headers(sales.headers, 1),
    )
    assert response.status_code == 200
    after = read(raw_client, sales, draft["id"])
    assert (after["version"], after["lines"][0]["version"]) == (1, 1)


def test_a_stale_delete_cannot_delete_a_line_that_changed_elsewhere(raw_client, sales, draft):
    line = draft["lines"][0]
    raw_client.patch(f"/api/transactions/{draft['id']}/lines/{line['id']}", json={"quantity": "4"}, headers=with_headers(sales.headers, 1))

    stale = raw_client.delete(f"/api/transactions/{draft['id']}/lines/{line['id']}", headers=with_headers(sales.headers, 1))

    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_record"
    after = read(raw_client, sales, draft["id"])
    assert len(after["lines"]) == 2 and after["lines"][0]["quantity"] == "4.000"

    fresh = raw_client.delete(f"/api/transactions/{draft['id']}/lines/{line['id']}", headers=with_headers(sales.headers, 2))
    assert fresh.status_code == 204
    final = read(raw_client, sales, draft["id"])
    assert len(final["lines"]) == 1
    assert final["version"] == 3  # edit (2), delete (3)


def test_deleting_a_line_moves_the_document_version_so_a_stale_lifecycle_step_fails(raw_client, sales, draft):
    raw_client.delete(f"/api/transactions/{draft['id']}/lines/{draft['lines'][0]['id']}", headers=with_headers(sales.headers, 1))
    stale = raw_client.post(f"/api/transactions/{draft['id']}/complete", headers=with_headers(sales.headers, 1))
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_record"


# --- lifecycle -------------------------------------------------------------------------------------------------


def test_complete_reopen_and_cancel_each_need_the_current_version_and_move_it(raw_client, sales, draft):
    tx = draft["id"]
    done = raw_client.post(f"/api/transactions/{tx}/complete", headers=with_headers(sales.headers, 1))
    assert done.status_code == 200 and (done.json()["status"], done.json()["version"]) == ("completed", 2)

    stale_reopen = raw_client.post(f"/api/transactions/{tx}/reopen", headers=with_headers(sales.headers, 1))
    assert stale_reopen.status_code == 409 and stale_reopen.json()["detail"]["code"] == "stale_record"
    assert read(raw_client, sales, tx)["status"] == "completed"
    reopened = raw_client.post(f"/api/transactions/{tx}/reopen", headers=with_headers(sales.headers, 2))
    assert reopened.status_code == 200 and (reopened.json()["status"], reopened.json()["version"]) == ("draft", 3)

    stale_cancel = raw_client.post(f"/api/transactions/{tx}/cancel", headers=with_headers(sales.headers, 2))
    assert stale_cancel.status_code == 409
    assert read(raw_client, sales, tx)["status"] == "draft"
    cancelled = raw_client.post(f"/api/transactions/{tx}/cancel", headers=with_headers(sales.headers, 3))
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"


def test_a_stale_complete_is_refused_when_a_line_was_added_elsewhere_and_nothing_changes(raw_client, sales, draft):
    raw_client.post(f"/api/transactions/{draft['id']}/lines", json=LINE, headers=sales.headers)

    stale = raw_client.post(f"/api/transactions/{draft['id']}/complete", headers=with_headers(sales.headers, 1))

    assert stale.status_code == 409 and stale.json()["detail"]["current_version"] == 2
    after = read(raw_client, sales, draft["id"])
    assert (after["status"], after["version"], len(after["lines"])) == ("draft", 2, 3)


def test_the_status_conflict_is_answered_before_the_version_one(raw_client, sales, draft):
    """A tab whose transaction was completed elsewhere is told that, whatever version it sends."""
    raw_client.post(f"/api/transactions/{draft['id']}/complete", headers=with_headers(sales.headers, 1))
    line = draft["lines"][0]

    for headers in (with_headers(sales.headers, 1), with_headers(sales.headers, 2), sales.headers):
        header_edit = raw_client.patch(f"/api/transactions/{draft['id']}", json={"transaction_date": "2030-01-01"}, headers=headers)
        line_edit = raw_client.patch(f"/api/transactions/{draft['id']}/lines/{line['id']}", json={"quantity": "2"}, headers=headers)
        again = raw_client.post(f"/api/transactions/{draft['id']}/complete", headers=headers)
        assert header_edit.status_code == line_edit.status_code == again.status_code == 409
        assert "completed transaction cannot be" in header_edit.json()["detail"]
        assert "completed transaction cannot be" in line_edit.json()["detail"]
        assert "completed transaction cannot be completed" in again.json()["detail"]


def test_a_transaction_that_cannot_be_completed_for_another_reason_still_says_so_with_a_fresh_version(raw_client, sales):
    empty = raw_client.post("/api/transactions", json={"billing_customer_id": str(sales.billing.id)}, headers=sales.headers).json()
    response = raw_client.post(f"/api/transactions/{empty['id']}/complete", headers=with_headers(sales.headers, 1))
    assert response.status_code == 409
    assert "at least one line" in response.json()["detail"]


def test_deleting_a_draft_needs_the_current_version(raw_client, sales, draft):
    raw_client.post(f"/api/transactions/{draft['id']}/lines", json=LINE, headers=sales.headers)
    stale = raw_client.delete(f"/api/transactions/{draft['id']}", headers=with_headers(sales.headers, 1))
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_record"
    assert raw_client.get(f"/api/transactions/{draft['id']}", headers=sales.headers).status_code == 200
    fresh = raw_client.delete(f"/api/transactions/{draft['id']}", headers=with_headers(sales.headers, 2))
    assert fresh.status_code == 204


# --- the header itself -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_for",
    [
        lambda c, h, tx, line: c.patch(f"/api/transactions/{tx}", json={"transaction_date": "2030-01-01"}, headers=h),
        lambda c, h, tx, line: c.delete(f"/api/transactions/{tx}", headers=h),
        lambda c, h, tx, line: c.post(f"/api/transactions/{tx}/complete", headers=h),
        lambda c, h, tx, line: c.post(f"/api/transactions/{tx}/cancel", headers=h),
        lambda c, h, tx, line: c.patch(f"/api/transactions/{tx}/lines/{line}", json={"quantity": "2"}, headers=h),
        lambda c, h, tx, line: c.delete(f"/api/transactions/{tx}/lines/{line}", headers=h),
    ],
    ids=["header", "delete", "complete", "cancel", "line-edit", "line-delete"],
)
class TestThePreconditionIsRequired:
    def test_missing_is_428_and_changes_nothing(self, raw_client, sales, draft, request_for):
        before = read(raw_client, sales, draft["id"])
        response = request_for(raw_client, sales.headers, draft["id"], draft["lines"][0]["id"])
        assert response.status_code == 428
        assert "If-Match" in response.json()["detail"]
        assert read(raw_client, sales, draft["id"]) == before

    @pytest.mark.parametrize("token", ["abc", '"abc"', "", '""', "*", '"-1"', "1.5", '"1" "2"', "W/\"1\"", "12345678901234567890"])
    def test_malformed_is_400_and_changes_nothing(self, raw_client, sales, draft, request_for, token):
        before = read(raw_client, sales, draft["id"])
        response = request_for(raw_client, {**sales.headers, "If-Match": token}, draft["id"], draft["lines"][0]["id"])
        assert response.status_code == 400
        assert read(raw_client, sales, draft["id"]) == before

    @pytest.mark.parametrize("token", ["0", '"2"', '"99"'])
    def test_wrong_is_409_stale_and_changes_nothing(self, raw_client, sales, draft, request_for, token):
        before = read(raw_client, sales, draft["id"])
        response = request_for(raw_client, {**sales.headers, "If-Match": token}, draft["id"], draft["lines"][0]["id"])
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "stale_record"
        assert read(raw_client, sales, draft["id"]) == before


# --- tenancy: a version never reveals or reaches another organization's records ---------------------------------


class TestTenantSafety:
    @pytest.fixture
    def two_orgs(self, db_session):
        a, b = make_org(db_session, "A"), make_org(db_session, "B")
        user_a, user_b = make_user(db_session), make_user(db_session)
        add_member(db_session, a, user_a, Role.OWNER)
        add_member(db_session, b, user_b, Role.OWNER)
        tx_a = make_transaction(db_session, a, lines=[{}])
        tx_b = make_transaction(db_session, b, lines=[{}])
        line_b = db_session.scalar(select(TransactionLine).where(TransactionLine.transaction_id == tx_b.id))
        line_a = db_session.scalar(select(TransactionLine).where(TransactionLine.transaction_id == tx_a.id))
        return {"a_headers": {"X-Dev-User-Email": user_a.email}, "tx_a": tx_a, "tx_b": tx_b, "line_a": line_a, "line_b": line_b}

    def requests(self, tx, line):
        return [
            ("patch", f"/api/transactions/{tx}", {"transaction_date": "2030-01-01"}),
            ("delete", f"/api/transactions/{tx}", None),
            ("post", f"/api/transactions/{tx}/complete", None),
            ("post", f"/api/transactions/{tx}/reopen", None),
            ("post", f"/api/transactions/{tx}/cancel", None),
            ("patch", f"/api/transactions/{tx}/lines/{line}", {"quantity": "2"}),
            ("delete", f"/api/transactions/{tx}/lines/{line}", None),
        ]

    def send(self, raw_client, headers, method, url, body):
        kwargs = {"headers": headers}
        if body is not None:
            kwargs["json"] = body
        return getattr(raw_client, method)(url, **kwargs)

    def test_a_foreign_and_a_random_id_are_the_same_404_whatever_the_header_says(self, raw_client, two_orgs):
        for header in (None, '"1"', '"999"', "garbage"):  # right version, wrong version, nonsense, none
            headers = {**two_orgs["a_headers"], **({} if header is None else {"If-Match": header})}
            for foreign, random in zip(
                self.requests(two_orgs["tx_b"].id, two_orgs["line_b"].id),
                self.requests(uuid.uuid4(), uuid.uuid4()),
                strict=True,
            ):
                got_foreign = self.send(raw_client, headers, *foreign)
                got_random = self.send(raw_client, headers, *random)
                assert got_foreign.status_code == got_random.status_code == 404, (header, foreign)
                assert got_foreign.json() == got_random.json() == {"detail": "Not found"}

    def test_a_foreign_line_under_my_transaction_is_404_too(self, raw_client, two_orgs):
        headers = {**two_orgs["a_headers"], "If-Match": '"1"'}
        tx = two_orgs["tx_a"].id
        for method, url, body in (
            ("patch", f"/api/transactions/{tx}/lines/{two_orgs['line_b'].id}", {"quantity": "2"}),
            ("delete", f"/api/transactions/{tx}/lines/{two_orgs['line_b'].id}", None),
        ):
            response = self.send(raw_client, headers, method, url, body)
            assert response.status_code == 404 and response.json() == {"detail": "Not found"}

    def test_a_stale_answer_describes_only_the_callers_own_record(self, raw_client, two_orgs):
        headers = {**two_orgs["a_headers"], "If-Match": '"7"'}
        response = raw_client.post(f"/api/transactions/{two_orgs['tx_a'].id}/complete", headers=headers)
        assert response.status_code == 409
        assert set(response.json()["detail"]) == {"code", "message", "entity_type", "entity_id", "current_version"}
        assert response.json()["detail"]["entity_id"] == str(two_orgs["tx_a"].id)

    def test_nothing_in_the_other_organization_moves(self, raw_client, two_orgs, db_session):
        headers = {**two_orgs["a_headers"], "If-Match": '"1"'}
        for method, url, body in self.requests(two_orgs["tx_b"].id, two_orgs["line_b"].id):
            self.send(raw_client, headers, method, url, body)
        db_session.expire_all()
        tx_b = db_session.get(Transaction, two_orgs["tx_b"].id)
        assert (tx_b.status, tx_b.version, tx_b.header_version) == ("draft", 1, 1)
        assert db_session.get(TransactionLine, two_orgs["line_b"].id).version == 1


# --- the database refuses nonsense ---------------------------------------------------------------------------------


def test_versions_cannot_be_zero_or_negative_in_the_database(db_session):
    org = make_org(db_session, "Checks")
    tx = make_transaction(db_session, org, lines=[{}])
    for statement in (
        "update transactions set version = 0 where id = :i",
        "update transactions set header_version = -1 where id = :i",
    ):
        with pytest.raises(Exception, match="ck_transactions_versions_positive"):
            with db_session.begin_nested():
                db_session.execute(text(statement), {"i": tx.id})
    with pytest.raises(Exception, match="ck_transaction_lines_version_positive"):
        with db_session.begin_nested():
            db_session.execute(text("update transaction_lines set version = 0 where transaction_id = :i"), {"i": tx.id})


def test_the_migration_gave_unversioned_data_version_1(db_session):
    """Rows created without naming a version (like all rows that existed before the migration)
    start at 1 through the column default."""
    org = make_org(db_session, "Defaults")
    db_session.execute(text("insert into customers (organization_id, name, customer_type) values (:o, 'C', 'person')"), {"o": org.id})
    customer = db_session.execute(text("select id from customers where organization_id = :o"), {"o": org.id}).scalar_one()
    db_session.execute(
        text("insert into transactions (organization_id, billing_customer_id, transaction_date) values (:o, :c, '2026-10-01')"),
        {"o": org.id, "c": customer},
    )
    assert db_session.execute(text("select version || '/' || header_version from transactions where organization_id = :o"), {"o": org.id}).scalar_one() == "1/1"
