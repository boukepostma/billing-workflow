from datetime import timedelta

import temporalio.workflow
from temporalio.common import RetryPolicy

START_TO_CLOSE_TIMEOUT = timedelta(minutes=5)
RETRY_POLICY = RetryPolicy(maximum_attempts=3, backoff_coefficient=2.0)


async def call_activity(name: str, *args):
    return await temporalio.workflow.execute_activity(
        name,
        args=args,
        start_to_close_timeout=START_TO_CLOSE_TIMEOUT,
        retry_policy=RETRY_POLICY,
    )
