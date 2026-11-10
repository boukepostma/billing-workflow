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
