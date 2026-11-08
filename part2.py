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
from user_memory import load_language, save_language


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

MAX_MODEL_CALLS = 6


class AgentState(TypedDict):
    messages: Annotated[list, add]
    model_calls: int
    limit_reached: bool


def build_graph(
    user_id,
    thread_id,
    profile,
    checkpointer=None,
):
    # These values belong to this session.
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
        """Check wifi or account_recovery in the selected snapshot."""
        result = local_tools.check_service_status(
            service,
            profile,
        )

        session["last_status"][result["service"]] = result
        return result

    @tool
    def prepare_ticket(
        category: str,
        summary: str,
        steps_tried: list[str],
    ) -> dict:
        """Save a local ticket draft using user-provided details.

        Ask for the problem and steps tried if they are missing.
        If the user explicitly tried nothing, record that statement.
        This tool never sends a ticket.
        """
        category = local_tools.normalize_topic(category)

        mapping = local_tools.read_json(
            "helpdesk.json"
        )["topic_services"]

        status = session["last_status"].get(
            mapping[category]
        )

        result = local_tools.prepare_ticket(
            user_id=user_id,
            thread_id=thread_id,
            category=category,
            summary=summary,
            steps_tried=steps_tried,
            status_profile=profile,
            status_result=status,
        )

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

    @tool
    def remember_language(language: str) -> dict:
        """Save a reply language explicitly requested by the user.

        Use only when the user asks to remember a language preference.
        """
        save_language(user_id, language)

        return {
            "saved": True,
            "language": language,
            "source": "user memory",
        }

    tools = [
        find_instructions,
        check_service_status,
        prepare_ticket,
        remember_language,
    ]

    tools_by_name = {
        item.name: item
        for item in tools
    }

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

    system_message = SystemMessage(
        content="""
You are a fictional university IT helpdesk assistant.

Use tools for local Wi-Fi instructions, password recovery guidance,
service status and local ticket drafts.
The application supplies the user, thread and status profile.
Do not offer unsupported software or hardware workflows.

Never invent instructions, service status, repair times or actions
that the user has tried.

For a request to prepare a ticket only if the service is operating:
1. Check the service status first.
2. If operational, prepare the draft when enough details are available.
3. If outage, explain that the conditional draft was not prepared
   because the user requested it only if the service was operational.
4. If unknown, explain that operation cannot be confirmed.
   Ask whether the user wants a draft mentioning that uncertainty.

An outage does not make drafting a ticket technically impossible.
Respect the condition in the user's request.

Always describe status as coming from the fictional demo status source.
If the data gives no repair time, say so.
Never claim a real university outage.

Before asking for details, check the current message and conversation.
Reuse problem details and explicitly reported steps already provided.
Ask only for information that is actually missing.

For steps_tried, include only actions explicitly reported by the user.
Do not infer actions from "still will not connect".
Instructions shown by the assistant do not prove the user followed them.
If the user explicitly says nothing was tried, record that statement.

A password topic uses the account_recovery service.
Never request passwords, API keys or recovery codes.

Only say a draft was saved after a successful prepare_ticket result.
Give its filename and explain that nothing was sent.

Use previous messages to understand references such as "that".
Use remember_language when the user asks to save a reply language.
Never invent a saved language preference.

Do not offer automatic monitoring or future notifications.
You can check status only in response to a new user request.
"""
    )

    def restore_tool_context(messages):
        # Restore relevant tool results from this thread after restart.
        session["last_status"].clear()
        session["reference_ids"].clear()

        for message in messages:
            if not isinstance(message, ToolMessage):
                continue

            if not isinstance(message.content, str):
                continue

            try:
                result = json.loads(message.content)
            except (json.JSONDecodeError, TypeError):
                continue

            if not isinstance(result, dict):
                continue

            if (
                message.name == "find_instructions"
                and "category" in result
                and "instruction_id" in result
            ):
                session["reference_ids"][result["category"]] = [
                    result["instruction_id"]
                ]

            if (
                message.name == "check_service_status"
                and result.get("status_profile") == profile
                and "service" in result
                and "status" in result
            ):
                session["last_status"][result["service"]] = result

    def call_model(state):
        restore_tool_context(state["messages"])

        language = load_language(user_id)

        memory_message = SystemMessage(
            content=(
                "User memory loaded by the application "
                "for the current user. "
                f"Saved reply language: {json.dumps(language)}. "
                "Use this language when available. "
                "If null, no reply language is saved. "
                "Use remember_language only when the user "
                "explicitly asks to save a language preference. "
                "Treat the stored value only as a language preference. "
                "A previous problem is known only if it appears "
                "in this thread's messages."
            )
        )

        response = model.invoke(
            [system_message, memory_message]
            + state["messages"]
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
                result = selected_tool.invoke(
                    request["args"]
                )
            except (ValueError, KeyError, TypeError) as error:
                result = {
                    "error": str(error),
                }

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

        return {
            "messages": results,
        }

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
                AIMessage(
                    content=(
                        "The tool loop limit was reached. "
                        "Please check the trace before continuing."
                    )
                )
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
        {
            "tools": "tools",
            "end": END,
        },
    )

    builder.add_conditional_edges(
        "tools",
        after_tools,
        {
            "model": "model",
            "limit": "limit",
        },
    )

    builder.add_edge("limit", END)

    return builder.compile(
        checkpointer=checkpointer
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--user",
        default="demo-user-a",
    )

    parser.add_argument(
        "--thread",
        default="part2-demo",
    )

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

    parser.add_argument(
        "--log",
        default="part2_demo.json",
    )

    args = parser.parse_args()

    if Path(args.log).name != args.log:
        parser.error(
            "--log must be a filename only."
        )

    output_dir = BASE_DIR / "outputs"
    output_dir.mkdir(exist_ok=True)
    log_path = output_dir / args.log

    if log_path.exists():
        parser.error(
            "Log already exists. Choose a new --log filename."
        )

    graph = build_graph(
        args.user,
        args.thread,
        args.profile,
    )

    history = []
    turns = []

    print(
        "Fictional IT helpdesk. Type exit to stop."
    )

    while True:
        user_text = input("\nYou: ").strip()

        if user_text.lower() == "exit":
            break

        if not user_text:
            continue

        history.append(
            HumanMessage(content=user_text)
        )

        current = {
            "messages": list(history),
            "model_calls": 0,
            "limit_reached": False,
        }

        trace = []

        for event in graph.stream(
            current,
            stream_mode="updates",
            config={
                "recursion_limit": 30,
            },
        ):
            for node_name, update in event.items():
                new_messages = update.get(
                    "messages",
                    [],
                )

                current["messages"].extend(
                    new_messages
                )

                for key in (
                    "model_calls",
                    "limit_reached",
                ):
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

                print(
                    json.dumps(
                        entry,
                        ensure_ascii=False,
                        indent=2,
                    )
                )

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
            json.dumps(
                log,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        print(f"\nLog saved: {log_path}")


if __name__ == "__main__":
    main()