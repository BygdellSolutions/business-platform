"""The horse journal: one entry per visit, written as a draft and then finalized.

A therapy/self-evaluation journal of the practitioner (not a formal veterinary record). Who may do what (owner decision
2026-10-14, explicit journal permissions and NOT the broad horse-note rule):

  owner, admin, employee   read every entry of the organization's horses and write entries;
  employee                 changes and finalizes only the entries they wrote; an admin or an owner may change any draft;
  accountant, viewer       no access at all, not even to read (403);
  customers                never, and owning a horse confers nothing.

The role is judged before anything is looked up (so a foreign horse or entry is a 404 to those who may use the journal, and a 403 to
those who may not, whatever the id). A draft changes only with the version the caller last saw (If-Match, 409 `stale_record`
otherwise); a finalized entry never changes through this API.
"""

import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, Sorting, pagination, sorted_by, sorting
from app.core import audit
from app.core.authz import roles_required
from app.core.db import get_db
from app.core.query import commit_and_refresh
from app.core.tenant import TenantContext
from app.core.tenant_scope import create_scoped, get_scoped_or_404, reference_error, scoped_select
from app.core.versioning import ensure_current
from app.models import OrganizationUser, Role, User
from app.modules.equine.journal_schemas import CorrectionRead, CorrectionWrite, JournalCreate, JournalRead, JournalSummary, JournalUpdate
from app.modules.equine.models import DIAGRAM_VERSION, JOURNAL_DRAFT, JOURNAL_FINAL, Horse, HorseJournalCorrection, HorseJournalEntry

router = APIRouter(prefix="/{horse_id}/journal", tags=["horse journal"])

JOURNAL_ROLES = (Role.OWNER, Role.ADMIN, Role.EMPLOYEE)
journal_access = roles_required(*JOURNAL_ROLES)
CHANGE_ANY = (Role.OWNER, Role.ADMIN)
PREVIEW_LENGTH = 160
# What the horse's history records of an entry. The text and the marks are the journal's own content and are not copied into the
# history; the entry itself is the record of them (frozen at finalization).
AUDITED = ("visit_at", "practitioner_user_id", "practitioner_name", "follow_up_on", "status", "finalized_at")


def _practitioner(db: Session, ctx: TenantContext, user_id: uuid.UUID) -> User:
    """The chosen practitioner: a member of THIS organization who works with the journal (owner, admin or employee: an accountant or
    a viewer has no journal), anyone else or a made-up id being refused alike."""
    member = db.scalar(
        select(User)
        .join(OrganizationUser, OrganizationUser.user_id == User.id)
        .where(OrganizationUser.organization_id == ctx.organization_id, OrganizationUser.role.in_([role.value for role in JOURNAL_ROLES]), User.id == user_id)
    )
    if member is None:
        reference_error("practitioner_user_id", "Choose a member of this organization", "reference.not_found")
    return member


def _numbered(marks: list[dict]) -> list[dict]:
    """Every mark gets a number: the ones that have one keep it, the others take the next free numbers in the order they came."""
    taken = [mark["number"] for mark in marks if mark.get("number") is not None]
    following = (max(taken) if taken else 0) + 1
    result = []
    for mark in marks:
        if mark.get("number") is None:
            mark = {**mark, "number": following}
            following += 1
        result.append(mark)
    return result


def _may_change(ctx: TenantContext, entry: HorseJournalEntry) -> bool:
    return ctx.role in CHANGE_ANY or entry.created_by == ctx.user.id


def _names(db: Session, entries: list[HorseJournalEntry]) -> dict[uuid.UUID, str]:
    people = {e.created_by for e in entries} | {e.finalized_by for e in entries}
    people.discard(None)
    return dict(db.execute(select(User.id, User.name).where(User.id.in_(people))).all()) if people else {}


def _read(db: Session, ctx: TenantContext, entry: HorseJournalEntry) -> JournalRead:
    corrections = list(
        db.scalars(scoped_select(HorseJournalCorrection, ctx).where(HorseJournalCorrection.entry_id == entry.id).order_by(HorseJournalCorrection.created_at, HorseJournalCorrection.id))
    )
    names = _names(db, [entry])
    author_names = dict(db.execute(select(User.id, User.name).where(User.id.in_({c.created_by for c in corrections} - {None}))).all()) if corrections else {}
    return JournalRead.model_validate(entry).model_copy(
        update={
            "created_by_name": names.get(entry.created_by),
            "finalized_by_name": names.get(entry.finalized_by),
            "can_change": entry.status == JOURNAL_DRAFT and _may_change(ctx, entry),
            "can_correct": entry.status == JOURNAL_FINAL and _may_change(ctx, entry),
            "corrections": [CorrectionRead.model_validate(c).model_copy(update={"created_by_name": author_names.get(c.created_by)}) for c in corrections],
        }
    )


def _entry_or_404(db: Session, ctx: TenantContext, horse_id: uuid.UUID, entry_id: uuid.UUID, *, lock: bool = False) -> HorseJournalEntry:
    query = scoped_select(HorseJournalEntry, ctx).where(HorseJournalEntry.horse_id == horse_id, HorseJournalEntry.id == entry_id)
    entry = db.scalar(query.with_for_update() if lock else query)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    return entry


def _require_own_draft(ctx: TenantContext, entry: HorseJournalEntry) -> None:
    if entry.status != JOURNAL_DRAFT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "journal_entry_final", "message": "This entry is finalized and can no longer be changed"},
        )
    if not _may_change(ctx, entry):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the author, an admin or an owner may change this entry")


@router.get("", response_model=list[JournalSummary])
def list_entries(
    horse_id: uuid.UUID,
    date_from: date | None = Query(default=None, description="Visits on or after this day"),
    date_to: date | None = Query(default=None, description="Visits on or before this day"),
    page: Pagination = Depends(pagination),
    sort: Sorting = Depends(sorting),
    ctx: TenantContext = Depends(journal_access),
    db: Session = Depends(get_db),
) -> list[JournalSummary]:
    """The horse's entries, newest visit first unless sorted (number, visit, by, status, marks); each row is a summary (open an entry
for its text and marks)."""
    get_scoped_or_404(db, ctx, Horse, horse_id)
    query = scoped_select(HorseJournalEntry, ctx).where(HorseJournalEntry.horse_id == horse_id)
    if date_from is not None:
        query = query.where(HorseJournalEntry.visit_at >= datetime.combine(date_from, time.min, tzinfo=timezone.utc))
    if date_to is not None:
        query = query.where(HorseJournalEntry.visit_at < datetime.combine(date_to, time.min, tzinfo=timezone.utc) + timedelta(days=1))
    columns = {
        "number": HorseJournalEntry.number,
        "visit": HorseJournalEntry.visit_at,
        "by": func.lower(HorseJournalEntry.practitioner_name),
        "status": HorseJournalEntry.status,
        "marks": func.jsonb_array_length(HorseJournalEntry.marks),
    }
    ordered = sorted_by(query, sort, columns, (HorseJournalEntry.visit_at.desc(), HorseJournalEntry.id))
    entries = list(db.scalars(ordered.limit(page.limit).offset(page.offset)))
    names = _names(db, entries)
    return [
        JournalSummary(
            id=e.id, number=e.number, horse_id=e.horse_id, visit_at=e.visit_at, practitioner_name=e.practitioner_name, status=e.status,
            follow_up_on=e.follow_up_on, mark_count=len(e.marks), preview=e.body.strip()[:PREVIEW_LENGTH], version=e.version,
            created_by=e.created_by, created_by_name=names.get(e.created_by),
        )
        for e in entries
    ]


@router.post("", response_model=JournalRead, status_code=status.HTTP_201_CREATED)
def create_entry(horse_id: uuid.UUID, payload: JournalCreate, ctx: TenantContext = Depends(journal_access), db: Session = Depends(get_db)) -> JournalRead:
    get_scoped_or_404(db, ctx, Horse, horse_id)
    practitioner = _practitioner(db, ctx, payload.practitioner_user_id) if payload.practitioner_user_id is not None else ctx.user
    entry = create_scoped(
        db,
        ctx,
        HorseJournalEntry,
        horse_id=horse_id,
        visit_at=payload.visit_at or datetime.now(timezone.utc),
        practitioner_user_id=practitioner.id,
        practitioner_name=practitioner.name,
        body=payload.body,
        follow_up_on=payload.follow_up_on,
        marks=_numbered([mark.model_dump(mode="json", exclude_none=False) for mark in payload.marks]),
        diagram_version=DIAGRAM_VERSION,
        status=JOURNAL_DRAFT,
    )
    audit.created(db, ctx, entry, "horse_journal_entry", context=("horse", horse_id), fields=AUDITED)
    commit_and_refresh(db, entry)
    return _read(db, ctx, entry)


@router.get("/{entry_id}", response_model=JournalRead)
def read_entry(horse_id: uuid.UUID, entry_id: uuid.UUID, ctx: TenantContext = Depends(journal_access), db: Session = Depends(get_db)) -> JournalRead:
    return _read(db, ctx, _entry_or_404(db, ctx, horse_id, entry_id))


@router.patch("/{entry_id}", response_model=JournalRead)
def update_entry(
    horse_id: uuid.UUID,
    entry_id: uuid.UUID,
    payload: JournalUpdate,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(journal_access),
    db: Session = Depends(get_db),
) -> JournalRead:
    entry = _entry_or_404(db, ctx, horse_id, entry_id, lock=True)
    _require_own_draft(ctx, entry)
    ensure_current(if_match, entry.version, "horse_journal_entry", entry.id)
    values = {field: getattr(payload, field) for field in payload.model_fields_set}
    if "marks" in values:
        values["marks"] = _numbered([mark.model_dump(mode="json") for mark in values["marks"]])
    if "practitioner_user_id" in values:
        chosen = _practitioner(db, ctx, values["practitioner_user_id"])
        values["practitioner_name"] = chosen.name  # the name as it is now, kept with the entry
    if any(getattr(entry, field) != value for field, value in values.items()):  # a save that changes nothing moves no version
        values["version"] = entry.version + 1
    audit.apply_audited_update(db, ctx, entry, "horse_journal_entry", values, context=("horse", horse_id), fields=AUDITED)
    return _read(db, ctx, entry)


@router.post("/{entry_id}/finalize", response_model=JournalRead)
def finalize_entry(
    horse_id: uuid.UUID,
    entry_id: uuid.UUID,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(journal_access),
    db: Session = Depends(get_db),
) -> JournalRead:
    """Freeze the entry: its text and marks never change again (a later correction is a separate dated addition)."""
    entry = _entry_or_404(db, ctx, horse_id, entry_id, lock=True)
    _require_own_draft(ctx, entry)
    ensure_current(if_match, entry.version, "horse_journal_entry", entry.id)
    if not entry.body.strip() and not entry.marks:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="An entry needs some text or at least one mark before it is finalized")
    values = {"status": JOURNAL_FINAL, "finalized_at": datetime.now(timezone.utc), "finalized_by": ctx.user.id, "version": entry.version + 1}
    audit.apply_audited_update(db, ctx, entry, "horse_journal_entry", values, context=("horse", horse_id), fields=AUDITED)
    return _read(db, ctx, entry)


@router.post("/{entry_id}/corrections", response_model=JournalRead, status_code=status.HTTP_201_CREATED)
def add_correction(
    horse_id: uuid.UUID,
    entry_id: uuid.UUID,
    payload: CorrectionWrite,
    ctx: TenantContext = Depends(journal_access),
    db: Session = Depends(get_db),
) -> JournalRead:
    """A dated addition to a FINALIZED entry, by its author, an admin or an owner. The entry itself stays exactly as finalized."""
    entry = _entry_or_404(db, ctx, horse_id, entry_id, lock=True)
    if entry.status != JOURNAL_FINAL:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "journal_entry_not_final", "message": "A draft is edited directly; corrections are added to finalized entries"},
        )
    if not _may_change(ctx, entry):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the author, an admin or an owner may add a correction to this entry")
    # Authors are set on the INSERT: the table is append-only, so a later stamping UPDATE would be refused.
    correction = create_scoped(db, ctx, HorseJournalCorrection, entry_id=entry.id, body=payload.body, created_by=ctx.user.id, updated_by=ctx.user.id, created_at=datetime.now(timezone.utc))
    audit.created(db, ctx, correction, "horse_journal_correction", context=("horse", horse_id), fields=())
    commit_and_refresh(db, correction)
    return _read(db, ctx, entry)


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft(
    horse_id: uuid.UUID,
    entry_id: uuid.UUID,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(journal_access),
    db: Session = Depends(get_db),
) -> Response:
    """Delete an entry that is still a DRAFT (its author, an admin or an owner). A finalized entry is never deleted: 409, and the
    database refuses it too. It is gone for good; the horse's history records that it was deleted, not what it said."""
    entry = _entry_or_404(db, ctx, horse_id, entry_id, lock=True)
    _require_own_draft(ctx, entry)
    ensure_current(if_match, entry.version, "horse_journal_entry", entry.id)
    audit.deleted(db, ctx, entry, "horse_journal_entry", context=("horse", horse_id), fields=AUDITED)
    db.delete(entry)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
