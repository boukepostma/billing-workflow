import asyncio
import json
import logging
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

logger = logging.getLogger(__name__)


def _api_token(cfg: BillingConfig) -> str:
    return os.environ[cfg.eboekhouden.token_env]


async def _request(cfg: BillingConfig, method: str, path: str, **kwargs) -> httpx.Response:
    base = cfg.eboekhouden.base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=60.0) as client:
        session = await client.post(
            f"{base}/v1/session",
            json={"accessToken": _api_token(cfg), "source": cfg.eboekhouden.session_source},
        )
        session.raise_for_status()
        token = session.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        headers.update(kwargs.pop("headers", {}))
        return await client.request(method, f"{base}{path}", headers=headers, **kwargs)


async def _get_json(cfg: BillingConfig, path: str, params: dict | None = None) -> dict:
    response = await _request(cfg, "GET", path, params=params or {})
    response.raise_for_status()
    return response.json()


async def _resolve_relation_id(cfg: BillingConfig) -> int:
    data = await _get_json(cfg, "/v1/relation", {"code": cfg.eboekhouden.relation_code, "limit": 5})
    items = data.get("items", [])
    if not items:
        raise RuntimeError(f"relation with code {cfg.eboekhouden.relation_code} not found")
    return int(items[0]["id"])


async def _resolve_template_id(cfg: BillingConfig) -> int:
    data = await _get_json(cfg, "/v1/invoicetemplate", {"limit": 500})
    for item in data.get("items", []):
        if item.get("name") == cfg.eboekhouden.invoice.factuursjabloon and item.get("active", True):
            return int(item["id"])
    raise RuntimeError(f"invoice template {cfg.eboekhouden.invoice.factuursjabloon!r} not found")


async def _resolve_email_template_id(cfg: BillingConfig) -> int:
    data = await _get_json(cfg, "/v1/emailtemplate", {"limit": 500})
    for item in data.get("items", []):
        if item.get("name") == cfg.eboekhouden.invoice.emailsjabloon and item.get("useInvoice", True):
            return int(item["id"])
    raise RuntimeError(f"email template {cfg.eboekhouden.invoice.emailsjabloon!r} not found")


async def _resolve_unit_id(cfg: BillingConfig) -> int | None:
    data = await _get_json(cfg, "/v1/unit", {"limit": 500})
    wanted = cfg.eboekhouden.invoice.unit_name.lower()
    for item in data.get("items", []):
        if wanted in (str(item.get("singular", "")).lower(), str(item.get("plural", "")).lower()):
            return int(item["id"])
    return None


async def _resolve_ledger_id(cfg: BillingConfig) -> int:
    inv = cfg.eboekhouden.invoice
    if inv.ledger_code:
        data = await _get_json(cfg, "/v1/ledger", {"code": inv.ledger_code, "limit": 5})
        items = data.get("items", [])
        if items:
            return int(items[0]["id"])
        raise RuntimeError(f"ledger with code {inv.ledger_code} not found")
    data = await _get_json(cfg, "/v1/ledger", {"limit": 500})
    items = data.get("items", [])
    pnl = [i for i in items if str(i.get("category", "")).upper() == "VW"]
    for item in pnl:
        if "omzet" in str(item.get("description", "")).lower():
            return int(item["id"])
    if pnl:
        return int(pnl[0]["id"])
    logger.warning(
        "no VW-category ledger found; available: %s",
        [(i.get("code"), i.get("description"), i.get("category")) for i in items][:20],
    )
    raise RuntimeError("no revenue (VW) ledger found; set eboekhouden.invoice.ledger_code")


def _invoice_body(params: CreateInvoiceParams, cfg: BillingConfig, ids: dict) -> dict:
    inv = cfg.eboekhouden.invoice
    item: dict = {
        "description": params.regels[0].omschrijving if params.regels else inv.line_omschrijving_template,
        "quantity": sum(r.aantal for r in params.regels) if params.regels else 0,
        "code": inv.product_code,
        "pricePerUnit": inv.hourly_rate,
        "vatCode": inv.vat_code,
        "ledgerId": ids["ledger_id"],
    }
    if ids.get("unit_id") is not None:
        item["unitId"] = ids["unit_id"]
    return {
        "relationId": ids["relation_id"],
        "date": params.factuur_datum.isoformat(),
        "termOfPayment": inv.payment_term_days,
        "templateId": ids["template_id"],
        "emailTemplateId": ids["email_template_id"],
        "reference": params.ref,
        "text": params.factuurtekst,
        "inExVat": "EX",
        "items": [item],
    }


@workflows.activity(
    name="register-hours",
    start_to_close_timeout=timedelta(minutes=2),
    retry_policy_max_attempts=3,
    retry_policy_backoff_coefficient=2.0,
)
async def register_hours(entries: list[HoursEntry], cfg: BillingConfig) -> str:
    eb = cfg.eboekhouden
    if not eb.hours_via_api:
        logger.warning(
            "hours_via_api=false: e-Boekhouden public APIs (REST v1 and SOAP) expose no "
            "urenregistratie endpoint, so hours are not registered via API; they are carried "
            "on the invoice line instead"
        )
        return json.dumps(
            [HoursRegistration(date=e.date, hours=e.hours, registration_id=None).model_dump(mode="json") for e in entries]
        )
    results: list[HoursRegistration] = []
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
        try:
            response = await _request(cfg, "POST", eb.endpoints.hours, json=payload)
            response.raise_for_status()
            registration_id = str(response.json().get("id"))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logger.warning("hours endpoint %s not available (404); skipping API hour registration", eb.endpoints.hours)
                registration_id = None
            else:
                raise
        results.append(HoursRegistration(date=entry.date, hours=entry.hours, registration_id=registration_id))
    return json.dumps([r.model_dump(mode="json") for r in results])


@workflows.activity(
    name="create-invoice",
    start_to_close_timeout=timedelta(minutes=2),
    retry_policy_max_attempts=3,
    retry_policy_backoff_coefficient=2.0,
)
async def create_invoice(params: CreateInvoiceParams, cfg: BillingConfig) -> str:
    ids = {
        "relation_id": await _resolve_relation_id(cfg),
        "template_id": await _resolve_template_id(cfg),
        "email_template_id": await _resolve_email_template_id(cfg),
        "unit_id": await _resolve_unit_id(cfg),
        "ledger_id": await _resolve_ledger_id(cfg),
    }
    body = _invoice_body(params, cfg, ids)
    response = await _request(cfg, "POST", cfg.eboekhouden.endpoints.invoice, json=body)
    if response.status_code >= 300:
        logger.warning(
            "create-invoice failed: status=%s response=%s request=%s",
            response.status_code,
            response.text[:600],
            json.dumps(body)[:900],
        )
    response.raise_for_status()
    data = response.json()
    invoice_id = data.get("id")
    invoice_number = data.get("invoiceNumber")
    pdf_url = None
    if invoice_id is not None:
        detail = await _get_json(cfg, f"/v1/invoice/{invoice_id}")
        pdf_url = detail.get("urlPdfFile")
    result = InvoiceResult(
        invoice_id=str(invoice_id) if invoice_id is not None else None,
        invoice_number=str(invoice_number) if invoice_number is not None else None,
        factuur_datum=params.factuur_datum,
        linked_hours=False,
        pdf_url=pdf_url,
    )
    return result.model_dump_json()


@workflows.activity(
    name="invoice-paid",
    start_to_close_timeout=timedelta(minutes=2),
    retry_policy_max_attempts=3,
    retry_policy_backoff_coefficient=2.0,
)
async def invoice_paid(invoice_number: str, cfg: BillingConfig) -> bool:
    data = await _get_json(
        cfg,
        cfg.eboekhouden.endpoints.outstanding,
        {"credDeb": "D", "invoiceNumber": invoice_number, "limit": 10},
    )
    return not data.get("items", [])


@workflows.activity(
    name="send-invoice",
    start_to_close_timeout=timedelta(minutes=2),
    retry_policy_max_attempts=3,
    retry_policy_backoff_coefficient=2.0,
)
async def send_invoice(params: SendInvoiceParams, cfg: BillingConfig) -> str:
    inv = cfg.eboekhouden.invoice
    if inv.send_prefer == "eboekhouden":
        return "eboekhouden"
    pdf: bytes | None = None
    attachment_name = None
    if params.invoice_id:
        try:
            detail = await _get_json(cfg, f"/v1/invoice/{params.invoice_id}")
            url = detail.get("urlPdfFile")
            if url:
                async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                    response = await client.get(url)
                    if response.status_code == 200 and len(response.content) <= 1_500_000:
                        pdf = response.content
                        attachment_name = f"invoice-{params.invoice_number}.pdf"
        except httpx.HTTPError:
            pdf = None
    message = MailMessage(
        to=params.to,
        cc=params.cc,
        subject=params.smtp_subject,
        body=params.smtp_body,
        ref=params.ref,
        attachment_name=attachment_name,
        attachment_pdf=pdf,
    )
    await asyncio.to_thread(send_mail_message, message, cfg)
    return "smtp"
