from datetime import date

from worker.activities.reply_ingest import (
    classify_sender,
    extract_ref,
    is_confirm_text,
    parse_entries,
    reply_from_graph,
)
from worker.workflows.config_models import BillingConfig

CFG = BillingConfig(
    owner={"name": "Bouke Postma", "email": "admin@guero.nl", "confirm_domains": ["guero.nl"]},
    customer={"confirm_domains": ["tcs.com"]},
)


def test_classify_sender():
    assert classify_sender("admin@guero.nl", CFG) == "owner"
    assert classify_sender("abhirup.ghosh1@tcs.com", CFG) == "customer"
    assert classify_sender("evil@other.com", CFG) == "unknown"


def test_extract_ref():
    assert extract_ref("Re: Timesheet [SP-2026-09-29]", CFG.ingest.ref_pattern) == "SP-2026-09-29"
    assert extract_ref("no ref here", CFG.ingest.ref_pattern) is None


def test_parse_entries():
    entries = parse_entries("2026-10-06: 4\n2026-10-07: 6.5\nthanks")
    assert [e.model_dump() for e in entries] == [
        {"date": date(2026, 10, 6), "hours": 4.0},
        {"date": date(2026, 10, 7), "hours": 6.5},
    ]
    assert parse_entries("just confirm") is None


def test_is_confirm_text():
    assert is_confirm_text("Confirmed, looks good.")
    assert is_confirm_text("ok")
    assert not is_confirm_text("please reject this")
    assert not is_confirm_text("random text")


def test_reply_from_graph_owner():
    item = {
        "id": "AAA",
        "subject": "Re: Hours registration proposal [SP-2026-09-29]",
        "from": {"emailAddress": {"address": "admin@guero.nl", "name": "Bouke Postma"}},
        "receivedDateTime": "2026-10-20T09:30:00+02:00",
    }
    reply = reply_from_graph(item, "confirm", CFG.ingest.ref_pattern)
    assert reply.sender == "admin@guero.nl"
    assert reply.message_id == "AAA"
    assert "SP-2026-09-29" in reply.subject
    assert reply.date.year == 2026
    assert classify_sender(reply.sender, CFG) == "owner"
    assert is_confirm_text(reply.body)


def test_reply_from_graph_customer():
    item = {
        "id": "BBB",
        "subject": "Re: Timesheet [SP-2026-09-29]",
        "from": {"emailAddress": {"address": "abhirup.ghosh1@tcs.com"}},
        "receivedDateTime": "2026-10-21T12:00:00+02:00",
    }
    reply = reply_from_graph(item, "Confirmed, looks good.", CFG.ingest.ref_pattern)
    assert classify_sender(reply.sender, CFG) == "customer"
    assert is_confirm_text(reply.body)


def test_coerce_handles_payload_wrappers():
    from worker.workflows.activity_calls import coerce, coerce_list
    from worker.workflows.config_models import BillingConfig
    from worker.workflows.models import IngestOutcome

    class FakePayload:
        def __init__(self, value):
            self.value = value

    cfg = coerce(FakePayload({"owner": {"name": "B", "email": "b@guero.nl"}}), BillingConfig)
    assert cfg.owner.email == "b@guero.nl"
    assert cfg.eboekhouden.relation_code == "GEEKSOFT"
    outcomes = coerce_list([FakePayload({"message_id": "m1", "outcome": "signalled"})], IngestOutcome)
    assert outcomes[0].outcome == "signalled"


def test_coerce_unwraps_payload_with_context():
    from mistralai.workflows.models.payload import PayloadWithContext
    from worker.workflows.activity_calls import coerce
    from worker.workflows.config_models import BillingConfig

    payload = PayloadWithContext.model_construct(
        context=None, payload='{"sprint": {"anchor_tuesday": "2026-09-29"}}'
    )
    cfg = coerce(payload, BillingConfig)
    assert str(cfg.sprint.anchor_tuesday) == "2026-09-29"


def test_graph_token_request_refresh_token(monkeypatch):
    import os

    from worker.activities.reply_ingest import _token_request_data

    monkeypatch.setenv("MISTRAL_MS_GRAPH_REFRESH_TOKEN", "rt-123")
    cfg = BillingConfig()
    cfg.ingest.graph.auth_mode = "refresh_token"
    cfg.ingest.graph.client_id = "3e7b867b-bcf7-45a9-bf85-0e1a7cb8c1e1"
    data = _token_request_data(cfg)
    assert data["grant_type"] == "refresh_token"
    assert data["refresh_token"] == "rt-123"
    assert data["client_id"] == "3e7b867b-bcf7-45a9-bf85-0e1a7cb8c1e1"
    assert "scope" not in data  # refresh_token grant keeps original consented scopes


def test_graph_token_request_client_secret(monkeypatch):
    from worker.activities.reply_ingest import _token_request_data

    monkeypatch.setenv("MSGRAPH_CLIENT_SECRET", "cs-456")
    cfg = BillingConfig()
    cfg.ingest.graph.auth_mode = "client_secret"
    data = _token_request_data(cfg)
    assert data["grant_type"] == "client_credentials"
    assert data["client_secret"] == "cs-456"


def test_token_tenant_candidates_fallback_order():
    from worker.activities.reply_ingest import _token_tenant_candidates

    cfg = BillingConfig()
    cfg.ingest.graph.tenant_id = "39fa90e4-4d1f-43f3-a738-cc91f45ae7f0"
    assert _token_tenant_candidates(cfg) == [
        "39fa90e4-4d1f-43f3-a738-cc91f45ae7f0",
        "consumers",
        "common",
    ]
    cfg.ingest.graph.tenant_id = "consumers"
    assert _token_tenant_candidates(cfg) == ["consumers", "common"]


def test_coerce_list_unwraps_payload_wrapper_around_json_array():
    import json

    from mistralai.workflows.models.payload import PayloadWithContext
    from worker.workflows.activity_calls import coerce_list
    from worker.workflows.models import ReplyEmail

    payload = PayloadWithContext.model_construct(
        context=None,
        payload=json.dumps([{"message_id": "m1", "sender": "a@guero.nl", "subject": "s", "date": "2026-11-10T00:00:00Z", "body": "confirm"}]),
    )
    replies = coerce_list(payload, ReplyEmail)
    assert len(replies) == 1
    assert replies[0].sender == "a@guero.nl"


def test_run_items_normalization():
    from worker.activities.reply_ingest import _run_dict, _run_items

    class Resp:
        def __init__(self, result):
            self.result = result

    assert _run_items(Resp({"executions": [{"execution_id": "e1"}]})) == [{"execution_id": "e1"}]
    assert _run_items(Resp([{"execution_id": "e2"}])) == [{"execution_id": "e2"}]
    assert _run_items(Resp({"other": {"execution_id": "e3"}})) == [{"execution_id": "e3"}]
    assert _run_dict({"a": 1}) == {"a": 1}
    assert _run_dict("junk") is None


def test_run_items_model_response():
    from worker.activities.reply_ingest import _run_items

    class FakeListResp:
        def model_dump(self):
            return {"executions": [{"execution_id": "e9", "status": "RUNNING"}], "next_page_token": None}

    class Resp:
        def __init__(self, result):
            self.result = result

    items = _run_items(Resp(FakeListResp()))
    assert items == [{"execution_id": "e9", "status": "RUNNING"}]


def test_invoice_body_real_rest_shape():
    from datetime import date

    from worker.activities.eboekhouden import _invoice_body
    from worker.workflows.config_models import BillingConfig
    from worker.workflows.models import CreateInvoiceParams, InvoiceLine

    cfg = BillingConfig()
    params = CreateInvoiceParams(
        ref="SP-2026-10-20",
        sprint_start=date(2026, 10, 20),
        sprint_end=date(2026, 11, 9),
        factuur_datum=date(2026, 11, 12),
        factuurtekst="Sprint 20 okt - 9 nov",
        regels=[InvoiceLine(omschrijving="Sprint 20 okt - 9 nov", aantal=66.0)],
    )
    ids = {"relation_id": 123, "template_id": 45, "email_template_id": 6, "unit_id": 7, "ledger_id": 8000}
    body = _invoice_body(params, cfg, ids)
    assert body["relationId"] == 123
    assert body["date"] == "2026-11-12"
    assert body["termOfPayment"] == 45
    assert body["templateId"] == 45
    assert body["emailTemplateId"] == 6
    assert body["text"] == "Sprint 20 okt - 9 nov"
    assert body["reference"] == "SP-2026-10-20"
    assert body["inExVat"] == "EX"
    item = body["items"][0]
    assert item["quantity"] == 66.0
    assert item["code"] == "UREN"
    assert item["pricePerUnit"] == 105.0
    assert item["vatCode"] == "HOOG_VERK_21"
    assert item["ledgerId"] == 8000
    assert item["unitId"] == 7
    assert item["description"] == "Sprint 20 okt - 9 nov"


FORWARDED_OWNER_REPLY = """2026-11-03: 6

-------- Original Message --------
From: admin@guero.nl
Sent: Tuesday, 10 November 2026 01:23
To: admin@guero.nl
Subject: Hours registration proposal - Sprint 2026-10-20 - 2026-11-09 [SP-2026-10-20]

Hi Bouke Postma,

Sprint 20 okt - 9 nov has ended.

Reply to this mail:
- "confirm" to accept the full proposal as-is, or
- send only the lines you want to change or add, e.g. "2026-10-28: 4" (override that day), "2026-11-02: 6" (add a day), or "2026-10-27: 0" (remove a day).

- 2026-10-20 (Tuesday): 8 hours
- 2026-10-28 (Wednesday): 8 hours
"""


def test_strip_quoted_text_cuts_at_original_message():
    from worker.activities.reply_ingest import strip_quoted_text

    stripped = strip_quoted_text(FORWARDED_OWNER_REPLY)
    assert stripped.strip() == "2026-11-03: 6"
    assert "2026-10-28: 4" not in stripped


def test_owner_decision_ignores_quoted_examples():
    from worker.activities.reply_ingest import owner_decision

    signal, payload = owner_decision("SP-2026-10-20", "admin@guero.nl", FORWARDED_OWNER_REPLY)
    assert signal == "confirm_hours"
    assert payload["entries"] == [{"date": "2026-11-03", "hours": 6.0}]
    assert "Original Message" not in payload["note"]


def test_owner_decision_bare_forward_confirms():
    from worker.activities.reply_ingest import owner_decision

    body = '\n-------- Original Message --------\nFrom: admin@guero.nl\n"confirm" ... "2026-10-28: 4"'
    signal, payload = owner_decision("SP-2026-10-20", "admin@guero.nl", body)
    assert signal == "confirm_hours"
    assert payload["entries"] is None


def test_owner_decision_cancel_on_reject_words_only():
    from worker.activities.reply_ingest import owner_decision

    signal, payload = owner_decision("SP-2026-10-20", "admin@guero.nl", "no, please reject this\n\n> quoted")
    assert signal == "cancel"


def test_customer_decision_reject_in_quote_does_not_count():
    from datetime import datetime, timezone

    from worker.activities.reply_ingest import customer_decision

    body = 'Looks good.\n\n-------- Original Message --------\nplease reject'
    payload = customer_decision(
        "SP-2026-10-20", "a@tcs.com", datetime(2026, 11, 12, tzinfo=timezone.utc), body
    )
    assert payload["confirmed"] is True


def test_customer_decision_reject_in_new_part():
    from datetime import datetime, timezone

    from worker.activities.reply_ingest import customer_decision

    payload = customer_decision(
        "SP-2026-10-20", "a@tcs.com", datetime(2026, 11, 12, tzinfo=timezone.utc),
        "please reject this timesheet",
    )
    assert payload["confirmed"] is False


def test_overrides_deep_merge():
    from worker.activities.settings import load_billing_config

    cfg = load_billing_config(
        overrides={
            "mail": {
                "timesheet": {"to": ["admin@guero.nl"], "cc": []},
                "invoice": {"to": ["admin@guero.nl"]},
            },
            "sprint": {"hours_per_day": 6},
        }
    )
    assert cfg.mail.timesheet.to == ["admin@guero.nl"]
    assert cfg.mail.timesheet.cc == []
    assert cfg.mail.invoice.to == ["admin@guero.nl"]
    assert cfg.mail.invoice.cc == ["vandana.c@geeksoft.eu"]  # deep-merged, not replaced
    assert cfg.mail.proposal.to == []                        # untouched
    assert cfg.sprint.hours_per_day == 6.0
    assert cfg.sprint.anchor_tuesday is not None             # untouched
    assert cfg.eboekhouden.relation_code == "GEEKSOFT"       # untouched


CUSTOMER_FORWARD_BODY = """Approved, thanks!

-------- Forwarded message --------
From: Abhirup Ghosh <abhirup.ghosh1@tcs.com>
Sent: Thursday, 12 November 2026 14:02
To: admin@guero.nl
Subject: RE: Timesheet for your approval - Sprint 2026-10-20 - 2026-11-09 [SP-2026-10-20]

Hi,

Please find the timesheet ...
"""


def test_effective_sender_unwraps_guero_forward():
    from worker.activities.settings import load_billing_config
    from worker.activities.reply_ingest import effective_sender, extract_forwarded_from

    cfg = load_billing_config()
    assert extract_forwarded_from(CUSTOMER_FORWARD_BODY) == "abhirup.ghosh1@tcs.com"
    # any @guero.nl address is a trusted forwarder, not just admin@
    assert effective_sender("admin@guero.nl", CUSTOMER_FORWARD_BODY, cfg) == "abhirup.ghosh1@tcs.com"
    assert effective_sender("bouke@guero.nl", CUSTOMER_FORWARD_BODY, cfg) == "abhirup.ghosh1@tcs.com"
    # untrusted forwarder is NOT unwrapped
    assert effective_sender("mallory@example.com", CUSTOMER_FORWARD_BODY, cfg) == "mallory@example.com"
    # non-forward from guero.nl stays owner
    assert effective_sender("admin@guero.nl", "confirm please", cfg) == "admin@guero.nl"


def test_classify_unwrapped_customer_forward():
    from worker.activities.settings import load_billing_config
    from worker.activities.reply_ingest import classify_sender, effective_sender

    cfg = load_billing_config()
    eff = effective_sender("admin@guero.nl", CUSTOMER_FORWARD_BODY, cfg)
    assert classify_sender(eff, cfg) == "customer"
    assert classify_sender("admin@guero.nl", cfg) == "owner"


def test_customer_decision_uses_unwrapped_sender():
    from datetime import datetime, timezone

    from worker.activities.settings import load_billing_config
    from worker.activities.reply_ingest import customer_decision, effective_sender

    cfg = load_billing_config()
    sender = effective_sender("admin@guero.nl", CUSTOMER_FORWARD_BODY, cfg)
    payload = customer_decision(
        "SP-2026-10-20", sender, datetime(2026, 11, 12, 14, 5, tzinfo=timezone.utc), CUSTOMER_FORWARD_BODY
    )
    assert payload["sender"] == "abhirup.ghosh1@tcs.com"
    assert payload["confirmed"] is True


def test_legacy_vat_code_normalized():
    from worker.activities.eboekhouden import _normalize_vat_code

    assert _normalize_vat_code("HOOG") == "HOOG_VERK_21"
    assert _normalize_vat_code("LAAG") == "LAAG_VERK_9"
    assert _normalize_vat_code("HOOG_VERK_21") == "HOOG_VERK_21"
