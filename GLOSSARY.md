# Glossary

- **Sprint** — work period starting on a Tuesday; the Monday after the last working week is always the last day of the sprint.
- **Sprint end (Monday)** — the day the billing run starts. With anchor Tuesday 2026-09-29 the sprint ends are the Mondays at day offsets 20/41/62/90 (mod 91) from the anchor.
- **Quarter cycle** — 13 weeks per calendar quarter, split into sprints of 3 + 3 + 3 + 4 weeks; the 4-week sprint covers the last week of the quarter.
- **Anchor Tuesday** — the Tuesday that starts sprint 1 of a quarter cycle (`sprint.anchor_tuesday`, currently 2026-09-29).
- **Ref code** — `SP-YYYY-MM-DD` (sprint start date) embedded in every billing mail subject; used to match email replies to the right workflow execution.
- **Hours proposal** — mail to the owner listing 8 h per worked day (Mon/Tue/Wed) of the sprint; confirmed or edited by reply.
- **Hours registration** — one e-Boekhouden urenregistratie per worked day (relation GEEKSOFT, medewerker Bouke Postma, TCS project, 0 km).
- **Timesheet** — mail to the customer (to/cc list) with the sprint's registered hours, asking for confirmation by reply.
- **Customer confirmation** — any reply from an allowed customer domain (tcs.com) containing the ref code; the email's date becomes the invoice date.
- **Invoice** — e-Boekhouden factuur, template `EN - Simpel met aantallen`, mail template `02. EN - Eerste Factuur`, factuurtekst `Sprint <start> - <end>` (e.g. `Sprint 29 sept - 19 okt`).
- **Factuurtekst** — free text on the invoice; here `Sprint <start> - <end>` with the actual sprint dates.
- **Payment term** — 45 days; the workflow checks payment 46 days after the invoice was sent.
- **Outstanding invoice** — e-Boekhouden `/mutation/invoice/outstanding`: unpaid invoices; used to detect payment.
- **Signal** — asynchronous message into a running workflow execution (confirm_hours / customer_confirmation / cancel).
- **Ingest** — the `mail-reply-ingest` workflow: polls the Outlook inbox via the outlook connector and signals the matching running `billing-sprint` execution.
- **Dry run** — `dry_run: true` input: full flow without any external side effects.
- **Overrides** — runtime config deep-merged over `config/billing.yaml`, carried in the schedule input (editable in Studio without git).
