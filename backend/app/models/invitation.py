"""An invitation to join an organization: a bearer secret bound to one organization, one email and one role.

Lifecycle (every state is a column, nothing is ever deleted):

    pending   revoked_at IS NULL AND accepted_at IS NULL          (and, to be USABLE, expires_at is still in the future)
    expired   pending, but expires_at has passed: not usable. It still occupies the "one pending invitation per
              organization and email" slot until someone supersedes it (creating or regenerating an invitation for
              the same email revokes it first), because a unique index cannot use the moving `now()`.
    revoked   revoked_at set (also what "superseded" and "regenerated" are)
    accepted  accepted_at and accepted_by set; the membership exists (or already existed and was left untouched)

Only SHA-256(token) is stored. The raw token is returned once, at creation, and never again. The row is immutable
except for the two lifecycle pairs above.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.models.mixins import TenantOwned
from app.models.organization_user import Role

HEX64 = "^[0-9a-f]{64}$"


class OrganizationInvitation(TenantOwned, Base):
    __tablename__ = "organization_invitations"
    __table_args__ = (
        CheckConstraint("role IN ('" + "', '".join(Role) + "')", name="ck_organization_invitations_role"),
        CheckConstraint(f"token_hash ~ '{HEX64}'", name="ck_organization_invitations_token_hash_shape"),
        CheckConstraint("email = lower(btrim(email)) AND email <> ''", name="ck_organization_invitations_email_normalized"),
        CheckConstraint("NOT (revoked_at IS NOT NULL AND accepted_at IS NOT NULL)", name="ck_organization_invitations_one_outcome"),
        CheckConstraint("(accepted_at IS NULL) = (accepted_by IS NULL)", name="ck_organization_invitations_accepted_pair"),
        # At most one invitation that is neither revoked nor accepted per organization and email. Expiry is NOT part of
        # the predicate (it cannot be: now() is not immutable); an expired row is revoked before it is replaced.
        Index(
            "uq_organization_invitations_pending",
            "organization_id",
            "email",
            unique=True,
            postgresql_where=text("revoked_at IS NULL AND accepted_at IS NULL"),
        ),
        Index("uq_organization_invitations_token_hash", "token_hash", unique=True),
    )

    # The email is NOT a foreign key: the invitee may have no account yet. Stored normalized (trimmed, lowercased).
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(32))
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
