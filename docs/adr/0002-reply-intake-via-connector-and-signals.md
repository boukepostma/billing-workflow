# ADR-0002: Email reply intake

Status: accepted (confirmed by owner 2026-11-09)

## Context
Steps 2 and 5 require email replies (owner hours confirmation, customer @tcs.com timesheet confirmation) to reach a running workflow execution. Proton has no webhooks; triggers are unavailable on the current plan; scheduled (unattended) runs cannot use user-scope OAuth connector credentials.

## Decision
- All billing mail is sent via Proton SMTP from bouke@guero.nl; Proton auto-forwards to the connected Outlook mailbox.
- A scheduled `mail-reply-ingest` workflow (every 15 min) polls the Outlook inbox via the **outlook connector with `run_as="deployment"`** (requires a workspace-level credential on the connector), using `search_outlook`/`open_outlook_email`, and moves processed mails to `archive`.
- Replies are matched to executions by the ref code `SP-YYYY-MM-DD` in the subject; the running `billing-sprint` execution is found via the Mistral API (list executions + `state` query) and signalled (`confirm_hours` / `customer_confirmation` / `cancel`).
- Owner replies: "confirm"-like text accepts the proposal; lines `YYYY-MM-DD: N` override hours; otherwise the run is cancelled. Customer replies: any @tcs.com sender confirms unless the text rejects; the email Date becomes the invoice date.

## Alternatives rejected
- IMAP polling with secrets (dropped once the owner chose connector polling; kept out of scope).
- Manual signals from Studio (kept as a manual fallback, documented in README).
- Custom webhook provider (triggers 403 on the current plan).

## Consequences
- Setup requires: Proton auto-forward to Outlook + a workspace credential on the outlook connector (only the owner can add it in Studio).
