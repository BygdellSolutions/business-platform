"""What Scheduling asks Sales for, in words neither module needs to import from the other.

An appointment can make a draft order (and later bring it up to date). Scheduling describes WHAT should be billed as a `BookingOrder`
and asks through the registry (`order.create_from_booking`, `order.sync_from_booking`, `order.cancel_draft`, `order.statuses`); Sales answers. Nothing here
knows what a horse or an order line is.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

# What a line made from an appointment is marked with, so that updating the order touches only those lines.
APPOINTMENT_SOURCE = "appointment"


@dataclass(frozen=True)
class BookingLine:
    """One service performed for one subject (a horse, or the customer when the booking names none)."""

    item_id: uuid.UUID
    subject_type: str
    subject_id: uuid.UUID


@dataclass(frozen=True)
class BookingOrder:
    source_type: str
    source_id: uuid.UUID
    customer_id: uuid.UUID
    performed_at: datetime  # when it happens: the order's date (in the organization's time zone) and the time on each service line
    performed_by_user_id: uuid.UUID | None
    lines: tuple[BookingLine, ...]
