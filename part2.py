import argparse
import json
import os
from operator import add
from pathlib import Path
from typing import Annotated, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END

import helpdesk_tools as local_tools


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

MAX_MODEL_CALLS = 6


class AgentState(TypedDict):
    messages: Annotated[list, add]
    model_calls: int
    limit_reached: bool


def build_graph(user_id, thread_id, profile):

    # Эти значения задаёт программа, а не модель.
    session = {
        "last_status": {},
        "reference_ids": {},
    }

    @tool
    def find_instructions(topic: str) -> dict:
        """Get local instructions for wifi or password."""
        result = local_tools.find_instructions(topic)
        session["reference_ids"][result["category"]] = [
            result["instruction_id"]
        ]
        return result

    @tool
    def check_service_status(service: str) -> dict:
        """Check wifi or account_recovery using the session profile."""
        result = local_tools.check_service_status(service, profile)
        session["last_status"][result["service"]] = result
        return result

    @tool
    def prepare_ticket(
        category: str,
        summary: str,
        steps_tried: list[str],
    ) -> dict:
        """Save a local ticket draft using user-provided details.

        Ask the user first if the problem or steps tried are missing.
        If the user explicitly tried nothing, record that statement.
        This tool never sends a ticket.
        """
        category = local_tools.normalize_topic(category)

        mapping = local_tools.read_json(
            "helpdesk.json"
        )["topic_services"]

        status = session["last_status"].get(mapping[category])

        result = local_tools.prepare_ticket(
            user_id=user_id,
            thread_id=thread_id,
            category=category,
            summary=summary,
            steps_tried=steps_tried,
            status_profile=profile,
            status_result=status,
        )

        # Include only references actually retrieved this session.
        result["ticket"]["reference_ids"] = (
            session["reference_ids"].get(category, [])
        )

        ticket_path = BASE_DIR / result["filename"]
        ticket_path.write_text(
            json.dumps(
                result["ticket"],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        return result

    tools = [
        find_instructions,
        check_service_status,
        prepare_ticket,
    ]
    tools_by_name = {item.name: item for item in tools}

    model_name = os.getenv("MODEL_NAME")
    api_key = os.getenv("OPENAI_API_KEY")

    if not model_name or not api_key:
        raise ValueError(
            "Set MODEL_NAME and OPENAI_API_KEY in .env."
        )

    model = ChatOpenAI(
        model=model_name,
        temperature=0,
        max_retries=1,
        timeout=60,
    ).bind_tools(tools)

    system_message = SystemMessage(content="""
You are a fictional university IT helpdesk assistant.
Use tools for local instructions, service status and ticket drafts.
The application supplies the user, thread and status profile.

Never invent instructions, status, repair times or troubleshooting
steps that the user has tried.

For a request to prepare a ticket only if the service is operating:
first check the service status.
If operational, prepare the draft if enough details are available.
If outage, explain the outage and do not prepare that conditional draft.
If unknown, explain that status cannot be confirmed and ask whether
the user wants a draft mentioning this uncertainty.

For missing problem details or missing steps tried, ask the user.
If the user explicitly says nothing was tried, record that.
Use previous messages to understand references such as "that".

A password topic uses the account_recovery service.
Never request passwords, API keys or recovery codes.
All status information is fictional.
Only say a draft was saved after a successful tool result.
Give its filename and say that nothing was sent.
For steps_tried, include only actions explicitly reported by the user.
Do not infer attempted steps from statements such as "still will not connect".
Instructions shown by the assistant are not evidence that the user followed them.
Always describe service status as coming from the fictional demo status source.
When an outage prevents a conditional ticket request, explain:
"I have not prepared the draft because you requested it only if the service was operational."
Do not claim that an outage makes drafting a ticket impossible.
If the fixture gives no repair time, say that the supplied data gives no repair time.
Do not offer automatic monitoring or future notifications.
You can check status only when the user sends a new request.
Before asking for missing details, check the current message and conversation history.
Reuse problem details and explicitly reported steps already provided.
Ask only for information that is actually missing.
""")

    def call_model(state):
        response = model.invoke(
            [system_message] + state["messages"]
        )

        return {
            "messages": [response],
            "model_calls": state["model_calls"] + 1,
        }

    def run_tools(state):
        results = []

        for request in state["messages"][-1].tool_calls:
            name = request["name"]

            try:
                selected_tool = tools_by_name[name]
                result = selected_tool.invoke(request["args"])
            except (ValueError, KeyError, TypeError) as error:
                result = {"error": str(error)}

            results.append(
                ToolMessage(
                    content=json.dumps(
                        result,
                        ensure_ascii=False,
                    ),
                    tool_call_id=request["id"],
                    name=name,
                )
            )

        return {"messages": results}

    def after_model(state):
        if state["messages"][-1].tool_calls:
            return "tools"
        return "end"

    def after_tools(state):
        if state["model_calls"] >= MAX_MODEL_CALLS:
            return "limit"
        return "model"

    def stop_at_limit(state):
        return {
            "limit_reached": True,
            "messages": [
                AIMessage(content=(
                    "The tool loop limit was reached. "
                    "Please check the trace before continuing."
                ))
            ],
        }

    builder = StateGraph(AgentState)
    builder.add_node("model", call_model)
    builder.add_node("tools", run_tools)
    builder.add_node("limit", stop_at_limit)

    builder.add_edge(START, "model")

    builder.add_conditional_edges(
        "model",
        after_model,
        {"tools": "tools", "end": END},
    )

    builder.add_conditional_edges(
        "tools",
        after_tools,
        {"model": "model", "limit": "limit"},
    )

    builder.add_edge("limit", END)

    return builder.compile()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="demo-user-a")
    parser.add_argument("--thread", default="part2-demo")
    parser.add_argument(
        "--profile",
        choices=[
            "normal",
            "wifi_outage",
            "recovery_outage",
            "status_unavailable",
        ],
        default="normal",
    )
    parser.add_argument("--log", default="part2_demo.json")
    args = parser.parse_args()

    # Keep the log directly inside outputs.
    if Path(args.log).name != args.log:
        parser.error("--log must be a filename only.")

    graph = build_graph(
        args.user,
        args.thread,
        args.profile,
    )

    history = []
    turns = []

    output_dir = BASE_DIR / "outputs"
    output_dir.mkdir(exist_ok=True)
    log_path = output_dir / args.log

    print("Fictional IT helpdesk. Type exit to stop.")

    while True:
        user_text = input("\nYou: ").strip()

        if user_text.lower() == "exit":
            break

        if not user_text:
            continue

        history.append(HumanMessage(content=user_text))

        current = {
            "messages": list(history),
            "model_calls": 0,
            "limit_reached": False,
        }

        trace = []

        for event in graph.stream(
            current,
            stream_mode="updates",
            config={"recursion_limit": 30},
        ):
            for node_name, update in event.items():
                new_messages = update.get("messages", [])
                current["messages"].extend(new_messages)

                for key in ("model_calls", "limit_reached"):
                    if key in update:
                        current[key] = update[key]

                entry = {
                    "node": node_name,
                    "messages": [
                        message.model_dump(mode="json")
                        for message in new_messages
                    ],
                    "model_calls": current["model_calls"],
                    "limit_reached": current["limit_reached"],
                }

                trace.append(entry)

                print(f"\n[Node: {node_name}]")
                print(json.dumps(
                    entry,
                    ensure_ascii=False,
                    indent=2,
                ))

        history = current["messages"]
        final_reply = history[-1].content

        print(f"\nHelpdesk: {final_reply}")

        turns.append({
            "user_message": user_text,
            "trace": trace,
            "final_response": final_reply,
            "loop_limit_reached": current["limit_reached"],
        })

        log = {
            "mode": "Real model demonstration",
            "provider": "OpenAI",
            "model": os.getenv("MODEL_NAME"),
            "user_id": args.user,
            "thread_id": args.thread,
            "status_profile": args.profile,
            "turns": turns,
        }

        log_path.write_text(
            json.dumps(log, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(f"\nLog saved: {log_path}")


if __name__ == "__main__":
    main()