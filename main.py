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
            from workflows.software_team_graph import (
                OUTPUT_SECTIONS,
                run_software_team_workflow,
            )
        except Exception as exc:
            print(f"Failed to import run_software_team_workflow: {exc}")
            sys.exit(1)

        try:
            result = run_software_team_workflow(idea)
        except Exception as exc:
            print(f"Error running workflow: {exc}")
            sys.exit(1)

        if not isinstance(result, dict):
            print("Unexpected workflow result format. Expected a dict.")
            print_section("raw_result", result)
            sys.exit(1)

        print_section("status", result.get("status", "<missing>"))
        print_section("lifecycle_status", result.get("lifecycle_status", "<missing>"))
        print_section("run_id", result.get("run_id", "<missing>"))
        print_section("artifact_hash", result.get("artifact_hash", "<missing>"))
        print_section("errors", result.get("errors", []))
        print_section("test_results", result.get("test_results", []))
        print_section("repair_history", result.get("repair_history", []))
        print_section("artifact_manifest", result.get("artifact_manifest", []))
        print_section("validation_results", result.get("validation_results", []))
        for _label, key in OUTPUT_SECTIONS:
            print_section(key, result.get(key, "<missing>"))

        paused = result.get("status") in {"awaiting_approval", "approved"} and not result.get("errors")
        if not paused:
            sys.exit(1)

    except KeyboardInterrupt:
        print("\nInterrupted. Exiting.")
        sys.exit(1)


if __name__ == "__main__":
    main()
