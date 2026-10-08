import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, event, func, inspect, text
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


class Authored:
    """Who created a record and who changed it last (the user of the active membership at the time).

    NULL for records that existed before authors were recorded: shown as "not recorded", never guessed. The full
    history, with old and new values, is in `audit_events`.
    """

    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))


class BusinessProfile:
    """Optional postal address and business identifiers, shared by Organization (the seller
    on a future invoice) and Customer (the buyer).

    Everything is nullable FREE TEXT. The only structure enforced is the SHAPE of the country
    code (two capital letters, ISO 3166-1 alpha-2 style). Whether an address is complete, or what
    a registration or VAT number looks like, is a jurisdiction policy and is deliberately not
    decided here.
    """

    address_line1: Mapped[str | None] = mapped_column(String(255))
    address_line2: Mapped[str | None] = mapped_column(String(255))
    postal_code: Mapped[str | None] = mapped_column(String(32))
    city: Mapped[str | None] = mapped_column(String(128))
    country_code: Mapped[str | None] = mapped_column(String(2))
    registration_number: Mapped[str | None] = mapped_column(String(64))
    vat_number: Mapped[str | None] = mapped_column(String(64))


def profile_constraints(table: str) -> tuple[CheckConstraint, ...]:
    """The structural checks for the BusinessProfile columns of `table`."""
    return (CheckConstraint("country_code IS NULL OR country_code ~ '^[A-Z]{2}$'", name=f"ck_{table}_country_code_shape"),)
