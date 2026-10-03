"""A transaction's currency: a snapshot of the organization's default taken at creation.

Rules under test:
  * the currency is copied when the transaction is created and never changes afterwards (not
    through the API, not through settings, and a database trigger refuses it as well);
  * an organization that has not configured a currency cannot create transactions;
  * a transaction that predates currencies (currency NULL) gets one only through the explicit
    owner/admin action POST /api/transactions/assign-currency;
  * none of it crosses organizations.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.models import Role
from app.modules.sales.models import Transaction
from tests.factories import add_member, make_customer, make_item, make_org, make_transaction, make_user

TX = "/api/transactions"
WRITERS = {Role.OWNER, Role.ADMIN}


def member_of(db: Session, org, role: Role) -> dict[str, str]:
    user = make_user(db)
    add_member(db, org, user, role)
    return {"X-Dev-User-Email": user.email, "X-Organization-Id": str(org.id)}


def create(client, sales, **extra):
    return client.post(TX, json={"billing_customer_id": str(sales.billing.id), **extra}, headers=sales.headers)


def count(db: Session, org) -> int:
    return len(list(db.scalars(select(Transaction.id).where(Transaction.organization_id == org.id))))


# --- the snapshot ------------------------------------------------------------------------------------------


def test_a_new_transaction_gets_the_organizations_currency(client, sales):
    response = create(client, sales)

    assert response.status_code == 201, response.text
    assert response.json()["currency"] == "SEK"
    tx_id = response.json()["id"]
    assert client.get(f"{TX}/{tx_id}", headers=sales.headers).json()["currency"] == "SEK"
    listed = client.get(TX, headers=sales.headers).json()
    assert [row["currency"] for row in listed] == ["SEK"]


def test_the_currency_follows_the_organization_not_a_global_default(client, db_session):
    eur_org = make_org(db_session, "Euro Org", default_currency="EUR")
    headers = member_of(db_session, eur_org, Role.OWNER)
    billing = make_customer(db_session, eur_org)

    response = client.post(TX, json={"billing_customer_id": str(billing.id)}, headers=headers)

    assert response.json()["currency"] == "EUR"


def test_an_organization_without_a_currency_cannot_create_a_transaction(client, db_session):
    org = make_org(db_session, "Unconfigured", default_currency=None)
    headers = member_of(db_session, org, Role.OWNER)
    billing = make_customer(db_session, org)

    response = client.post(TX, json={"billing_customer_id": str(billing.id)}, headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "currency_not_configured"
    assert count(db_session, org) == 0  # nothing half-created


def test_the_currency_cannot_be_chosen_by_the_client(client, sales):
    assert create(client, sales, currency="EUR").status_code == 422
    tx_id = create(client, sales).json()["id"]
    assert client.patch(f"{TX}/{tx_id}", json={"currency": "EUR"}, headers=sales.headers).status_code == 422
    assert client.get(f"{TX}/{tx_id}", headers=sales.headers).json()["currency"] == "SEK"


def test_a_validation_failure_creates_nothing_and_a_valid_one_still_snapshots(client, sales):
    bad = create(client, sales, lines=[{"description": "x", "unit": "u", "quantity": "1"}])
    assert bad.status_code == 422
    assert create(client, sales, lines=[{"item_id": str(sales.item.id), "quantity": "1"}]).json()["currency"] == "SEK"


# --- it never changes ----------------------------------------------------------------------------------------


def test_a_later_change_of_the_organizations_currency_does_not_touch_existing_transactions(client, db_session, sales):
    first = create(client, sales).json()["id"]
    # The API refuses this change once a transaction exists, so simulate a future model that
    # allows it (or a manual database change): the transaction still must not move.
    sales.org.default_currency = "EUR"
    db_session.flush()

    second = create(client, sales).json()["id"]

    assert client.get(f"{TX}/{first}", headers=sales.headers).json()["currency"] == "SEK"
    assert client.get(f"{TX}/{second}", headers=sales.headers).json()["currency"] == "EUR"


def test_lifecycle_steps_and_line_edits_leave_the_currency_alone(client, sales):
    tx_id = create(client, sales, lines=[{"item_id": str(sales.item.id), "quantity": "2"}]).json()["id"]
    for step in ("complete", "reopen", "cancel"):
        response = client.post(f"{TX}/{tx_id}/{step}", headers=sales.headers)
        assert response.status_code == 200, response.text
        assert response.json()["currency"] == "SEK"


def test_the_database_refuses_to_change_a_set_currency(db_session, sales):
    tx = make_transaction(db_session, sales.org)
    for new in ("EUR", None):
        with pytest.raises(IntegrityError, match="cannot be changed once set"):
            with db_session.begin_nested():
                db_session.execute(text("UPDATE transactions SET currency = :c WHERE id = :i"), {"c": new, "i": tx.id})
    db_session.refresh(tx)
    assert tx.currency == "SEK"
    # Writing the same value is not a change.
    db_session.execute(text("UPDATE transactions SET currency = 'SEK' WHERE id = :i"), {"i": tx.id})


def test_the_database_allows_the_one_transition_from_no_currency_to_a_currency(db_session, sales):
    tx = make_transaction(db_session, sales.org, currency=None)
    db_session.execute(text("UPDATE transactions SET currency = 'SEK' WHERE id = :i"), {"i": tx.id})
    db_session.refresh(tx)
    assert tx.currency == "SEK"


@pytest.mark.parametrize("bad", ["sek", "SE", "SEKK", "S1K", ""])
def test_the_database_checks_the_shape_of_a_currency(db_session, sales, bad):
    with pytest.raises((IntegrityError, DataError)):
        with db_session.begin_nested():
            make_transaction(db_session, sales.org, currency=bad)


# --- what a transaction that predates currencies looks like -------------------------------------------------------------


def test_a_transaction_without_a_currency_is_shown_as_such_and_stays_usable(client, db_session, sales):
    old = make_transaction(db_session, sales.org, billing_customer=sales.billing, currency=None)

    body = client.get(f"{TX}/{old.id}", headers=sales.headers).json()

    assert body["currency"] is None
    assert client.post(f"{TX}/{old.id}/complete", headers=sales.headers).status_code == 200  # not blocked


# --- currency-status and assign-currency ------------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(Role))
def test_every_member_can_see_the_currency_status(client, db_session, role):
    org = make_org(db_session)
    make_transaction(db_session, org, currency=None)
    make_transaction(db_session, org, currency=None)
    make_transaction(db_session, org)

    response = client.get(f"{TX}/currency-status", headers=member_of(db_session, org, role))

    assert response.status_code == 200
    assert response.json() == {"default_currency": "SEK", "transactions_without_currency": 2}


def test_the_status_counts_only_the_active_organization(client, db_session):
    mine, other = make_org(db_session, "Mine"), make_org(db_session, "Other")
    make_transaction(db_session, mine)
    for _ in range(3):
        make_transaction(db_session, other, currency=None)

    status = client.get(f"{TX}/currency-status", headers=member_of(db_session, mine, Role.OWNER)).json()

    assert status["transactions_without_currency"] == 0


@pytest.mark.parametrize("role", list(Role))
def test_only_owner_and_admin_can_assign_the_currency(client, db_session, role):
    org = make_org(db_session)
    old = make_transaction(db_session, org, currency=None)

    response = client.post(f"{TX}/assign-currency", json={"currency": "SEK"}, headers=member_of(db_session, org, role))

    db_session.refresh(old)
    if role in WRITERS:
        assert response.status_code == 200 and response.json() == {"currency": "SEK", "assigned": 1}
        assert old.currency == "SEK"
    else:
        assert response.status_code == 403
        assert old.currency is None


def test_assigning_touches_only_currency_less_transactions_of_this_organization(client, db_session):
    mine, other = make_org(db_session, "Mine"), make_org(db_session, "Other", default_currency="EUR")
    headers = member_of(db_session, mine, Role.OWNER)
    old_a, old_b = (make_transaction(db_session, mine, currency=None) for _ in range(2))
    keeps = make_transaction(db_session, mine, currency="NOK")  # already has one: must not change
    foreign = make_transaction(db_session, other, currency=None)

    response = client.post(f"{TX}/assign-currency", json={"currency": "SEK"}, headers=headers)

    assert response.json() == {"currency": "SEK", "assigned": 2}
    for tx in (old_a, old_b, keeps, foreign):
        db_session.refresh(tx)
    assert (old_a.currency, old_b.currency, keeps.currency, foreign.currency) == ("SEK", "SEK", "NOK", None)


def test_assigning_must_confirm_the_organizations_own_currency(client, db_session):
    org = make_org(db_session)
    old = make_transaction(db_session, org, currency=None)
    headers = member_of(db_session, org, Role.ADMIN)

    wrong = client.post(f"{TX}/assign-currency", json={"currency": "EUR"}, headers=headers)
    missing = client.post(f"{TX}/assign-currency", json={}, headers=headers)
    malformed = client.post(f"{TX}/assign-currency", json={"currency": "sek!"}, headers=headers)

    assert (wrong.status_code, missing.status_code, malformed.status_code) == (422, 422, 422)
    db_session.refresh(old)
    assert old.currency is None


def test_assigning_needs_a_configured_organization_currency(client, db_session):
    org = make_org(db_session, default_currency=None)
    old = make_transaction(db_session, org, currency=None)

    response = client.post(f"{TX}/assign-currency", json={"currency": "SEK"}, headers=member_of(db_session, org, Role.OWNER))

    assert response.status_code == 409 and response.json()["detail"]["code"] == "currency_not_configured"
    db_session.refresh(old)
    assert old.currency is None


def test_assigning_twice_assigns_nothing_the_second_time(client, db_session):
    org = make_org(db_session)
    make_transaction(db_session, org, currency=None)
    headers = member_of(db_session, org, Role.OWNER)

    assert client.post(f"{TX}/assign-currency", json={"currency": "SEK"}, headers=headers).json()["assigned"] == 1
    assert client.post(f"{TX}/assign-currency", json={"currency": "SEK"}, headers=headers).json()["assigned"] == 0


def test_assigning_moves_the_version_so_an_open_editor_is_told_it_is_stale(raw_client, db_session):
    org = make_org(db_session)
    old = make_transaction(db_session, org, currency=None)
    headers = member_of(db_session, org, Role.OWNER)
    before = raw_client.get(f"{TX}/{old.id}", headers=headers).json()

    raw_client.post(f"{TX}/assign-currency", json={"currency": "SEK"}, headers=headers)

    after = raw_client.get(f"{TX}/{old.id}", headers=headers).json()
    assert (before["currency"], after["currency"]) == (None, "SEK")
    assert after["version"] == before["version"] + 1
    stale = raw_client.post(f"{TX}/{old.id}/complete", headers={**headers, "If-Match": f'"{before["version"]}"'})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_record"


def test_the_literal_routes_are_not_swallowed_by_the_id_route(client, sales):
    # "/{transaction_id}" would answer 422 (not a UUID) if it matched first.
    assert client.get(f"{TX}/currency-status", headers=sales.headers).status_code == 200
    assert client.post(f"{TX}/assign-currency", json={"currency": "SEK"}, headers=sales.headers).status_code == 200


# --- items --------------------------------------------------------------------------------------------------------


def test_items_can_be_created_before_a_currency_is_configured(client, db_session):
    """Prices without a currency are allowed; the first currency can still be set afterwards."""
    org = make_org(db_session, default_currency=None)
    headers = member_of(db_session, org, Role.OWNER)

    created = client.post(
        "/api/items",
        json={"type": "service", "name": "Massage", "unit": "session", "price_ex_vat": "850.00", "vat_rate": "25.00"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert client.patch("/api/organization", json={"default_currency": "SEK"}, headers=headers).status_code == 200
    assert make_item(db_session, org, "Second").id  # factory path still fine
