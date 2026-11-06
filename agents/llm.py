from __future__ import annotations

import json
import os
import time

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from agents.artifacts import AgentArtifacts
from agents.errors import AgentError
from agents.fast_mode import fast_dev_mode, simulated_artifacts, simulated_text
from agents.structured_output import parse_agent_artifacts
from execution.settings import (
    load_model_num_predict,
    load_model_timeout_seconds,
    load_model_transport_retries,
)
from observability.logging import log_event
from observability.metrics import record_metric

load_dotenv()

DEFAULT_MODEL = "llama3.1:8b"
DEFAULT_TEMPERATURE = 0.0


_COMPACT_SCHEMA = (
    '{"summary": "string", "files": [{"relative_path": "string", '
    '"content": "string", "language": "string", "source_agent": "string"}]}'
)


def get_chat_model(agent_name: str | None = None) -> ChatOllama:
    """Build the chat model used by every software-team agent."""
    model = os.getenv("OLLAMA_MODEL", DEFAULT_MODEL).strip()
    if not model:
        raise AgentError("LLM configuration", "OLLAMA_MODEL cannot be empty.")

    raw_temperature = os.getenv("OLLAMA_TEMPERATURE", str(DEFAULT_TEMPERATURE)).strip()
    try:
        temperature = float(raw_temperature)
    except ValueError as exc:
        raise AgentError(
            "LLM configuration",
            f"OLLAMA_TEMPERATURE must be a number, got {raw_temperature!r}.",
        ) from exc
    try:
        timeout_seconds = load_model_timeout_seconds(agent_name)
        num_predict = load_model_num_predict(agent_name)
    except ValueError as exc:
        raise AgentError("LLM configuration", str(exc)) from exc

    kwargs: dict[str, object] = {
        "model": model,
        "temperature": temperature,
        "num_predict": num_predict,
        "sync_client_kwargs": {"timeout": timeout_seconds},
        "async_client_kwargs": {"timeout": timeout_seconds},
    }
    base_url = os.getenv("OLLAMA_BASE_URL", "").strip()
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOllama(**kwargs)


def _message_text(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text") or block.get("content") or ""
                parts.append(str(text))
            else:
                parts.append(str(block))
        return "\n".join(part for part in parts if part).strip()
    return str(content).strip()


def invoke_agent(agent_name: str, system_prompt: str, user_content: str) -> str:
    """Send one system prompt and one user message, and return the text reply."""
    if not agent_name or not agent_name.strip():
        raise AgentError("LLM configuration", "Agent name cannot be empty.")
    if not system_prompt or not system_prompt.strip():
        raise AgentError(agent_name, "System prompt cannot be empty.")
    if not user_content or not str(user_content).strip():
        raise AgentError(agent_name, "Input cannot be empty.")

    if fast_dev_mode():
        _record_simulated_call(agent_name, user_content)
        return simulated_text(agent_name, user_content)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]
    try:
        response = _invoke_with_retries(
            agent_name,
            lambda chat: chat.invoke(messages),
            prompt_chars=len(system_prompt) + len(user_content),
        )
    except AgentError:
        raise
    except Exception as exc:
        raise AgentError(agent_name, str(exc)) from exc

    text = _message_text(getattr(response, "content", response))
    if not text:
        raise AgentError(agent_name, "Model returned an empty response.")
    return text


def invoke_structured_agent(agent_name: str, system_prompt: str, user_content: str) -> AgentArtifacts:
    """Ask the model for AgentArtifacts and reject malformed responses."""
    if not user_content or not str(user_content).strip():
        raise AgentError(agent_name, "Input cannot be empty.")
    if fast_dev_mode():
        _record_simulated_call(agent_name, user_content)
        return simulated_artifacts(agent_name)
    instructions = (
        f"{system_prompt.rstrip()}\n\n"
        "Return only JSON for an AgentArtifacts object. "
        f"Every file source_agent must be {agent_name!r}. "
        "Do not use FILE: blocks and do not add prose around the JSON.\n"
        f"JSON schema:\n{_COMPACT_SCHEMA}"
    )
    messages = [
        SystemMessage(content=instructions),
        HumanMessage(content=user_content),
    ]
    prompt_chars = len(instructions) + len(user_content)
    if hasattr(ChatOllama, "with_structured_output"):
        return _invoke_native_structured(agent_name, messages, prompt_chars)
    return _invoke_json_text(agent_name, messages, prompt_chars)


def _invoke_native_structured(agent_name: str, messages: list, prompt_chars: int = 0) -> AgentArtifacts:
    def call(chat):
        return chat.with_structured_output(AgentArtifacts, include_raw=True).invoke(messages)

    try:
        result = _invoke_with_retries(agent_name, call, prompt_chars=prompt_chars)
    except AgentError:
        raise
    except Exception as exc:
        raise AgentError(agent_name, f"Structured output call failed: {exc}") from exc

    if isinstance(result, AgentArtifacts):
        return parse_agent_artifacts(agent_name, result.model_dump_json())
    if not isinstance(result, dict):
        raise AgentError(agent_name, "Structured output returned an unexpected type.")

    parsing_error = result.get("parsing_error")
    parsed = result.get("parsed")
    if parsing_error is not None or parsed is None:
        raw_message = result.get("raw")
        raw_text = _message_text(getattr(raw_message, "content", raw_message))
        if raw_text:
            try:
                return parse_agent_artifacts(agent_name, raw_text)
            except AgentError as exc:
                raise AgentError(
                    agent_name,
                    f"{exc.message} Structured parser also reported: {parsing_error}",
                ) from exc
        raise AgentError(
            agent_name,
            f"Structured output was malformed: {parsing_error or 'parsed payload was empty'}",
        )
    if isinstance(parsed, AgentArtifacts):
        return parse_agent_artifacts(agent_name, parsed.model_dump_json())
    if isinstance(parsed, dict):
        try:
            return parse_agent_artifacts(agent_name, json.dumps(parsed))
        except AgentError:
            raise
    raise AgentError(agent_name, "Structured output could not be validated as AgentArtifacts.")


def _invoke_json_text(agent_name: str, messages: list, prompt_chars: int = 0) -> AgentArtifacts:
    try:
        response = _invoke_with_retries(
            agent_name,
            lambda chat: chat.invoke(messages),
            prompt_chars=prompt_chars,
        )
    except AgentError:
        raise
    except Exception as exc:
        raise AgentError(agent_name, str(exc)) from exc
    text = _message_text(getattr(response, "content", response))
    return parse_agent_artifacts(agent_name, text)


def _record_simulated_call(agent_name: str, user_content: str) -> None:
    log_event(
        "model_invocation",
        execution="simulated",
        agent_name=agent_name,
        duration_ms=0,
        prompt_chars=len(user_content),
        num_predict=load_model_num_predict(agent_name),
        timeout_seconds=load_model_timeout_seconds(agent_name),
        note="SIMULATED FAST_DEV_MODE output. Not real model output.",
    )
    try:
        record_metric(
            metric_name="model_invocation",
            agent_name=agent_name,
            duration_ms=0,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            detail={
                "execution": "simulated",
                "token_usage_reported": False,
                "prompt_chars": len(user_content),
                "num_predict": load_model_num_predict(agent_name),
                "timeout_seconds": load_model_timeout_seconds(agent_name),
            },
        )
    except Exception as exc:
        log_event("metric_write_failed", agent_name=agent_name, error_type=type(exc).__name__)


def _invoke_with_retries(agent_name: str, call, prompt_chars: int = 0):
    """Retry transport failures only. Malformed model text is not retried."""
    attempts = load_model_transport_retries() + 1
    started = time.perf_counter()
    last_error: Exception | None = None
    response = None
    for attempt in range(1, attempts + 1):
        try:
            chat = get_chat_model(agent_name)
            response = call(chat)
            last_error = None
            break
        except AgentError:
            raise
        except Exception as exc:
            last_error = exc
            log_event(
                "model_transport_error",
                agent_name=agent_name,
                attempt=attempt,
                error_type=type(exc).__name__,
            )
            if attempt == attempts or not _is_transient(exc):
                break
            time.sleep(min(0.2 * attempt, 1))
    duration_ms = int((time.perf_counter() - started) * 1000)
    usage = extract_token_usage(response)
    try:
        record_metric(
            metric_name="model_invocation",
            agent_name=agent_name,
            duration_ms=duration_ms,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            detail={
                "execution": "ollama",
                "token_usage_reported": usage["reported"],
                "attempts": attempt,
                "error_type": None if last_error is None else type(last_error).__name__,
                "prompt_chars": prompt_chars,
                "num_predict": load_model_num_predict(agent_name),
                "timeout_seconds": load_model_timeout_seconds(agent_name),
            },
        )
    except Exception as exc:
        log_event("metric_write_failed", agent_name=agent_name, error_type=type(exc).__name__)
    if last_error is not None:
        raise last_error
    return response


def extract_token_usage(response: object) -> dict[str, object]:
    """Copy token counts only when the model response reports them."""
    prompt = _token_field(response, "prompt_eval_count", "input_tokens")
    completion = _token_field(response, "eval_count", "output_tokens")
    total = _direct_total(response)
    source = None
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
        source = "sum_of_reported_counts"
    elif total is not None:
        source = "provider"
    reported = prompt is not None or completion is not None or total is not None
    return {
        "reported": reported,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "total_tokens_source": source,
    }


def _token_field(response: object, metadata_key: str, usage_key: str) -> int | None:
    raw = response
    if isinstance(response, dict):
        raw = response.get("raw")
    metadata = getattr(raw, "response_metadata", None)
    if isinstance(metadata, dict) and isinstance(metadata.get(metadata_key), int):
        return metadata[metadata_key]
    usage = getattr(raw, "usage_metadata", None)
    if isinstance(usage, dict) and isinstance(usage.get(usage_key), int):
        return usage[usage_key]
    return None


def _direct_total(response: object) -> int | None:
    raw = response.get("raw") if isinstance(response, dict) else response
    usage = getattr(raw, "usage_metadata", None)
    if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
        return usage["total_tokens"]
    return None


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, AgentError):
        return False
    try:
        import httpx
    except ImportError:
        httpx = None
    if httpx is not None and isinstance(
        exc,
        (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError),
    ):
        return True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    status = getattr(exc, "status_code", None)
    return type(exc).__name__ == "ResponseError" and status in {502, 503, 504}
