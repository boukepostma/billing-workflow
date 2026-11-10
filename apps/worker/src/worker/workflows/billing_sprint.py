import asyncio
from datetime import date, datetime, timedelta

import mistralai.workflows as workflows
from mistralai.workflows import workflow

from worker.workflows import sprint_calendar as cal
from worker.workflows.activity_calls import call_activity, coerce, coerce_list
from worker.workflows.config_models import BillingConfig
from worker.workflows.models import (
    CancelSignal,
    ConfirmHours,
    CreateInvoiceParams,
    CustomerConfirmation,
    HoursEntry,
    HoursRegistration,
    InvoiceLine,
    InvoiceResult,
    MailMessage,
    SendInvoiceParams,
    SprintInput,
    SprintResult,
)




async def _sleep(duration: timedelta) -> None:
    try:
        await workflow.wait_condition(lambda: False, timeout=duration)
    except asyncio.TimeoutError:
        pass




def _merge_entries(
    proposal: list[HoursEntry],
    overrides: list[HoursEntry] | None,
    sprint_start: date,
    sprint_end: date,
) -> list[HoursEntry]:
    if not overrides:
        return list(proposal)
    override_map = {e.date: e.hours for e in overrides}
    merged: list[HoursEntry] = []
    for p in proposal:
        hours = override_map.pop(p.date, p.hours)
        if hours > 0:
            merged.append(HoursEntry(date=p.date, hours=hours))
    for extra_date, hours in override_map.items():
        if sprint_start <= extra_date <= sprint_end and hours > 0:
            merged.append(HoursEntry(date=extra_date, hours=hours))
    merged.sort(key=lambda e: e.date)
    return merged




def _preview_wrap(cfg: BillingConfig, msg: MailMessage) -> MailMessage:
    banner = (
        f"[PREVIEW - not sent to customers. Original To: {', '.join(msg.to)}; "
        f"Cc: {', '.join(msg.cc)}]\n\n"
    )
    return MailMessage(
        to=[cfg.owner.email],
        cc=[],
        subject=f"[PREVIEW] {msg.subject}",
        body=banner + msg.body,
        ref=msg.ref,
    )


def _send(cfg: BillingConfig, msg: MailMessage, preview: bool) -> MailMessage:
    return _preview_wrap(cfg, msg) if preview else msg


def _recipients(
    cfg: BillingConfig, template, default_to: list[str], default_cc: list[str] | None = None
) -> tuple[list[str], list[str]]:
    to = template.to or default_to
    cc = template.cc or (default_cc or [])
    return to, cc


def fmt_date(d: date, cfg: BillingConfig) -> str:
    inv = cfg.eboekhouden.invoice
    if inv.date_format == "nl-short":
        month = inv.month_names[d.month - 1] if d.month - 1 < len(inv.month_names) else d.strftime("%b").lower()
        return f"{d.day} {month}"
    return d.isoformat()


def _fmt_dates(cfg: BillingConfig, sprint_start: date, sprint_end: date) -> dict[str, str]:
    return {"start": fmt_date(sprint_start, cfg), "end": fmt_date(sprint_end, cfg)}


def _proposal_mail(cfg: BillingConfig, ref: str, sprint_start: date, sprint_end: date,
                   proposal: list[HoursEntry]) -> MailMessage:
    lines = "\n".join(
        f"- {entry.date.isoformat()} ({entry.date.strftime('%A')}): {entry.hours:g} hours"
        for entry in proposal
    )
    dates = _fmt_dates(cfg, sprint_start, sprint_end)
    body = (
        f"Hi {cfg.owner.name},\n\n"
        f"Sprint {dates['start']} - {dates['end']} has ended (last day: Monday {sprint_end.isoformat()}).\n"
        f"Proposed hour registration ({len(proposal)} days, {sum(e.hours for e in proposal):g} hours total, "
        f"customer {cfg.eboekhouden.relation_code}, project \"{cfg.eboekhouden.project}\", "
        f"{cfg.eboekhouden.kilometers} km):\n\n"
        f"{lines}\n\n"
        f"Reply to this mail:\n"
        f"- \"confirm\" to accept the full proposal as-is, or\n"
        f"- send only the lines you want to change or add, e.g. \"2026-10-28: 4\" (override that day), "
        f"\"2026-11-02: 6\" (add a day), or \"2026-10-27: 0\" (remove a day). "
        f"Days you don't mention keep the proposed {cfg.sprint.hours_per_day:g} hours. Reference: {ref}\n"
    )
    to, cc = _recipients(cfg, cfg.mail.proposal, [cfg.owner.email])
    iso = {"sprint_start": sprint_start.isoformat(), "sprint_end": sprint_end.isoformat(), "ref": ref}
    return MailMessage(
        to=to, cc=cc, subject=cfg.mail.proposal.subject.format(**iso), body=body, ref=ref
    )


def _timesheet_mail(cfg: BillingConfig, ref: str, sprint_start: date, sprint_end: date,
                    registered: list[HoursRegistration]) -> MailMessage:
    lines = "\n".join(
        f"- {reg.date.isoformat()} ({reg.date.strftime('%A')}): {reg.hours:g} hours" for reg in registered
    )
    dates = _fmt_dates(cfg, sprint_start, sprint_end)
    body = (
        f"Hi,\n\n"
        f"Please find the timesheet for sprint {dates['start']} - {dates['end']} "
        f"({len(registered)} days, {sum(r.hours for r in registered):g} hours total):\n\n{lines}\n\n"
        f"Please reply to this mail to confirm the timesheet (e.g. \"confirmed\"). Reference: {ref}\n"
    )
    to, cc = _recipients(cfg, cfg.mail.timesheet, [])
    iso = {"sprint_start": sprint_start.isoformat(), "sprint_end": sprint_end.isoformat(), "ref": ref}
    return MailMessage(
        to=to, cc=cc, subject=cfg.mail.timesheet.subject.format(**iso), body=body, ref=ref
    )


def _simple_mail(cfg: BillingConfig, template, default_to: list[str], default_cc: list[str] | None, ref: str,
                 sprint_start: date, sprint_end: date, body: str, extra: dict | None = None) -> MailMessage:
    to, cc = _recipients(cfg, template, default_to, default_cc)
    fmt = {
        "sprint_start": sprint_start.isoformat(),
        "sprint_end": sprint_end.isoformat(),
        "ref": ref,
    }
    if extra:
        fmt.update(extra)
    return MailMessage(to=to, cc=cc, subject=template.subject.format(**fmt), body=body, ref=ref)


@workflows.workflow.define(
    name="billing-sprint",
    workflow_display_name="Billing Sprint",
    workflow_description=(
        "End-to-end sprint billing: propose hours to the owner, register them in "
        "e-Boekhouden (one per worked day), send the timesheet to the customer for "
        "approval (reminders EOD Wed/Fri), create the invoice dated on the customer's "
        "confirmation, send it (e-Boekhouden mail first, SMTP fallback), then monitor "
        "payment 46 days after sending with daily reminders until paid."
    ),
    execution_timeout=timedelta(days=120),
)
class BillingSprintWorkflow:
    def __init__(self) -> None:
        self._hours_confirmation: ConfirmHours | None = None
        self._customer_confirmation: CustomerConfirmation | None = None
        self._cancel_reason: str | None = None
        self._phase = "init"
        self._sprint_start: date | None = None

    @workflows.workflow.signal(name="confirm_hours")
    async def on_confirm_hours(self, payload: ConfirmHours) -> None:
        self._hours_confirmation = payload

    @workflows.workflow.signal(name="customer_confirmation")
    async def on_customer_confirmation(self, payload: CustomerConfirmation) -> None:
        self._customer_confirmation = payload

    @workflows.workflow.signal(name="cancel")
    async def on_cancel(self, payload: CancelSignal) -> None:
        self._cancel_reason = payload.reason

    @workflows.workflow.query(name="state")
    def get_state(self) -> dict:
        return {
            "phase": self._phase,
            "sprint_start": self._sprint_start.isoformat() if self._sprint_start else None,
            "hours_confirmation": self._hours_confirmation.model_dump(mode="json")
            if self._hours_confirmation
            else None,
            "customer_confirmation": self._customer_confirmation.model_dump(mode="json")
            if self._customer_confirmation
            else None,
            "cancel_reason": self._cancel_reason,
        }

    @workflows.workflow.entrypoint
    async def run(self, input: SprintInput) -> SprintResult:
        cfg = coerce(await call_activity("load-config", input.overrides), BillingConfig)
        now = workflow.now()
        run_date = input.run_date or now.date()
        window = cal.resolve_sprint_window(cfg.sprint, run_date, input.sprint_start)
        if window is None:
            return SprintResult(
                status="skipped_not_sprint_end",
                dry_run=input.dry_run,
                detail=(
                    f"{run_date.isoformat()} is a Monday but not a sprint end "
                    f"(anchor={cfg.sprint.anchor_tuesday})"
                ),
            )
        sprint_start, sprint_end = window
        ref = cal.ref_for(sprint_start)
        proposal = [
            HoursEntry(date=d, hours=cfg.sprint.hours_per_day)
            for d in cal.work_dates(sprint_start, sprint_end, cfg.sprint.work_days)
        ]
        result = SprintResult(
            status="running",
            ref=ref,
            sprint_start=sprint_start,
            sprint_end=sprint_end,
            dry_run=input.dry_run,
            proposed_hours=proposal,
        )
        self._sprint_start = sprint_start

        preview = input.preview
        if not input.dry_run:
            self._phase = "awaiting_owner_confirmation"
            await call_activity(
                "send-mail",
                _send(cfg, _proposal_mail(cfg, ref, sprint_start, sprint_end, proposal), preview),
                cfg,
            )
        if input.dry_run or preview:
            self._hours_confirmation = ConfirmHours(ref=ref, note="dry-run/preview auto-confirm")
            self._customer_confirmation = CustomerConfirmation(
                ref=ref, sender="preview@example.com", confirmed=True, received_at=now
            )
        else:
            await self._wait_for_owner(cfg, ref, sprint_start, sprint_end, preview)

        if self._cancel_reason is not None:
            result.status = "cancelled"
            result.detail = self._cancel_reason
            return result
        entries = _merge_entries(
            proposal,
            self._hours_confirmation.entries if self._hours_confirmation else None,
            sprint_start,
            sprint_end,
        )

        self._phase = "registering_hours"
        if input.dry_run or preview:
            registered = [
                HoursRegistration(date=e.date, hours=e.hours, registration_id="DRY-RUN")
                for e in entries
            ]
        else:
            registered = coerce_list(await call_activity("register-hours", entries, cfg), HoursRegistration)
        result.registered = registered

        self._phase = "awaiting_customer_confirmation"
        timesheet_sent_at = workflow.now()
        result.timesheet_sent_at = timesheet_sent_at
        if not input.dry_run:
            await call_activity(
                "send-mail",
                _send(cfg, _timesheet_mail(cfg, ref, sprint_start, sprint_end, registered), preview),
                cfg,
            )
        if not input.dry_run and not preview:
            await self._wait_for_customer(cfg, ref, sprint_start, sprint_end, timesheet_sent_at, result, preview)

        confirmation = self._customer_confirmation
        if confirmation is None or not confirmation.confirmed:
            result.status = "cancelled"
            result.detail = "customer rejected the timesheet" if confirmation else "no customer confirmation"
            return result
        result.customer_sender = confirmation.sender
        result.customer_confirmed_at = confirmation.received_at

        self._phase = "creating_invoice"
        invoice_date = confirmation.received_at.date()
        dates = _fmt_dates(cfg, sprint_start, sprint_end)
        inv_cfg = cfg.eboekhouden.invoice
        factuurtekst = inv_cfg.factuurtekst_template.format(**dates)
        if inv_cfg.lines_mode == "per_day":
            regels = [
                InvoiceLine(
                    omschrijving=f"{cfg.eboekhouden.hours_description} {reg.date.isoformat()}",
                    aantal=reg.hours,
                )
                for reg in registered
            ]
        else:
            regels = [
                InvoiceLine(
                    omschrijving=inv_cfg.line_omschrijving_template.format(**dates),
                    aantal=sum(reg.hours for reg in registered),
                )
            ]
        params = CreateInvoiceParams(
            ref=ref,
            sprint_start=sprint_start,
            sprint_end=sprint_end,
            factuur_datum=invoice_date,
            factuurtekst=factuurtekst,
            regels=regels,
            registration_ids=[r.registration_id for r in registered if r.registration_id],
        )
        if input.dry_run or preview:
            invoice = InvoiceResult(
                invoice_id="DRY-RUN", invoice_number="DRY-RUN", factuur_datum=invoice_date
            )
        else:
            invoice = coerce(await call_activity("create-invoice", params, cfg), InvoiceResult)
        result.invoice = invoice

        self._phase = "sending_invoice"
        invoice_sent_at = workflow.now()
        result.invoice_sent_at = invoice_sent_at
        if not input.dry_run and not preview:
            pdf = (
                await call_activity("download-invoice-pdf", invoice.invoice_id, cfg)
                if invoice.invoice_id
                else None
            )
            dates_iso = {
                "sprint_start": sprint_start.isoformat(),
                "sprint_end": sprint_end.isoformat(),
                "ref": ref,
                "invoice_number": invoice.invoice_number or "-",
            }
            send_params = SendInvoiceParams(
                ref=ref,
                invoice_id=invoice.invoice_id,
                invoice_number=invoice.invoice_number,
                to=cfg.mail.invoice.to,
                cc=cfg.mail.invoice.cc,
                emailsjabloon=inv_cfg.emailsjabloon,
                smtp_subject=cfg.mail.invoice.subject.format(**dates_iso),
                smtp_body=(
                    f"Hi,\n\nInvoice {invoice.invoice_number} for sprint {dates['start']} - "
                    f"{dates['end']} (invoice date {invoice_date.isoformat()}).\n\n"
                    f"Payment term: {inv_cfg.payment_term_days} days. Reference: {ref}\n"
                ),
                attachment_name=f"invoice-{invoice.invoice_number}.pdf" if pdf else None,
                attachment_pdf=pdf,
            )
            if preview:
                send_params.to = [cfg.owner.email]
                send_params.cc = []
                send_params.smtp_subject = f"[PREVIEW] {send_params.smtp_subject}"
                send_params.smtp_body = (
                    f"[PREVIEW - not sent to customers. Original To: {', '.join(cfg.mail.invoice.to)}; "
                    f"Cc: {', '.join(cfg.mail.invoice.cc)}]\n\n" + send_params.smtp_body
                )
            await call_activity("send-invoice", send_params, cfg)

        self._phase = "monitoring_payment"
        if not input.dry_run and not preview:
            await _sleep(timedelta(days=cfg.eboekhouden.invoice.check_after_days))
            escalated = False
            while True:
                paid = await call_activity("invoice-paid", invoice.invoice_number or "", cfg)
                if paid:
                    break
                if not escalated and result.payment_reminders_sent >= cfg.timeouts.max_payment_reminders:
                    escalated = True
                    await call_activity(
                        "send-mail",
                        _simple_mail(
                            cfg, cfg.mail.escalation, [cfg.owner.email], [], ref, sprint_start, sprint_end,
                            f"Invoice {invoice.invoice_number} is still unpaid after "
                            f"{result.payment_reminders_sent} reminders ({ref}).",
                            extra={"invoice_number": invoice.invoice_number or "-"},
                        ),
                        cfg,
                    )
                await call_activity(
                    "send-mail",
                    _simple_mail(
                        cfg, cfg.mail.payment_reminder, cfg.mail.invoice.to, cfg.mail.invoice.cc, ref, sprint_start, sprint_end,
                        f"Friendly reminder: invoice {invoice.invoice_number} "
                        f"(sprint {dates['start']} - {dates['end']}) is still unpaid. "
                        f"Payment term: {inv_cfg.payment_term_days} days.",
                        extra={"invoice_number": invoice.invoice_number or "-"},
                    ),
                    cfg,
                )
                result.payment_reminders_sent += 1
                await _sleep(timedelta(days=cfg.timeouts.payment_reminder_interval_days))
        result.paid = True
        result.payment_checked_at = workflow.now()
        result.status = "completed"
        self._phase = "completed"
        return result

    async def _wait_for_owner(
        self, cfg: BillingConfig, ref: str, sprint_start: date, sprint_end: date, preview: bool
    ) -> None:
        deadline = workflow.now() + timedelta(days=cfg.timeouts.owner_confirm_days)
        escalated = False
        while self._hours_confirmation is None and self._cancel_reason is None:
            now = workflow.now()
            wait = timedelta(hours=cfg.timeouts.owner_reminder_hours)
            if now < deadline:
                wait = min(wait, deadline - now)
            try:
                await workflow.wait_condition(
                    lambda: self._hours_confirmation is not None or self._cancel_reason is not None,
                    timeout=wait,
                )
            except asyncio.TimeoutError:
                if workflow.now() >= deadline and not escalated:
                    escalated = True
                    await call_activity(
                        "send-mail",
                        _send(cfg, _simple_mail(
                            cfg, cfg.mail.escalation, [cfg.owner.email], [], ref, sprint_start, sprint_end,
                            f"No hours confirmation received for {ref} within "
                            f"{cfg.timeouts.owner_confirm_days} days; still waiting.",
                        ), preview),
                        cfg,
                    )
                elif workflow.now() < deadline or not escalated:
                    await call_activity(
                        "send-mail",
                        _send(cfg, _simple_mail(
                            cfg, cfg.mail.owner_reminder, [cfg.owner.email], [], ref, sprint_start, sprint_end,
                            f"Reminder: please confirm the proposed hour registration for {ref} "
                            f"by replying to the proposal mail.",
                        ), preview),
                        cfg,
                    )

    async def _wait_for_customer(
        self,
        cfg: BillingConfig,
        ref: str,
        sprint_start: date,
        sprint_end: date,
        sent_at: datetime,
        result: SprintResult,
        preview: bool,
    ) -> None:
        deadline = sent_at + timedelta(days=cfg.timeouts.customer_confirm_days)
        escalated = False
        while self._customer_confirmation is None:
            now = workflow.now()
            if now >= deadline and not escalated:
                escalated = True
                await call_activity(
                    "send-mail",
                    _send(cfg, _simple_mail(
                        cfg, cfg.mail.escalation, [cfg.owner.email], [], ref, sprint_start, sprint_end,
                        f"No timesheet confirmation from the customer for {ref} within "
                        f"{cfg.timeouts.customer_confirm_days} days; still waiting.",
                    ), preview),
                    cfg,
                )
            next_reminder = cal.next_reminder_time(
                now, cfg.timeouts.customer_reminder_weekdays, cfg.timeouts.customer_reminder_hour_utc
            )
            wait = next_reminder - now
            try:
                await workflow.wait_condition(
                    lambda: self._customer_confirmation is not None, timeout=wait
                )
            except asyncio.TimeoutError:
                if workflow.now() >= next_reminder:
                    await call_activity(
                        "send-mail",
                        _send(cfg, _simple_mail(
                            cfg, cfg.mail.timesheet_reminder, cfg.mail.timesheet.to, cfg.mail.timesheet.cc, ref,
                            sprint_start, sprint_end,
                            f"Reminder: please confirm the timesheet for sprint "
                            f"{sprint_start.isoformat()} - {sprint_end.isoformat()} by replying "
                            f"to this mail.",
                        ), preview),
                        cfg,
                    )
                    result.customer_reminders_sent += 1
