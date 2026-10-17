import uuid

from sqlalchemy import ForeignKey, Integer, LargeBinary, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.models.mixins import TenantOwned


class OrganizationLogo(TenantOwned, Base):
    """One uploaded version of an organization's logo, as a normalized PNG.

    Insert-only: replacing or removing the logo only changes `organizations.logo_id`. An issued invoice records the
    version it was issued with, so every version stays until the organization itself is deleted.
    """

    __tablename__ = "organization_logos"

    content: Mapped[bytes] = mapped_column(LargeBinary)
    sha256: Mapped[str] = mapped_column(String(64))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    byte_size: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
