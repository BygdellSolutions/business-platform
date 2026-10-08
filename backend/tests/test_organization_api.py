"""The organization settings API: /api/organization (the ACTIVE organization only).

Reading is open to every member; changing is for owners and admins. The tenant is always the
one resolved from the membership: there is no id in the path or the body to tamper with.
"""

import uuid

import pytest
from sqlalchemy.orm import Session

from app.core.entity_registry import registry
from app.models import Organization, Role
from tests.factories import add_member, make_customer, make_item, make_org, make_transaction, make_user

URL = "/api/organization"
WRITERS = {Role.OWNER, Role.ADMIN}


def member_of(db: Session, org: Organization, role: Role) -> dict[str, str]:
    user = make_user(db)
    add_member(db, org, user, role)
    return {"X-Dev-User-Email": user.email, "X-Organization-Id": str(org.id)}


# --- reading ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(Role))
def test_every_member_can_read_the_settings(client, db_session, role):
    org = make_org(db_session, "Readable", legal_name="Readable AB", city="Umeå", country_code="SE")
    response = client.get(URL, headers=member_of(db_session, org, role))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(org.id)
    assert (body["name"], body["legal_name"], body["city"], body["country_code"]) == ("Readable", "Readable AB", "Umeå", "SE")
    assert body["default_currency"] == "SEK"
    assert body["default_currency_locked"] is False and body["default_currency_lock_reason"] is None
    assert body["address_line2"] is None and body["vat_number"] is None and body["registration_number"] is None


def test_an_organization_without_currency_reads_as_unconfigured(client, db_session):
    org = make_org(db_session, "Fresh", default_currency=None)
    body = client.get(URL, headers=member_of(db_session, org, Role.VIEWER)).json()
    assert body["default_currency"] is None and body["default_currency_locked"] is False


def test_the_response_has_no_secrets_or_foreign_ids(client, db_session):
    org = make_org(db_session)
    body = client.get(URL, headers=member_of(db_session, org, Role.OWNER)).json()
    assert set(body) == {
        "id", "name", "legal_name", "default_currency", "default_currency_locked",
        "default_currency_lock_reason", "address_line1", "address_line2", "postal_code", "city",
        "country_code", "registration_number", "vat_number", "timezone", "today", "created_at", "updated_at",
        # The seller's contact and payment details (shown on documents; no secret among them).
        "phone", "email", "website", "bankgiro", "plusgiro", "iban", "bic", "payment_terms_days", "approved_for_f_tax", "document_language",
    }


# --- who may change ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(Role))
def test_only_owner_and_admin_can_change_the_settings(client, db_session, role):
    org = make_org(db_session, "Before")
    headers = member_of(db_session, org, role)

    response = client.patch(URL, json={"name": "After", "city": "Luleå"}, headers=headers)

    if role in WRITERS:
        assert response.status_code == 200, response.text
        assert (response.json()["name"], response.json()["city"]) == ("After", "Luleå")
    else:
        assert response.status_code == 403
        db_session.refresh(org)
        assert (org.name, org.city) == ("Before", None)  # nothing changed


def test_a_non_member_gets_404_not_403_and_changes_nothing(client, db_session):
    mine, theirs = make_org(db_session, "Mine"), make_org(db_session, "Theirs")
    headers = member_of(db_session, mine, Role.OWNER)
    aimed_at_theirs = {**headers, "X-Organization-Id": str(theirs.id)}

    assert client.get(URL, headers=aimed_at_theirs).status_code == 404
    assert client.patch(URL, json={"name": "Hijacked"}, headers=aimed_at_theirs).status_code == 404
    random = {**headers, "X-Organization-Id": str(uuid.uuid4())}
    assert client.patch(URL, json={"name": "Hijacked"}, headers=random).status_code == 404
    db_session.refresh(theirs)
    assert theirs.name == "Theirs"


def test_an_unauthenticated_request_is_refused(raw_client):
    assert raw_client.get(URL).status_code in (401, 403)
    assert raw_client.patch(URL, json={"name": "x"}).status_code in (401, 403)


def test_changing_one_organization_never_touches_another(client, db_session):
    a, b = make_org(db_session, "A", city="Umeå"), make_org(db_session, "B", city="Umeå")
    owner_a = member_of(db_session, a, Role.OWNER)

    client.patch(URL, json={"city": "Luleå", "legal_name": "A AB", "default_currency": "EUR"}, headers=owner_a)

    db_session.refresh(b)
    assert (b.city, b.legal_name, b.default_currency) == ("Umeå", None, "SEK")


def test_a_user_in_two_organizations_changes_only_the_selected_one(client, db_session):
    a, b = make_org(db_session, "A"), make_org(db_session, "B")
    user = make_user(db_session)
    add_member(db_session, a, user, Role.OWNER)
    add_member(db_session, b, user, Role.VIEWER)

    ok = client.patch(URL, json={"city": "X"}, headers={"X-Dev-User-Email": user.email, "X-Organization-Id": str(a.id)})
    refused = client.patch(URL, json={"city": "Y"}, headers={"X-Dev-User-Email": user.email, "X-Organization-Id": str(b.id)})

    assert ok.status_code == 200 and refused.status_code == 403
    db_session.refresh(a), db_session.refresh(b)
    assert (a.city, b.city) == ("X", None)


# --- validation: free text, only the SHAPE of codes ---------------------------------------------------------------


def patch(client, db_session, body, role=Role.OWNER, org=None):
    org = org or make_org(db_session, "Edit")
    return client.patch(URL, json=body, headers=member_of(db_session, org, role)), org


def test_profile_fields_are_saved_and_trimmed(client, db_session):
    response, org = patch(
        client, db_session,
        {
            "legal_name": "  Acme Hästterapi AB ", "address_line1": " Storgatan 1", "address_line2": "c/o Anna",
            "postal_code": "903 26", "city": "Umeå", "country_code": "SE",
            "registration_number": "556000-0001", "vat_number": "SE556000000101",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["legal_name"] == "Acme Hästterapi AB" and body["address_line1"] == "Storgatan 1"
    assert body["vat_number"] == "SE556000000101"
    db_session.refresh(org)
    assert (org.legal_name, org.postal_code, org.registration_number) == ("Acme Hästterapi AB", "903 26", "556000-0001")


def test_blank_and_null_clear_an_optional_field(client, db_session):
    org = make_org(db_session, city="Umeå", vat_number="SE1", address_line1="x", country_code="SE")
    response, _ = patch(client, db_session, {"city": "   ", "vat_number": None, "address_line1": "", "country_code": ""}, org=org)
    assert response.status_code == 200
    db_session.refresh(org)
    assert (org.city, org.vat_number, org.address_line1, org.country_code) == (None, None, None, None)


def test_an_omitted_field_is_left_alone(client, db_session):
    org = make_org(db_session, city="Umeå", vat_number="SE1")
    response, _ = patch(client, db_session, {"name": "Renamed"}, org=org)
    assert response.status_code == 200
    db_session.refresh(org)
    assert (org.name, org.city, org.vat_number) == ("Renamed", "Umeå", "SE1")


def test_codes_are_upper_cased_before_their_shape_is_checked(client, db_session):
    response, _ = patch(client, db_session, {"country_code": "se", "default_currency": "eur"})
    assert response.status_code == 200, response.text
    assert (response.json()["country_code"], response.json()["default_currency"]) == ("SE", "EUR")


@pytest.mark.parametrize("country", ["SWE", "S", "S1", "1A", "S E", "Å1"])
def test_a_country_code_must_be_two_letters(client, db_session, country):
    response, org = patch(client, db_session, {"country_code": country})
    assert response.status_code == 422
    db_session.refresh(org)
    assert org.country_code is None


@pytest.mark.parametrize("currency", ["EURO", "SE", "S3K", "12E", "€", "", None])
def test_a_currency_must_be_three_letters_and_can_not_be_cleared(client, db_session, currency):
    response, org = patch(client, db_session, {"default_currency": currency})
    assert response.status_code == 422
    db_session.refresh(org)
    assert org.default_currency == "SEK"


def test_there_is_no_jurisdiction_validation_of_identifiers(client, db_session):
    response, _ = patch(
        client, db_session,
        {"registration_number": "anything goes 123", "vat_number": "not-a-real-vat", "postal_code": "ZZ 9", "country_code": "XX"},
    )
    assert response.status_code == 200, response.text  # XX has the right shape; whether it is assigned is not our business


@pytest.mark.parametrize(
    "body",
    [
        {"name": None}, {"name": "   "}, {"name": "x" * 256},
        {"legal_name": "x" * 256}, {"address_line1": "x" * 256}, {"postal_code": "x" * 33},
        {"city": "x" * 129}, {"registration_number": "x" * 65}, {"vat_number": "x" * 65},
    ],
)
def test_limits_and_required_name(client, db_session, body):
    response, org = patch(client, db_session, body)
    assert response.status_code == 422
    db_session.refresh(org)
    assert org.name == "Edit"


@pytest.mark.parametrize("field", ["id", "organization_id", "created_at", "default_currency_locked", "role", "unknown"])
def test_unknown_and_server_owned_fields_are_rejected(client, db_session, field):
    response, _ = patch(client, db_session, {field: str(uuid.uuid4())})
    assert response.status_code == 422


def test_the_name_is_the_display_name_and_the_legal_name_is_separate(client, db_session):
    response, _ = patch(client, db_session, {"legal_name": "Fredrik Horse Therapy AB"})
    assert response.json()["name"] == "Edit" and response.json()["legal_name"] == "Fredrik Horse Therapy AB"


# --- the default currency ------------------------------------------------------------------------------------


def test_the_first_currency_can_be_set_even_when_prices_already_exist(client, db_session):
    """No currency was assumed before, so setting one is configuration, not a reinterpretation."""
    org = make_org(db_session, default_currency=None)
    make_item(db_session, org)
    make_transaction(db_session, org, currency=None)

    response, _ = patch(client, db_session, {"default_currency": "NOK"}, org=org)

    assert response.status_code == 200, response.text
    assert response.json()["default_currency"] == "NOK"
    # NOK is now fixed: prices exist.
    assert response.json()["default_currency_locked"] is True


def test_the_currency_can_change_while_nothing_has_a_price(client, db_session):
    response, org = patch(client, db_session, {"default_currency": "EUR"})
    assert response.status_code == 200
    assert response.json()["default_currency"] == "EUR" and response.json()["default_currency_locked"] is False


def test_an_item_locks_the_currency(client, db_session):
    org = make_org(db_session)
    make_item(db_session, org)

    response, _ = patch(client, db_session, {"default_currency": "EUR"}, org=org)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "currency_locked" and "items" in detail["message"]
    db_session.refresh(org)
    assert org.default_currency == "SEK"


def test_a_transaction_locks_the_currency_whatever_its_status(client, db_session):
    for status in ("draft", "completed", "cancelled"):
        org = make_org(db_session, f"With {status}")
        make_transaction(db_session, org, status=status)
        response, _ = patch(client, db_session, {"default_currency": "EUR"}, org=org)
        assert response.status_code == 409, status
        assert response.json()["detail"]["code"] == "currency_locked"
        assert "Transactions" in response.json()["detail"]["message"]


def test_a_lock_is_reported_in_the_settings_with_its_reason(client, db_session):
    org = make_org(db_session)
    make_item(db_session, org)
    body = client.get(URL, headers=member_of(db_session, org, Role.EMPLOYEE)).json()
    assert body["default_currency_locked"] is True
    assert "items" in body["default_currency_lock_reason"]


def test_resending_the_current_currency_is_not_a_change_and_is_never_refused(client, db_session):
    org = make_org(db_session)
    make_item(db_session, org)
    response, _ = patch(client, db_session, {"default_currency": "SEK", "city": "Umeå"}, org=org)
    assert response.status_code == 200 and response.json()["city"] == "Umeå"


def test_other_settings_stay_editable_while_the_currency_is_locked(client, db_session):
    org = make_org(db_session)
    make_transaction(db_session, org)
    response, _ = patch(client, db_session, {"name": "Still editable", "vat_number": "SE9"}, org=org)
    assert response.status_code == 200


def test_a_refused_currency_change_changes_nothing_else_in_the_request(client, db_session):
    org = make_org(db_session)
    make_item(db_session, org)
    response, _ = patch(client, db_session, {"city": "Luleå", "default_currency": "EUR"}, org=org)
    assert response.status_code == 409
    db_session.refresh(org)
    assert (org.city, org.default_currency) == (None, "SEK")


def test_another_organizations_prices_do_not_lock_this_one(client, db_session):
    quiet, busy = make_org(db_session, "Quiet"), make_org(db_session, "Busy")
    make_item(db_session, busy)
    make_transaction(db_session, busy)

    response, _ = patch(client, db_session, {"default_currency": "EUR"}, org=quiet)

    assert response.status_code == 200
    db_session.refresh(busy)
    assert busy.default_currency == "SEK"


def test_the_currency_unlocks_when_the_prices_are_gone(client, db_session):
    org = make_org(db_session)
    item = make_item(db_session, org)
    header = member_of(db_session, org, Role.OWNER)
    assert client.patch(URL, json={"default_currency": "EUR"}, headers=header).status_code == 409

    assert client.delete(f"/api/items/{item.id}", headers=header).status_code == 204

    assert client.patch(URL, json={"default_currency": "EUR"}, headers=header).status_code == 200


def test_a_module_can_lock_the_currency_through_the_registry_alone(client, db_session):
    """Core knows no module: a guard registered on the registry is enough to lock the currency."""
    org = make_org(db_session)
    seen: list[uuid.UUID] = []
    with registry.isolated():
        registry.add_currency_guard(lambda db, organization_id: seen.append(organization_id) or "A module says no.")
        response, _ = patch(client, db_session, {"default_currency": "EUR"}, org=org)
        assert response.status_code == 409
        assert "A module says no." in response.json()["detail"]["message"]
        assert seen and set(seen) == {org.id}  # asked about THIS organization only
    # ... and the guard left no trace.
    response, _ = patch(client, db_session, {"default_currency": "EUR"}, org=org)
    assert response.status_code == 200


def test_a_customer_does_not_lock_the_currency(client, db_session):
    org = make_org(db_session)
    make_customer(db_session, org)
    response, _ = patch(client, db_session, {"default_currency": "EUR"}, org=org)
    assert response.status_code == 200
