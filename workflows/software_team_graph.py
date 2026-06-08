from typing import TypedDict
from langgraph.graph import StateGraph, START, END


class SoftwareTeamState(TypedDict):
    user_request: str
    requirements: str
    architecture: str
    backend_code: str
    frontend_code: str
    qa_tests: str
    devops_files: str
    review: str
    status: str
    errors: str


def product_manager_agent(state: SoftwareTeamState) -> SoftwareTeamState:
    """Product Manager analyzes requirements from user request."""
    requirements = f"Requirements based on: {state['user_request']}\n- Feature analysis\n- User stories\n- Acceptance criteria"
    state["requirements"] = requirements
    return state


def architect_agent(state: SoftwareTeamState) -> SoftwareTeamState:
    """Architect designs the system architecture."""
    architecture = f"Architecture design based on:\n{state['requirements']}\n- System design\n- Technology stack\n- Data flow diagram"
    state["architecture"] = architecture
    return state


def backend_engineer_agent(state: SoftwareTeamState) -> SoftwareTeamState:
    """Backend Engineer implements backend code."""
    backend_code = f"Backend implementation:\n{state['architecture']}\n- APIs\n- Database schema\n- Business logic"
    state["backend_code"] = backend_code
    return state


def frontend_engineer_agent(state: SoftwareTeamState) -> SoftwareTeamState:
    """Frontend Engineer implements frontend code."""
    frontend_code = f"Frontend implementation:\n{state['architecture']}\n- UI components\n- User interactions\n- State management"
    state["frontend_code"] = frontend_code
    return state


def qa_engineer_agent(state: SoftwareTeamState) -> SoftwareTeamState:
    """QA Engineer creates test suite."""
    qa_tests = f"QA tests for:\n- Backend: {state['backend_code'][:50]}...\n- Frontend: {state['frontend_code'][:50]}...\n- Test cases and coverage"
    state["qa_tests"] = qa_tests
    return state


def devops_engineer_agent(state: SoftwareTeamState) -> SoftwareTeamState:
    """DevOps Engineer creates deployment files."""
    devops_files = f"DevOps configuration:\n- Docker setup\n- CI/CD pipeline\n- Infrastructure as code"
    state["devops_files"] = devops_files
    return state


def reviewer_agent(state: SoftwareTeamState) -> SoftwareTeamState:
    """Reviewer performs final review and QA."""
    review = f"Final review completed:\n- Code quality check\n- Architecture validation\n- Deployment readiness"
    state["review"] = review
    state["status"] = "completed"
    state["errors"] = ""
    return state


def run_software_team_workflow(user_request: str) -> dict:
    """Run the software team workflow and return final state."""
    graph_builder = StateGraph(SoftwareTeamState)

    # Add nodes
    graph_builder.add_node("product_manager", product_manager_agent)
    graph_builder.add_node("architect", architect_agent)
    graph_builder.add_node("backend_engineer", backend_engineer_agent)
    graph_builder.add_node("frontend_engineer", frontend_engineer_agent)
    graph_builder.add_node("qa_engineer", qa_engineer_agent)
    graph_builder.add_node("devops_engineer", devops_engineer_agent)
    graph_builder.add_node("reviewer", reviewer_agent)

    # Add edges
    graph_builder.add_edge(START, "product_manager")
    graph_builder.add_edge("product_manager", "architect")
    graph_builder.add_edge("architect", "backend_engineer")
    graph_builder.add_edge("architect", "frontend_engineer")
    graph_builder.add_edge("backend_engineer", "qa_engineer")
    graph_builder.add_edge("frontend_engineer", "qa_engineer")
    graph_builder.add_edge("qa_engineer", "devops_engineer")
    graph_builder.add_edge("devops_engineer", "reviewer")
    graph_builder.add_edge("reviewer", END)

    # Compile and run
    graph = graph_builder.compile()

    initial_state = {
        "user_request": user_request,
        "requirements": "",
        "architecture": "",
        "backend_code": "",
        "frontend_code": "",
        "qa_tests": "",
        "devops_files": "",
        "review": "",
        "status": "in_progress",
        "errors": "",
    }

    final_state = graph.invoke(initial_state)
    return final_state
from typing import TypedDict

from langgraph.graph import StateGraph, END

from agents.product_manager_agent import run_product_manager_agent
from agents.architect_agent import run_architect_agent
from agents.backend_engineer_agent import run_backend_engineer_agent
from agents.frontend_engineer_agent import run_frontend_engineer_agent
from agents.qa_engineer_agent import run_qa_engineer_agent
from agents.devops_engineer_agent import run_devops_engineer_agent
from agents.reviewer_agent import run_reviewer_agent


class SoftwareTeamState(TypedDict):
    user_request: str
    requirements: str
    architecture: str
    backend_code: str
    frontend_code: str
    qa_tests: str
    devops_files: str
    review: str
    status: str
    errors: list[str]


def product_manager_node(state: SoftwareTeamState) -> dict:
    requirements = run_product_manager_agent(state["user_request"])
    return {"requirements": requirements}


def architect_node(state: SoftwareTeamState) -> dict:
    architecture = run_architect_agent(state["requirements"])
    return {"architecture": architecture}


def backend_node(state: SoftwareTeamState) -> dict:
    backend_code = run_backend_engineer_agent(
        state["architecture"],
        state["requirements"],
    )
    return {"backend_code": backend_code}


def frontend_node(state: SoftwareTeamState) -> dict:
    frontend_code = run_frontend_engineer_agent(
        state["architecture"],
        state["requirements"],
    )
    return {"frontend_code": frontend_code}


def qa_node(state: SoftwareTeamState) -> dict:
    generated_code = f"""
BACKEND CODE:
{state["backend_code"]}

FRONTEND CODE:
{state["frontend_code"]}
"""
    qa_tests = run_qa_engineer_agent(
        generated_code,
        state["requirements"],
    )
    return {"qa_tests": qa_tests}


def devops_node(state: SoftwareTeamState) -> dict:
    project_files_summary = f"""
REQUIREMENTS:
{state["requirements"]}

ARCHITECTURE:
{state["architecture"]}

BACKEND CODE:
{state["backend_code"]}

FRONTEND CODE:
{state["frontend_code"]}

QA TESTS:
{state["qa_tests"]}
"""
    devops_files = run_devops_engineer_agent(project_files_summary)
    return {"devops_files": devops_files}


def reviewer_node(state: SoftwareTeamState) -> dict:
    all_outputs = f"""
REQUIREMENTS:
{state["requirements"]}

ARCHITECTURE:
{state["architecture"]}

BACKEND CODE:
{state["backend_code"]}

FRONTEND CODE:
{state["frontend_code"]}

QA TESTS:
{state["qa_tests"]}

DEVOPS FILES:
{state["devops_files"]}
"""
    review = run_reviewer_agent(all_outputs)
    return {
        "review": review,
        "status": "completed",
    }


def build_software_team_graph():
    graph = StateGraph(SoftwareTeamState)

    graph.add_node("product_manager", product_manager_node)
    graph.add_node("architect", architect_node)
    graph.add_node("backend_engineer", backend_node)
    graph.add_node("frontend_engineer", frontend_node)
    graph.add_node("qa_engineer", qa_node)
    graph.add_node("devops_engineer", devops_node)
    graph.add_node("reviewer", reviewer_node)

    graph.set_entry_point("product_manager")

    graph.add_edge("product_manager", "architect")
    graph.add_edge("architect", "backend_engineer")
    graph.add_edge("backend_engineer", "frontend_engineer")
    graph.add_edge("frontend_engineer", "qa_engineer")
    graph.add_edge("qa_engineer", "devops_engineer")
    graph.add_edge("devops_engineer", "reviewer")
    graph.add_edge("reviewer", END)

    return graph.compile()


def run_software_team_workflow(user_request: str) -> dict:
    app = build_software_team_graph()

    initial_state: SoftwareTeamState = {
        "user_request": user_request,
        "requirements": "",
        "architecture": "",
        "backend_code": "",
        "frontend_code": "",
        "qa_tests": "",
        "devops_files": "",
        "review": "",
        "status": "running",
        "errors": [],
    }

    return app.invoke(initial_state)