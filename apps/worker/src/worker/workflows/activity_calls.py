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
    if isinstance(value, str):
        value = json.loads(value)
    if isinstance(value, model_cls):
        return value
    if isinstance(value, BaseModel):
        return model_cls.model_validate(value.model_dump(mode="json"))
    if isinstance(value, (bytes, bytearray)):
        value = json.loads(value.decode("utf-8"))
    if not isinstance(value, dict):
        for attr in ("value", "payload", "data"):
            inner = getattr(value, attr, None)
            if isinstance(inner, (dict, BaseModel, bytes, bytearray)):
                return coerce(inner, model_cls)
    return model_cls.model_validate(value)


def coerce_list(value: Any, model_cls: type[T]) -> list[T]:
    return [coerce(item, model_cls) for item in (value or [])]
