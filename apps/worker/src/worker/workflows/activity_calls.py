import json
from datetime import timedelta
from typing import Any, TypeVar

import temporalio.workflow
from pydantic import BaseModel
from temporalio.common import RetryPolicy

START_TO_CLOSE_TIMEOUT = timedelta(minutes=5)
RETRY_POLICY = RetryPolicy(maximum_attempts=3, backoff_coefficient=2.0)

T = TypeVar("T", bound=BaseModel)


async def call_activity(name: str, *args: Any) -> Any:
    return await temporalio.workflow.execute_activity(
        name,
        args=args,
        start_to_close_timeout=START_TO_CLOSE_TIMEOUT,
        retry_policy=RETRY_POLICY,
    )


def coerce(value: Any, model_cls: type[T]) -> T:
    for _ in range(10):
        if isinstance(value, model_cls):
            return value
        if isinstance(value, str):
            value = json.loads(value)
            continue
        if isinstance(value, (bytes, bytearray)):
            value = json.loads(value.decode("utf-8"))
            continue
        if isinstance(value, dict):
            return model_cls.model_validate(value)
        if isinstance(value, BaseModel) and hasattr(value, "payload"):
            value = value.payload
            continue
        if isinstance(value, BaseModel):
            return model_cls.model_validate(value.model_dump(mode="json"))
        unwrapped = False
        for attr in ("value", "payload", "data"):
            inner = getattr(value, attr, None)
            if inner is not None and not callable(inner):
                value = inner
                unwrapped = True
                break
        if not unwrapped:
            break
    return model_cls.model_validate(value)


def coerce_list(value: Any, model_cls: type[T]) -> list[T]:
    if isinstance(value, str):
        value = json.loads(value)
    return [coerce(item, model_cls) for item in (value or [])]
