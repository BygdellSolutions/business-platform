"""Appointments: the organization's calendar.

Everyone in the organization may read it; whoever may write business records (owner, admin, accountant, employee) may book, move
and cancel. A viewer only looks. The practitioner of an appointment is a member who does the work (owner, admin or employee);
leaving it empty books the time for nobody in particular. Times travel with their offset and are stored as instants: the browser
shows them in the organization's time zone.

A change needs the version the caller last saw (If-Match, 409 `stale_record` otherwise), like other records several people edit.
"""

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit, subjects
from app.core.authz import record_writer
from app.core.db import get_db
from app.core.query import commit_and_refresh, delete_or_409
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import create_scoped, get_scoped_or_404, reference_error, resolve_reference, scoped_select
from app.core.versioning import ensure_current
from app.models import Customer, OrganizationUser, Role, User
from app.modules.scheduling.models import CANCELLED, Appointment
from app.modules.scheduling.schemas import AppointmentCreate, AppointmentRead, AppointmentUpdate, check_span

router = APIRouter(prefix="/api/appointments", tags=["appointments"])

PRACTITIONER_ROLES = (Role.OWNER, Role.ADMIN, Role.EMPLOYEE)
# One request shows at most a month view with its leading and trailing days; more is a mistake (or a way to ask for everything).
LONGEST_WINDOW = timedelta(days=62)
MAX_ROWS = 1000
AUDITED = ("title", "starts_at", "ends_at", "location", "status", "practitioner_user_id", "customer_id", "subject_type", "subject_id")


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


def _reads(db: Session, ctx: TenantContext, rows: list[Appointment]) -> list[AppointmentRead]:
    """The appointments with the names of who, which customer and what they are about (looked up in this organization only)."""
    people = dict(db.execute(select(User.id, User.name).where(User.id.in_({r.practitioner_user_id for r in rows if r.practitioner_user_id}))).all()) if rows else {}
    customer_ids = {r.customer_id for r in rows if r.customer_id}
    customers = (
        dict(db.execute(select(Customer.id, Customer.name).where(Customer.organization_id == ctx.organization_id, Customer.id.in_(customer_ids))).all())
        if customer_ids
        else {}
    )
    labels = subjects.subject_labels(db, ctx.organization_id, {(r.subject_type, r.subject_id) for r in rows if r.subject_type})
    return [
        AppointmentRead(
            **{field: getattr(r, field) for field in AppointmentRead.model_fields if hasattr(r, field)},
            practitioner_name=people.get(r.practitioner_user_id),
            customer_name=customers.get(r.customer_id),
            subject_label=labels.get((r.subject_type, r.subject_id)) if r.subject_type else None,
        )
        for r in rows
    ]


def _read(db: Session, ctx: TenantContext, appointment: Appointment) -> AppointmentRead:
    return _reads(db, ctx, [appointment])[0]


def _validate_references(db: Session, ctx: TenantContext, values: dict, current: Appointment | None = None) -> None:
    """Check only what is being set or changed (an existing link stays valid even if the record was deactivated since)."""
    if values.get("practitioner_user_id") is not None and (current is None or values["practitioner_user_id"] != current.practitioner_user_id):
        _practitioner(db, ctx, values["practitioner_user_id"])
    if values.get("customer_id") is not None and (current is None or values["customer_id"] != current.customer_id):
        resolve_reference(db, ctx, Customer, values["customer_id"], "customer_id")
    if values.get("subject_type") is not None and (current is None or (values["subject_type"], values["subject_id"]) != (current.subject_type, current.subject_id)):
        subjects.resolve_subject(db, ctx, values["subject_type"], values["subject_id"])


@router.post("", response_model=AppointmentRead, status_code=status.HTTP_201_CREATED)
def create_appointment(payload: AppointmentCreate, ctx: TenantContext = Depends(record_writer), db: Session = Depends(get_db)) -> AppointmentRead:
    values = payload.model_dump()
    _validate_references(db, ctx, values)
    appointment = create_scoped(db, ctx, Appointment, **values)
    audit.created(db, ctx, appointment, "appointment", fields=AUDITED)
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
    values = {field: getattr(payload, field) for field in payload.model_fields_set}
    _validate_references(db, ctx, values, appointment)
    try:
        check_span(values.get("starts_at", appointment.starts_at), values.get("ends_at", appointment.ends_at))
    except ValueError as error:
        reference_error("ends_at", str(error), "value_error")
    if any(getattr(appointment, field) != value for field, value in values.items()):  # a save that changes nothing moves no version
        values["version"] = appointment.version + 1
    audit.apply_audited_update(db, ctx, appointment, "appointment", values, fields=AUDITED)
    return _read(db, ctx, appointment)


@router.delete("/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_appointment(
    appointment_id: uuid.UUID, if_match: str | None = Header(default=None), ctx: TenantContext = Depends(record_writer), db: Session = Depends(get_db)
) -> Response:
    """Remove an appointment for good (cancel it instead to keep it on record). The history records that it was deleted."""
    appointment = get_scoped_or_404(db, ctx, Appointment, appointment_id, for_update=True)
    ensure_current(if_match, appointment.version, "appointment", appointment.id)
    delete_or_409(db, appointment, "Appointment is referenced by other records", after_delete=audit.deletion(db, ctx, appointment, "appointment", fields=AUDITED))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
