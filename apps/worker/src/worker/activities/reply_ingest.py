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


QUOTE_START_RE = re.compile(
    r"(original message|forwarded message|begin forwarded message|^on .+ wrote:$)", re.IGNORECASE
)


def _split_quote(body: str) -> tuple[str, str]:
    lines = body.splitlines()
    out: list[str] = []
    for i, line in enumerate(lines):
        if QUOTE_START_RE.search(line) or line.lstrip().startswith(">"):
            return "\n".join(out), "\n".join(lines[i:])
        if line.startswith("From:") and i + 1 < len(lines) and lines[i + 1].startswith("Sent:"):
            return "\n".join(out), "\n".join(lines[i:])
        out.append(line)
    return body, ""


def strip_quoted_text(body: str) -> str:
    return _split_quote(body)[0]


FORWARDED_FROM_RE = re.compile(r"^From:\s*(?:[^<]*<)?([\w.+-]+@[\w.-]+)", re.IGNORECASE | re.MULTILINE)


def extract_forwarded_from(body: str) -> str | None:
    quoted = _split_quote(body)[1]
    match = FORWARDED_FROM_RE.search(quoted)
    return match.group(1) if match else None


def effective_sender(sender: str, body: str, cfg: BillingConfig) -> str:
    trusted = {d.lower() for d in cfg.ingest.trusted_forwarder_domains}
    if sender_domain(sender) in trusted:
        forwarded = extract_forwarded_from(body)
        if forwarded:
            return forwarded
    return sender


def owner_decision(ref: str, sender: str, body: str) -> tuple[str, dict]:
    new_body = strip_quoted_text(body)
    entries = parse_entries(new_body)
    confirmed = is_confirm_text(new_body) or not new_body.strip()
    if not confirmed and entries is None:
        return "cancel", {"reason": new_body[:500]}
    payload = ConfirmHours(
        ref=ref, sender=sender, entries=entries, note=new_body[:500]
    ).model_dump(mode="json")
    return "confirm_hours", payload


def customer_decision(ref: str, sender: str, received_at: datetime, body: str) -> dict:
    new_body = strip_quoted_text(body)
    lowered = new_body.lower()
    confirmed = not any(word in lowered for word in REJECT_WORDS)
    return CustomerConfirmation(
        ref=ref, sender=sender, confirmed=confirmed, received_at=received_at
    ).model_dump(mode="json")


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


def _token_tenant_candidates(cfg: BillingConfig) -> list[str]:
    candidates = [cfg.ingest.graph.tenant_id, "consumers", "common"]
    return list(dict.fromkeys(t for t in candidates if t))


def _graph_token_url(cfg: BillingConfig, tenant: str) -> str:
    return f"{cfg.ingest.graph.login_base}/{tenant}/oauth2/v2.0/token"


def _token_request_data(cfg: BillingConfig) -> dict:
    graph = cfg.ingest.graph
    if graph.auth_mode == "client_secret":
        return {
            "grant_type": "client_credentials",
            "client_id": graph.client_id,
            "client_secret": os.environ[graph.client_secret_env],
            "scope": graph.scope,
        }
    data = {
        "grant_type": "refresh_token",
        "client_id": graph.client_id,
        "refresh_token": os.environ[graph.refresh_token_env],
    }
    if graph.scope:
        data["scope"] = graph.scope
    return data


async def _graph_token(cfg: BillingConfig) -> str:
    import logging

    logger = logging.getLogger(__name__)
    data = _token_request_data(cfg)
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for tenant in _token_tenant_candidates(cfg):
            response = await client.post(_graph_token_url(cfg, tenant), data=data)
            if response.status_code == 200:
                return response.json()["access_token"]
            try:
                body = response.json()
                detail = f"{body.get('error')} {body.get('error_description', '')[:200]}"
            except ValueError:
                detail = response.text[:200]
            logger.warning("graph token via tenant %s failed: %s %s", tenant, response.status_code, detail)
            errors.append(f"{tenant}: {response.status_code} {detail}")
    raise RuntimeError("graph token failed for all tenants: " + " | ".join(errors))


def _graph_client(cfg: BillingConfig, token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=cfg.ingest.graph.graph_base,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
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


def _mistral_client(cfg: BillingConfig):
    from mistralai.workflows.client import get_mistral_client

    return get_mistral_client()


def _run_items(runs) -> list:
    result = getattr(runs, "result", None)
    if result is None:
        return []
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("runs", "executions", "items", "data"):
            if isinstance(result.get(key), list):
                return result[key]
        return [v for v in result.values() if isinstance(v, dict) or hasattr(v, "model_dump")]
    return []


def _run_dict(run) -> dict | None:
    try:
        if isinstance(run, dict):
            return run
        if hasattr(run, "model_dump"):
            return run.model_dump()
    except Exception:
        return None
    return None


async def _find_running_execution(cfg: BillingConfig, sprint_start: date) -> str | None:
    import json as _json
    import logging

    logger = logging.getLogger(__name__)
    client = _mistral_client(cfg)
    runs = await client.workflows.runs.list_runs_async(
        workflow_identifier=cfg.ingest.billing_workflow_name, page_size=50
    )
    items = _run_items(runs)
    logger.warning("find_running_execution: %d runs for %s", len(items), cfg.ingest.billing_workflow_name)
    for run in items:
        data = _run_dict(run)
        if not data:
            logger.warning("find_running_execution: unparseable run item: %r", str(run)[:120])
            continue
        status = str(data.get("status", "")).upper()
        execution_id = data.get("execution_id") or data.get("id")
        logger.warning(
            "find_running_execution: run %s status=%s keys=%s", execution_id, status, sorted(data.keys())
        )
        if status != "RUNNING" or not execution_id:
            continue
        try:
            query = await client.workflows.executions.query_workflow_execution_async(
                execution_id=str(execution_id), name="state"
            )
        except Exception as exc:
            logger.warning("find_running_execution: query %s failed: %s", execution_id, exc)
            continue
        payload = getattr(query, "result", None)
        if isinstance(payload, str):
            try:
                payload = _json.loads(payload)
            except ValueError:
                pass
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        logger.warning(
            "find_running_execution: query %s payload_type=%s payload=%s",
            execution_id,
            type(payload).__name__,
            str(payload)[:200],
        )
        if isinstance(payload, dict) and payload.get("sprint_start") == sprint_start.isoformat():
            return str(execution_id)
    return None


async def _signal_execution(cfg: BillingConfig, execution_id: str, name: str, payload: dict) -> None:
    client = _mistral_client(cfg)
    await client.workflows.executions.signal_workflow_execution_async(
        execution_id=execution_id, name=name, input=payload
    )


@workflows.activity(
    name="dispatch-reply",
    start_to_close_timeout=timedelta(minutes=2),
    retry_policy_max_attempts=3,
    retry_policy_backoff_coefficient=2.0,
)
async def dispatch_reply(reply: ReplyEmail, cfg: BillingConfig, dry_run: bool = False) -> str:
    sender = effective_sender(reply.sender, reply.body, cfg)
    kind = classify_sender(sender, cfg)
    ref = extract_ref(f"{reply.subject}\n{reply.body}", cfg.ingest.ref_pattern)
    if kind == "unknown":
        return IngestOutcome(message_id=reply.message_id, outcome="ignored_sender", detail=reply.sender)
    if not ref:
        return IngestOutcome(message_id=reply.message_id, outcome="ignored_no_ref", detail=reply.subject)
    sprint_start = cal.parse_ref(ref)
    if kind == "owner":
        signal_name, payload = owner_decision(ref, sender, reply.body)
    else:
        payload = customer_decision(ref, sender, reply.date, reply.body)
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
