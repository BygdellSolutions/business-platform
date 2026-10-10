"""Numbers handed out by the application (not on INSERT): from `record_counters`, one series per organization.

Same mechanism as the insert trigger of `app.models.mixins.Numbered`: one atomic upsert that locks the counter row
until the transaction ends, so two callers never get the same number and a rolled-back caller gives its number back.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


def next_number(db: Session, organization_id: uuid.UUID, series: str, first: int) -> int:
    return db.execute(
        text(
            "INSERT INTO record_counters (organization_id, series, next_number) VALUES (:o, :s, :first + 1)"
            " ON CONFLICT (organization_id, series) DO UPDATE SET next_number = record_counters.next_number + 1"
            " RETURNING next_number - 1"
        ),
        {"o": organization_id, "s": series, "first": first},
    ).scalar_one()
