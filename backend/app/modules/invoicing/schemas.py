import uuid
from decimal import Decimal
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.modules.invoicing.models import InvoiceStatus
from app.schemas.customer import CustomerRef
from app.schemas.money import CountAndAmounts, MoneyIn, MoneyOut, PercentOut, QuantityOut
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
    # The price before discounts and the discount layers (null: ad-hoc or a manually set price; see the line CHECK).
    list_unit_price: MoneyOut | None
    catalog_discount_percent: PercentOut | None
    customer_discount_percent: PercentOut | None
    line_discount_percent: PercentOut | None
    # For a service line: {performed_at, performed_by, subject_type, subject_label, notes} as shown at invoicing.
    service: dict[str, Any] | None = None
    vat_rate: PercentOut
    net_amount: MoneyOut
    vat_amount: MoneyOut
    gross_amount: MoneyOut
    fields: list[dict[str, Any]]
    # Credit notes: how much of the line has been credited, and (for a line whose stock Inventory tracks) how much can
    # still go back into stock; null when the line has no stock to return, so the credit form offers no "returned" tick.
    credited_quantity: QuantityOut = Decimal("0.000")
    # What is left to credit (the line's quantity less what is credited), so no client needs to subtract.
    creditable_quantity: QuantityOut | None = None
    stock_returnable: QuantityOut | None = None


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
    # Payments (issued invoices only; null for a draft): the sum of recorded payments, what is left, and the state.
    paid_amount: MoneyOut | None = None
    outstanding_amount: MoneyOut | None = None
    payment_status: Literal["unpaid", "partially_paid", "paid"] | None = None
    # Credit notes (issued invoices only): the credited gross and whether all or part of the invoice is credited.
    credited_amount: MoneyOut | None = None
    credit_status: Literal["partly_credited", "credited"] | None = None
    # Paid beyond what is owed after credits: to be paid back to the customer.
    refund_due_amount: MoneyOut | None = None
    # Return cases not closed yet (requested, goods received or approved).
    open_returns: int = 0


class InvoicePaymentRead(BaseModel):
    id: uuid.UUID
    amount: MoneyOut
    paid_on: date
    method: str
    reference: str | None
    note: str | None
    # "payment" received, "refund" paid back to the customer (negative), or "reversal" of either (the opposite sign).
    kind: Literal["payment", "refund", "reversal"]
    # A reversal names the row it cancels; a payment or refund that was reversed says so.
    reverses_payment_id: uuid.UUID | None
    reversed: bool
    created_at: datetime
    created_by_name: str | None


PaymentMethod = Literal["bankgiro", "plusgiro", "bank_transfer", "swish", "card", "cash", "other"]


class PaymentCreate(BaseModel):
    """A payment a person records for an issued invoice."""

    model_config = ConfigDict(extra="forbid")

    amount: MoneyIn
    paid_on: date
    method: PaymentMethod
    reference: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)] | None = None
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] | None = None

    @field_validator("amount")
    @classmethod
    def positive(cls, value):
        if value <= 0:
            raise ValueError("must be greater than zero")
        return value


class RefundCreate(BaseModel):
    """Money paid back to the customer (after a credit note, when more was paid than is owed). Positive here; stored
    as a negative row."""

    model_config = ConfigDict(extra="forbid")

    amount: MoneyIn
    paid_on: date
    method: PaymentMethod
    reference: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)] | None = None
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] | None = None

    @field_validator("amount")
    @classmethod
    def positive(cls, value):
        if value <= 0:
            raise ValueError("must be greater than zero")
        return value


class PaymentReversal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] | None = None


class InvoiceRead(InvoiceSummary):
    """The whole stored document. Every field of it comes from the invoicing tables."""

    issued_by: uuid.UUID | None
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None
    customer_snapshot: dict[str, Any]
    issuer_snapshot: dict[str, Any]
    transactions: list[InvoiceTransactionRead]
    lines: list[InvoiceLineRead]
    vat_breakdown: list[VatRowRead]
    payments: list[InvoicePaymentRead] = []
    credit_notes: list["CreditNoteSummary"] = []
    returns: list["ReturnRead"] = []


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



class InvoicingSummary(BaseModel):
    """The dashboard's figures from Invoicing: what is to do and pending NOW, and the chosen month (issued, paid)."""

    month_start: date
    month_end: date
    ready_to_invoice: CountAndAmounts
    draft_invoices: int
    # Issued, past the due date and not fully paid: what is still outstanding.
    past_due: CountAndAmounts
    # Pending money: every issued invoice not fully paid; of those, the ones not yet due and the partially paid ones
    # (amounts are what is still outstanding).
    unpaid: CountAndAmounts
    not_yet_due: CountAndAmounts
    partially_paid: CountAndAmounts
    # Paid beyond what is owed after credit notes: money to pay back (amounts are the excess).
    refund_due: CountAndAmounts
    # Return cases still open, and of those the ones whose follow-up date has come (today or earlier).
    returns_open: int = 0
    returns_follow_up_due: int = 0
    # The chosen month: issued invoices (gross) and payments dated in it (reversals subtracted).
    issued_this_month: CountAndAmounts
    paid_this_month: CountAndAmounts
    # The same for the year of the month shown (to today for the current year).
    issued_this_year: CountAndAmounts
    paid_this_year: CountAndAmounts


# --- credit notes ----------------------------------------------------------------------------------------------------


class CreditLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_line_id: uuid.UUID
    quantity: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=3)]
    returned_to_stock: bool = False


class CreditNoteCreate(BaseModel):
    """What to credit of an issued invoice. The amounts are never accepted: each comes from the invoice line."""

    model_config = ConfigDict(extra="forbid")

    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    lines: list[CreditLineIn] = Field(min_length=1, max_length=500)
    # The approved return case this credit note settles (it is closed in the same database transaction).
    return_id: uuid.UUID | None = None

    @field_validator("lines")
    @classmethod
    def each_line_once(cls, value: list[CreditLineIn]) -> list[CreditLineIn]:
        if len({line.invoice_line_id for line in value}) != len(value):
            raise ValueError("each invoice line may be listed once")
        return value


class CreditNoteSummary(BaseModel):
    id: uuid.UUID
    number_text: str
    credit_date: date
    reason: str
    currency: str
    net_amount: MoneyOut
    vat_amount: MoneyOut
    gross_amount: MoneyOut
    issued_at: datetime
    issued_by_name: str | None


class CreditNoteLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    invoice_line_id: uuid.UUID
    position: int
    description: str
    unit: str
    quantity: QuantityOut
    unit_price_ex_vat: MoneyOut
    vat_rate: PercentOut
    net_amount: MoneyOut
    vat_amount: MoneyOut
    gross_amount: MoneyOut
    returned_to_stock: bool


class CreditNoteRead(CreditNoteSummary):
    invoice_id: uuid.UUID
    invoice_number_text: str | None
    invoice_date: date
    customer_snapshot: dict[str, Any]
    issuer_snapshot: dict[str, Any]
    lines: list[CreditNoteLineRead]
    vat_breakdown: list[VatRowRead]




# --- return cases ----------------------------------------------------------------------------------------------------

ReasonText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
NoteText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ReturnLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_line_id: uuid.UUID
    quantity: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=3)]


class ReturnCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: ReasonText
    follow_up_on: date
    lines: list[ReturnLineIn] = Field(min_length=1, max_length=500)

    @field_validator("lines")
    @classmethod
    def each_line_once(cls, value: list[ReturnLineIn]) -> list[ReturnLineIn]:
        if len({line.invoice_line_id for line in value}) != len(value):
            raise ValueError("each invoice line may be listed once")
        return value


class GoodsReceived(BaseModel):
    """The goods came back; the listed invoice lines go back into stock with their whole returned quantity."""

    model_config = ConfigDict(extra="forbid")

    to_stock: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    note: NoteText | None = None


class ReturnStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: NoteText | None = None


class ReturnRejection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: ReasonText


class ReturnNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: NoteText


class ReturnFollowUp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    follow_up_on: date


class ReturnLineRead(BaseModel):
    invoice_line_id: uuid.UUID
    description: str
    unit: str
    quantity: QuantityOut
    returned_to_stock: bool


class ReturnEventRead(BaseModel):
    kind: Literal["opened", "note", "goods_received", "approved", "rejected", "credited", "follow_up"]
    note: str | None
    created_at: datetime
    created_by_name: str | None


class ReturnRead(BaseModel):
    id: uuid.UUID
    state: Literal["requested", "goods_received", "approved", "rejected", "credited"]
    reason: str
    follow_up_on: date
    rejection_reason: str | None
    credit_note_id: uuid.UUID | None
    created_at: datetime
    lines: list[ReturnLineRead]
    events: list[ReturnEventRead]


InvoiceRead.model_rebuild()
