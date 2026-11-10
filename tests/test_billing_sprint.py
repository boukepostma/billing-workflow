import asyncio
from datetime import date, datetime, timezone

import mistralai.workflows as workflows
from mistralai.workflows.testing import create_test_worker

from worker.workflows.billing_sprint import BillingSprintWorkflow, _merge_entries
from worker.workflows.config_models import BillingConfig
from worker.workflows.models import (
    ConfirmHours,
    CreateInvoiceParams,
    CustomerConfirmation,
    HoursEntry,
    HoursRegistration,
    InvoiceResult,
    MailMessage,
    SendInvoiceParams,
    SprintInput,
)

SENT_MAILS: list[MailMessage] = []
REGISTRATIONS: list[HoursEntry] = []
INVOICES: list[CreateInvoiceParams] = []
SENT_INVOICES: list[SendInvoiceParams] = []
PAID_CHECKS: list[str] = []


def make_config() -> BillingConfig:
    from worker.activities.settings import load_billing_config

    return load_billing_config()


@workflows.activity(name="load-config")
async def mock_load_config(overrides: dict | None = None) -> BillingConfig:
    return make_config()


@workflows.activity(name="send-mail")
async def mock_send_mail(message: MailMessage, cfg: BillingConfig) -> str:
    SENT_MAILS.append(message)
    return f"mock-{len(SENT_MAILS)}"


@workflows.activity(name="register-hours")
async def mock_register_hours(entries: list[HoursEntry], cfg: BillingConfig) -> str:
    REGISTRATIONS.extend(entries)
    import json

    return json.dumps(
        [
            HoursRegistration(
                date=e.date, hours=e.hours, registration_id=f"reg-{e.date.isoformat()}"
            ).model_dump(mode="json")
            for e in entries
        ]
    )


@workflows.activity(name="create-invoice")
async def mock_create_invoice(params: CreateInvoiceParams, cfg: BillingConfig) -> str:
    INVOICES.append(params)
    return InvoiceResult(
        invoice_id="inv-1", invoice_number="2026-001", factuur_datum=params.factuur_datum
    ).model_dump_json()


@workflows.activity(name="send-invoice")
async def mock_send_invoice(params: SendInvoiceParams, cfg: BillingConfig) -> str:
    SENT_INVOICES.append(params)
    return "eboekhouden"


@workflows.activity(name="invoice-paid")
async def mock_invoice_paid(invoice_number: str, cfg: BillingConfig) -> bool:
    PAID_CHECKS.append(invoice_number)
    return len(PAID_CHECKS) >= 2


@workflows.activity(name="download-invoice-pdf")
async def mock_download_pdf(invoice_id: str, cfg: BillingConfig) -> bytes | None:
    return b"%PDF-1.4 mock"


MOCK_ACTIVITIES = [
    mock_load_config,
    mock_send_mail,
    mock_register_hours,
    mock_create_invoice,
    mock_send_invoice,
    mock_invoice_paid,
    mock_download_pdf,
]


async def test_skips_non_sprint_end_monday(temporal_env):
    async with create_test_worker(
        temporal_env, workflows=[BillingSprintWorkflow], activities=MOCK_ACTIVITIES
    ):
        handle = await temporal_env.client.start_workflow(
            "billing-sprint",
            SprintInput(run_date=date(2026, 11, 2), dry_run=True),
            id="test-skip",
            task_queue="test-task-queue",
        )
        result = await handle.result()
        assert result["status"] == "skipped_not_sprint_end"


async def test_full_flow_with_signals(temporal_env):
    SENT_MAILS.clear()
    REGISTRATIONS.clear()
    INVOICES.clear()
    SENT_INVOICES.clear()
    PAID_CHECKS.clear()
    async with create_test_worker(
        temporal_env, workflows=[BillingSprintWorkflow], activities=MOCK_ACTIVITIES
    ):
        handle = await temporal_env.client.start_workflow(
            "billing-sprint",
            SprintInput(run_date=date(2026, 10, 19)),
            id="test-full",
            task_queue="test-task-queue",
        )
        await asyncio.sleep(0.1)
        await handle.signal(
            "confirm_hours",
            ConfirmHours(ref="SP-2026-09-29", sender="bouke@guero.nl", entries=None).model_dump(
                mode="json"
            ),
        )
        await handle.signal(
            "customer_confirmation",
            CustomerConfirmation(
                ref="SP-2026-09-29",
                sender="abhirup.ghosh1@tcs.com",
                confirmed=True,
                received_at=datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc),
            ).model_dump(mode="json"),
        )
        result = await handle.result()
        assert result["status"] == "completed"
        assert result["sprint_start"] == "2026-09-29"
        assert result["sprint_end"] == "2026-10-19"
        assert len(result["registered"]) == 9
        assert len(result["proposed_hours"]) == 9
        invoice_params = INVOICES[0]
        assert invoice_params.factuur_datum == date(2026, 10, 21)
        assert invoice_params.factuurtekst == "Sprint 29 sept - 19 okt"
        assert len(invoice_params.regels) == 1
        assert invoice_params.regels[0].aantal == 72.0
        assert invoice_params.regels[0].omschrijving == "Sprint 29 sept - 19 okt"
        assert invoice_params.registration_ids[0] == "reg-2026-09-29"
        assert result["invoice"]["invoice_number"] == "2026-001"
        assert SENT_INVOICES[0].to == ["timesheets@geeksoftconsulting.com"]
        assert SENT_INVOICES[0].cc == ["vandana.c@geeksoft.eu"]
        assert SENT_INVOICES[0].emailsjabloon == "02. EN - Eerste Factuur"
        assert result["paid"] is True
        assert result["payment_reminders_sent"] == 1
        subjects = [m.subject for m in SENT_MAILS]
        assert any("Hours registration proposal" in s for s in subjects)
        assert any("Timesheet for your approval" in s for s in subjects)
        assert any("Payment reminder" in s for s in subjects)
        timesheet = next(m for m in SENT_MAILS if "Timesheet for your approval" in m.subject)
        assert timesheet.to == ["abhirup.ghosh1@tcs.com"]
        assert "pranab.mukherjee@tcs.com" in timesheet.cc


async def test_owner_changes_hours_via_signal(temporal_env):
    SENT_MAILS.clear()
    REGISTRATIONS.clear()
    INVOICES.clear()
    SENT_INVOICES.clear()
    PAID_CHECKS.clear()
    async with create_test_worker(
        temporal_env, workflows=[BillingSprintWorkflow], activities=MOCK_ACTIVITIES
    ):
        handle = await temporal_env.client.start_workflow(
            "billing-sprint",
            SprintInput(run_date=date(2026, 10, 19)),
            id="test-changes",
            task_queue="test-task-queue",
        )
        await asyncio.sleep(0.1)
        await handle.signal(
            "confirm_hours",
            ConfirmHours(
                ref="SP-2026-09-29",
                sender="bouke@guero.nl",
                entries=[{"date": "2026-10-06", "hours": 4.0}],
            ).model_dump(mode="json"),
        )
        await handle.signal(
            "customer_confirmation",
            CustomerConfirmation(
                ref="SP-2026-09-29",
                sender="x@tcs.com",
                confirmed=True,
                received_at=datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc),
            ).model_dump(mode="json"),
        )
        result = await handle.result()
        assert result["status"] == "completed"
        registered = {r["date"]: r["hours"] for r in result["registered"]}
        assert len(registered) == 9
        assert registered["2026-10-06"] == 4.0
        assert INVOICES[0].regels[0].aantal == 68.0  # 72 - 4


async def test_dry_run_completes_without_side_effects(temporal_env):
    SENT_MAILS.clear()
    REGISTRATIONS.clear()
    INVOICES.clear()
    SENT_INVOICES.clear()
    PAID_CHECKS.clear()
    async with create_test_worker(
        temporal_env, workflows=[BillingSprintWorkflow], activities=MOCK_ACTIVITIES
    ):
        handle = await temporal_env.client.start_workflow(
            "billing-sprint",
            SprintInput(run_date=date(2026, 10, 19), dry_run=True),
            id="test-dry",
            task_queue="test-task-queue",
        )
        result = await handle.result()
        assert result["status"] == "completed"
        assert result["dry_run"] is True
        assert SENT_MAILS == []
        assert REGISTRATIONS == []
        assert INVOICES == []
        assert SENT_INVOICES == []
        assert PAID_CHECKS == []


def test_merge_entries_semantics():
    from datetime import date as d

    proposal = [HoursEntry(date=d(2026, 10, 20), hours=8.0), HoursEntry(date=d(2026, 10, 21), hours=8.0)]
    overrides = [
        HoursEntry(date=d(2026, 10, 21), hours=4.0),   # override
        HoursEntry(date=d(2026, 10, 20), hours=0.0),   # remove
        HoursEntry(date=d(2026, 10, 25), hours=6.0),   # add (in sprint)
        HoursEntry(date=d(2026, 12, 24), hours=8.0),   # outside sprint -> ignored
    ]
    merged = _merge_entries(proposal, overrides, d(2026, 10, 20), d(2026, 11, 9))
    assert [(e.date.isoformat(), e.hours) for e in merged] == [
        ("2026-10-21", 4.0),
        ("2026-10-25", 6.0),
    ]
    assert _merge_entries(proposal, None, d(2026, 10, 20), d(2026, 11, 9)) == proposal


async def test_partial_reply_merges(temporal_env):
    SENT_MAILS.clear()
    REGISTRATIONS.clear()
    INVOICES.clear()
    SENT_INVOICES.clear()
    PAID_CHECKS.clear()
    async with create_test_worker(
        temporal_env, workflows=[BillingSprintWorkflow], activities=MOCK_ACTIVITIES
    ):
        handle = await temporal_env.client.start_workflow(
            "billing-sprint",
            SprintInput(run_date=date(2026, 10, 19)),
            id="test-merge",
            task_queue="test-task-queue",
        )
        await asyncio.sleep(0.1)
        await handle.signal(
            "confirm_hours",
            ConfirmHours(
                ref="SP-2026-09-29",
                sender="admin@guero.nl",
                entries=[
                    {"date": "2026-10-06", "hours": 4.0},
                    {"date": "2026-09-29", "hours": 0.0},
                    {"date": "2026-10-08", "hours": 6.0},
                ],
            ).model_dump(mode="json"),
        )
        await handle.signal(
            "customer_confirmation",
            CustomerConfirmation(
                ref="SP-2026-09-29",
                sender="x@tcs.com",
                confirmed=True,
                received_at=datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc),
            ).model_dump(mode="json"),
        )
        result = await handle.result()
        assert result["status"] == "completed"
        registered = [(r["date"], r["hours"]) for r in result["registered"]]
        assert ("2026-09-29", 8.0) not in registered
        assert ("2026-10-06", 4.0) in registered
        assert ("2026-10-08", 6.0) in registered
        assert len(registered) == 9
        assert INVOICES[0].regels[0].aantal == 66.0  # 7x8 + 4 + 6
