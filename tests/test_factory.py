"""FR-015, FR-016, FR-017: one construction path, model from settings, workspace confinement."""

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from freyja_agent.agent import factory
from freyja_agent.agent.factory import build_agent
from freyja_agent.agent.prompts import DEFAULT_PROMPT
from tests.fakes import ScriptedChatModel


def _run_once(workspace: Path) -> ScriptedChatModel:
    model = ScriptedChatModel()
    agent = build_agent(
        model, checkpointer=InMemorySaver(), store=InMemoryStore(), workspace_dir=workspace
    )
    agent.invoke(
        {"messages": [HumanMessage("hello")]}, config={"configurable": {"thread_id": "t-1"}}
    )
    return model


def test_model_sees_gated_tool_and_task_but_never_execute(workspace: Path) -> None:
    model = _run_once(workspace)
    assert "send_notification" in model.bound_tool_names
    assert "task" in model.bound_tool_names
    assert "execute" not in model.bound_tool_names


def test_system_prompt_is_default_prompt_with_skills_listed(workspace: Path) -> None:
    model = _run_once(workspace)
    first = model.seen[0][0]
    assert isinstance(first, SystemMessage)
    text = first.text
    assert DEFAULT_PROMPT.strip() in text
    assert "case-notes" in text


def test_workspace_is_created_and_used_as_root(tmp_path: Path) -> None:
    root = tmp_path / "fresh"
    assert not root.exists()
    build_agent(
        ScriptedChatModel(), checkpointer=InMemorySaver(), store=InMemoryStore(), workspace_dir=root
    )
    assert root.is_dir()


def test_model_string_is_passed_through_unchanged(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    captured: dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "graph"

    monkeypatch.setattr(factory, "create_deep_agent", fake_create)
    result = build_agent(
        "anthropic:claude-sonnet-5-5",
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
        workspace_dir=workspace,
    )
    assert result == "graph"
    assert captured["model"] == "anthropic:claude-sonnet-5-5"
    assert captured["skills"] == ["/skills/"]
    assert captured["interrupt_on"]["send_notification"]["allowed_decisions"] == [
        "approve",
        "edit",
        "reject",
    ]
    assert Path(captured["backend"].cwd) == workspace.resolve()
    assert [tool.name for tool in captured["tools"]] == ["send_notification"]
