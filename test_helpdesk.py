import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import helpdesk_tools
import part1
import part1_clarify
import part2
import user_memory

from langchain_core.messages import AIMessage, HumanMessage


@pytest.mark.parametrize(
    "message, expected",
    [
        ("I cannot connect to Wi-Fi.", "wifi"),
        ("I forgot my PASSWORD.", "password"),
        ("My laptop screen is broken.", "other"),
        ("My Wi-Fi password does not work.", "wifi"),
    ],
)
def test_original_routing(message, expected):
    result = part1.graph.invoke({
        "message": message,
        "category": "",
        "reply": "",
    })

    assert result["category"] == expected
    assert result["message"] == message
    assert result["reply"]


def test_clarification_changes_path():
    events = list(part1_clarify.graph.stream(
        {
            "message": "My Wi-Fi password does not work.",
            "category": "",
            "reply": "",
        },
        stream_mode="updates",
    ))

    nodes = [
        node
        for event in events
        for node in event
    ]

    assert nodes == ["read_request", "clarify_problem"]
    assert events[0]["read_request"]["category"] == "both"


def test_instruction_lookup():
    result = helpdesk_tools.find_instructions("WI-FI")

    assert result["instruction_id"] == "GUIDE-WIFI"
    assert result["steps"]
    assert result["source"] == "data/helpdesk.json"


@pytest.mark.parametrize(
    "profile, expected",
    [
        ("normal", "operational"),
        ("wifi_outage", "outage"),
        ("status_unavailable", "unknown"),
    ],
)
def test_wifi_status_profiles(profile, expected):
    result = helpdesk_tools.check_service_status(
        "wifi", profile
    )

    assert result["status"] == expected
    assert result["status_profile"] == profile


def test_password_service_mapping():
    result = helpdesk_tools.check_service_status(
        "password", "recovery_outage"
    )

    assert result["service"] == "account_recovery"
    assert result["status"] == "outage"


def test_ticket_saved_locally(tmp_path, monkeypatch):
    # Keep the real reference data, but write drafts to a temp folder.
    monkeypatch.setattr(helpdesk_tools, "BASE_DIR", tmp_path)

    result = helpdesk_tools.prepare_ticket(
        user_id="demo-user-a",
        thread_id="test-ticket",
        category="wifi",
        summary="Cannot connect.",
        steps_tried=["Enabled Wi-Fi."],
        status_profile="normal",
    )

    filename = tmp_path / result["filename"]
    saved = json.loads(filename.read_text(encoding="utf-8"))

    assert saved["status"] == "draft"
    assert saved["steps_tried"] == ["Enabled Wi-Fi."]
    assert saved["service_status"] == "not_checked"
    assert saved["user_id"] == "demo-user-a"
    assert saved["thread_id"] == "test-ticket"
    assert result["sent"] is False


def test_ticket_requires_details(tmp_path, monkeypatch):
    monkeypatch.setattr(helpdesk_tools, "BASE_DIR", tmp_path)

    with pytest.raises(ValueError):
        helpdesk_tools.prepare_ticket(
            user_id="demo-user-a",
            thread_id="test-missing",
            category="wifi",
            summary="",
            steps_tried=[],
            status_profile="normal",
        )

    assert not (tmp_path / "outputs").exists()


class FakeModel:
    """Test-only model: no network requests or real API key."""

    def __init__(self, **kwargs):
        pass

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(content="Test response.")


def test_graph_finishes_without_tool_calls(monkeypatch):
    monkeypatch.setattr(part2, "ChatOpenAI", FakeModel)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("MODEL_NAME", "fake-test-model")
    monkeypatch.setattr(
        part2, "load_language", lambda user_id: None
    )

    graph = part2.build_graph(
        "demo-user-a", "fake-test", "normal"
    )

    result = graph.invoke({
        "messages": [HumanMessage(content="Hello.")],
        "model_calls": 0,
        "limit_reached": False,
    })

    assert result["messages"][-1].content == "Test response."
    assert result["model_calls"] == 1
    assert result["limit_reached"] is False


class AlwaysToolsModel(FakeModel):
    """Test-only model that keeps asking for instructions."""

    def invoke(self, messages):
        return AIMessage(
            content="",
            tool_calls=[{
                "name": "find_instructions",
                "args": {"topic": "wifi"},
                "id": f"test-call-{len(messages)}",
                "type": "tool_call",
            }],
        )


def test_tool_loop_stops_at_limit(monkeypatch):
    monkeypatch.setattr(
        part2, "ChatOpenAI", AlwaysToolsModel
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("MODEL_NAME", "fake-test-model")
    monkeypatch.setattr(
        part2, "load_language", lambda user_id: None
    )

    graph = part2.build_graph(
        "demo-user-a", "limit-test", "normal"
    )

    result = graph.invoke(
        {
            "messages": [HumanMessage(content="Wi-Fi help.")],
            "model_calls": 0,
            "limit_reached": False,
        },
        config={"recursion_limit": 30},
    )

    assert result["limit_reached"] is True
    assert result["model_calls"] == part2.MAX_MODEL_CALLS


# This script runs in two genuinely separate Python processes.
RESTART_SCRIPT = r'''
import json
import sqlite3
import sys
from pathlib import Path

import part2
import user_memory

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver

folder = Path(sys.argv[1])
mode = sys.argv[2]

user_memory.storage_dir = lambda: folder
part2.load_language = user_memory.load_language

class FakeModel:
    def __init__(self, **kwargs):
        pass

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(content="Fake model response.")

part2.ChatOpenAI = FakeModel

connection = sqlite3.connect(
    folder / "conversations.sqlite",
    check_same_thread=False,
)

try:
    graph = part2.build_graph(
        "demo-user-a",
        "restart-test",
        "normal",
        checkpointer=SqliteSaver(connection),
    )

    def config(user, thread):
        return {
            "configurable": {
                "thread_id": json.dumps([user, thread])
            }
        }

    original = config("demo-user-a", "restart-test")

    if mode == "save":
        user_memory.save_language("demo-user-a", "Kazakh")

        graph.invoke(
            {
                "messages": [
                    HumanMessage(
                        content="Wi-Fi fails. I tried reconnecting."
                    )
                ],
                "model_calls": 0,
                "limit_reached": False,
            },
            config=original,
        )

    else:
        state = graph.get_state(original).values
        assert state["messages"][0].content == (
            "Wi-Fi fails. I tried reconnecting."
        )

        assert user_memory.load_language("demo-user-a") == "Kazakh"
        assert user_memory.load_language("demo-user-b") is None

        new_thread = config("demo-user-a", "new-thread")
        other_user = config("demo-user-b", "restart-test")

        assert not graph.get_state(new_thread).values.get("messages")
        assert not graph.get_state(other_user).values.get("messages")

        print("Restart and isolation checks passed.")

finally:
    connection.close()
'''


def test_restart_and_memory_isolation(tmp_path):
    environment = os.environ.copy()

    project_dir = Path(__file__).resolve().parent

    for mode in ("save", "read"):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                RESTART_SCRIPT,
                str(tmp_path),
                mode,
            ],
            cwd=project_dir,
            env=environment,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, (
            result.stdout + result.stderr
        )