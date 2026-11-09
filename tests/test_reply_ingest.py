from datetime import date

from worker.activities.reply_ingest import (
    classify_sender,
    extract_ref,
    is_confirm_text,
    parse_entries,
    reply_from_outlook,
)
from worker.workflows.config_models import BillingConfig

CFG = BillingConfig(
    owner={"name": "Bouke Postma", "email": "bouke@guero.nl", "confirm_domains": ["guero.nl"]},
    customer={"confirm_domains": ["tcs.com"]},
)


def test_classify_sender():
    assert classify_sender("bouke@guero.nl", CFG) == "owner"
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


def test_reply_from_outlook_owner():
    item = {"metadata": {"message_id": "AAA"}, "title": "Re: Hours registration proposal [SP-2026-09-29]"}
    full = {
        "from": "Bouke Postma <bouke@guero.nl>",
        "subject": "Re: Hours registration proposal [SP-2026-09-29]",
        "receivedDateTime": "2026-10-20T09:30:00+02:00",
        "body": "confirm",
    }
    reply = reply_from_outlook(item, full, CFG.ingest.ref_pattern)
    assert reply.sender == "bouke@guero.nl"
    assert reply.message_id == "AAA"
    assert "SP-2026-09-29" in reply.subject
    assert reply.date.year == 2026
    assert classify_sender(reply.sender, CFG) == "owner"
    assert is_confirm_text(reply.body)


def test_reply_from_outlook_customer():
    item = {"metadata": {"message_id": "BBB"}, "title": "Re: Timesheet [SP-2026-09-29]"}
    full = {
        "from": "abhirup.ghosh1@tcs.com",
        "subject": "Re: Timesheet [SP-2026-09-29]",
        "receivedDateTime": "2026-10-21T12:00:00+02:00",
        "body": "Confirmed, looks good.",
    }
    reply = reply_from_outlook(item, full, CFG.ingest.ref_pattern)
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
