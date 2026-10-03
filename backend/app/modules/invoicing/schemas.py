import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.invoicing.models import InvoiceStatus
from app.schemas.customer import CustomerRef
from app.schemas.money import MoneyOut, PercentOut, QuantityOut
from app.schemas.profile import optional_text

MAX_TRANSACTIONS_PER_INVOICE = 200

Description = optional_text(2000)  # blank means "no description"

# As everywhere: no organization_id field and extra="forbid". The customer, the currency and the
# amounts are never accepted: they are derived from the locked source transactions.


class InvoiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_TRANSACTIONS_PER_INVOICE)
    invoice_date: date | None = None  # defaults to today (UTC)
    due_date: date | None = None
    description: Description = None

    @field_validator("transaction_ids")
    @classmethod
    def no_duplicates(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(value)) != len(value):
            raise ValueError("each transaction may be listed once")
        return value

    @model_validator(mode="after")
    def due_not_before_invoice_date(self):
        if self.invoice_date and self.due_date and self.due_date < self.invoice_date:
            raise ValueError("the due date cannot be before the invoice date")
        return self


class InvoiceUpdate(BaseModel):
    """Draft header data only. Sources and amounts are never editable: delete the draft and
    create another one to choose different transactions."""

    model_config = ConfigDict(extra="forbid")

    invoice_date: date | None = None
    due_date: date | None = None
    description: Description = None

    @field_validator("invoice_date")
    @classmethod
    def not_null(cls, value):
        # Runs only for fields that were sent; the column is NOT NULL.
        if value is None:
            raise ValueError("may not be null")
        return value


class VatRowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vat_rate: PercentOut
    net_amount: MoneyOut
    vat_amount: MoneyOut


class InvoiceLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    source_transaction_id: uuid.UUID  # navigation metadata, never document content
    source_line_id: uuid.UUID
    description: str
    unit: str
    quantity: QuantityOut
    unit_price_ex_vat: MoneyOut
    vat_rate: PercentOut
    net_amount: MoneyOut
    vat_amount: MoneyOut
    gross_amount: MoneyOut
    fields: list[dict[str, Any]]


class InvoiceTransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transaction_id: uuid.UUID
    position: int
    transaction_date: date
    source_version: int
    fields: list[dict[str, Any]]


class InvoiceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: InvoiceStatus
    version: int  # send as If-Match when editing, issuing or deleting a draft
    series: str
    number: int | None
    number_text: str | None
    customer_id: uuid.UUID  # navigation metadata
    customer_name: str  # the snapshot's name, not the live customer's
    currency: str
    invoice_date: date
    due_date: date | None
    description: str | None
    net_amount: MoneyOut
    vat_amount: MoneyOut
    gross_amount: MoneyOut
    transaction_count: int
    issued_at: datetime | None
    created_at: datetime
    updated_at: datetime


class InvoiceRead(InvoiceSummary):
    """The whole stored document. Every field of it comes from the invoicing tables."""

    issued_by: uuid.UUID | None
    customer_snapshot: dict[str, Any]
    issuer_snapshot: dict[str, Any]
    transactions: list[InvoiceTransactionRead]
    lines: list[InvoiceLineRead]
    vat_breakdown: list[VatRowRead]


class InvoiceableTotals(BaseModel):
    net_amount: MoneyOut
    vat_amount: MoneyOut
    gross_amount: MoneyOut


class InvoiceableTransaction(BaseModel):
    id: uuid.UUID
    transaction_date: date
    billing_customer_id: uuid.UUID
    billing_customer: CustomerRef
    currency: str
    line_count: int
    version: int
    totals: InvoiceableTotals


class InvoiceStateRead(BaseModel):
    """Whether a transaction is on an invoice. Derived from invoicing records only."""

    transaction_id: uuid.UUID
    state: Literal["none", "draft", "invoiced"]
    invoice_id: uuid.UUID | None
    number_text: str | None

