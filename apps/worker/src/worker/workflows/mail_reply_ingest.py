from datetime import timedelta

import mistralai.workflows as workflows

from worker.workflows.activity_calls import call_activity, coerce, coerce_list
from worker.workflows.config_models import BillingConfig
from worker.workflows.models import IngestInput, IngestOutcome, IngestResult, ReplyEmail


@workflows.workflow.define(
    name="mail-reply-ingest",
    workflow_display_name="Mail Reply Ingest",
    workflow_description=(
        "Polls the Outlook inbox for replies to billing mails (Proton auto-forwards "
        "to Outlook) and signals the matching running billing-sprint execution: "
        "owner hours confirmation or customer timesheet confirmation, matched by "
        "the sprint ref code (SP-YYYY-MM-DD) in the subject."
    ),
    execution_timeout=timedelta(minutes=30),
)
class MailReplyIngestWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: IngestInput) -> IngestResult:
        cfg = coerce(await call_activity("load-config", input.overrides), BillingConfig)
        lookback = input.lookback_days or cfg.ingest.lookback_days
        replies = coerce_list(await call_activity("poll-replies", cfg, lookback), ReplyEmail)
        outcomes: list[IngestOutcome] = []
        processed: list[str] = []
        for reply in replies:
            outcome = coerce(await call_activity("dispatch-reply", reply, cfg, input.dry_run), IngestOutcome)
            outcomes.append(outcome)
            if outcome.outcome not in ("ignored_sender", "ignored_no_ref", "no_running_execution"):
                processed.append(reply.mailbox_seq)
        if processed and not input.dry_run:
            await call_activity("mark-replies-processed", cfg, processed)
        return IngestResult(checked=len(replies), outcomes=outcomes)
