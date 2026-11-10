# billing-workflow

End-to-end sprint billing automation on [Mistral AI Workflows](https://docs.mistral.ai/studio/workflows/overview), built for Bouke Postma's process with customer **TCS** (debtor code `GEEKSOFT`).

## Process (one run per sprint, started on the last day of the sprint = Monday)

1. **Propose hours by mail** — 8 h for every Monday/Tuesday/Wednesday of the sprint, mailed to the owner (Proton SMTP). Subject carries a ref code `SP-YYYY-MM-DD`.
2. **Owner confirms or suggests changes by replying** to the mail. The `mail-reply-ingest` workflow (scheduled every 15 min) polls the Outlook mailbox via Microsoft Graph (app registration, client credentials; Proton auto-forwards there), matches the ref code, and signals the running execution (`confirm_hours`). "confirm" accepts the proposal as-is; otherwise send only the lines to change/add — `2026-10-28: 4` overrides that day, `2026-11-02: 6` adds a day, `2026-10-27: 0` removes it; unmentioned days keep the proposed hours (merge semantics).
3. **Register hours in e-Boekhouden** via API — one registration per worked day: relation `GEEKSOFT`, medewerker `Bouke Postma`, project `Project for Tata Consultancy Services Netherlands`, 0 km.
4. **Send the timesheet by mail** (Proton SMTP) to the configured customer to/cc for confirmation.
5. **Customer confirms by mail** — any `@tcs.com` sender may confirm; the ingest workflow signals `customer_confirmation` with the email's date. If unconfirmed: reminder EOD Wednesday and EOD Friday (16:00 UTC). After 14 days the owner gets an escalation mail; the run keeps waiting (it does not fail on missing human input).
6. **Create the invoice in e-Boekhouden** — invoice date = date of the customer's confirmation email; factuursjabloon `EN - Simpel met aantallen`; e-mailsjabloon `02. EN - Eerste Factuur`; factuurtekst `Sprint 29 sept - 19 okt` (actual sprint dates); one line with total hours (code `UREN`, €105,00, Btw hoog 21%). The invoice is related to the hour registrations via `urenregistratieIds` when the API accepts it, otherwise left unrelated.
7. **Send the invoice by mail** to the separate invoice to/cc list — via the e-Boekhouden API mail send (using the e-mailsjabloon) when available, falling back to Proton SMTP with the PDF attached.
8. **Monitor payment** — 46 days after the invoice was sent (payment term 45 days), check e-Boekhouden (`/mutation/invoice/outstanding`). If unpaid: daily reminder mails; after 30 reminders an escalation to the owner (keeps reminding). If paid: done.

## Sprint calendar

Sprints start on **Tuesday**; a **Monday is always the last day of a sprint**. Each quarter (13 weeks) runs sprints of 3, 3, 3, **4** weeks. With anchor Tuesday `2026-09-29` the sprint ends are Mondays 2026-10-19, 2026-11-09, 2026-11-30, 2026-12-28, repeating every 91 days.

The `billing-sprint` schedule fires **every Monday 08:00 Europe/Amsterdam**; runs on non-sprint-end Mondays return `skipped_not_sprint_end` immediately.

## Layout

```
apps/worker/src/worker/
  entrypoints/worker.py        worker entrypoint (discovers workflows, registers activities)
  workflows/
    billing_sprint.py          main workflow (signals: confirm_hours, customer_confirmation, cancel; query: state)
    mail_reply_ingest.py       scheduled reply ingest (outlook connector -> signals)
    sprint_calendar.py         pure sprint math
    models.py / config_models.py / activity_calls.py / connector_slots.py
  activities/
    settings.py                loads config/billing.yaml (+ runtime overrides)
    smtp_mail.py               Proton SMTP sender
    eboekhouden.py             e-Boekhouden REST API client (hours, invoice, send, pdf, outstanding)
    reply_ingest.py            MS Graph poll + dispatch signal via Mistral API
config/billing.yaml            all configurable values (see below)
deploy/docker/Dockerfile.worker
tests/                         pytest + temporalio time-skipping tests
```

## Configuration without git

`config/billing.yaml` holds the defaults. Every value there can be overridden at runtime through the **`overrides` object in the schedule's input JSON** (Studio > Workflows > Schedules > edit input) — deep-merged over the file. E.g. change the timesheet recipients without touching git:

```json
{"dry_run": false, "overrides": {"mail": {"timesheet": {"to": ["new@tcs.com"], "cc": []}}}}
```

## Credentials in Mistral Studio (never in chat, never in git)

Scheduled runs are unattended, so they can only use **workspace-level** credentials:

1. **Workspace secrets bound to the deployment (recommended)** — create secrets at
   [console.mistral.ai/secrets](https://console.mistral.ai/secrets):
   `SMTP_HOST` (e.g. `smtp.protonmail.ch`), `SMTP_PORT` (`587`), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` (`admin@guero.nl`), `EBOEKHOUDEN_API_TOKEN`
   (e-Boekhouden: Beheer > Inrichting > Koppelingen > API). Then bind them on the deployment
   (Deployments > your deployment > Settings > environment variables, or the `secrets` parameter
   of the deploy call: `{"env_var_name": "SMTP_PASSWORD", "secret_name": "smtp_password"}`).
   The worker reads them from its environment at boot.
2. **Custom HTTP connector with a workspace credential** — alternative if you want Studio to hold
   the credentials per connector; the workflow would call it via `ToolCallClient`.
3. **Mailbox polling** uses the **outlook connector with `run_as="deployment"`**, which requires a
   **workspace-level credential on the outlook connector** (user OAuth credentials cannot be used by
   scheduled runs). Add it in Studio > Connectors > outlook > Credentials (workspace scope).
   Proton side: set an auto-forward rule Proton -> that Outlook mailbox.

## Deploy

```bash
uv sync && uv run pytest                 # local tests (temporalio time-skipping)
git push origin <branch>                 # managed deployment builds from the pushed SHA
```

Managed deployment `billing-worker` builds from this repo (`deploy/docker/Dockerfile.worker`, build context = repo root).
Schedules created via API/Studio:

- `billing-sprint`: cron `0 8 * * 1`, `time_zone_name: Europe/Amsterdam`, input `{"dry_run": false, "overrides": {...}}`
- `mail-reply-ingest`: interval `PT15M`, input `{"dry_run": false}`

Test a deployment safely with a dry run first: execute `billing-sprint` with `{"dry_run": true, "run_date": "2026-10-19"}` — it walks the whole flow with all external side effects disabled.

## Manual signals (if needed)

```bash
curl -X POST https://api.mistral.ai/v1/workflows/executions/<execution_id>/signals \
  -H "Authorization: Bearer $MISTRAL_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "confirm_hours", "input": {"ref": "SP-2026-09-29", "entries": null}}'
```

Signals: `confirm_hours` ({ref, sender?, entries? = null|[{date, hours}], note?}), `customer_confirmation` ({ref, sender, confirmed, received_at}), `cancel` ({reason}). Query: `state`.
