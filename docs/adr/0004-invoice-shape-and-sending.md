# ADR-0004: Invoice shape, hour linking, sending

Status: accepted (confirmed by owner 2026-11-09, inferred from example invoice)

## Decision
- Invoice in e-Boekhouden: factuursjabloon `EN - Simpel met aantallen`, e-mailsjabloon `02. EN - Eerste Factuur`, factuurtekst `Sprint <start> - <end>` with actual dates in Dutch short-month format (e.g. `Sprint 29 sept - 19 okt`), invoice date = date of the customer's confirmation email, payment term 45 days.
- One invoice line per sprint (`lines_mode: sprint_total`): Aantal = total sprint hours, Eenheid uren, Code `UREN`, P.p.e. 105,00, Btw `Btw hoog 21%` (vat_code HOOG), Omschrijving `Sprint <start> - <end>`. (`per_day` mode available in config.)
## API findings (verified against the live OpenAPI spec /openapi/v1.json and the SOAP WSDL)
- Auth: POST /v1/session {accessToken, source} -> session token used as Bearer on all calls.
- **No urenregistratie endpoint exists** in REST v1 or SOAP — hour registration is GUI/app-only. Per the owner's fallback, invoices stay unrelated to hours; the hours ride on the invoice line (quantity + description). `hours_via_api=true` still attempts the legacy endpoint, 404-tolerant.
- Invoice creation: POST /v1/invoice with relationId (resolved from code GEEKSOFT via GET /v1/relation?code=), templateId (name "EN - Simpel met aantallen"), emailTemplateId ("02. EN - Eerste Factuur"), unitId (unit "uren"), ledgerId (code or first category Omzet), date = customer confirmation date, text = factuurtekst, items=[{quantity, code UREN, pricePerUnit, vatCode HOOG_VERK_21, description}], termOfPayment 45, reference = ref code.
- PDF: GET /v1/invoice/{id} returns urlPdfFile (used for the SMTP attachment).
- Sending: REST v1 can only email the invoice to the relation's address (creation-time email block); custom to/cc therefore go via Proton SMTP with the PDF. send_prefer=smtp default.
- Payment: GET /v1/mutation/invoice/outstanding?credDeb=D&invoiceNumber=... — empty items means paid.

## Consequences
- Endpoint paths and field names are config-driven (`eboekhouden.endpoints`, `invoice.link_hours_field`) so API drift can be fixed via `overrides` without a code change.
