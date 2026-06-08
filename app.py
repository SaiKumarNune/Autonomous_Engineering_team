import streamlit as st

try:
    from workflows import run_software_team_workflow
    import_error = None
except Exception as e:
    run_software_team_workflow = None
    import_error = e

st.set_page_config(page_title="Autonomous Software Engineering Team", layout="wide")
st.title("Autonomous Software Engineering Team")

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
            with st.expander(f"Run {len(st.session_state.previous_runs) - index + 1}: {run.get('idea', '')[:30]}"):
                st.write(f"**Project idea:** {run.get('idea', '')}")
                output = run.get("output", {})
                for section, text in output.items():
                    st.write(f"**{section}**")
                    st.write(text)
    else:
        st.write("No previous outputs yet.")

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
                workflow_output = {"Requirements": str(workflow_output)}

            if "previous_runs" not in st.session_state:
                st.session_state.previous_runs = []

            st.session_state.previous_runs.append({"idea": project_idea.strip(), "output": workflow_output})
            st.success("Project generated successfully.")

            tab_labels = ["Requirements", "Architecture", "Backend", "Frontend", "Tests", "DevOps", "Review"]
            tabs = st.tabs(tab_labels)
            for tab, label in zip(tabs, tab_labels):
                with tab:
                    content = workflow_output.get(label, "")
                    if content:
                        st.write(content)
                    else:
                        st.info(f"No {label} output returned by the workflow.")

        except Exception as err:
            st.error(f"Error generating project: {err}")
