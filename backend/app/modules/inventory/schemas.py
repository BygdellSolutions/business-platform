import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from app.schemas.money import CountIn, QuantityOut

Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class StockAdjustment(BaseModel):
    """A person's change of an item's stock.

    `count`: "there are N on the shelf" (the first count of an item is its opening stock);
    `add` / `remove`: N more or fewer. Every change after the opening count needs a note saying why.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["count", "add", "remove"]
    quantity: CountIn
    note: Note | None = None

    @model_validator(mode="after")
    def moves_something(self):
        if self.kind != "count" and self.quantity == 0:
            raise ValueError("quantity must be greater than zero")
        return self


class StockMovementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reason: str
    quantity_change: QuantityOut
    quantity_before: QuantityOut
    quantity_after: QuantityOut
    note: str | None
    transaction_id: uuid.UUID | None
    created_at: datetime
    created_by: uuid.UUID | None
    created_by_name: str | None = None


class ItemAvailability(BaseModel):
    item_id: uuid.UUID
    on_hand: QuantityOut
    available: QuantityOut


class TransactionDemand(BaseModel):
    """What a draft asks of one stock-tracking item, summed over its lines, against what is available now.

    A warning, never a refusal: at completion the available units are delivered and the shortage is backordered.
    """

    item_id: uuid.UUID
    requested: QuantityOut
    on_hand: QuantityOut
    available: QuantityOut
    shortage: QuantityOut


class StockRead(BaseModel):
    item_id: uuid.UUID
    track_stock: bool
    on_hand: QuantityOut
    # The latest movements, newest first.
    movements: list[StockMovementRead]
