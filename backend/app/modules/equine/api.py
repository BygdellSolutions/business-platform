import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, select
from sqlalchemy.orm import Session, aliased

from app.api.deps import Pagination, pagination
from app.core import audit
from app.core.authz import record_writer
from app.core.db import get_db
from app.core.query import commit_and_refresh, contains_pattern, delete_or_409
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import (
    create_scoped,
    get_scoped_or_404,
    resolve_reference,
    scoped_select,
)
from app.models import Customer, User
from app.modules.equine.models import Horse, HorseNote
from app.modules.equine.schemas import HorseCreate, HorseNoteRead, HorseNoteWrite, HorseRead, HorseUpdate
from app.schemas.customer import CustomerRef

router = APIRouter(prefix="/api/horses", tags=["horses"])

REFERENCE_FIELDS = ("owner_customer_id", "stable_customer_id")

Owner = aliased(Customer)
Stable = aliased(Customer)


def _horse_rows(ctx: TenantContext):
    """Horses of the active organization with their owner and stable customers.

    Starts from scoped_select. The joins match on (organization_id, id), the same
    pair the composite foreign keys guarantee, so they cannot cross tenants.
    """
    return (
        scoped_select(Horse, ctx)
        .add_columns(Owner, Stable)
        .join(
            Owner,
            and_(Owner.organization_id == Horse.organization_id, Owner.id == Horse.owner_customer_id),
        )
        .outerjoin(
            Stable,
            and_(
                Stable.organization_id == Horse.organization_id,
                Stable.id == Horse.stable_customer_id,
            ),
        )
    )


def _to_read(horse: Horse, owner: Customer, stable: Customer | None) -> HorseRead:
    return HorseRead(
        id=horse.id,
        name=horse.name,
        owner_customer_id=horse.owner_customer_id,
        stable_customer_id=horse.stable_customer_id,
        owner=CustomerRef.model_validate(owner),
        stable=CustomerRef.model_validate(stable) if stable is not None else None,
        birth_year=horse.birth_year,
        sex=horse.sex,
        breed=horse.breed,
        active=horse.active,
        created_at=horse.created_at,
        updated_at=horse.updated_at,
        created_by=horse.created_by,
        updated_by=horse.updated_by,
    )


def _read_one(db: Session, ctx: TenantContext, horse_id: uuid.UUID) -> HorseRead:
    # Callers have already checked the horse exists in this organization.
    row = db.execute(_horse_rows(ctx).where(Horse.id == horse_id)).one()
    return _to_read(*row)


@router.post("", response_model=HorseRead, status_code=status.HTTP_201_CREATED)
def create_horse(
    payload: HorseCreate,
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> HorseRead:
    for field in REFERENCE_FIELDS:
        value = getattr(payload, field)
        if value is not None:
            resolve_reference(db, ctx, Customer, value, field)
    horse = create_scoped(db, ctx, Horse, **payload.model_dump())
    audit.created(db, ctx, horse, "horse")
    commit_and_refresh(db, horse)
    return _read_one(db, ctx, horse.id)


@router.get("", response_model=list[HorseRead])
def list_horses(
    q: str | None = Query(default=None, max_length=255, description="Name contains"),
    owner_customer_id: uuid.UUID | None = None,
    stable_customer_id: uuid.UUID | None = None,
    active: bool | None = None,
    page: Pagination = Depends(pagination),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[HorseRead]:
    # A filter id from another organization simply matches nothing in this one.
    query = _horse_rows(ctx)
    if q:
        query = query.where(Horse.name.ilike(contains_pattern(q), escape="\\"))
    if owner_customer_id is not None:
        query = query.where(Horse.owner_customer_id == owner_customer_id)
    if stable_customer_id is not None:
        query = query.where(Horse.stable_customer_id == stable_customer_id)
    if active is not None:
        query = query.where(Horse.active == active)
    query = query.order_by(Horse.name, Horse.id).limit(page.limit).offset(page.offset)
    return [_to_read(*row) for row in db.execute(query).all()]


@router.get("/{horse_id}", response_model=HorseRead)
def read_horse(
    horse_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> HorseRead:
    get_scoped_or_404(db, ctx, Horse, horse_id)
    return _read_one(db, ctx, horse_id)


@router.patch("/{horse_id}", response_model=HorseRead)
def update_horse(
    horse_id: uuid.UUID,
    payload: HorseUpdate,
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> HorseRead:
    horse = get_scoped_or_404(db, ctx, Horse, horse_id)
    values = payload.model_dump(exclude_unset=True)
    for field in REFERENCE_FIELDS:
        # Only a NEW target must exist and be active. An unchanged reference stays
        # valid even if its customer was deactivated since.
        if values.get(field) is not None and values[field] != getattr(horse, field):
            resolve_reference(db, ctx, Customer, values[field], field)
    audit.apply_audited_update(db, ctx, horse, "horse", values)
    return _read_one(db, ctx, horse_id)


@router.delete("/{horse_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_horse(
    horse_id: uuid.UUID,
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> Response:
    horse = get_scoped_or_404(db, ctx, Horse, horse_id)
    delete_or_409(db, horse, "Horse is referenced by other records", after_delete=audit.deletion(db, ctx, horse, "horse"))
    return Response(status_code=status.HTTP_204_NO_CONTENT)



# --- notes: any member reads them, record writers add, change and delete them ------------------------------------

NOTES_SHOWN = 500


def _note_reads(db: Session, notes: list[HorseNote]) -> list[HorseNoteRead]:
    people = {note.created_by for note in notes} | {note.updated_by for note in notes}
    people.discard(None)
    names = dict(db.execute(select(User.id, User.name).where(User.id.in_(people))).all()) if people else {}
    return [
        HorseNoteRead.model_validate(note).model_copy(update={"created_by_name": names.get(note.created_by), "updated_by_name": names.get(note.updated_by)})
        for note in notes
    ]


def _note_or_404(db: Session, ctx: TenantContext, horse_id: uuid.UUID, note_id: uuid.UUID) -> HorseNote:
    note = db.scalar(scoped_select(HorseNote, ctx).where(HorseNote.horse_id == horse_id, HorseNote.id == note_id))
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    return note


@router.get("/{horse_id}/notes", response_model=list[HorseNoteRead])
def list_horse_notes(horse_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> list[HorseNoteRead]:
    """The horse's notes, newest first."""
    get_scoped_or_404(db, ctx, Horse, horse_id)
    notes = list(
        db.scalars(scoped_select(HorseNote, ctx).where(HorseNote.horse_id == horse_id).order_by(HorseNote.created_at.desc(), HorseNote.id).limit(NOTES_SHOWN))
    )
    return _note_reads(db, notes)


@router.post("/{horse_id}/notes", response_model=HorseNoteRead, status_code=status.HTTP_201_CREATED)
def add_horse_note(
    horse_id: uuid.UUID, payload: HorseNoteWrite, ctx: TenantContext = Depends(record_writer), db: Session = Depends(get_db)
) -> HorseNoteRead:
    get_scoped_or_404(db, ctx, Horse, horse_id)
    note = create_scoped(db, ctx, HorseNote, horse_id=horse_id, body=payload.body)
    audit.created(db, ctx, note, "horse_note", context=("horse", horse_id))
    commit_and_refresh(db, note)
    return _note_reads(db, [note])[0]


@router.patch("/{horse_id}/notes/{note_id}", response_model=HorseNoteRead)
def update_horse_note(
    horse_id: uuid.UUID, note_id: uuid.UUID, payload: HorseNoteWrite, ctx: TenantContext = Depends(record_writer), db: Session = Depends(get_db)
) -> HorseNoteRead:
    note = _note_or_404(db, ctx, horse_id, note_id)
    audit.apply_audited_update(db, ctx, note, "horse_note", {"body": payload.body}, context=("horse", horse_id))
    return _note_reads(db, [note])[0]


@router.delete("/{horse_id}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_horse_note(horse_id: uuid.UUID, note_id: uuid.UUID, ctx: TenantContext = Depends(record_writer), db: Session = Depends(get_db)) -> Response:
    note = _note_or_404(db, ctx, horse_id, note_id)
    audit.deleted(db, ctx, note, "horse_note", context=("horse", horse_id))
    db.delete(note)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
