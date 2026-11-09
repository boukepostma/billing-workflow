import os
from pathlib import Path
from typing import Any

import yaml

import mistralai.workflows as workflows
from worker.workflows.config_models import BillingConfig

__all__ = ["BillingConfig", "load_billing_config", "load_config"]


def _default_config_path() -> Path:
    env_path = os.environ.get("CONFIG_PATH")
    if env_path:
        return Path(env_path)
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "config" / "billing.yaml"
        if candidate.is_file():
            return candidate
    return Path("config") / "billing.yaml"


def _deep_merge(base: dict, overrides: dict) -> dict:
    merged = dict(base)
    for key, value in overrides.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_billing_config(path: Path | None = None, overrides: dict[str, Any] | None = None) -> BillingConfig:
    config_path = path or _default_config_path()
    with open(config_path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if overrides:
        raw = _deep_merge(raw, overrides)
    return BillingConfig(**raw)


@workflows.activity(name="load-config")
async def load_config(overrides: dict[str, Any] | None = None) -> BillingConfig:
    return load_billing_config(overrides=overrides)
