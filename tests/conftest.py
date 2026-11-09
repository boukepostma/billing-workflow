import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from temporalio.converter import DataConverter
from temporalio.testing import WorkflowEnvironment

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "worker" / "src"))

from mistralai.workflows.core.config.config import config  # noqa: E402
from mistralai.workflows.core.dependencies.dependency_injector import DependencyInjector  # noqa: E402
from mistralai.workflows.core.temporal.payload_converter import (  # noqa: E402
    MistralWorkflowsPayloadConverter,
)


@pytest.fixture(autouse=True)
def _sdk_test_config():
    original_deployment_name = config.worker.deployment_name
    original_backoff = config.worker.local_retry_backoff_coefficient
    config.worker.deployment_name = "test-task-queue"
    config.worker.local_retry_backoff_coefficient = 0
    common = config.common
    originals = (
        common.mistral_workflows_otel_traces_export,
        common.mistral_workflows_otel_metrics_export,
        common.mistral_workflows_otel_logs_export,
    )
    common.mistral_workflows_otel_traces_export = False
    common.mistral_workflows_otel_metrics_export = False
    common.mistral_workflows_otel_logs_export = False
    with patch(
        "mistralai.workflows.core.tracing.utils.temporalio.workflow.upsert_search_attributes"
    ):
        yield
    config.worker.deployment_name = original_deployment_name
    config.worker.local_retry_backoff_coefficient = original_backoff
    (
        common.mistral_workflows_otel_traces_export,
        common.mistral_workflows_otel_metrics_export,
        common.mistral_workflows_otel_logs_export,
    ) = originals
    DependencyInjector.clear_resolved_dependencies()


@pytest_asyncio.fixture
async def temporal_env():
    data_converter = DataConverter(payload_converter_class=MistralWorkflowsPayloadConverter)
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=data_converter
    ) as env:
        yield env
