import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    ACCOUNTANT = "accountant"
    EMPLOYEE = "employee"
    VIEWER = "viewer"


class OrganizationUser(Base):
    """Membership: which user belongs to which organization, with what role.

    This is the source of truth for tenant access. Roles are stored here but
    not enforced anywhere yet.
    """

    __tablename__ = "organization_users"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_organization_users_org_user"),
        # varchar + CHECK instead of a native enum: much easier to change in Alembic.
        CheckConstraint(
            "role IN ('" + "', '".join(Role) + "')", name="ck_organization_users_role"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    # No separate index: the unique (organization_id, user_id) constraint covers it.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    role: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
