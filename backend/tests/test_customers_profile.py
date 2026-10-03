"""The customer billing profile: address, country code, registration and VAT number.

All optional, all free text. Only the shape of the country code (two capital letters) is
checked; there is no jurisdiction-specific validation. Isolation of these fields is covered
here as well as in the generic contract (test_tenant_isolation_contract.py).
"""

import uuid

import pytest
from sqlalchemy.orm import Session

from tests.factories import make_customer, make_org

PROFILE = {
    "address_line1": "Ridvägen 2",
    "address_line2": "Box 17",
    "postal_code": "903 30",
    "city": "Umeå",
    "country_code": "SE",
    "registration_number": "802000-0001",
    "vat_number": "SE802000000101",
}
BASE = {"customer_type": "company", "name": "Umeå HK"}


def test_a_customer_without_any_profile_reads_nulls(client, member):
    _, headers = member
    body = client.post("/api/customers", json=BASE, headers=headers).json()
    assert all(body[name] is None for name in PROFILE)


def test_the_profile_roundtrips_through_create_read_list_and_update(client, member):
    _, headers = member
    created = client.post("/api/customers", json={**BASE, **PROFILE}, headers=headers)
    assert created.status_code == 201, created.text
    body = created.json()
    assert {name: body[name] for name in PROFILE} == PROFILE

    assert client.get(f"/api/customers/{body['id']}", headers=headers).json() == body
    assert client.get("/api/customers", headers=headers).json() == [body]

    updated = client.patch(f"/api/customers/{body['id']}", json={"city": "Luleå", "vat_number": "SE1"}, headers=headers).json()
    assert (updated["city"], updated["vat_number"]) == ("Luleå", "SE1")
    assert updated["address_line1"] == PROFILE["address_line1"]  # untouched fields kept


def test_profile_text_is_trimmed_and_blank_means_not_set(client, member):
    _, headers = member
    body = client.post(
        "/api/customers",
        json={**BASE, "address_line1": "  Ridvägen 2  ", "address_line2": "   ", "city": "", "vat_number": None},
        headers=headers,
    ).json()
    assert (body["address_line1"], body["address_line2"], body["city"], body["vat_number"]) == ("Ridvägen 2", None, None, None)


def test_a_profile_field_is_cleared_with_null_or_blank(client, member):
    _, headers = member
    created = client.post("/api/customers", json={**BASE, **PROFILE}, headers=headers).json()
    cleared = client.patch(
        f"/api/customers/{created['id']}", json={"address_line2": None, "city": "  ", "country_code": ""}, headers=headers
    ).json()
    assert (cleared["address_line2"], cleared["city"], cleared["country_code"]) == (None, None, None)
    assert cleared["address_line1"] == PROFILE["address_line1"]


def test_the_country_code_is_upper_cased_and_checked_for_shape_only(client, member):
    _, headers = member
    assert client.post("/api/customers", json={**BASE, "country_code": "se"}, headers=headers).json()["country_code"] == "SE"
    # XX is well-formed, so it is accepted: whether a code is assigned is not the platform's business.
    assert client.post("/api/customers", json={**BASE, "country_code": "XX"}, headers=headers).status_code == 201
    for bad in ("SWE", "S", "S1", "1S", "S E", "Å1"):
        response = client.post("/api/customers", json={**BASE, "country_code": bad}, headers=headers)
        assert response.status_code == 422, bad


def test_identifiers_are_free_text(client, member):
    _, headers = member
    odd = {"registration_number": "anything / goes #1", "vat_number": "not a vat number", "postal_code": "ZZ-9 9ZZ"}
    assert client.post("/api/customers", json={**BASE, **odd}, headers=headers).status_code == 201


@pytest.mark.parametrize(
    "field, limit",
    [("address_line1", 255), ("address_line2", 255), ("postal_code", 32), ("city", 128), ("registration_number", 64), ("vat_number", 64)],
)
def test_lengths_are_limited(client, member, field, limit):
    _, headers = member
    assert client.post("/api/customers", json={**BASE, field: "x" * limit}, headers=headers).status_code == 201
    assert client.post("/api/customers", json={**BASE, field: "x" * (limit + 1)}, headers=headers).status_code == 422


def test_existing_rules_still_apply(client, member):
    _, headers = member
    assert client.post("/api/customers", json={"customer_type": "company"}, headers=headers).status_code == 422  # name required
    assert client.post("/api/customers", json={**BASE, "organization_id": str(uuid.uuid4())}, headers=headers).status_code == 422
    assert client.post("/api/customers", json={**BASE, "mystery": "x"}, headers=headers).status_code == 422
    created = client.post("/api/customers", json=BASE, headers=headers).json()
    assert client.patch(f"/api/customers/{created['id']}", json={"name": None}, headers=headers).status_code == 422


def test_the_database_checks_the_country_code_shape_too(db_session: Session):
    org = make_org(db_session)
    for bad in ("se", "SWE", "S1"):
        with pytest.raises(Exception):
            with db_session.begin_nested():
                make_customer(db_session, org, country_code=bad)
    assert make_customer(db_session, org, country_code="SE").country_code == "SE"
    assert make_customer(db_session, org, name="No country").country_code is None


# --- tenant isolation ------------------------------------------------------------------------------------------------


def test_one_organizations_profile_data_is_invisible_and_untouchable_from_another(client, db_session: Session, member):
    mine, headers = member
    theirs = make_org(db_session, "Theirs")
    foreign = make_customer(db_session, theirs, "Anna Andersson", **PROFILE)
    own = make_customer(db_session, mine, "Anna Andersson")  # same name, different tenant

    listing = client.get("/api/customers", headers=headers).json()
    assert [row["id"] for row in listing] == [str(own.id)]
    assert listing[0]["city"] is None and listing[0]["vat_number"] is None  # no leakage into mine
    assert client.get(f"/api/customers/{foreign.id}", headers=headers).status_code == 404
    assert client.patch(f"/api/customers/{foreign.id}", json={"city": "Hijacked", "vat_number": "X"}, headers=headers).status_code == 404
    # ... and searching their data finds nothing
    assert client.get("/api/customers", params={"q": "Ridvägen"}, headers=headers).json() == []

    db_session.refresh(foreign)
    assert (foreign.city, foreign.vat_number) == ("Umeå", PROFILE["vat_number"])


def test_updating_a_profile_never_changes_the_organization(client, db_session: Session, member):
    org, headers = member
    own = make_customer(db_session, org)
    client.patch(f"/api/customers/{own.id}", json=PROFILE, headers=headers)
    db_session.refresh(org)
    assert org.city is None and org.vat_number is None  # the seller's profile is a different record
