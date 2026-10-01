"""Horse behaviour inside a single organization. Cross-tenant isolation is covered by
test_tenant_isolation_contract.py; references by test_horse_references.py."""

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.equine.models import Horse
from tests.factories import make_customer, make_horse, make_org

THIS_YEAR = datetime.date.today().year


@pytest.fixture
def setup(db_session: Session, member):
    org, headers = member
    owner = make_customer(db_session, org, "Anna Andersson")
    stable = make_customer(db_session, org, "Umeå HK", email=None)
    return org, headers, owner, stable


def body(owner, **fields):
    return {"name": "Kalle", "owner_customer_id": str(owner.id), **fields}


def test_create_read_list_update_delete_roundtrip(client: TestClient, setup):
    _, headers, owner, stable = setup

    created = client.post(
        "/api/horses",
        json=body(
            owner,
            stable_customer_id=str(stable.id),
            birth_year=2015,
            sex="gelding",
            breed=" Swedish Warmblood ",
        ),
        headers=headers,
    )

    assert created.status_code == 201, created.text
    horse = created.json()
    assert horse["name"] == "Kalle"
    assert horse["birth_year"] == 2015
    assert horse["sex"] == "gelding"
    assert horse["breed"] == "Swedish Warmblood"  # trimmed
    assert horse["active"] is True
    assert horse["owner_customer_id"] == str(owner.id)
    assert horse["owner"] == {"id": str(owner.id), "name": "Anna Andersson", "active": True}
    assert horse["stable"] == {"id": str(stable.id), "name": "Umeå HK", "active": True}
    assert "organization_id" not in horse

    assert client.get(f"/api/horses/{horse['id']}", headers=headers).json() == horse
    assert [h["id"] for h in client.get("/api/horses", headers=headers).json()] == [horse["id"]]

    updated = client.patch(
        f"/api/horses/{horse['id']}", json={"name": "Kalle II", "sex": "stallion"}, headers=headers
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Kalle II"
    assert updated.json()["sex"] == "stallion"
    assert updated.json()["breed"] == "Swedish Warmblood"  # untouched
    assert updated.json()["owner"] == horse["owner"]  # untouched

    assert client.delete(f"/api/horses/{horse['id']}", headers=headers).status_code == 204
    assert client.get(f"/api/horses/{horse['id']}", headers=headers).status_code == 404


def test_only_name_and_owner_are_required(client: TestClient, setup):
    _, headers, owner, _ = setup

    created = client.post("/api/horses", json=body(owner), headers=headers)

    assert created.status_code == 201
    horse = created.json()
    assert horse["stable_customer_id"] is None and horse["stable"] is None
    assert horse["birth_year"] is None and horse["sex"] is None and horse["breed"] is None


@pytest.mark.parametrize("missing", ["name", "owner_customer_id"])
def test_create_requires_name_and_owner(client: TestClient, setup, missing):
    _, headers, owner, _ = setup
    payload = {k: v for k, v in body(owner).items() if k != missing}
    assert client.post("/api/horses", json=payload, headers=headers).status_code == 422


# --- sex: a small explicit set, no lookup table -----------------------------------------


@pytest.mark.parametrize("sex", ["mare", "stallion", "gelding", None])
def test_valid_sex_values(client: TestClient, setup, sex):
    _, headers, owner, _ = setup
    response = client.post("/api/horses", json=body(owner, sex=sex), headers=headers)
    assert response.status_code == 201
    assert response.json()["sex"] == sex


@pytest.mark.parametrize("sex", ["Mare", "MARE", "colt", "filly", "male", "m", "", " ", 1, True])
def test_invalid_sex_values_are_rejected(client: TestClient, setup, sex):
    _, headers, owner, _ = setup
    assert client.post("/api/horses", json=body(owner, sex=sex), headers=headers).status_code == 422


# --- birth_year: a plausible integer, no age logic -------------------------------------


@pytest.mark.parametrize("year", [1900, 1999, 2015, THIS_YEAR, None])
def test_valid_birth_years(client: TestClient, setup, year):
    _, headers, owner, _ = setup
    response = client.post("/api/horses", json=body(owner, birth_year=year), headers=headers)
    assert response.status_code == 201
    assert response.json()["birth_year"] == year


@pytest.mark.parametrize(
    "year",
    [1899, 0, -2015, THIS_YEAR + 1, 2100, 2101, 99999, "2015", 2015.0, 2015.5, True, [], {}],
    ids=repr,
)
def test_invalid_birth_years_are_rejected(client: TestClient, setup, year):
    _, headers, owner, _ = setup
    assert (
        client.post("/api/horses", json=body(owner, birth_year=year), headers=headers).status_code
        == 422
    )


def test_birth_year_is_not_turned_into_an_age(client: TestClient, setup):
    _, headers, owner, _ = setup
    horse = client.post("/api/horses", json=body(owner, birth_year=2010), headers=headers).json()
    assert "age" not in horse


# --- breed: free text ---------------------------------------------------------------------


@pytest.mark.parametrize("breed", ["Swedish Warmblood", "Svenskt halvblod ÅÄÖ", "x", "b" * 100])
def test_breed_is_free_text(client: TestClient, setup, breed):
    _, headers, owner, _ = setup
    response = client.post("/api/horses", json=body(owner, breed=breed), headers=headers)
    assert response.status_code == 201
    assert response.json()["breed"] == breed


@pytest.mark.parametrize("breed", ["", "   ", "b" * 101])
def test_invalid_breed_is_rejected(client: TestClient, setup, breed):
    _, headers, owner, _ = setup
    assert client.post("/api/horses", json=body(owner, breed=breed), headers=headers).status_code == 422


# --- other validation ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "override",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 256},
        {"owner_customer_id": None},
        {"owner_customer_id": "not-a-uuid"},
        {"notes": "deferred"},  # notes are not part of the model yet
        {"billing_customer_id": "00000000-0000-4000-8000-0000000000b2"},  # never on a horse
        {"billing": "x"},
        {"organization_id": "00000000-0000-4000-8000-0000000000b2"},
        {"owner": {"name": "nested"}},
        {"unexpected": 1},
    ],
)
def test_create_validation(client: TestClient, setup, override):
    _, headers, owner, _ = setup
    assert client.post("/api/horses", json={**body(owner), **override}, headers=headers).status_code == 422


# --- update ----------------------------------------------------------------------------------


def test_nullable_attributes_can_be_cleared(client: TestClient, setup):
    _, headers, owner, stable = setup
    horse = client.post(
        "/api/horses",
        json=body(owner, stable_customer_id=str(stable.id), birth_year=2015, sex="mare", breed="x"),
        headers=headers,
    ).json()

    cleared = client.patch(
        f"/api/horses/{horse['id']}",
        json={"stable_customer_id": None, "birth_year": None, "sex": None, "breed": None},
        headers=headers,
    )

    assert cleared.status_code == 200
    result = cleared.json()
    assert result["stable_customer_id"] is None and result["stable"] is None
    assert result["birth_year"] is None and result["sex"] is None and result["breed"] is None
    assert result["owner"]["id"] == str(owner.id)


@pytest.mark.parametrize(
    "patch",
    [
        {"name": None},
        {"owner_customer_id": None},
        {"active": None},
        {"name": ""},
        {"sex": "colt"},
        {"birth_year": THIS_YEAR + 1},
        {"birth_year": "2015"},
        {"breed": ""},
        {"notes": "x"},
        {"billing_customer_id": "00000000-0000-4000-8000-0000000000b2"},
    ],
)
def test_update_validation(client: TestClient, db_session: Session, setup, patch):
    org, headers, owner, _ = setup
    horse = make_horse(db_session, org, owner=owner)

    assert client.patch(f"/api/horses/{horse.id}", json=patch, headers=headers).status_code == 422


def test_empty_patch_changes_nothing(client: TestClient, db_session: Session, setup):
    org, headers, owner, _ = setup
    horse = make_horse(db_session, org, owner=owner)
    before = client.get(f"/api/horses/{horse.id}", headers=headers).json()

    response = client.patch(f"/api/horses/{horse.id}", json={}, headers=headers)

    assert response.status_code == 200 and response.json() == before


# --- list, filters, nested summaries ------------------------------------------------------------


def test_list_filters_and_ordering(client: TestClient, db_session: Session, setup):
    org, headers, anna, hk = setup
    erik = make_customer(db_session, org, "Erik Svensson", email=None)
    make_horse(db_session, org, "Kalle", owner=anna, stable=hk)
    make_horse(db_session, org, "bella", owner=anna)
    make_horse(db_session, org, "Storm", owner=erik, stable=hk, active=False)

    def names(**params):
        response = client.get("/api/horses", params=params, headers=headers)
        assert response.status_code == 200
        return [h["name"] for h in response.json()]

    assert names() == ["bella", "Kalle", "Storm"]  # by name, case-insensitive
    assert names(owner_customer_id=str(anna.id)) == ["bella", "Kalle"]
    assert names(owner_customer_id=str(erik.id)) == ["Storm"]
    assert names(stable_customer_id=str(hk.id)) == ["Kalle", "Storm"]
    assert names(active="false") == ["Storm"]
    assert names(owner_customer_id=str(anna.id), stable_customer_id=str(hk.id)) == ["Kalle"]
    assert names(q="LL") == ["bella", "Kalle"]
    assert names(q="%") == []
    assert client.get("/api/horses", params={"owner_customer_id": "x"}, headers=headers).status_code == 422
    assert client.get("/api/horses", params={"limit": 201}, headers=headers).status_code == 422


def test_nested_summary_follows_the_customer(client: TestClient, db_session: Session, setup):
    org, headers, anna, _ = setup
    horse = make_horse(db_session, org, owner=anna)

    client.patch(f"/api/customers/{anna.id}", json={"name": "Anna Svensson"}, headers=headers)

    assert client.get(f"/api/horses/{horse.id}", headers=headers).json()["owner"]["name"] == "Anna Svensson"


def test_malformed_id_is_422(client: TestClient, member):
    _, headers = member
    assert client.get("/api/horses/not-a-uuid", headers=headers).status_code == 422


# --- the database enforces the same limits ---------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [{"sex": "colt"}, {"birth_year": 1899}, {"birth_year": 2101}],
    ids=["unknown sex", "year too early", "year too late"],
)
def test_database_check_constraints(db_session: Session, fields):
    org = make_org(db_session)
    with pytest.raises(IntegrityError), db_session.begin_nested():
        make_horse(db_session, org, **fields)


def test_database_requires_name_and_owner(db_session: Session):
    org = make_org(db_session)
    owner = make_customer(db_session, org)
    for fields in ({"name": None, "owner_customer_id": owner.id}, {"name": "x", "owner_customer_id": None}):
        with pytest.raises(IntegrityError), db_session.begin_nested():
            db_session.add(Horse(organization_id=org.id, **fields))
            db_session.flush()
