"""A horse's documents: PDFs and pictures kept with the horse (vet reports, x-ray photos, passports).

Everyone who can see the horse can list and open its documents; whoever may edit records (owner, admin, employee) uploads and
deletes. The file travels as base64 inside the JSON body (the same way as the organization logo, so the BFF stays JSON-only on the
way in) and is checked here, never trusted: the declared type must be one of four, the bytes must START like that type, and the
size is capped. The name is cleaned (no path, no control characters). Opening a document streams the stored bytes with headers
that stop the browser from treating them as anything else.
"""

import base64
import binascii
import hashlib
import re
import unicodedata
import uuid
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer

from app.core import audit, rate_limit
from app.core.authz import record_writer
from app.core.db import get_db
from app.core.query import commit_and_refresh
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import create_scoped, get_scoped_or_404, scoped_select
from app.models import User
from app.modules.equine.models import DOCUMENT_MAX_BYTES, DOCUMENT_MAX_PER_HORSE, DOCUMENT_TYPES, Horse, HorseDocument

router = APIRouter()

# base64 makes 3 bytes into 4 characters
MAX_BASE64_CHARS = (DOCUMENT_MAX_BYTES + 2) // 3 * 4

# The first bytes a file of each type must start with; a file that claims to be a PDF but is not one is refused.
SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),  # plus "WEBP" at byte 8, checked below
}
AUDITED = ("filename", "content_type", "byte_size")


class DocumentUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(max_length=100)
    data_base64: str = Field(min_length=4, max_length=MAX_BASE64_CHARS)


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    byte_size: int
    created_at: datetime
    created_by_name: str | None = None


def _invalid(message: str, code: str = "invalid_document") -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"code": code, "message": message})


def clean_filename(raw: str) -> str:
    """The name as shown and offered for download: no folder part, no control characters, at most 200 characters."""
    name = re.split(r"[\\/]", raw)[-1]
    name = "".join(char for char in unicodedata.normalize("NFC", name) if unicodedata.category(char)[0] != "C").strip(" .")
    return name[:200] or "document"


def check_content(content_type: str, content: bytes) -> None:
    if content_type not in DOCUMENT_TYPES:
        raise _invalid("Only PDF, JPEG, PNG and WebP files can be added.", "unsupported_document_type")
    if not content:
        raise _invalid("The file is empty.")
    if len(content) > DOCUMENT_MAX_BYTES:
        raise _invalid(f"The file is larger than {DOCUMENT_MAX_BYTES // (1024 * 1024)} MB.", "document_too_large")
    if not any(content.startswith(signature) for signature in SIGNATURES[content_type]):
        raise _invalid("The file is not what its type says.", "document_type_mismatch")
    if content_type == "image/webp" and content[8:12] != b"WEBP":
        raise _invalid("The file is not what its type says.", "document_type_mismatch")


def _reads(db: Session, rows: list[HorseDocument]) -> list[DocumentRead]:
    people = {row.created_by for row in rows}
    people.discard(None)
    names = dict(db.execute(select(User.id, User.name).where(User.id.in_(people))).all()) if people else {}
    return [DocumentRead.model_validate(row).model_copy(update={"created_by_name": names.get(row.created_by)}) for row in rows]


def _document_or_404(db: Session, ctx: TenantContext, horse_id: uuid.UUID, document_id: uuid.UUID, *, with_content: bool = False) -> HorseDocument:
    query = scoped_select(HorseDocument, ctx).where(HorseDocument.horse_id == horse_id, HorseDocument.id == document_id)
    document = db.scalar(query if with_content else query.options(defer(HorseDocument.content)))
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    return document


@router.get("/{horse_id}/documents", response_model=list[DocumentRead])
def list_documents(horse_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> list[DocumentRead]:
    """The horse's documents, newest first (without their bytes)."""
    get_scoped_or_404(db, ctx, Horse, horse_id)
    rows = list(
        db.scalars(
            scoped_select(HorseDocument, ctx)
            .where(HorseDocument.horse_id == horse_id)
            .options(defer(HorseDocument.content))
            .order_by(HorseDocument.created_at.desc(), HorseDocument.id)
            .limit(DOCUMENT_MAX_PER_HORSE)
        )
    )
    return _reads(db, rows)


@router.post("/{horse_id}/documents", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
def upload_document(
    horse_id: uuid.UUID,
    payload: DocumentUpload,
    ctx: TenantContext = Depends(record_writer),
    _heavy: None = Depends(rate_limit.heavy),
    db: Session = Depends(get_db),
) -> DocumentRead:
    try:
        content = base64.b64decode(payload.data_base64, validate=True)
    except (binascii.Error, ValueError):
        raise _invalid("The file could not be read.") from None
    check_content(payload.content_type, content)
    get_scoped_or_404(db, ctx, Horse, horse_id)
    count = db.scalar(
        select(func.count()).select_from(HorseDocument).where(HorseDocument.organization_id == ctx.organization_id, HorseDocument.horse_id == horse_id)
    )
    if (count or 0) >= DOCUMENT_MAX_PER_HORSE:
        raise _invalid(f"A horse can have at most {DOCUMENT_MAX_PER_HORSE} documents.", "too_many_documents")
    document = create_scoped(
        db,
        ctx,
        HorseDocument,
        horse_id=horse_id,
        filename=clean_filename(payload.filename),
        content_type=payload.content_type,
        byte_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        content=content,
    )
    audit.created(db, ctx, document, "horse_document", context=("horse", horse_id), fields=AUDITED)
    commit_and_refresh(db, document)
    return _reads(db, [document])[0]


@router.get("/{horse_id}/documents/{document_id}/file")
def open_document(horse_id: uuid.UUID, document_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> Response:
    """The stored bytes, shown in the browser (a PDF in its viewer, a picture as a picture)."""
    document = _document_or_404(db, ctx, horse_id, document_id, with_content=True)
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", unicodedata.normalize("NFKD", document.filename).encode("ascii", "ignore").decode("ascii")) or "document"
    headers = {
        "Content-Disposition": f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(document.filename, safe='')}",
        "ETag": f'"{document.sha256}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    if document.content_type != "application/pdf":
        headers["Content-Security-Policy"] = "default-src 'none'; sandbox"  # a PDF viewer needs more; a picture needs nothing
    return Response(content=bytes(document.content), media_type=document.content_type, headers=headers)


@router.delete("/{horse_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(horse_id: uuid.UUID, document_id: uuid.UUID, ctx: TenantContext = Depends(record_writer), db: Session = Depends(get_db)) -> Response:
    document = _document_or_404(db, ctx, horse_id, document_id)
    audit.deleted(db, ctx, document, "horse_document", context=("horse", horse_id), fields=AUDITED)
    db.delete(document)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
