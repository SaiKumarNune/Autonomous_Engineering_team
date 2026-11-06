"""Validated limits for the repair loop.

The model cannot change these values. A missing variable uses the default.
A value outside the allowed range stops the workflow before any agent runs.
"""

from __future__ import annotations

import os
import re

DEFAULT_MAX_REPAIR_ATTEMPTS = 3
DEFAULT_MODEL_TIMEOUT_SECONDS = 240
DEFAULT_MODEL_NUM_PREDICT = 1024
DEFAULT_MODEL_TRANSPORT_RETRIES = 1
# Caps sit under the local 4096-token context window. Measured llama3.1:8b
# completions were about 600–1900 tokens, except DevOps, which ran to the old
# 4096 cap and took about five minutes.
AGENT_NUM_PREDICT = {
    "Product Manager": 768,
    "Architect": 1024,
    "Backend Engineer": 2048,
    "Frontend Engineer": 1024,
    "QA Engineer": 1280,
    "DevOps Engineer": 1280,
    "Reviewer": 768,
    "Repair": 1280,
}
_ATTEMPTS = re.compile(r"[0-3]")
_RETRIES = re.compile(r"[0-2]")
_NUMBER = re.compile(r"[0-9]+")


def load_max_repair_attempts() -> int:
    raw = os.getenv("MAX_REPAIR_ATTEMPTS", str(DEFAULT_MAX_REPAIR_ATTEMPTS)).strip()
    if not _ATTEMPTS.fullmatch(raw):
        raise ValueError(
            "MAX_REPAIR_ATTEMPTS must be an integer from 0 through 3, "
            f"got {raw!r}."
        )
    return int(raw)


def load_model_timeout_seconds(agent_name: str | None = None) -> float:
    raw = _agent_setting("OLLAMA_TIMEOUT_SECONDS", agent_name)
    if raw is None:
        raw = str(DEFAULT_MODEL_TIMEOUT_SECONDS)
    if not _NUMBER.fullmatch(raw):
        raise ValueError(f"OLLAMA_TIMEOUT_SECONDS must be a whole number of seconds, got {raw!r}.")
    value = int(raw)
    if not 10 <= value <= 600:
        raise ValueError("OLLAMA_TIMEOUT_SECONDS must be from 10 through 600.")
    return float(value)


def load_model_num_predict(agent_name: str | None = None) -> int:
    specific = _agent_setting("OLLAMA_NUM_PREDICT", agent_name, fallback=False)
    if specific is not None:
        return _checked_num_predict(specific)
    global_raw = os.getenv("OLLAMA_NUM_PREDICT", "").strip()
    if global_raw:
        return _checked_num_predict(global_raw)
    if agent_name and agent_name in AGENT_NUM_PREDICT:
        return AGENT_NUM_PREDICT[agent_name]
    return DEFAULT_MODEL_NUM_PREDICT


def _checked_num_predict(raw: str) -> int:
    if not _NUMBER.fullmatch(raw):
        raise ValueError(f"OLLAMA_NUM_PREDICT must be an integer, got {raw!r}.")
    value = int(raw)
    if not 256 <= value <= 8192:
        raise ValueError("OLLAMA_NUM_PREDICT must be from 256 through 8192.")
    return value


def _agent_setting(prefix: str, agent_name: str | None, fallback: bool = True) -> str | None:
    if agent_name:
        suffix = "".join(char if char.isalnum() else "_" for char in agent_name.upper())
        while "__" in suffix:
            suffix = suffix.replace("__", "_")
        specific = os.getenv(f"{prefix}_{suffix.strip('_')}", "").strip()
        if specific:
            return specific
    if not fallback:
        return None
    shared = os.getenv(prefix, "").strip()
    return shared or None


def load_model_transport_retries() -> int:
    raw = os.getenv("OLLAMA_TRANSPORT_RETRIES", str(DEFAULT_MODEL_TRANSPORT_RETRIES)).strip()
    if not _RETRIES.fullmatch(raw):
        raise ValueError(
            "OLLAMA_TRANSPORT_RETRIES must be an integer from 0 through 2, "
            f"got {raw!r}."
        )
    return int(raw)
