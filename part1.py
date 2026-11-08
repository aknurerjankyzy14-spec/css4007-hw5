import json
from pathlib import Path
from typing import TypedDict

from langgraph.graph import StateGraph, START, END


# Пути считаем от расположения этого Python-файла.
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"

with (BASE_DIR / "data" / "helpdesk.json").open(
    encoding="utf-8"
) as file:
    helpdesk = json.load(file)


# Состояние, которое передаётся между узлами.
class HelpdeskState(TypedDict):
    message: str
    category: str
    reply: str


# Узел читает сообщение и определяет категорию.
def read_request(state: HelpdeskState):
    message = state["message"].lower()

    if "wifi" in message or "wi-fi" in message:
        category = "wifi"
    elif "password" in message:
        category = "password"
    else:
        category = "other"

    return {"category": category}


# Условное ребро выбирает следующий узел.
def choose_next_node(state: HelpdeskState):
    routes = {
        "wifi": "wifi_help",
        "password": "password_help",
        "other": "other_help",
    }
    return routes[state["category"]]


def format_instructions(topic: str):
    guide = helpdesk["instructions"][topic]

    steps = "\n".join(
        f"{number}. {step}"
        for number, step in enumerate(guide["steps"], start=1)
    )

    return (
        f'{guide["title"]}\n'
        f'Instruction ID: {guide["instruction_id"]}\n'
        f"{steps}"
    )


def wifi_help(state: HelpdeskState):
    return {"reply": format_instructions("wifi")}


def password_help(state: HelpdeskState):
    return {"reply": format_instructions("password")}


def other_help(state: HelpdeskState):
    return {
        "reply": "Is your problem about Wi-Fi or a password?"
    }


# Создаём граф.
builder = StateGraph(HelpdeskState)

builder.add_node("read_request", read_request)
builder.add_node("wifi_help", wifi_help)
builder.add_node("password_help", password_help)
builder.add_node("other_help", other_help)

builder.add_edge(START, "read_request")

builder.add_conditional_edges(
    "read_request",
    choose_next_node,
    {
        "wifi_help": "wifi_help",
        "password_help": "password_help",
        "other_help": "other_help",
    },
)

builder.add_edge("wifi_help", END)
builder.add_edge("password_help", END)
builder.add_edge("other_help", END)

graph = builder.compile()


def run_example(scenario_id: str, message: str):
    initial_state: HelpdeskState = {
        "message": message,
        "category": "",
        "reply": "",
    }

    current_state = initial_state.copy()
    trace = []

    print(f"\n{'=' * 60}")
    print(f"{scenario_id}: {message}")

    # updates показывает обновление от каждого выполненного узла.
    for event in graph.stream(
        initial_state,
        stream_mode="updates",
    ):
        for node_name, update in event.items():
            current_state.update(update)

            trace.append({
                "node": node_name,
                "update": update,
                "state_after": current_state.copy(),
            })

            print(f"\nNode: {node_name}")
            print(
                json.dumps(
                    current_state,
                    ensure_ascii=False,
                    indent=2,
                )
            )

    route = ["START"] + [
        item["node"] for item in trace
    ] + ["END"]

    log = {
        "scenario_id": scenario_id,
        "mode": "Part 1: no model",
        "service_status_profile": "not used",
        "user_id": "demo-user-a",
        "thread_id": f"part1-{scenario_id.lower()}",
        "initial_state": initial_state,
        "route": route,
        "trace": trace,
        "sources": (
            ["data/helpdesk.json"]
            if current_state["category"] in ("wifi", "password")
            else []
        ),
        "final_response": current_state["reply"],
        "ticket_filename": None,
        "loop_limit_reached": False,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OUTPUT_DIR / f"{scenario_id}_part1.json"

    log_path.write_text(
        json.dumps(log, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\nRoute:", " → ".join(route))
    print("\nReply:")
    print(current_state["reply"])
    print(f"\nLog saved: {log_path}")


if __name__ == "__main__":
    examples = [
        ("S01", "I cannot connect to Wi-Fi."),
        ("S02", "I forgot my PASSWORD."),
        ("S03", "My laptop screen is broken."),
        ("S04", "My Wi-Fi password does not work."),
    ]

    for scenario_id, message in examples:
        run_example(scenario_id, message)