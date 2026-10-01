import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, event, func, inspect, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class TenantOwned:
    """Standard columns for every tenant-owned business record.

    A model that inherits this belongs to exactly one organization. Read and
    write it only through `app.core.tenant_scope`, which takes the organization
    from the validated TenantContext.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


@event.listens_for(TenantOwned, "before_update", propagate=True)
def _forbid_organization_change(mapper, connection, target) -> None:
    """Defense in depth: a record can never be moved to another organization.

    The API schemas already have no organization_id field; this catches any
    code path that assigns it on an existing record.
    """
    if inspect(target).attrs.organization_id.history.has_changes():
        raise ValueError("organization_id of a tenant-owned record cannot be changed")
