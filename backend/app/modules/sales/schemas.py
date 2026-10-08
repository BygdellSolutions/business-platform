import uuid
from datetime import date, datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.modules.sales.models import TransactionStatus
from app.schemas.customer import CustomerRef
from app.schemas.profile import CurrencyCode
from app.schemas.money import MoneyIn, MoneyOut, PercentIn, PercentOut, QuantityIn, QuantityOut

Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Unit = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]

MAX_LINES_PER_REQUEST = 200

# As with every tenant-owned resource: no organization_id field and extra="forbid".
# Amounts (net/VAT/gross) and status are never accepted from the client: amounts are
# calculated, and the lifecycle moves only through the action endpoints.


class LineCreate(BaseModel):
    """A line. With `item_id`, description/unit/price/VAT default from the Item (a copy)
    and any of them may be overridden. Without `item_id` (ad-hoc line) all four are required."""

    model_config = ConfigDict(extra="forbid")

    item_id: uuid.UUID | None = None
    description: Description | None = None
    unit: Unit | None = None
    quantity: QuantityIn
    unit_price_ex_vat: MoneyIn | None = None
    vat_rate: PercentIn | None = None

    @model_validator(mode="after")
    def ad_hoc_lines_need_every_value(self):
        if self.item_id is None:
            missing = [
                name
                for name in ("description", "unit", "unit_price_ex_vat", "vat_rate")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(f"without item_id these are required: {', '.join(missing)}")
        return self


class LineUpdate(BaseModel):
    """Partial update. Changing `item_id` to another item re-copies the Item's values for
    every field not overridden in the same request; `item_id: null` detaches the line
    from the catalog and keeps its current values."""

    model_config = ConfigDict(extra="forbid")

    item_id: uuid.UUID | None = None
    description: Description | None = None
    unit: Unit | None = None
    quantity: QuantityIn | None = None
    unit_price_ex_vat: MoneyIn | None = None
    vat_rate: PercentIn | None = None

    @field_validator("description", "unit", "quantity", "unit_price_ex_vat", "vat_rate")
    @classmethod
    def not_null(cls, value):
        # Runs only for fields that were sent; these columns are NOT NULL.
        if value is None:
            raise ValueError("may not be null")
        return value


class TransactionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    billing_customer_id: uuid.UUID
    transaction_date: date | None = None  # defaults to today (UTC)
    lines: list[LineCreate] = Field(default_factory=list, max_length=MAX_LINES_PER_REQUEST)


class TransactionUpdate(BaseModel):
    """Header changes (draft only)."""

    model_config = ConfigDict(extra="forbid")

    billing_customer_id: uuid.UUID | None = None
    transaction_date: date | None = None

    @field_validator("billing_customer_id", "transaction_date")
    @classmethod
    def not_null(cls, value):
        if value is None:
            raise ValueError("may not be null")
        return value


class LineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    transaction_id: uuid.UUID
    item_id: uuid.UUID | None
    position: int
    version: int  # send as If-Match when editing or deleting this line
    description: str
    unit: str
    quantity: QuantityOut
    unit_price_ex_vat: MoneyOut
    vat_rate: PercentOut
    net_amount: MoneyOut
    vat_amount: MoneyOut
    gross_amount: MoneyOut
    created_at: datetime
    updated_at: datetime
    # Who created it and who changed it last (null: not recorded, e.g. before authors were kept).
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None


class VatBreakdownRead(BaseModel):
    vat_rate: PercentOut
    net_amount: MoneyOut
    vat_amount: MoneyOut


class TotalsRead(BaseModel):
    """Sums of the stored line amounts; the breakdown groups those same sums by VAT rate."""

    net_amount: MoneyOut
    vat_amount: MoneyOut
    gross_amount: MoneyOut
    vat_breakdown: list[VatBreakdownRead]


class TransactionSummary(BaseModel):
    id: uuid.UUID
    billing_customer_id: uuid.UUID
    billing_customer: CustomerRef
    transaction_date: date
    status: TransactionStatus
    # Copied from the organization's default when the transaction was created; never changes.
    # null only for a transaction that predates currencies and was not assigned one yet.
    currency: str | None
    line_count: int
    version: int  # send as If-Match when completing, reopening or cancelling
    header_version: int  # send as If-Match when editing the header
    totals: TotalsRead
    created_at: datetime
    updated_at: datetime
    # Who created it and who changed it last (null: not recorded, e.g. before authors were kept).
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None


class TransactionRead(TransactionSummary):
    lines: list[LineRead]


class CurrencyStatus(BaseModel):
    default_currency: str | None
    transactions_without_currency: int


class AssignCurrency(BaseModel):
    """Confirm that the prices of every currency-less transaction are in `currency`, which must
    be the organization's default. Sent on purpose: nothing assumes it."""

    model_config = ConfigDict(extra="forbid")

    currency: CurrencyCode


class AssignCurrencyResult(BaseModel):
    currency: str
    assigned: int
