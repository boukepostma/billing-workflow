# ADR-0005: Reminder and failure semantics

Status: accepted (confirmed by owner 2026-11-09)

## Decision
- The run **never fails because a human didn't reply**. Timeouts only trigger escalation mails to the owner; the execution keeps waiting (bounded by the 120-day execution timeout, or the `cancel` signal).
- Owner hours confirmation: reminder every 24 h; escalation after 7 days.
- Customer timesheet confirmation: reminders EOD Wednesday and Friday (16:00 UTC); escalation to the owner after 14 days; reminders continue.
- Payment: checked 46 days after the invoice was sent, then daily while unpaid (via `/mutation/invoice/outstanding`); escalation to the owner after 30 reminders, reminders continue until paid.
- Runs fail only on real errors (API/SMTP failures after activity retries).
- Payment monitoring runs inside the same execution via timers, so the Monday schedule no-op on non-sprint-end Mondays does not interfere.
