import json

from streamlit.testing.v1 import AppTest

from tools.workspace import create_run_workspace, write_text_atomic

FAILED_RUN_SCRIPT = """
import app

app._render_output(
    {
        "status": "failed",
        "errors": [{"agent": "Backend Engineer", "message": "model down"}],
        "requirements": "REQ-TEXT",
        "architecture": "",
        "backend_code": "",
        "frontend_code": "",
        "qa_tests": "",
        "devops_files": "",
        "review": "",
    }
)
"""


def test_streamlit_imports_workflow_and_rejects_empty_idea():
    app = AppTest.from_file("app.py", default_timeout=30)
    app.run()

    assert not app.exception
    assert app.title[0].value == "Autonomous Software Engineering Team"
    assert [error.value for error in app.error] == []

    app.button[0].click().run()

    assert not app.exception
    assert [warning.value for warning in app.warning] == [
        "Please enter a project idea before generating."
    ]
    assert [success.value for success in app.success] == []


def test_streamlit_failed_run_uses_state_keys_and_does_not_claim_success():
    app = AppTest.from_string(FAILED_RUN_SCRIPT, default_timeout=30)
    app.run()

    assert not app.exception
    assert [error.value for error in app.error] == ["Workflow status: failed"]
    assert [success.value for success in app.success] == []
    markdown = [item.value for item in app.markdown]
    assert "Backend Engineer: model down" in markdown
    assert "REQ-TEXT" in markdown
    assert [tab.label for tab in app.tabs] == [
        "Requirements",
        "Architecture",
        "Backend",
        "Frontend",
        "Tests",
        "DevOps",
        "Review",
    ]
    assert "No Requirements output returned by the workflow." not in [
        item.value for item in app.info
    ]


def test_streamlit_shows_run_files_validation_and_zip(tmp_path):
    workspace = create_run_workspace("runui0001", tmp_path)
    write_text_atomic(workspace, "backend/main.py", "print('ok')\n")
    payload = {
        "status": "completed",
        "run_id": "runui0001",
        "errors": [],
        "workspace_path": str(workspace),
        "requirements": "REQ-TEXT",
        "architecture": "ARCH",
        "backend_code": "BACK",
        "frontend_code": "",
        "qa_tests": "",
        "devops_files": "",
        "review": "",
        "artifact_manifest": [
            {
                "relative_path": "backend/main.py",
                "source_agent": "Backend Engineer",
                "language": "python",
                "size_bytes": 12,
                "sha256": "abc",
            }
        ],
        "validation_results": [
            {
                "agent": "Backend Engineer",
                "valid": True,
                "issues": [],
                "blocking": True,
                "limitation": "syntax only",
                "kind": "artifact_validation",
            }
        ],
    }
    script = (
        "import json\n"
        "import app\n"
        f"app._render_output(json.loads({json.dumps(json.dumps(payload))}))\n"
    )
    rendered = AppTest.from_string(script, default_timeout=30)
    rendered.run()

    assert not rendered.exception
    assert [item.value for item in rendered.success] == []
    markdown = [item.value for item in rendered.markdown]
    assert any("runui0001" in value for value in markdown)
    assert any("has not completed a sandbox test run" in value for value in markdown)
    assert any("Backend Engineer" in value and "valid" in value for value in markdown)
    assert any("print('ok')" in item.value for item in rendered.code)
    warnings = [item.value for item in rendered.warning]
    assert "Download is disabled until this artifact version is approved." in warnings
    assert rendered.get("download_button") == []


def test_streamlit_shows_sandbox_results_and_approval_controls():
    payload = {
        "status": "awaiting_approval",
        "lifecycle_status": "awaiting_approval",
        "run_id": "runui0002",
        "artifact_hash": "abc123",
        "tests_passed": True,
        "errors": [],
        "test_results": [
            {
                "kind": "passed",
                "command": ["pytest", "-q", "--tb=line", "--disable-warnings", "-p", "no:cacheprovider"],
                "exit_code": 0,
                "stdout": "1 passed",
                "stderr": "",
                "sandbox_enforced": True,
                "message": "Sandbox tests passed.",
                "diagnostics": [],
                "duration_ms": 4,
                "failure_signature": "",
            }
        ],
        "repair_history": [
            {
                "attempt": 1,
                "status": "applied",
                "summary": "Fixed the assertion.",
                "diff": "--- a/backend/main.py\n+++ b/backend/main.py\n",
            }
        ],
    }
    script = (
        "import json\n"
        "import app\n"
        f"output = json.loads({json.dumps(json.dumps(payload))})\n"
        "app._render_output(output)\n"
        "app._render_approval(output)\n"
    )
    rendered = AppTest.from_string(script, default_timeout=30)
    rendered.run()

    assert not rendered.exception
    markdown = [item.value for item in rendered.markdown]
    assert any("Sandbox tests passed." in value for value in markdown)
    assert any("abc123" in value for value in markdown)
    assert any("1 passed" in item.value for item in rendered.code)
    assert [button.label for button in rendered.button] == [
        "Approve artifact version",
        "Reject artifact version",
    ]
    assert rendered.get("download_button") == []


def test_streamlit_shows_metrics_without_inventing_tokens():
    payload = {
        "run_metrics": {
            "token_usage": {
                "reported": False,
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
            },
            "sandbox_duration_ms": 12,
            "stages": [{"agent_name": "reviewer", "duration_ms": 4, "metric_name": "stage"}],
        },
        "consistency": {"consistent": True, "issues": []},
        "dependency_inspection": {"unapproved": ["flask==1.0.0"]},
    }
    script = (
        "import json\n"
        "import app\n"
        f"app._render_metrics(json.loads({json.dumps(json.dumps(payload))}))\n"
    )
    rendered = AppTest.from_string(script, default_timeout=30)
    rendered.run()

    assert not rendered.exception
    markdown = [item.value for item in rendered.markdown]
    assert "Token usage was not reported for this run." in markdown
    assert any("reviewer: 4 ms" in value for value in markdown)
    assert any("Sandbox duration: 12 ms" in value for value in markdown)
    assert any("Unapproved dependency request: flask==1.0.0" in value for value in markdown)
    assert not any("cost" in value.lower() for value in markdown)
