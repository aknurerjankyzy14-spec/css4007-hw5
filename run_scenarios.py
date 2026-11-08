import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

def write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def worker():
    # Each worker is a separate Python process.
    import part2
    import user_memory

    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.sqlite import SqliteSaver

    payload = json.load(sys.stdin)
    storage = Path(payload["storage"])

    # Use isolated scenario storage instead of normal user memory.
    user_memory.storage_dir = lambda: storage

    connection = sqlite3.connect(
        storage / "conversations.sqlite",
        check_same_thread=False,
    )

    turns = []

    try:
        saver = SqliteSaver(connection)

        graph = part2.build_graph(
            payload["user_id"],
            payload["thread_id"],
            payload["profile"],
            checkpointer=saver,
        )

        config = {
            "configurable": {
                "thread_id": json.dumps([
                    payload["user_id"],
                    payload["thread_id"],
                ])
            },
            "recursion_limit": 30,
        }

        for text in payload["messages"]:
            before = graph.get_state(config).values

            memory_read = {
                "conversation_source": "LangGraph SQLite checkpoint",
                "previous_message_count": len(
                    before.get("messages", [])
                ),
                "user_memory_source": "users.sqlite",
                "saved_language": user_memory.load_language(
                    payload["user_id"]
                ),
            }

            trace = []

            for event in graph.stream(
                {
                    "messages": [HumanMessage(content=text)],
                    "model_calls": 0,
                    "limit_reached": False,
                },
                config=config,
                stream_mode="updates",
            ):
                for node, update in event.items():
                    trace.append({
                        "node": node,
                        "messages": [
                            message.model_dump(mode="json")
                            for message in update.get("messages", [])
                        ],
                        "state_updates": {
                            key: value
                            for key, value in update.items()
                            if key != "messages"
                        },
                    })

            after = graph.get_state(config).values

            turns.append({
                "user_message": text,
                "memory_read": memory_read,
                "trace": trace,
                "saved_language_after": user_memory.load_language(
                    payload["user_id"]
                ),
                "final_response": after["messages"][-1].content,
                "loop_limit_reached": after.get(
                    "limit_reached", False
                ),
            })

        print(json.dumps({
            "user_id": payload["user_id"],
            "thread_id": payload["thread_id"],
            "turns": turns,
        }, ensure_ascii=False))

    finally:
        connection.close()


def run_part1(scenario):
    import part1
    import part1_clarify

    graph = (
        part1_clarify.graph
        if scenario.get("graph_variant") == "with_clarification"
        else part1.graph
    )

    event = scenario["events"][0]

    state = {
        "message": event["message"],
        "category": "",
        "reply": "",
    }

    trace = []

    for update_event in graph.stream(
        state.copy(),
        stream_mode="updates",
    ):
        for node, update in update_event.items():
            state.update(update)
            trace.append({
                "node": node,
                "update": update,
                "state_after": state.copy(),
            })

    return [{
        "user_id": event["user_id"],
        "thread_id": event["thread_id"],
        "user_message": event["message"],
        "trace": trace,
        "final_response": state["reply"],
        "loop_limit_reached": False,
    }]


def group_events(events):
    groups = []
    current = None

    for event in events:
        if event["action"] == "restart_process":
            current = None
            continue

        if event["action"] != "message":
            raise ValueError("Unsupported scenario event.")

        identity = (
            event["user_id"],
            event["thread_id"],
        )

        if current is None or current["identity"] != identity:
            current = {
                "identity": identity,
                "messages": [],
            }
            groups.append(current)

        current["messages"].append(event["message"])

    return groups


def run_model_scenario(scenario):
    launches = []

    # Fresh databases before the scenario, preserved between its launches.
    with tempfile.TemporaryDirectory(
        prefix=f"helpdesk-{scenario['id']}-"
    ) as storage:
        for number, group in enumerate(
            group_events(scenario["events"]),
            start=1,
        ):
            user_id, thread_id = group["identity"]

            payload = {
                "storage": storage,
                "user_id": user_id,
                "thread_id": thread_id,
                "profile": scenario["status_profile"],
                "messages": group["messages"],
            }

            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker",
            ]

            result = subprocess.run(
                command,
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=BASE_DIR,
            )

            if result.returncode != 0:
                raise RuntimeError(
                    f"{scenario['id']}, launch {number} failed:\n"
                    + result.stderr
                )

            launch = json.loads(result.stdout)
            launch["launch_number"] = number
            launch["process_command"] = command
            launch["input_messages"] = group["messages"]

            launches.append(launch)

    return launches


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument(
        "--only",
        nargs="+",
        help="For example: --only S10 S11 S12 S13",
    )
    parser.add_argument(
        "--out",
        default="outputs/scenarios_run1",
    )
    args = parser.parse_args()

    if args.worker:
        worker()
        return

    scenarios = json.loads(
        (BASE_DIR / "data" / "scenarios.json").read_text(
            encoding="utf-8"
        )
    )["scenarios"]

    if args.only:
        known = {item["id"] for item in scenarios}
        unknown = set(args.only) - known

        if unknown:
            parser.error(f"Unknown scenarios: {sorted(unknown)}")

        scenarios = [
            item for item in scenarios
            if item["id"] in args.only
        ]

    output = BASE_DIR / args.out

    if output.exists():
        parser.error(
            "Output folder already exists. Use a new --out name."
        )

    output.mkdir(parents=True)

    for scenario in scenarios:
        scenario_id = scenario["id"]
        print(f"Running {scenario_id}...", flush=True)

        log = {
            "scenario_id": scenario_id,
            "title": scenario["title"],
            "status_profile": scenario["status_profile"],
            "reset_before": scenario["reset_before"],
            "memory_setup": (
                "Fresh temporary storage for each scenario; "
                "preserved between launches within the scenario."
            ),
            "supplied_events": scenario["events"],
        }

        try:
            if scenario["part"] == 1:
                log["runs"] = run_part1(scenario)
            else:
                log["launches"] = run_model_scenario(scenario)

            log["execution_completed"] = True

        except Exception as error:
            log["execution_completed"] = False
            log["error"] = str(error)

            write_json(output / f"{scenario_id}.json", log)
            print(f"Failed: {scenario_id}. See its log.")
            raise

        write_json(output / f"{scenario_id}.json", log)
        print(f"Saved {scenario_id}.json", flush=True)

    print(f"\nLogs saved in: {output}")
    print("Execution completed. Review behaviour before marking scenarios passed.")


if __name__ == "__main__":
    main()