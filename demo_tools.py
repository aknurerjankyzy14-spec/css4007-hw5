import json
from pathlib import Path

from helpdesk_tools import (
    find_instructions,
    check_service_status,
    prepare_ticket,
)


BASE_DIR = Path(__file__).resolve().parent


def main():
    calls = []

    def record(tool_name, arguments, result):
        entry = {
            "tool": tool_name,
            "arguments": arguments,
            "result": result,
        }
        calls.append(entry)
        print(json.dumps(entry, ensure_ascii=False, indent=2))

    instructions = find_instructions("wifi")

    record(
        "find_instructions",
        {"topic": "wifi"},
        instructions,
    )

    for profile in (
        "normal",
        "wifi_outage",
        "status_unavailable",
    ):
        result = check_service_status("wifi", profile)

        record(
            "check_service_status",
            {
                "service": "wifi",
                "status_profile": profile,
            },
            result,
        )

    # Password recovery uses account_recovery.
    recovery = check_service_status(
        "password",
        "recovery_outage",
    )

    record(
        "check_service_status",
        {
            "service": "password",
            "status_profile": "recovery_outage",
        },
        recovery,
    )

    status = check_service_status("wifi", "normal")

    arguments = {
        "user_id": "demo-user-a",
        "thread_id": "direct-tools-demo",
        "category": "wifi",
        "summary": "Campus-Demo still will not connect.",
        "steps_tried": [
            "Enabled Wi-Fi.",
            "Forgot the saved network.",
        ],
        "status_profile": "normal",
        "status_result": status,
    }

    ticket = prepare_ticket(**arguments)
    record("prepare_ticket", arguments, ticket)

    output_dir = BASE_DIR / "outputs"
    output_dir.mkdir(exist_ok=True)

    log_path = output_dir / "direct_tools_demo.json"
    log_path.write_text(
        json.dumps(
            {
                "mode": "Direct tool calls without a model",
                "calls": calls,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\nLog saved: {log_path}")
    print(f"Ticket saved: {ticket['filename']}")
    print("Nothing was sent.")


if __name__ == "__main__":
    main()