from __future__ import annotations

import json

from pydantic import ValidationError

from agents.artifacts import AgentArtifacts
from agents.errors import AgentError


def parse_agent_artifacts(agent_name: str, raw: str) -> AgentArtifacts:
    """Parse model text into AgentArtifacts. Malformed JSON is an error."""
    if not raw or not raw.strip():
        raise AgentError(agent_name, "Model returned an empty response.")
    try:
        payload = _extract_json_object(raw)
    except ValueError as exc:
        raise AgentError(agent_name, str(exc)) from exc
    if not isinstance(payload, dict):
        raise AgentError(
            agent_name,
            "Artifact response must be a JSON object with 'files' and 'summary'.",
        )
    try:
        artifacts = AgentArtifacts.model_validate(payload)
    except ValidationError as exc:
        raise AgentError(agent_name, _format_validation_error(exc)) from exc
    if not artifacts.files and agent_name != "Frontend Engineer":
        raise AgentError(agent_name, "Artifact JSON failed validation: files cannot be empty.")

    mismatches = [
        f"{item.relative_path}: source_agent must be {agent_name!r}, got {item.source_agent!r}."
        for item in artifacts.files
        if item.source_agent != agent_name
    ]
    if mismatches:
        raise AgentError(
            agent_name,
            "Artifact JSON failed validation: " + "; ".join(mismatches),
        )
    return artifacts


def _extract_json_object(raw: str) -> object:
    text = raw.strip()
    if text.startswith("```"):
        text = _strip_fence(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as first_error:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError(
                "Response is not JSON. Expected an object with 'files' and 'summary', "
                f"not free-form text. Parser error: {first_error.msg}."
            ) from first_error
        snippet = text[start : end + 1]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "Response JSON is malformed "
                f"at line {exc.lineno}, column {exc.colno}: {exc.msg}. "
                "Return only the AgentArtifacts object."
            ) from exc


def _strip_fence(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", ())) or "response"
        parts.append(f"{location}: {error.get('msg', 'invalid value')}")
    detail = "; ".join(parts) or "the payload did not match AgentArtifacts."
    return (
        "Artifact JSON failed validation: "
        f"{detail}. Include files[].relative_path, content, language, source_agent, and summary."
    )
