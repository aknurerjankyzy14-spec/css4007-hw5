import argparse
import json
import os
import sqlite3
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from part2 import build_graph
from user_memory import storage_dir, load_language


BASE_DIR = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", required=True)
    parser.add_argument("--thread", required=True)
    parser.add_argument(
        "--profile",
        default="normal",
        choices=[
            "normal",
            "wifi_outage",
            "recovery_outage",
            "status_unavailable",
        ],
    )
    parser.add_argument("--log", required=True)
    args = parser.parse_args()

    if Path(args.log).name != args.log:
        parser.error("--log must be a filename only.")

    # Combine user and thread IDs to keep their histories separate.
    checkpoint_id = json.dumps([
        args.user,
        args.thread,
    ])

    config = {
        "configurable": {
            "thread_id": checkpoint_id,
        },
        "recursion_limit": 30,
    }

    output_dir = BASE_DIR / "outputs"
    output_dir.mkdir(exist_ok=True)
    log_path = output_dir / args.log

    # Each launch gets a separate log; do not overwrite old evidence.
    if log_path.exists():
        parser.error(
            "Log already exists. Choose a new --log filename."
        )

    turns = []

    connection = sqlite3.connect(
        storage_dir() / "conversations.sqlite",
        check_same_thread=False,
    )

    try:
        checkpointer = SqliteSaver(connection)

        graph = build_graph(
            args.user,
            args.thread,
            args.profile,
            checkpointer=checkpointer,
        )

        print("Persistent helpdesk. Type exit to stop.")

        while True:
            text = input("\nYou: ").strip()

            if text.lower() == "exit":
                break

            if not text:
                continue

            previous = graph.get_state(config).values
            previous_messages = previous.get("messages", [])
            language_before = load_language(args.user)

            memory_read = {
                "conversation_source": "LangGraph SQLite checkpoint",
                "previous_message_count": len(previous_messages),
                "user_memory_source": "users.sqlite",
                "saved_language": language_before,
            }

            print("\n[Memory read]")
            print(json.dumps(
                memory_read,
                ensure_ascii=False,
                indent=2,
            ))

            trace = []

            # Send only the new message.
            # The checkpointer restores the previous messages.
            for event in graph.stream(
                {
                    "messages": [HumanMessage(content=text)],
                    "model_calls": 0,
                    "limit_reached": False,
                },
                config=config,
                stream_mode="updates",
            ):
                for node_name, update in event.items():
                    entry = {
                        "node": node_name,
                        "messages": [
                            message.model_dump(mode="json")
                            for message in update.get("messages", [])
                        ],
                        "state_updates": {
                            key: value
                            for key, value in update.items()
                            if key != "messages"
                        },
                    }

                    trace.append(entry)

                    print(json.dumps(
                        entry,
                        ensure_ascii=False,
                        indent=2,
                    ))

            final_state = graph.get_state(config).values
            reply = final_state["messages"][-1].content

            print(f"\nHelpdesk: {reply}")

            turns.append({
                "user_message": text,
                "memory_read": memory_read,
                "trace": trace,
                "language_after": load_language(args.user),
                "final_response": reply,
                "loop_limit_reached": final_state.get(
                    "limit_reached", False
                ),
            })

            log_path.write_text(
                json.dumps(
                    {
                        "provider": "OpenAI",
                        "model": os.getenv("MODEL_NAME"),
                        "user_id": args.user,
                        "thread_id": args.thread,
                        "status_profile": args.profile,
                        "turns": turns,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            print(f"\nLog saved: {log_path}")

    finally:
        connection.close()


if __name__ == "__main__":
    main()