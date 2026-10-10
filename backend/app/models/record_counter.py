import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base


class RecordCounter(Base):
    """The next record number per organization and series (the numbered table's name).

    Written only by the `assign_record_number` trigger (see `app.models.mixins.Numbered`); the model exists so
    Alembic sees the table and organization deletion can remove its rows.
    """

    __tablename__ = "record_counters"
    __table_args__ = (
        PrimaryKeyConstraint("organization_id", "series", name="pk_record_counters"),
        CheckConstraint("next_number >= 1", name="ck_record_counters_next_positive"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"))
    series: Mapped[str] = mapped_column(String(32))
    next_number: Mapped[int] = mapped_column(BigInteger)
