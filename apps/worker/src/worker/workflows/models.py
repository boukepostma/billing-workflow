from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class HoursEntry(BaseModel):
    date: date
    hours: float


class SprintInput(BaseModel):
    run_date: date | None = None
    sprint_start: date | None = None
    dry_run: bool = False
    overrides: dict[str, Any] | None = None


class ConfirmHours(BaseModel):
    ref: str
    sender: str | None = None
    entries: list[HoursEntry] | None = None
    note: str | None = None


class CustomerConfirmation(BaseModel):
    ref: str
    sender: str
    confirmed: bool
    received_at: datetime


class CancelSignal(BaseModel):
    reason: str = ""


class MailMessage(BaseModel):
    to: list[str]
    cc: list[str] = Field(default_factory=list)
    subject: str
    body: str
    ref: str = ""
    attachment_name: str | None = None
    attachment_pdf: bytes | None = None


class HoursRegistration(BaseModel):
    date: date
    hours: float
    registration_id: str | None = None


class InvoiceLine(BaseModel):
    omschrijving: str
    aantal: float


class CreateInvoiceParams(BaseModel):
    ref: str
    sprint_start: date
    sprint_end: date
    factuur_datum: date
    factuurtekst: str
    regels: list[InvoiceLine]
    registration_ids: list[str] = Field(default_factory=list)


class InvoiceResult(BaseModel):
    invoice_id: str | None = None
    invoice_number: str | None = None
    factuur_datum: date | None = None
    linked_hours: bool = False
    pdf_url: str | None = None


class SendInvoiceParams(BaseModel):
    ref: str
    invoice_id: str | None = None
    invoice_number: str | None = None
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    emailsjabloon: str = ""
    smtp_subject: str = ""
    smtp_body: str = ""


class ReplyEmail(BaseModel):
    message_id: str
    mailbox_seq: str = ""
    sender: str
    subject: str
    date: datetime
    body: str


class IngestInput(BaseModel):
    lookback_days: int | None = None
    dry_run: bool = False
    overrides: dict[str, Any] | None = None


class IngestOutcome(BaseModel):
    message_id: str
    outcome: str
    detail: str = ""


class IngestResult(BaseModel):
    checked: int
    outcomes: list[IngestOutcome] = Field(default_factory=list)


class SprintResult(BaseModel):
    status: str
    ref: str | None = None
    sprint_start: date | None = None
    sprint_end: date | None = None
    dry_run: bool = False
    proposed_hours: list[HoursEntry] = Field(default_factory=list)
    registered: list[HoursRegistration] = Field(default_factory=list)
    timesheet_sent_at: datetime | None = None
    customer_reminders_sent: int = 0
    customer_sender: str | None = None
    customer_confirmed_at: datetime | None = None
    invoice: InvoiceResult | None = None
    invoice_sent_at: datetime | None = None
    paid: bool | None = None
    payment_reminders_sent: int = 0
    payment_checked_at: datetime | None = None
    detail: str = ""
