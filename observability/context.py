"""Run id used by model and stage metrics. Absent outside a workflow run."""

from __future__ import annotations

from contextvars import ContextVar, Token

_run_id: ContextVar[str | None] = ContextVar("ase_run_id", default=None)
_db_path: ContextVar[str | None] = ContextVar("ase_db_path", default=None)


def set_run_context(run_id: str, db_path: str) -> tuple[Token, Token]:
    return _run_id.set(run_id), _db_path.set(db_path)


def reset_run_context(tokens: tuple[Token, Token]) -> None:
    run_token, db_token = tokens
    _run_id.reset(run_token)
    _db_path.reset(db_token)


def current_run_id() -> str | None:
    return _run_id.get()


def current_db_path() -> str | None:
    return _db_path.get()
