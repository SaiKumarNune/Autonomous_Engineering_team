import json
import sys


def print_section(name, content):
    print(f"\n=== {name} ===")
    try:
        if isinstance(content, (dict, list)):
            print(json.dumps(content, indent=2))
        else:
            print(content)
    except Exception:
        print(str(content))


def main():
    try:
        idea = input("Enter a software project idea: ").strip()
        if not idea:
            print("No idea provided. Exiting.")
            sys.exit(1)

        try:
            # Import here to allow this script to run even if workflow module is missing
            from workflows.software_team_graph import run_software_team_workflow
        except Exception as e:
            print(f"Failed to import run_software_team_workflow: {e}")
            sys.exit(1)

        try:
            result = run_software_team_workflow(idea)
        except Exception as e:
            print(f"Error running workflow: {e}")
            sys.exit(1)

        if not isinstance(result, dict):
            print("Unexpected workflow result format. Expected a dict.")
            print_section("raw_result", result)
            sys.exit(1)

        sections = [
            "requirements",
            "architecture",
            "backend_code",
            "frontend_code",
            "qa_tests",
            "devops_files",
            "review",
        ]

        for s in sections:
            print_section(s, result.get(s, "<missing>"))

    except KeyboardInterrupt:
        print("\nInterrupted. Exiting.")
        sys.exit(1)


if __name__ == "__main__":
    main()
