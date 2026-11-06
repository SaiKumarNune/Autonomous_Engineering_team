"""JSON logs for workflow events. Prompts and file contents are not logged."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

_LOGGER_NAME = "autonomous_team"
_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    logger = logging.getLogger(_LOGGER_NAME)
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)
    logger.propagate = False
    if not logger.handlers:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(stream)
        path = _log_path()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = logging.FileHandler(path, encoding="utf-8")
            handle.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handle)
    _configured = True


def log_event(event: str, **fields: object) -> None:
    configure_logging()
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
    }
    for key, value in fields.items():
        if value is not None:
            payload[key] = value
    logging.getLogger(_LOGGER_NAME).info(json.dumps(payload, default=str, sort_keys=True))


def _log_path() -> Path | None:
    if os.getenv("PYTEST_CURRENT_TEST") and not os.getenv("ASE_LOG_FILE"):
        return None
    configured = os.getenv("ASE_LOG_FILE", "").strip() or os.getenv("LOG_FILE_PATH", "").strip()
    if not configured:
        return None
    path = Path(configured)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path
    return path
