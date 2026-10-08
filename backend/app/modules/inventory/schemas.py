import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.money import CountIn, QuantityIn, QuantityOut

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
    # Promised to open backorders.
    committed: QuantityOut
    # On hand minus committed (never below zero).
    available: QuantityOut
    # On its way: open incoming deliveries not received yet.
    incoming: QuantityOut
    low_stock_threshold: QuantityOut | None
    # Separate states that can hold at the same time: "out_of_stock" (nothing on hand) or "low_stock" (some, but
    # below the threshold), "backordered" (sales are waiting) and "incoming" (a delivery is on its way).
    states: list[Literal["out_of_stock", "low_stock", "backordered", "incoming"]]


class TransactionDemand(BaseModel):
    """What a draft asks of one stock-tracking item, summed over its lines, against what is available now.

    A warning, never a refusal: at completion the available units are delivered and the shortage is backordered.
    """

    item_id: uuid.UUID
    requested: QuantityOut
    on_hand: QuantityOut
    available: QuantityOut
    incoming: QuantityOut
    shortage: QuantityOut


class LineFulfillmentRead(BaseModel):
    """What completion did with one stock-tracking line (the active record; a cancelled one is history)."""

    transaction_line_id: uuid.UUID
    item_id: uuid.UUID
    ordered: QuantityOut
    delivered: QuantityOut
    backordered: QuantityOut
    fulfilled_later: QuantityOut
    # Backordered units still waiting.
    remaining: QuantityOut
    state: Literal["waiting_for_stock", "partially_fulfilled", "ready_to_fulfill", "fulfilled", "cancelled"]


Text255 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class IncomingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: uuid.UUID
    quantity: QuantityIn
    expected_on: date | None = None
    supplier: Text255 | None = None
    reference: Text255 | None = None


class Receipt(BaseModel):
    """A person receives goods: `quantity` of them (empty: everything still expected)."""

    model_config = ConfigDict(extra="forbid")

    quantity: QuantityIn | None = None
    note: Note | None = None


class IncomingRead(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    item_name: str
    item_unit: str
    quantity: QuantityOut
    received: QuantityOut
    remaining: QuantityOut
    expected_on: date | None
    supplier: str | None
    reference: str | None
    state: Literal["expected", "partially_received", "received", "cancelled"]
    created_at: datetime
    created_by_name: str | None
    cancelled_at: datetime | None


class BackorderRead(BaseModel):
    """An open (or, on request, closed) backorder: units of a completed sale still waiting for stock."""

    fulfillment_id: uuid.UUID
    transaction_id: uuid.UUID
    transaction_line_id: uuid.UUID
    transaction_date: date
    customer_name: str | None
    item_id: uuid.UUID
    item_name: str
    item_unit: str
    backordered: QuantityOut
    fulfilled_later: QuantityOut
    remaining: QuantityOut
    state: Literal["waiting_for_stock", "partially_fulfilled", "ready_to_fulfill", "fulfilled", "cancelled"]
    # When the sale was completed (the backlog is served oldest first).
    created_at: datetime


class ProposedAllocation(BaseModel):
    fulfillment_id: uuid.UUID
    transaction_id: uuid.UUID
    remaining: QuantityOut
    proposed: QuantityOut


class AllocationProposal(BaseModel):
    """How the stock on hand WOULD be shared among the item's open backorders, oldest first. Nothing happens until a
    person confirms it (and they may change the quantities)."""

    item_id: uuid.UUID
    on_hand: QuantityOut
    proposals: list[ProposedAllocation]


class Allocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fulfillment_id: uuid.UUID
    quantity: QuantityIn


class AllocationConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allocations: list[Allocation] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def each_backorder_once(self):
        ids = [allocation.fulfillment_id for allocation in self.allocations]
        if len(set(ids)) != len(ids):
            raise ValueError("each backorder may appear only once")
        return self


class StockRead(BaseModel):
    item_id: uuid.UUID
    track_stock: bool
    on_hand: QuantityOut
    # The latest movements, newest first.
    movements: list[StockMovementRead]
