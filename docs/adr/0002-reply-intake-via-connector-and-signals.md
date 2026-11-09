# ADR-0002: Email reply intake via Microsoft Graph + signals

Status: accepted (confirmed by owner 2026-11-09)

## Context
Steps 2 and 5 require email replies (owner hours confirmation, customer @tcs.com timesheet confirmation) to reach a running workflow execution. Proton has no webhooks; triggers are unavailable on the current plan; scheduled (unattended) runs cannot use user-scope OAuth connector credentials.

## Decision
- All billing mail is sent via Proton SMTP from admin@guero.nl; Proton auto-forwards to the Outlook mailbox.
- A scheduled `mail-reply-ingest` workflow (every 15 min) polls the Outlook mailbox via **Microsoft Graph directly, using an Azure app registration with refresh-token flow (public client) or client-credentials** (client ID in config; client secret as workspace secret `MISTRAL_MS_GRAPH_REFRESH_TOKEN`). It lists unread inbox messages since the lookback window, fetches bodies for mails containing the ref code, and moves processed mails to `archive` (fallback: mark read).
- Replies are matched to executions by the ref code `SP-YYYY-MM-DD` in the subject; the running `billing-sprint` execution is found via the Mistral API (list executions + `state` query) and signalled (`confirm_hours` / `customer_confirmation` / `cancel`).
- Owner replies: "confirm"-like text accepts the proposal; lines `YYYY-MM-DD: N` override hours; otherwise the run is cancelled. Customer replies: any @tcs.com sender confirms unless the text rejects; the email Date becomes the invoice date.

## Alternatives rejected
- **Outlook connector slot** — rejected by the owner: connector OAuth credentials are user-scoped and cannot be used by scheduled (unattended) runs.
- IMAP polling with secrets — viable fallback, kept out of scope.
- Manual signals from Studio (kept as a manual fallback, documented in README).
- Custom webhook provider (triggers 403 on the current plan).

## Consequences
- Setup requires: Proton auto-forward to the Outlook mailbox; Azure app registration (client ID `3e7b867b-bcf7-45a9-bf85-0e1a7cb8c1e1`) with application permissions (Mail.Read; Mail.ReadWrite recommended so processed mails can be moved/marked) and admin consent; tenant ID + mailbox UPN in config; workspace secret `MISTRAL_MS_GRAPH_REFRESH_TOKEN` bound to the deployment.
