import asyncio
import json
import os
from datetime import timedelta

import httpx

import mistralai.workflows as workflows

from worker.activities.smtp_mail import send_mail_message
from worker.workflows.config_models import BillingConfig
from worker.workflows.models import (
    CreateInvoiceParams,
    HoursEntry,
    HoursRegistration,
    InvoiceResult,
    MailMessage,
    SendInvoiceParams,
)


def _client(cfg: BillingConfig) -> httpx.AsyncClient:
    token = os.environ[cfg.eboekhouden.token_env]
    return httpx.AsyncClient(
        base_url=cfg.eboekhouden.base_url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=timedelta(seconds=60),
    )


def _extract_id(payload: dict) -> str | None:
    for key in ("id", "urenregistratieId", "factuurId", "invoiceId"):
        value = payload.get(key)
        if value is not None:
            return str(value)
    return None


@workflows.activity(
    name="register-hours",
    start_to_close_timeout=timedelta(minutes=2),
    retry_policy_max_attempts=3,
    retry_policy_backoff_coefficient=2.0,
)
async def register_hours(entries: list[HoursEntry], cfg: BillingConfig) -> str:
    eb = cfg.eboekhouden
    results: list[HoursRegistration] = []
    async with _client(cfg) as client:
        for entry in entries:
            payload = {
                "relatieCode": eb.relation_code,
                "medewerker": eb.medewerker,
                "project": eb.project,
                "datum": entry.date.isoformat(),
                "aantalUren": entry.hours,
                "aantalKm": eb.kilometers,
                "omschrijving": eb.hours_description,
            }
            response = await client.post(eb.endpoints.hours, json=payload)
            response.raise_for_status()
            registration_id = _extract_id(response.json())
            results.append(
                HoursRegistration(date=entry.date, hours=entry.hours, registration_id=registration_id)
            )
    return json.dumps([r.model_dump(mode="json") for r in results])


@workflows.activity(
    name="create-invoice",
    start_to_close_timeout=timedelta(minutes=2),
    retry_policy_max_attempts=3,
    retry_policy_backoff_coefficient=2.0,
)
async def create_invoice(params: CreateInvoiceParams, cfg: BillingConfig) -> str:
    eb = cfg.eboekhouden
    inv = eb.invoice
    body: dict = {
        "relatieCode": eb.relation_code,
        "factuurDatum": params.factuur_datum.isoformat(),
        "factuursjabloon": inv.factuursjabloon,
        "emailsjabloon": inv.emailsjabloon,
        "factuurtekst": params.factuurtekst,
        "betaalTermijn": inv.payment_term_days,
        "regels": [
            {
                "productCode": inv.product_code,
                "omschrijving": line.omschrijving,
                "aantal": line.aantal,
                "prijs": inv.hourly_rate,
                "btwCode": inv.vat_code,
            }
            for line in params.regels
        ],
    }
    linked = False
    if inv.link_hours and params.registration_ids:
        body[inv.link_hours_field] = params.registration_ids
    async with _client(cfg) as client:
        response = await client.post(eb.endpoints.invoice, json=body)
        if response.status_code in (400, 422) and inv.link_hours and inv.link_hours_field in body:
            body.pop(inv.link_hours_field)
            response = await client.post(eb.endpoints.invoice, json=body)
        response.raise_for_status()
        data = response.json()
        linked = inv.link_hours_field in body and bool(params.registration_ids)
        number = data.get("factuurnummer") or data.get("invoiceNumber") or data.get("FactuurNummer")
        result = InvoiceResult(
            invoice_id=_extract_id(data),
            invoice_number=str(number) if number is not None else None,
            factuur_datum=params.factuur_datum,
            linked_hours=linked,
        )
        return result.model_dump_json()


@workflows.activity(
    name="invoice-paid",
    start_to_close_timeout=timedelta(minutes=2),
    retry_policy_max_attempts=3,
    retry_policy_backoff_coefficient=2.0,
)
async def invoice_paid(invoice_number: str, cfg: BillingConfig) -> bool:
    eb = cfg.eboekhouden
    async with _client(cfg) as client:
        response = await client.get(
            eb.endpoints.outstanding, params={"relatieCode": eb.relation_code}
        )
        response.raise_for_status()
        data = response.json()
    items = data.get("items") if isinstance(data, dict) else data
    if items is None and isinstance(data, dict):
        items = data.get("outstanding") or data.get("openstaandePosten") or []
    for item in items or []:
        number = item.get("invoiceNumber") or item.get("factuurNummer") or item.get("FactuurNummer")
        if number is not None and str(number) == invoice_number:
            return False
    return True


@workflows.activity(
    name="send-invoice",
    start_to_close_timeout=timedelta(minutes=2),
    retry_policy_max_attempts=3,
    retry_policy_backoff_coefficient=2.0,
)
async def send_invoice(params: SendInvoiceParams, cfg: BillingConfig) -> str:
    eb = cfg.eboekhouden
    if eb.invoice.send_prefer == "eboekhouden" and params.invoice_id:
        async with _client(cfg) as client:
            response = await client.post(
                eb.endpoints.send_invoice.format(invoice_id=params.invoice_id),
                json={
                    "emailsjabloon": params.emailsjabloon,
                    "to": params.to,
                    "cc": params.cc,
                },
            )
        if response.status_code < 300:
            return "eboekhouden"
    message = MailMessage(
        to=params.to,
        cc=params.cc,
        subject=params.smtp_subject,
        body=params.smtp_body,
        ref=params.ref,
        attachment_name=params.attachment_name,
        attachment_pdf=params.attachment_pdf,
    )
    await asyncio.to_thread(send_mail_message, message, cfg)
    return "smtp"


@workflows.activity(
    name="download-invoice-pdf",
    start_to_close_timeout=timedelta(minutes=2),
    retry_policy_max_attempts=2,
)
async def download_invoice_pdf(invoice_id: str, cfg: BillingConfig) -> bytes | None:
    eb = cfg.eboekhouden
    async with _client(cfg) as client:
        try:
            response = await client.get(eb.endpoints.invoice_pdf.format(invoice_id=invoice_id))
            if response.status_code == 200 and response.content:
                return response.content
        except httpx.HTTPError:
            return None
    return None
