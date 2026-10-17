"""Services on an appointment, and the draft order made from it (and brought up to date): who may, what the order holds, what an update touches
and what it leaves alone, and that nothing crosses organizations."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import ItemType, Role
from tests.factories import add_member, make_customer, make_horse, make_item, make_org, make_user

MORNING = {"starts_at": "2026-10-19T08:00:00+00:00", "ends_at": "2026-10-19T10:00:00+00:00"}
HORSE = lambda horse: {"subject_type": "horse", "subject_id": str(horse.id)}  # noqa: E731


IDS: dict[str, str] = {}  # the user id behind each set of headers, by email


def _person(db: Session, org, role: Role, name: str):
    user = make_user(db, name=name)
    add_member(db, org, user, role)
    IDS[user.email] = str(user.id)
    return user, {"X-Dev-User-Email": user.email}


def _prac(headers) -> dict:
    """The practitioner a booking needs: the person whose headers these are (every booking is for a real person)."""
    return {"practitioner_user_id": IDS[headers["X-Dev-User-Email"]]}


@pytest.fixture
def world(db_session: Session):
    org = make_org(db_session)
    umea = make_customer(db_session, org, "Umeå HK")
    kalle = make_horse(db_session, org, "Kalle", owner=umea)
    storm = make_horse(db_session, org, "Storm", owner=umea)
    massage = make_item(db_session, org, "Massage", price_ex_vat="850.00")
    stretch = make_item(db_session, org, "Stretching", price_ex_vat="400.00")
    product = make_item(db_session, org, "Liniment", type=ItemType.PRODUCT, unit="pcs", price_ex_vat="120.00")
    people = {name: _person(db_session, org, role, name.title()) for name, role in (("owner", Role.OWNER), ("employee", Role.EMPLOYEE), ("viewer", Role.VIEWER))}
    return org, umea, kalle, storm, massage, stretch, product, people


def _book(client: TestClient, headers, **body) -> dict:
    response = client.post("/api/appointments", json={"title": "Stable day", **MORNING, **_prac(headers), **body}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _etag(appointment: dict) -> dict:
    return {"If-Match": f'"{appointment["version"]}"'}


def _order(client: TestClient, headers, appointment: dict):
    return client.post(f"/api/appointments/{appointment['id']}/order", headers={**headers, **_etag(appointment)})


def _update(client: TestClient, headers, appointment: dict):
    return client.post(f"/api/appointments/{appointment['id']}/order/update", headers={**headers, **_etag(appointment)})


def _lines(client: TestClient, headers, order_id: str) -> list[dict]:
    response = client.get(f"/api/transactions/{order_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["lines"]


def _patch(client: TestClient, headers, appointment: dict, **body) -> dict:
    response = client.patch(f"/api/appointments/{appointment['id']}", json=body, headers={**headers, **_etag(appointment)})
    assert response.status_code == 200, response.text
    return response.json()


# --- services on the appointment -------------------------------------------------------------------------------------------------------


def test_an_appointment_names_its_services_by_name_and_only_catalog_services_are_accepted(client: TestClient, world):
    _, _, _, _, massage, stretch, product, people = world
    headers = people["owner"][1]
    booked = _book(client, headers, items=[str(stretch.id), str(massage.id)])
    assert [i["name"] for i in booked["items"]] == ["Massage", "Stretching"]
    assert [i["name"] for i in client.get(f"/api/appointments/{booked['id']}", headers=headers).json()["items"]] == ["Massage", "Stretching"]

    fewer = _patch(client, headers, booked, items=[str(massage.id)])
    assert [i["name"] for i in fewer["items"]] == ["Massage"] and fewer["version"] == booked["version"] + 1
    assert _patch(client, headers, fewer, items=[str(massage.id)])["version"] == fewer["version"]  # the same again changes nothing
    assert _patch(client, headers, fewer, title="Renamed")["items"][0]["name"] == "Massage"  # a save without items leaves them
    assert client.post("/api/appointments", json={"title": "x", **MORNING, **_prac(headers), "items": [str(product.id)]}, headers=headers).status_code == 422  # a product is not a service
    assert client.post("/api/appointments", json={"title": "x", **MORNING, **_prac(headers), "items": [str(massage.id), str(massage.id)]}, headers=headers).status_code == 422


def test_another_organizations_service_cannot_be_named(client: TestClient, db_session: Session, world):
    people = world[7]
    headers = people["owner"][1]
    other = make_org(db_session, "Other")
    theirs = make_item(db_session, other, "Their massage")
    assert client.post("/api/appointments", json={"title": "x", **MORNING, **_prac(headers), "items": [str(theirs.id)]}, headers=headers).status_code == 422
    mine = _book(client, headers)
    assert client.patch(f"/api/appointments/{mine['id']}", json={"items": [str(theirs.id)]}, headers={**headers, **_etag(mine)}).status_code == 422
    assert client.get(f"/api/appointments/{mine['id']}", headers=headers).json()["items"] == []


def test_the_history_names_the_services(client: TestClient, db_session: Session, world):
    from sqlalchemy import select

    from app.models import AuditEvent

    _, _, _, _, massage, stretch, _, people = world
    headers = people["owner"][1]
    booked = _book(client, headers, items=[str(massage.id)])
    _patch(client, headers, booked, items=[str(massage.id), str(stretch.id)])
    events = list(db_session.scalars(select(AuditEvent).where(AuditEvent.entity_type == "appointment").order_by(AuditEvent.id)))
    assert [e.changes["services"] for e in events] == [{"from": None, "to": "Massage"}, {"from": "Massage", "to": "Massage, Stretching"}]


# --- making the order -------------------------------------------------------------------------------------------------------------------


def test_the_order_has_a_service_line_for_each_service_and_each_horse_priced_from_the_catalog(client: TestClient, world):
    _, umea, kalle, storm, massage, stretch, _, people = world
    employee, employee_headers = people["employee"]
    headers = people["owner"][1]
    booked = _book(client, headers, customer_id=str(umea.id), practitioner_user_id=str(employee.id), subjects=[HORSE(kalle), HORSE(storm)], items=[str(massage.id), str(stretch.id)])
    made = _order(client, employee_headers, booked)
    assert made.status_code == 201, made.text
    appointment = made.json()
    assert appointment["order_id"] and appointment["order_number"] >= 1001 and appointment["order_status"] == "draft"
    assert appointment["version"] == booked["version"] + 1

    order = client.get(f"/api/transactions/{appointment['order_id']}", headers=headers).json()
    assert (order["status"], order["billing_customer_id"], order["transaction_date"]) == ("draft", str(umea.id), "2026-10-19")
    lines = order["lines"]
    assert len(lines) == 4  # 2 services x 2 horses
    assert {(l["description"], l["subject_id"]) for l in lines} == {(d, str(h.id)) for d in ("Massage", "Stretching") for h in (kalle, storm)}
    assert {l["kind"] for l in lines} == {"service"} and {l["subject_type"] for l in lines} == {"horse"}
    assert {l["performed_by"] for l in lines} == {str(employee.id)} and {l["quantity"] for l in lines} == {"1.000"}
    assert sorted({l["unit_price_ex_vat"] for l in lines}) == ["400.00", "850.00"]  # the catalog's prices
    assert all(l["performed_at"].startswith("2026-10-19T08:00:00") for l in lines)


def test_an_appointment_about_no_horse_bills_the_service_for_the_customer(client: TestClient, world):
    _, umea, _, _, massage, _, _, people = world
    headers = people["owner"][1]
    appointment = _order(client, headers, _book(client, headers, customer_id=str(umea.id), items=[str(massage.id)])).json()
    lines = _lines(client, headers, appointment["order_id"])
    assert [(l["subject_type"], l["subject_id"]) for l in lines] == [("customer", str(umea.id))]


def test_the_order_needs_a_customer_and_a_service_and_a_live_appointment(client: TestClient, world):
    _, umea, kalle, _, massage, _, _, people = world
    headers = people["owner"][1]
    assert _order(client, headers, _book(client, headers, items=[str(massage.id)])).status_code == 422  # no customer to bill
    assert _order(client, headers, _book(client, headers, customer_id=str(umea.id))).status_code == 422  # nothing to bill
    cancelled = _book(client, headers, customer_id=str(umea.id), items=[str(massage.id)])
    cancelled = _patch(client, headers, cancelled, status="cancelled")
    assert _order(client, headers, cancelled).status_code == 409


def test_a_second_order_is_refused_and_a_deleted_one_can_be_made_again(client: TestClient, world):
    _, umea, _, _, massage, _, _, people = world
    headers = people["owner"][1]
    first = _order(client, headers, _book(client, headers, customer_id=str(umea.id), items=[str(massage.id)])).json()
    again = _order(client, headers, first)
    assert (again.status_code, again.json()["detail"]["code"]) == (409, "order_exists")

    order = client.get(f"/api/transactions/{first['order_id']}", headers=headers).json()
    assert client.delete(f"/api/transactions/{first['order_id']}", headers={**headers, "If-Match": f'"{order["version"]}"'}).status_code == 204
    gone = client.get(f"/api/appointments/{first['id']}", headers=headers).json()
    assert gone["order_id"] == first["order_id"] and gone["order_number"] is None  # the link is there, the order is not
    assert client.post(f"/api/appointments/{first['id']}/order/update", headers={**headers, **_etag(gone)}).status_code == 409
    remade = _order(client, headers, gone)
    assert remade.status_code == 201 and remade.json()["order_id"] != first["order_id"]


def test_a_stale_view_and_a_viewer_cannot_make_an_order(client: TestClient, world):
    _, umea, _, _, massage, _, _, people = world
    headers = people["owner"][1]
    booked = _book(client, headers, customer_id=str(umea.id), items=[str(massage.id)])
    assert _order(client, people["viewer"][1], booked).status_code == 403
    assert client.post(f"/api/appointments/{booked['id']}/order", headers=headers).status_code == 428  # without the version it was drawn from
    _patch(client, headers, booked, title="Changed meanwhile")
    stale = _order(client, headers, booked)  # the old version
    assert (stale.status_code, stale.json()["detail"]["code"]) == (409, "stale_record")
    assert client.get(f"/api/appointments/{booked['id']}", headers=headers).json()["order_id"] is None  # no order was made


def test_another_organizations_appointment_cannot_make_an_order(client: TestClient, db_session: Session, world):
    people = world[7]
    other = make_org(db_session, "Other")
    their_customer = make_customer(db_session, other, "Umeå HK")
    their_item = make_item(db_session, other, "Massage")
    _, their_headers = _person(db_session, other, Role.OWNER, "Their Owner")
    theirs = _book(client, their_headers, customer_id=str(their_customer.id), items=[str(their_item.id)])
    assert _order(client, people["owner"][1], theirs).status_code == 404
    assert _update(client, people["owner"][1], theirs).status_code == 404
    assert client.get(f"/api/appointments/{theirs['id']}", headers=their_headers).json()["order_id"] is None


# --- bringing the order up to date --------------------------------------------------------------------------------------------------------


def _made(client: TestClient, world, **more):
    _, umea, kalle, storm, massage, stretch, _, people = world
    headers = people["owner"][1]
    booked = _book(client, headers, customer_id=str(umea.id), subjects=[HORSE(kalle)], items=[str(massage.id)], **more)
    return headers, _order(client, headers, booked).json()


def test_an_added_horse_and_service_become_lines_and_removed_ones_go(client: TestClient, world):
    _, _, kalle, storm, massage, stretch, _, people = world
    headers, appointment = _made(client, world)
    assert len(_lines(client, headers, appointment["order_id"])) == 1

    appointment = _patch(client, headers, appointment, subjects=[HORSE(kalle), HORSE(storm)], items=[str(massage.id), str(stretch.id)])
    done = _update(client, headers, appointment)
    assert done.status_code == 200, done.text
    assert (done.json()["added"], done.json()["removed"], done.json()["kept"]) == (3, 0, 0)
    assert len(_lines(client, headers, appointment["order_id"])) == 4
    assert _update(client, headers, appointment).json()["added"] == 0  # nothing new: nothing added

    appointment = _patch(client, headers, appointment, subjects=[HORSE(kalle)], items=[str(massage.id)])
    removed = _update(client, headers, appointment).json()
    assert (removed["added"], removed["removed"], removed["kept"]) == (0, 3, 0)
    assert [(l["description"], l["subject_id"]) for l in _lines(client, headers, appointment["order_id"])] == [("Massage", str(kalle.id))]


def test_an_update_keeps_what_a_person_edited_or_added_by_hand(client: TestClient, world):
    _, _, kalle, storm, massage, stretch, _, people = world
    headers, appointment = _made(client, world, )
    order_id = appointment["order_id"]
    appointment = _patch(client, headers, appointment, subjects=[HORSE(kalle), HORSE(storm)])
    _update(client, headers, appointment)
    lines = {l["subject_id"]: l for l in _lines(client, headers, order_id)}
    # the person edits Storm's line (a second session) and types an extra line of their own
    edited = client.patch(f"/api/transactions/{order_id}/lines/{lines[str(storm.id)]['id']}", json={"quantity": "2"}, headers={**headers, "If-Match": f'"{lines[str(storm.id)]["version"]}"'})
    assert edited.status_code == 200, edited.text
    own = client.post(f"/api/transactions/{order_id}/lines", json={"description": "Travel", "unit": "km", "quantity": "30", "unit_price_ex_vat": "5.00", "vat_rate": "25.00"}, headers=headers)
    assert own.status_code == 201, own.text

    appointment = _patch(client, headers, appointment, subjects=[HORSE(kalle)])  # Storm is taken off the booking
    result = _update(client, headers, appointment).json()
    assert (result["added"], result["removed"], result["kept"]) == (0, 0, 1)  # Storm's line was edited: kept, and counted
    remaining = _lines(client, headers, order_id)
    assert sorted(l["description"] for l in remaining) == ["Massage", "Massage", "Travel"]
    assert {l["quantity"] for l in remaining if l["subject_id"] == str(storm.id)} == {"2.000"}


def test_a_completed_order_is_not_updated(client: TestClient, world):
    _, _, kalle, storm, massage, _, _, _ = world
    headers, appointment = _made(client, world)
    order = client.get(f"/api/transactions/{appointment['order_id']}", headers=headers).json()
    done = client.post(f"/api/transactions/{appointment['order_id']}/complete", headers={**headers, "If-Match": f'"{order["version"]}"'})
    assert done.status_code == 200, done.text
    appointment = _patch(client, headers, appointment, subjects=[HORSE(kalle), HORSE(storm)])
    refused = _update(client, headers, appointment)
    assert refused.status_code == 409
    assert len(_lines(client, headers, appointment["order_id"])) == 1  # untouched
    assert client.get(f"/api/appointments/{appointment['id']}", headers=headers).json()["order_status"] == "completed"
