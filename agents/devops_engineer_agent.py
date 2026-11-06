from agents.artifacts import AgentArtifacts
from agents.devops_templates import render_fastapi_sqlite
from agents.errors import AgentError, ArtifactValidationError
from agents.fast_mode import fast_dev_mode, simulated_artifacts
from tools.validators import validate_agent_artifacts

AGENT_NAME = "DevOps Engineer"


def run_devops_engineer_agent(
    project_files_summary: str,
    correction: str = "",
    *,
    workspace_path: str = "",
    manifest: list[dict] | None = None,
) -> AgentArtifacts:
    """Select a deployment template and explain it.

    Standard Docker and CI files are rendered from version-controlled templates.
    ``correction`` is accepted so the graph signature stays stable; a model is
    not asked to rewrite the files. FAST_DEV_MODE returns labeled simulated
    files because that mode does not provide a supported application.
    """
    del correction
    if not project_files_summary or not project_files_summary.strip():
        raise AgentError(AGENT_NAME, "project_files_summary cannot be empty.")
    if fast_dev_mode():
        return simulated_artifacts(AGENT_NAME)
    if not str(workspace_path or "").strip():
        raise AgentError(
            AGENT_NAME,
            "No supported DevOps template: validated workspace metadata is required.",
        )
    artifacts = render_fastapi_sqlite(workspace_path, list(manifest or []))
    result = validate_agent_artifacts(artifacts, agent_name=AGENT_NAME)
    if not result.valid:
        raise ArtifactValidationError(AGENT_NAME, result)
    return artifacts
