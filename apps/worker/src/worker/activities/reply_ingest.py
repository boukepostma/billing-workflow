import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime

import httpx

import html as html_lib
import os
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime

import httpx

import mistralai.workflows as workflows

from worker.workflows import sprint_calendar as cal
from worker.workflows.config_models import BillingConfig
from worker.workflows.models import (
    ConfirmHours,
    CustomerConfirmation,
    HoursEntry,
    IngestOutcome,
    ReplyEmail,
)

CONFIRM_WORDS = ("confirm", "approved", "approve", "akkoord", "accorderen", "ok", "yes", "go")
REJECT_WORDS = ("reject", "afgewezen", "afkeuren", "no")
ENTRY_LINE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*[:=]\s*(\d+(?:[.,]\d+)?)")


def extract_ref(text: str, ref_pattern: str) -> str | None:
    match = re.search(ref_pattern, text)
    return match.group(0) if match else None


def sender_domain(sender: str) -> str:
    return sender.rsplit("@", 1)[-1].lower() if "@" in sender else ""


def classify_sender(sender: str, cfg: BillingConfig) -> str:
    domain = sender_domain(sender)
    owner_domain = sender_domain(cfg.owner.email)
    if sender.lower() == cfg.owner.email.lower() or domain == owner_domain or domain in cfg.owner.confirm_domains:
        return "owner"
    if domain in cfg.customer.confirm_domains:
        return "customer"
    return "unknown"


def parse_entries(body: str) -> list[HoursEntry] | None:
    entries: list[HoursEntry] = []
    for match in ENTRY_LINE_RE.finditer(body):
        entries.append(
            HoursEntry(date=date.fromisoformat(match.group(1)), hours=float(match.group(2).replace(",", ".")))
        )
    return entries or None


def is_confirm_text(body: str) -> bool:
    lowered = body.lower()
    if any(word in lowered for word in REJECT_WORDS):
        return False
    return any(word in lowered for word in CONFIRM_WORDS)


def _pick(payload: dict, *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value:
            return str(value)
    return ""


def _parse_dt(value: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def reply_from_outlook(item: dict, full: dict, ref_pattern: str) -> ReplyEmail:
    message_id = _pick(item.get("metadata", {}), "message_id")
    title = _pick(item, "title", "subject")
    sender = _pick(full, "from", "sender", "fromEmailAddress")
    if "@" in sender:
        sender = parseaddr(sender)[1] or sender
    subject = _pick(full, "subject", "title") or title
    raw_date = _pick(full, "date", "receivedDateTime", "sentDateTime") or _pick(item, "date")
    body = _pick(full, "body", "content", "bodyPreview") or " ".join(item.get("snippets") or [])
    received = _parse_dt(raw_date) if raw_date else datetime.now(timezone.utc)
    if extract_ref(subject, ref_pattern) is None and extract_ref(body, ref_pattern):
        subject = f"{subject} {extract_ref(body, ref_pattern)}"
    return ReplyEmail(
        message_id=message_id,
        mailbox_seq=message_id,
        sender=sender,
        subject=subject,
        date=received,
        body=body,
    )


def _graph_token_url(cfg: BillingConfig) -> str:
    return f"{cfg.ingest.graph.login_base}/{cfg.ingest.graph.tenant_id}/oauth2/v2.0/token"


def _token_request_data(cfg: BillingConfig) -> dict:
    graph = cfg.ingest.graph
    if graph.auth_mode == "client_secret":
        return {
            "grant_type": "client_credentials",
            "client_id": graph.client_id,
            "client_secret": os.environ[graph.client_secret_env],
            "scope": graph.scope,
        }
    return {
        "grant_type": "refresh_token",
        "client_id": graph.client_id,
        "refresh_token": os.environ[graph.refresh_token_env],
        "scope": graph.scope,
    }


async def _graph_token(cfg: BillingConfig) -> str:
    async with httpx.AsyncClient(timeout=timedelta(seconds=30)) as client:
        response = await client.post(_graph_token_url(cfg), data=_token_request_data(cfg))
        response.raise_for_status()
        return response.json()["access_token"]


def _graph_client(cfg: BillingConfig, token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=cfg.ingest.graph.graph_base,
        headers={"Authorization": f"Bearer {token}"},
        timeout=timedelta(seconds=30),
    )


def _strip_html(content: str) -> str:
    text = re.sub(r"<[^>]+>", "\n", content or "")
    return html_lib.unescape(text)


def reply_from_graph(item: dict, body: str, ref_pattern: str) -> ReplyEmail:
    sender_info = (item.get("from") or {}).get("emailAddress") or {}
    sender = sender_info.get("address") or ""
    subject = item.get("subject") or ""
    received = _parse_dt(item.get("receivedDateTime") or "")
    if extract_ref(subject, ref_pattern) is None and extract_ref(body, ref_pattern):
        subject = f"{subject} {extract_ref(body, ref_pattern)}"
    return ReplyEmail(
        message_id=item.get("id") or "",
        mailbox_seq=item.get("id") or "",
        sender=sender,
        subject=subject,
        date=received,
        body=body,
    )


@workflows.activity(
    name="poll-replies",
    start_to_close_timeout=timedelta(minutes=2),
    retry_policy_max_attempts=3,
    retry_policy_backoff_coefficient=2.0,
)
async def poll_replies(cfg: BillingConfig, lookback_days: int) -> str:
    token = await _graph_token(cfg)
    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    async with _graph_client(cfg, token) as client:
        response = await client.get(
            f"/users/{cfg.ingest.graph.mailbox}/mailFolders/{cfg.ingest.search_folder}/messages",
            params={
                "$filter": f"isRead eq false and receivedDateTime ge {since}",
                "$top": cfg.ingest.poll_limit,
                "$select": "id,subject,from,receivedDateTime,bodyPreview",
                "$orderby": "receivedDateTime desc",
            },
        )
        response.raise_for_status()
        items = response.json().get("value", [])
        replies: list[ReplyEmail] = []
        for item in items:
            body = item.get("bodyPreview") or ""
            if extract_ref(item.get("subject") or "", cfg.ingest.ref_pattern) is None and extract_ref(body, cfg.ingest.ref_pattern) is None:
                continue
            full = await client.get(f"/users/{cfg.ingest.graph.mailbox}/messages/{item['id']}", params={"$select": "id,body"})
            body_content = ""
            if full.status_code == 200:
                body_content = _strip_html((full.json().get("body") or {}).get("content") or "")
            replies.append(reply_from_graph(item, body_content or body, cfg.ingest.ref_pattern))
    return json.dumps([r.model_dump(mode="json") for r in replies])


@workflows.activity(name="mark-replies-processed", start_to_close_timeout=timedelta(minutes=1))
async def mark_replies_processed(cfg: BillingConfig, message_ids: list[str]) -> None:
    if not message_ids:
        return
    token = await _graph_token(cfg)
    async with _graph_client(cfg, token) as client:
        for message_id in message_ids:
            try:
                response = await client.post(
                    f"/users/{cfg.ingest.graph.mailbox}/messages/{message_id}/move",
                    json={"destinationFolderId": cfg.ingest.processed_folder},
                )
                if response.status_code >= 300:
                    await client.patch(
                        f"/users/{cfg.ingest.graph.mailbox}/messages/{message_id}",
                        json={"isRead": True},
                    )
            except httpx.HTTPError:
                continue


def _api_client(cfg: BillingConfig) -> httpx.AsyncClient:
    api_key = os.environ[cfg.ingest.mistral_api_key_env]
    return httpx.AsyncClient(
        base_url=cfg.ingest.mistral_api_base,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=timedelta(seconds=30),
    )


async def _find_running_execution(cfg: BillingConfig, sprint_start: date) -> str | None:
    async with _api_client(cfg) as client:
        response = await client.get("/v1/workflows/executions")
        response.raise_for_status()
        payload = response.json()
        executions = payload.get("executions", payload if isinstance(payload, list) else [])
        for execution in executions:
            if execution.get("workflow_name") != cfg.ingest.billing_workflow_name:
                continue
            if execution.get("status") not in ("RUNNING", "running", None):
                continue
            execution_id = execution.get("execution_id") or execution.get("id")
            if not execution_id:
                continue
            query = await client.post(
                f"/v1/workflows/executions/{execution_id}/queries",
                json={"name": "state"},
            )
            if query.status_code != 200:
                continue
            state = query.json()
            result = state.get("result", state)
            state_start = result.get("sprint_start") if isinstance(result, dict) else None
            if state_start == sprint_start.isoformat():
                return str(execution_id)
    return None


async def _signal_execution(cfg: BillingConfig, execution_id: str, name: str, payload: dict) -> None:
    async with _api_client(cfg) as client:
        response = await client.post(
            f"/v1/workflows/executions/{execution_id}/signals",
            json={"name": name, "input": payload},
        )
        response.raise_for_status()


@workflows.activity(
    name="dispatch-reply",
    start_to_close_timeout=timedelta(minutes=2),
    retry_policy_max_attempts=3,
    retry_policy_backoff_coefficient=2.0,
)
async def dispatch_reply(reply: ReplyEmail, cfg: BillingConfig, dry_run: bool = False) -> str:
    kind = classify_sender(reply.sender, cfg)
    ref = extract_ref(f"{reply.subject}\n{reply.body}", cfg.ingest.ref_pattern)
    if kind == "unknown":
        return IngestOutcome(message_id=reply.message_id, outcome="ignored_sender", detail=reply.sender)
    if not ref:
        return IngestOutcome(message_id=reply.message_id, outcome="ignored_no_ref", detail=reply.subject)
    sprint_start = cal.parse_ref(ref)
    if kind == "owner":
        entries = parse_entries(reply.body)
        if not is_confirm_text(reply.body) and entries is None:
            payload = {"reason": reply.body[:500]}
            signal_name = "cancel"
        else:
            payload = ConfirmHours(
                ref=ref, sender=reply.sender, entries=entries, note=reply.body[:500]
            ).model_dump(mode="json")
            signal_name = "confirm_hours"
    else:
        payload = CustomerConfirmation(
            ref=ref,
            sender=reply.sender,
            confirmed=is_confirm_text(reply.body),
            received_at=reply.date,
        ).model_dump(mode="json")
        signal_name = "customer_confirmation"
    execution_id = await _find_running_execution(cfg, sprint_start)
    if execution_id is None:
        return IngestOutcome(
            message_id=reply.message_id, outcome="no_running_execution", detail=ref
        )
    if not dry_run:
        await _signal_execution(cfg, execution_id, signal_name, payload)
    return IngestOutcome(
        message_id=reply.message_id,
        outcome="signalled_dry_run" if dry_run else "signalled",
        detail=f"{signal_name} -> {execution_id}",
    ).model_dump_json()
