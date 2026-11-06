from pathlib import Path

import streamlit as st

try:
    from database.repository import latest_approval
    from execution.fingerprint import artifact_hash
    from execution.status import describe_verification, export_allowed
    from tools.workspace import build_project_zip, build_tree, read_text
    from workflows import OUTPUT_SECTIONS, resume_software_team_workflow, run_software_team_workflow

    import_error = None
except Exception as exc:
    OUTPUT_SECTIONS = ()
    build_project_zip = None
    build_tree = None
    read_text = None
    artifact_hash = None
    latest_approval = None
    describe_verification = None
    export_allowed = None
    resume_software_team_workflow = None
    run_software_team_workflow = None
    import_error = exc

st.set_page_config(page_title="Autonomous Software Engineering Team", layout="wide")
st.title("Autonomous Software Engineering Team")


def _render_errors(errors: object) -> None:
    if not errors:
        return
    st.write("**Errors**")
    if not isinstance(errors, list):
        st.write(errors)
        return
    for error in errors:
        if isinstance(error, dict):
            agent = error.get("agent", "agent")
            message = error.get("message", "")
            st.write(f"{agent}: {message}")
        else:
            st.write(str(error))


def _render_validation(results: object) -> None:
    if not results:
        return
    st.subheader("Validation")
    if not isinstance(results, list):
        st.write(results)
        return
    for result in results:
        if not isinstance(result, dict):
            st.write(result)
            continue
        agent = result.get("agent", "validation")
        state = "valid" if result.get("valid") else "invalid"
        blocking = "blocking" if result.get("blocking") else "informational"
        st.write(f"**{agent}:** {state} ({blocking})")
        for issue in result.get("issues") or []:
            st.write(f"- {issue}")
        if result.get("limitation"):
            st.caption(str(result["limitation"]))


def _render_files(output: dict) -> None:
    workspace = output.get("workspace_path") or ""
    manifest = output.get("artifact_manifest") or []
    if not workspace and not manifest:
        return
    st.subheader("Generated files")
    workspace_path = Path(workspace) if workspace else None
    if workspace_path and workspace_path.is_dir() and build_tree is not None:
        st.code(build_tree(workspace_path, str(output.get("run_id") or "project")))
    elif manifest:
        for item in manifest:
            st.write(item.get("relative_path", ""))

    if workspace_path and workspace_path.is_dir() and read_text is not None:
        for item in manifest:
            relative = str(item.get("relative_path") or "")
            if not relative:
                continue
            try:
                content = read_text(workspace_path, relative)
            except Exception as exc:
                st.warning(f"{relative}: {exc}")
                continue
            with st.expander(f"Preview {relative}"):
                st.code(content)
        if build_project_zip is not None:
            try:
                payload = build_project_zip(workspace_path)
            except Exception as exc:
                st.warning(f"ZIP was not created: {exc}")
                payload = b""
            if payload:
                allowed = False
                if export_allowed is not None:
                    allowed = export_allowed(output, approval_matches=_approval_matches(output))
                if allowed:
                    st.download_button(
                        "Download approved project ZIP",
                        data=payload,
                        file_name=f"{output.get('run_id') or 'project'}.zip",
                        mime="application/zip",
                    )
                else:
                    st.warning("Download is disabled until this artifact version is approved.")


def _approval_matches(output: dict) -> bool:
    if artifact_hash is None or latest_approval is None:
        return False
    workspace = output.get("workspace_path") or ""
    if not workspace or not output.get("db_path"):
        return False
    try:
        current = artifact_hash(Path(workspace))
    except OSError:
        return False
    decision = latest_approval(output.get("db_path"), str(output.get("run_id") or ""), current)
    return decision == "approved" and current == output.get("artifact_hash")


def _render_metrics(output: dict) -> None:
    metrics = output.get("run_metrics") or {}
    if not metrics and not output.get("consistency"):
        return
    st.subheader("Run metrics")
    token_usage = metrics.get("token_usage") or {}
    if token_usage.get("reported"):
        st.write(
            "Token usage reported by the model: "
            f"prompt {token_usage.get('prompt_tokens')}, "
            f"completion {token_usage.get('completion_tokens')}, "
            f"total {token_usage.get('total_tokens')}."
        )
    else:
        st.write("Token usage was not reported for this run.")
    if metrics.get("sandbox_duration_ms") is not None:
        st.write(f"Sandbox duration: {metrics.get('sandbox_duration_ms')} ms")
    for stage in metrics.get("stages") or []:
        st.write(
            f"{stage.get('agent_name') or stage.get('metric_name')}: "
            f"{stage.get('duration_ms')} ms"
        )
    consistency = output.get("consistency") or {}
    if consistency:
        state = "consistent" if consistency.get("consistent") else "inconsistent"
        st.write(f"Artifact check: {state}")
        for issue in consistency.get("issues") or []:
            st.write(f"- {issue}")
    inspection = output.get("dependency_inspection") or {}
    if inspection:
        st.write("Sandbox packages stay pinned. Generated requirements are not installed.")
        for item in inspection.get("unapproved") or []:
            st.write(f"Unapproved dependency request: {item}")


def _render_execution(output: dict) -> None:
    verification = {"detail": "This project has not completed a sandbox test run."}
    if describe_verification is not None:
        verification = describe_verification(output, approval_matches=_approval_matches(output))
    lifecycle = output.get("lifecycle_status") or "unknown"
    st.write(f"**Lifecycle:** {lifecycle}")
    st.write(str(verification["detail"]))
    _render_metrics(output)
    if output.get("artifact_hash"):
        st.write(f"**Artifact hash:** {output.get('artifact_hash')}")

    results = output.get("test_results") or []
    st.subheader("Sandbox test results")
    if not results:
        st.write("No sandbox test results.")
    for item in results:
        if not isinstance(item, dict):
            st.write(item)
            continue
        st.write(
            f"{item.get('kind', 'unknown')} "
            f"(exit {item.get('exit_code')}, sandbox_enforced={item.get('sandbox_enforced')})"
        )
        if item.get("message"):
            st.write(str(item["message"]))
        if item.get("kind") == "sandbox_error":
            st.error(str(item.get("message") or "Sandbox execution failed."))
        log = "\n".join(
            part for part in (str(item.get("stdout") or ""), str(item.get("stderr") or "")) if part
        )
        if log:
            st.code(log[:4000])
        for diagnostic in item.get("diagnostics") or []:
            st.write(f"- {diagnostic}")

    st.subheader("Repair attempts")
    history = output.get("repair_history") or []
    if not history:
        st.write("No repair attempts.")
    for item in history:
        if not isinstance(item, dict):
            st.write(item)
            continue
        label = f"Attempt {item.get('attempt')}: {item.get('status', '')}"
        with st.expander(label):
            st.write(item.get("summary", ""))
            if item.get("diff"):
                st.code(str(item["diff"]))


def _render_approval(output: dict) -> None:
    if output.get("status") != "awaiting_approval" or not resume_software_team_workflow:
        return
    st.subheader("Human approval")
    st.write("Approval applies only to this run id and artifact hash.")
    digest = str(output.get("artifact_hash") or "")
    run_id = str(output.get("run_id") or "")
    if st.button("Approve artifact version"):
        _resume(output, approved=True, digest=digest, run_id=run_id)
    if st.button("Reject artifact version"):
        _resume(output, approved=False, digest=digest, run_id=run_id)


def _resume(output: dict, *, approved: bool, digest: str, run_id: str) -> None:
    try:
        updated = resume_software_team_workflow(
            run_id,
            approved=approved,
            artifact_hash=digest,
            db_path=output.get("db_path"),
        )
    except Exception as exc:
        st.error(f"Approval could not be recorded: {exc}")
        return
    st.session_state.workflow_output = updated
    st.rerun()


def _render_output(output: dict) -> None:
    status = output.get("status", "")
    errors = output.get("errors") or []
    run_id = output.get("run_id", "")
    if run_id:
        st.write(f"**Run ID:** {run_id}")
    if status == "failed":
        st.error(f"Workflow status: {status}")
        _render_errors(errors)
    elif status == "approved":
        st.success(f"Workflow status: {status}")
    elif status:
        st.warning(f"Workflow status: {status}")
        _render_errors(errors)
    else:
        st.warning("Workflow status: unknown")

    _render_execution(output)
    _render_validation(output.get("validation_results") or [])
    _render_files(output)

    if not OUTPUT_SECTIONS:
        return

    tabs = st.tabs([label for label, _key in OUTPUT_SECTIONS])
    for tab, (label, key) in zip(tabs, OUTPUT_SECTIONS):
        with tab:
            content = output.get(key, "")
            if content:
                st.write(content)
            else:
                st.info(f"No {label} output returned by the workflow.")


with st.sidebar:
    st.header("Tech stack")
    st.write("- Ollama")
    st.write("- LangGraph")
    st.write("- LangChain")
    st.write("- Streamlit")
    st.write("- SQLite")
    st.write("---")
    st.subheader("Previous Outputs")

    if "previous_runs" not in st.session_state:
        st.session_state.previous_runs = []

    if st.session_state.previous_runs:
        for index, run in enumerate(reversed(st.session_state.previous_runs), start=1):
            run_number = len(st.session_state.previous_runs) - index + 1
            with st.expander(f"Run {run_number}: {run.get('idea', '')[:30]}"):
                st.write(f"**Project idea:** {run.get('idea', '')}")
                output = run.get("output", {})
                if isinstance(output, dict):
                    st.write(f"**Status:** {output.get('status', '')}")
                    if output.get("run_id"):
                        st.write(f"**Run ID:** {output.get('run_id')}")
                    _render_errors(output.get("errors") or [])
                    for label, key in OUTPUT_SECTIONS:
                        st.write(f"**{label}**")
                        st.write(output.get(key, ""))
                else:
                    st.write(output)
    else:
        st.write("No previous outputs yet.")

if "workflow_output" not in st.session_state:
    st.session_state.workflow_output = None

project_idea = st.text_area("Enter your project idea", height=200)

if st.button("Generate Project"):
    if import_error:
        st.error(f"Unable to import workflow engine: {import_error}")
    elif not run_software_team_workflow:
        st.error("Workflow function not available.")
    elif not project_idea.strip():
        st.warning("Please enter a project idea before generating.")
    else:
        try:
            with st.spinner("Generating the project workflow..."):
                workflow_output = run_software_team_workflow(project_idea.strip())

            if not isinstance(workflow_output, dict):
                st.error("Workflow returned an unexpected result format. Expected a dictionary.")
            else:
                if "previous_runs" not in st.session_state:
                    st.session_state.previous_runs = []
                st.session_state.previous_runs.append(
                    {"idea": project_idea.strip(), "output": workflow_output}
                )
                st.session_state.workflow_output = workflow_output
        except Exception as err:
            st.error(f"Error generating project: {err}")

if isinstance(st.session_state.workflow_output, dict):
    _render_output(st.session_state.workflow_output)
    _render_approval(st.session_state.workflow_output)
