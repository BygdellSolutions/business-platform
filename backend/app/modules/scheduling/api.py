"""Appointments: the organization's calendar.

Everyone in the organization may read it; whoever may write business records (owner, admin, accountant, employee) may book, move
and cancel. A viewer only looks. The practitioner of an appointment is a member who does the work (owner, admin or employee);
leaving it empty books the time for nobody in particular. An appointment can be about several records (a stable books one long
session for many horses) and for several catalog services; from those a DRAFT order can be made (and brought up to date after the
booking changes): Scheduling describes what to bill and Sales builds it, through the registry. Times travel with their offset and are
stored as instants: the browser shows them in the organization's time zone.

A change needs the version the caller last saw (If-Match, 409 `stale_record` otherwise), like other records several people edit.
"""

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit, subjects
from app.core.authz import record_writer
from app.core.booking_orders import APPOINTMENT_SOURCE, BookingLine, BookingOrder
from app.core.db import get_db
from app.core.entity_registry import registry
from app.core.query import commit_and_refresh, delete_or_409
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import create_scoped, get_scoped_or_404, reference_error, resolve_reference, scoped_select
from app.core.versioning import ensure_current
from app.models import Customer, Item, OrganizationUser, Role, User
from app.modules.scheduling.models import CANCELLED, Appointment, AppointmentItem, AppointmentSubject
from app.modules.scheduling.schemas import AppointmentCreate, AppointmentRead, AppointmentUpdate, ItemRead, OrderUpdateResult, SubjectRead, check_span

router = APIRouter(prefix="/api/appointments", tags=["appointments"])

PRACTITIONER_ROLES = (Role.OWNER, Role.ADMIN, Role.EMPLOYEE)
# One request shows at most a month view with its leading and trailing weeks; more is a mistake (or a way to ask for everything).
LONGEST_WINDOW = timedelta(days=62)
MAX_ROWS = 1000
AUDITED = ("title", "starts_at", "ends_at", "location", "status", "practitioner_user_id", "customer_id")
Pair = tuple[str, uuid.UUID]
# With no record named, the service is performed for the customer (a registered service subject too).
CUSTOMER_SUBJECT = "customer"


def _practitioner(db: Session, ctx: TenantContext, user_id: uuid.UUID) -> User:
    """A member of THIS organization who does the work; anyone else (or a made-up id) is refused alike."""
    member = db.scalar(
        select(User)
        .join(OrganizationUser, OrganizationUser.user_id == User.id)
        .where(OrganizationUser.organization_id == ctx.organization_id, OrganizationUser.role.in_([role.value for role in PRACTITIONER_ROLES]), User.id == user_id)
    )
    if member is None:
        reference_error("practitioner_user_id", "Choose a member of this organization", "reference.not_found")
    return member


def _links(db: Session, ctx: TenantContext, appointment_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[AppointmentSubject]]:
    found: dict[uuid.UUID, list[AppointmentSubject]] = {}
    if appointment_ids:
        rows = db.scalars(scoped_select(AppointmentSubject, ctx).where(AppointmentSubject.appointment_id.in_(appointment_ids)))
        for row in rows:
            found.setdefault(row.appointment_id, []).append(row)
    return found


def _item_links(db: Session, ctx: TenantContext, appointment_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[uuid.UUID]]:
    found: dict[uuid.UUID, list[uuid.UUID]] = {}
    if appointment_ids:
        for row in db.scalars(scoped_select(AppointmentItem, ctx).where(AppointmentItem.appointment_id.in_(appointment_ids))):
            found.setdefault(row.appointment_id, []).append(row.item_id)
    return found


def _ordered(links: list[AppointmentSubject], labels: dict[Pair, str]) -> list[SubjectRead]:
    """The subjects by name (so the same horses always read in the same order)."""
    reads = [SubjectRead(subject_type=l.subject_type, subject_id=l.subject_id, label=labels.get((l.subject_type, l.subject_id))) for l in links]
    return sorted(reads, key=lambda s: ((s.label or "").lower(), str(s.subject_id)))


def _asked(name: str, db: Session, ctx: TenantContext, ids: set[uuid.UUID]) -> dict:
    """What the orders module answers about orders of THIS organization (nothing when it is not there)."""
    merged: dict = {}
    for answer in registry.call_hooks(name, db, ctx.organization_id, ids) if ids else []:
        merged.update(answer)
    return merged


def _reads(db: Session, ctx: TenantContext, rows: list[Appointment]) -> list[AppointmentRead]:
    """The appointments with the names of who, which customer and what they are about (looked up in this organization only)."""
    people = dict(db.execute(select(User.id, User.name).where(User.id.in_({r.practitioner_user_id for r in rows if r.practitioner_user_id}))).all()) if rows else {}
    customer_ids = {r.customer_id for r in rows if r.customer_id}
    customers = (
        dict(db.execute(select(Customer.id, Customer.name).where(Customer.organization_id == ctx.organization_id, Customer.id.in_(customer_ids))).all())
        if customer_ids
        else {}
    )
    links = _links(db, ctx, [r.id for r in rows])
    labels = subjects.subject_labels(db, ctx.organization_id, {(l.subject_type, l.subject_id) for group in links.values() for l in group})
    item_links = _item_links(db, ctx, [r.id for r in rows])
    item_ids = {item_id for group in item_links.values() for item_id in group}
    item_names = dict(db.execute(select(Item.id, Item.name).where(Item.organization_id == ctx.organization_id, Item.id.in_(item_ids))).all()) if item_ids else {}
    order_ids = {r.order_id for r in rows if r.order_id}
    numbers = _asked("order.numbers", db, ctx, order_ids)
    statuses = _asked("order.statuses", db, ctx, order_ids)
    return [
        AppointmentRead(
            **{field: getattr(r, field) for field in AppointmentRead.model_fields if hasattr(r, field)},
            practitioner_name=people.get(r.practitioner_user_id),
            customer_name=customers.get(r.customer_id),
            subjects=_ordered(links.get(r.id, []), labels),
            items=sorted((ItemRead(item_id=i, name=item_names.get(i)) for i in item_links.get(r.id, [])), key=lambda s: ((s.name or "").lower(), str(s.item_id))),
            order_number=numbers.get(r.order_id) if r.order_id else None,
            order_status=statuses.get(r.order_id) if r.order_id else None,
        )
        for r in rows
    ]


def _read(db: Session, ctx: TenantContext, appointment: Appointment) -> AppointmentRead:
    return _reads(db, ctx, [appointment])[0]


def _names(db: Session, ctx: TenantContext, pairs: set[Pair]) -> str:
    """What the history says about the records an appointment is about: their names, in order."""
    labels = subjects.subject_labels(db, ctx.organization_id, pairs)
    return ", ".join(sorted((labels.get(pair) or "a record that no longer exists" for pair in pairs), key=str.lower))


def _item_names(db: Session, ctx: TenantContext, ids: set[uuid.UUID]) -> str:
    names = dict(db.execute(select(Item.id, Item.name).where(Item.organization_id == ctx.organization_id, Item.id.in_(ids))).all()) if ids else {}
    return ", ".join(sorted((names.get(i) or "a service that no longer exists" for i in ids), key=str.lower))


def _validate_references(db: Session, ctx: TenantContext, values: dict, current: Appointment | None = None) -> None:
    """Check only what is being set or changed (an existing link stays valid even if the record was deactivated since)."""
    if values.get("practitioner_user_id") is not None and (current is None or values["practitioner_user_id"] != current.practitioner_user_id):
        _practitioner(db, ctx, values["practitioner_user_id"])
    if values.get("customer_id") is not None and (current is None or values["customer_id"] != current.customer_id):
        resolve_reference(db, ctx, Customer, values["customer_id"], "customer_id")


def _validate_new_items(db: Session, ctx: TenantContext, ids) -> None:
    """A newly named service is an active catalog SERVICE of this organization (not a product or a charge)."""
    for item_id in ids:
        item = resolve_reference(db, ctx, Item, item_id, "items", label="Service")
        if item.type != "service":
            reference_error("items", "Choose a service from the catalog", "service.not_a_service")


@router.post("", response_model=AppointmentRead, status_code=status.HTTP_201_CREATED)
def create_appointment(payload: AppointmentCreate, ctx: TenantContext = Depends(record_writer), db: Session = Depends(get_db)) -> AppointmentRead:
    values = payload.model_dump(exclude={"subjects", "items"})
    pairs = [(s.subject_type, s.subject_id) for s in payload.subjects]
    _validate_references(db, ctx, values)
    for subject_type, subject_id in pairs:
        subjects.resolve_subject(db, ctx, subject_type, subject_id, field="subjects")
    _validate_new_items(db, ctx, payload.items)
    appointment = create_scoped(db, ctx, Appointment, **values)
    for subject_type, subject_id in pairs:
        create_scoped(db, ctx, AppointmentSubject, appointment_id=appointment.id, subject_type=subject_type, subject_id=subject_id)
    for item_id in payload.items:
        create_scoped(db, ctx, AppointmentItem, appointment_id=appointment.id, item_id=item_id)
    db.flush()
    audit.stamp(appointment, ctx, created=True)
    changes = {field: {"from": None, "to": value} for field, value in audit.snapshot(appointment, AUDITED).items() if value is not None}
    if pairs:
        changes["subjects"] = {"from": None, "to": _names(db, ctx, set(pairs))}
    if payload.items:
        changes["services"] = {"from": None, "to": _item_names(db, ctx, set(payload.items))}
    audit.record(db, ctx, entity_type="appointment", entity_id=appointment.id, action=audit.CREATED, changes=changes)
    commit_and_refresh(db, appointment)
    return _read(db, ctx, appointment)


@router.get("", response_model=list[AppointmentRead])
def list_appointments(
    window_from: datetime = Query(alias="from", description="Start of the window (with a time zone offset)"),
    window_to: datetime = Query(alias="to", description="End of the window (with a time zone offset)"),
    practitioner_user_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    include_cancelled: bool = False,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[AppointmentRead]:
    """The appointments that overlap the window, earliest first. The window is required and at most 62 days."""
    if window_from.tzinfo is None or window_to.tzinfo is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="'from' and 'to' must include a time zone offset")
    if window_to <= window_from or window_to - window_from > LONGEST_WINDOW:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="The window must be positive and at most 62 days")
    query = scoped_select(Appointment, ctx).where(Appointment.starts_at < window_to, Appointment.ends_at > window_from)
    if practitioner_user_id is not None:
        query = query.where(Appointment.practitioner_user_id == practitioner_user_id)
    if customer_id is not None:
        query = query.where(Appointment.customer_id == customer_id)
    if not include_cancelled:
        query = query.where(Appointment.status != CANCELLED)
    rows = list(db.scalars(query.order_by(Appointment.starts_at, Appointment.id).limit(MAX_ROWS)))
    return _reads(db, ctx, rows)


@router.get("/{appointment_id}", response_model=AppointmentRead)
def read_appointment(appointment_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> AppointmentRead:
    return _read(db, ctx, get_scoped_or_404(db, ctx, Appointment, appointment_id))


@router.patch("/{appointment_id}", response_model=AppointmentRead)
def update_appointment(
    appointment_id: uuid.UUID,
    payload: AppointmentUpdate,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> AppointmentRead:
    appointment = get_scoped_or_404(db, ctx, Appointment, appointment_id, for_update=True)
    ensure_current(if_match, appointment.version, "appointment", appointment.id)
    values = {field: getattr(payload, field) for field in payload.model_fields_set if field not in ("subjects", "items", "cancel_order")}
    if payload.cancel_order:
        if payload.status != CANCELLED or appointment.status == CANCELLED:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="cancel_order only goes with cancelling an appointment that is booked")
        if appointment.order_id is None or appointment.order_id not in _asked("order.numbers", db, ctx, {appointment.order_id}):
            raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "no_order", "message": "This appointment has no order (it may have been deleted)"})
    _validate_references(db, ctx, values, appointment)
    try:
        check_span(values.get("starts_at", appointment.starts_at), values.get("ends_at", appointment.ends_at))
    except ValueError as error:
        reference_error("ends_at", str(error), "value_error")

    existing = {(link.subject_type, link.subject_id): link for link in _links(db, ctx, [appointment.id]).get(appointment.id, [])}
    wanted = {(s.subject_type, s.subject_id) for s in payload.subjects} if payload.subjects is not None else set(existing)
    for subject_type, subject_id in wanted - set(existing):  # only what is newly added is checked: an old link stays valid
        subjects.resolve_subject(db, ctx, subject_type, subject_id, field="subjects")
    subjects_changed = wanted != set(existing)

    existing_items = {link.item_id: link for link in db.scalars(scoped_select(AppointmentItem, ctx).where(AppointmentItem.appointment_id == appointment.id))}
    wanted_items = set(payload.items) if payload.items is not None else set(existing_items)
    _validate_new_items(db, ctx, wanted_items - set(existing_items))
    items_changed = wanted_items != set(existing_items)

    # Asked before anything of the appointment changes, under the same commit: a completed order refuses (409) and nothing is cancelled.
    number = _answer("order.cancel_draft", db, ctx, appointment.order_id) if payload.cancel_order else None
    before = audit.snapshot(appointment, AUDITED)
    names_before = _names(db, ctx, set(existing)) if subjects_changed else ""
    services_before = _item_names(db, ctx, set(existing_items)) if items_changed else ""
    if subjects_changed or items_changed or any(getattr(appointment, field) != value for field, value in values.items()):  # a save that changes nothing moves no version
        values["version"] = appointment.version + 1
    for field, value in values.items():
        setattr(appointment, field, value)
    for pair in set(existing) - wanted:
        db.delete(existing[pair])
    for subject_type, subject_id in wanted - set(existing):
        create_scoped(db, ctx, AppointmentSubject, appointment_id=appointment.id, subject_type=subject_type, subject_id=subject_id)
    for item_id in set(existing_items) - wanted_items:
        db.delete(existing_items[item_id])
    for item_id in wanted_items - set(existing_items):
        create_scoped(db, ctx, AppointmentItem, appointment_id=appointment.id, item_id=item_id)
    db.flush()
    db.refresh(appointment)  # compare what the database STORED
    changes = audit.changes_between(before, audit.snapshot(appointment, AUDITED))
    if subjects_changed:
        changes["subjects"] = {"from": names_before or None, "to": _names(db, ctx, wanted) or None}
    if items_changed:
        changes["services"] = {"from": services_before or None, "to": _item_names(db, ctx, wanted_items) or None}
    if number is not None:
        changes["order"] = {"from": f"Order {number}", "to": f"Order {number} cancelled"}
    if changes:
        audit.stamp(appointment, ctx)
        audit.record(db, ctx, entity_type="appointment", entity_id=appointment.id, action=audit.UPDATED, changes=changes)
    commit_and_refresh(db, appointment)
    return _read(db, ctx, appointment)


def _booking_order(db: Session, ctx: TenantContext, appointment: Appointment) -> BookingOrder:
    """What the appointment says should be billed: each of its services for each record it is about (the customer when it names none)."""
    if appointment.status == CANCELLED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "appointment_cancelled", "message": "A cancelled appointment cannot make an order"})
    if appointment.customer_id is None:
        reference_error("customer_id", "Choose a customer first: the order is billed to them", "order.needs_customer")
    item_ids = [link.item_id for link in db.scalars(scoped_select(AppointmentItem, ctx).where(AppointmentItem.appointment_id == appointment.id))]
    if not item_ids:
        reference_error("items", "Choose at least one service first", "order.needs_service")
    pairs = [(link.subject_type, link.subject_id) for link in _links(db, ctx, [appointment.id]).get(appointment.id, [])] or [(CUSTOMER_SUBJECT, appointment.customer_id)]
    return BookingOrder(
        source_type=APPOINTMENT_SOURCE,
        source_id=appointment.id,
        customer_id=appointment.customer_id,
        performed_at=appointment.starts_at,
        performed_by_user_id=appointment.practitioner_user_id,
        lines=tuple(BookingLine(item_id=item_id, subject_type=subject_type, subject_id=subject_id) for item_id in sorted(item_ids, key=str) for subject_type, subject_id in sorted(pairs, key=str)),
    )


def _answer(name: str, *args):
    """What the orders module answers to `name` (Sales registers it); refused when no module provides orders."""
    answers = registry.call_hooks(name, *args)
    if not answers:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "orders_unavailable", "message": "Orders are not available"})
    return answers[0]


@router.post("/{appointment_id}/order", response_model=AppointmentRead, status_code=status.HTTP_201_CREATED)
def create_order(
    appointment_id: uuid.UUID, if_match: str | None = Header(default=None), ctx: TenantContext = Depends(record_writer), db: Session = Depends(get_db)
) -> AppointmentRead:
    """Make a DRAFT order from the appointment (its customer is billed; one service line for each service and each record it is about).
    The order is linked to the appointment; nothing is committed unless both are."""
    appointment = get_scoped_or_404(db, ctx, Appointment, appointment_id, for_update=True)
    ensure_current(if_match, appointment.version, "appointment", appointment.id)
    if appointment.order_id is not None and appointment.order_id in _asked("order.numbers", db, ctx, {appointment.order_id}):
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "order_exists", "message": "This appointment already has an order"})
    order = _booking_order(db, ctx, appointment)
    made = _answer("order.create_from_booking", db, ctx, order)
    audit.record(
        db, ctx, entity_type="appointment", entity_id=appointment.id, action=audit.UPDATED, changes={"order": {"from": None, "to": f"Order {made['number']}"}}
    )
    appointment.order_id = made["id"]
    appointment.version += 1
    audit.stamp(appointment, ctx)
    commit_and_refresh(db, appointment)
    return _read(db, ctx, appointment)


@router.post("/{appointment_id}/order/update", response_model=OrderUpdateResult)
def update_order(
    appointment_id: uuid.UUID, if_match: str | None = Header(default=None), ctx: TenantContext = Depends(record_writer), db: Session = Depends(get_db)
) -> OrderUpdateResult:
    """Bring the appointment's DRAFT order up to date with what the appointment now says: add the lines for services and records added
    since, remove the ones taken away (unless somebody edited them since), leave anything typed by hand. A completed or cancelled order
    is refused (409)."""
    appointment = get_scoped_or_404(db, ctx, Appointment, appointment_id, for_update=True)
    ensure_current(if_match, appointment.version, "appointment", appointment.id)
    if appointment.order_id is None or appointment.order_id not in _asked("order.numbers", db, ctx, {appointment.order_id}):
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "no_order", "message": "This appointment has no order (it may have been deleted)"})
    order = _booking_order(db, ctx, appointment)
    result = _answer("order.sync_from_booking", db, ctx, appointment.order_id, order)
    db.commit()
    return OrderUpdateResult(added=result["added"], removed=result["removed"], kept=result["kept"], appointment=_read(db, ctx, appointment))


@router.post("/{appointment_id}/order/cancel", response_model=AppointmentRead)
def cancel_order(
    appointment_id: uuid.UUID, if_match: str | None = Header(default=None), ctx: TenantContext = Depends(record_writer), db: Session = Depends(get_db)
) -> AppointmentRead:
    """Cancel the appointment's DRAFT order without touching the appointment's status (a completed order is refused, 409: that is done on the order)."""
    appointment = get_scoped_or_404(db, ctx, Appointment, appointment_id, for_update=True)
    ensure_current(if_match, appointment.version, "appointment", appointment.id)
    if appointment.order_id is None or appointment.order_id not in _asked("order.numbers", db, ctx, {appointment.order_id}):
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "no_order", "message": "This appointment has no order (it may have been deleted)"})
    number = _answer("order.cancel_draft", db, ctx, appointment.order_id)
    audit.record(
        db, ctx, entity_type="appointment", entity_id=appointment.id, action=audit.UPDATED, changes={"order": {"from": f"Order {number}", "to": f"Order {number} cancelled"}}
    )
    appointment.version += 1
    audit.stamp(appointment, ctx)
    commit_and_refresh(db, appointment)
    return _read(db, ctx, appointment)


@router.delete("/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_appointment(
    appointment_id: uuid.UUID,
    delete_order: bool = False,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> Response:
    """Remove an appointment for good (cancel it instead to keep it on record). The history records that it was deleted.

    A completed order (so also an invoiced one) is money: the appointment is then refused (409), cancel it instead. A draft order stays
    unless `delete_order` is set, which deletes it in the same step (either both go or neither)."""
    appointment = get_scoped_or_404(db, ctx, Appointment, appointment_id, for_update=True)
    ensure_current(if_match, appointment.version, "appointment", appointment.id)
    order_status = _asked("order.statuses", db, ctx, {appointment.order_id}).get(appointment.order_id) if appointment.order_id else None
    if order_status == "completed":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "order_completed", "message": "This appointment has a completed order, so it cannot be deleted. Cancel it instead."},
        )
    if delete_order:
        if order_status is None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "no_order", "message": "This appointment has no order (it may have been deleted)"})
        _answer("order.delete_draft", db, ctx, appointment.order_id)  # refuses anything but a draft (409)
    delete_or_409(db, appointment, "Appointment is referenced by other records", after_delete=audit.deletion(db, ctx, appointment, "appointment", fields=AUDITED))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
