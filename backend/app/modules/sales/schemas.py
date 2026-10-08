import uuid
from datetime import date, datetime
from typing import Annotated, Literal

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
from app.schemas.money import CountAndAmounts, MoneyIn, MoneyOut, PercentIn, PercentOut, QuantityIn, QuantityOut

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
    # A service line (kind "service"): a catalog service performed for a subject (a registered record such as a
    # customer or a horse), at a time (a time without an offset is the organization's local time; default: now), by a
    # member of the organization (optional). Notes are allowed on any line.
    kind: Literal["standard", "service"] = "standard"
    performed_at: datetime | None = None
    performed_by_user_id: uuid.UUID | None = None
    subject_type: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    subject_id: uuid.UUID | None = None
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None

    @model_validator(mode="after")
    def service_lines_need_a_service_and_a_subject(self):
        service_fields = ("performed_at", "performed_by_user_id", "subject_type", "subject_id")
        if self.kind == "service":
            missing = [name for name in ("item_id", "subject_type", "subject_id") if getattr(self, name) is None]
            if missing:
                raise ValueError(f"a service line needs: {', '.join(missing)}")
        elif any(getattr(self, name) is not None for name in service_fields):
            raise ValueError("only a service line has performed_at, performed_by_user_id and a subject")
        return self

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
    # Service details (service lines only; the subject changes as a pair).
    performed_at: datetime | None = None
    performed_by_user_id: uuid.UUID | None = None
    subject_type: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    subject_id: uuid.UUID | None = None
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None

    @model_validator(mode="after")
    def subject_changes_as_a_pair(self):
        if ("subject_type" in self.model_fields_set) != ("subject_id" in self.model_fields_set):
            raise ValueError("subject_type and subject_id change together")
        return self

    @field_validator("description", "unit", "quantity", "unit_price_ex_vat", "vat_rate", "performed_at", "subject_type", "subject_id")
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
    # The price before discounts and the discount layers (null: ad-hoc or a manually set price; see the line CHECK).
    list_unit_price: MoneyOut | None
    catalog_discount_percent: PercentOut | None
    customer_discount_percent: PercentOut | None
    kind: str
    performed_at: datetime | None
    performed_by: uuid.UUID | None
    subject_type: str | None
    subject_id: uuid.UUID | None
    notes: str | None
    # Resolved for display when read (live names; an invoice keeps its own snapshot).
    subject_label: str | None = None
    performed_by_name: str | None = None
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


class ServiceRecord(BaseModel):
    """One service performed (a service line), for a record's history: what, when, for whom, by whom, at what price,
    and in which transaction."""

    transaction_id: uuid.UUID
    transaction_date: date
    status: TransactionStatus
    currency: str | None
    line_id: uuid.UUID
    description: str
    quantity: QuantityOut
    gross_amount: MoneyOut
    performed_at: datetime
    performed_by_name: str | None
    subject_type: str
    subject_id: uuid.UUID
    subject_label: str | None
    notes: str | None


class SalesSummary(BaseModel):
    """The dashboard's figures from Sales, for the organization's current month (in its time zone)."""

    month_start: date
    today: date
    drafts: int
    completed_this_month: CountAndAmounts
    services_this_month: int
