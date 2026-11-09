# ADR-0004: Invoice shape, hour linking, sending

Status: accepted (confirmed by owner 2026-11-09, inferred from example invoice)

## Decision
- Invoice in e-Boekhouden: factuursjabloon `EN - Simpel met aantallen`, e-mailsjabloon `02. EN - Eerste Factuur`, factuurtekst `Sprint <start> - <end>` with actual dates in Dutch short-month format (e.g. `Sprint 29 sept - 19 okt`), invoice date = date of the customer's confirmation email, payment term 45 days.
- One invoice line per sprint (`lines_mode: sprint_total`): Aantal = total sprint hours, Eenheid uren, Code `UREN`, P.p.e. 105,00, Btw `Btw hoog 21%` (vat_code HOOG), Omschrijving `Sprint <start> - <end>`. (`per_day` mode available in config.)
- The invoice is related to the hour registrations via `urenregistratieIds` on the invoice payload; if the API rejects the field (4xx), the activity retries without it and the invoice stays unrelated (per owner: acceptable).
- Sending: try the e-Boekhouden API send endpoint (`/facturen/{id}/send` with the e-mailsjabloon and the configured to/cc) first; on failure fall back to Proton SMTP with the invoice PDF attached (best effort).
- Hour registrations stay one-per-day (relation GEEKSOFT, medewerker Bouke Postma, TCS project, 0 km).

## Consequences
- Endpoint paths and field names are config-driven (`eboekhouden.endpoints`, `invoice.link_hours_field`) so API drift can be fixed via `overrides` without a code change.
