from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base


class JobEffect(Base):
    """One row = "the effect of this job step has been applied" (see app/core/jobs.py `once`). Infrastructure, not business
    data: it belongs to no organization (the key says what it guards) and is inserted in the same transaction as the effect."""

    __tablename__ = "job_effects"
    __table_args__ = (CheckConstraint("length(key) BETWEEN 1 AND 255", name="ck_job_effects_key_length"),)

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
