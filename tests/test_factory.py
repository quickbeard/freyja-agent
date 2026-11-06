"""FR-015, FR-016, FR-017: one construction path, model from settings, workspace confinement."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from freyja_agent.agent import factory
from freyja_agent.agent.catalog import (
    OWN_TOOLS,
    SEND_NOTIFICATION_DESCRIPTION,
    Classification,
    UnclassifiedToolError,
)
from freyja_agent.agent.factory import build_agent, tool_node_tools
from freyja_agent.agent.prompts import DEFAULT_PROMPT
from freyja_agent.config import McpServerConfig, Settings
from freyja_agent.delegates.mcp_servers import open_tool_servers
from tests.fakes import ScriptedChatModel
from tests.mcp_stub import make_stub


def _run_once(workspace: Path) -> ScriptedChatModel:
    model = ScriptedChatModel()
    orchestrator = build_agent(
        model,
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
        workspace_dir=workspace,
        max_steps=Settings().max_steps,
    )
    orchestrator.agent.invoke(
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


def test_model_is_told_which_thread_it_works_on(workspace: Path) -> None:
    model = _run_once(workspace)
    text = model.seen[0][0].text
    assert "thread `t-1`" in text
    assert "cases/t-1/" in text


def test_workspace_is_created_and_used_as_root(tmp_path: Path) -> None:
    root = tmp_path / "fresh"
    assert not root.exists()
    build_agent(
        ScriptedChatModel(),
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
        workspace_dir=root,
        max_steps=Settings().max_steps,
    )
    assert root.is_dir()


def test_model_string_is_passed_through_unchanged(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    captured: dict[str, Any] = {}

    class FakeGraph:
        """Stands in for the compiled graph; the factory reads its tool node for the catalog."""

        def __init__(self, tools: list[Any]) -> None:
            node = SimpleNamespace(bound=SimpleNamespace(tools_by_name={t.name: t for t in tools}))
            self.nodes = {"tools": node}

    def fake_create(**kwargs: Any) -> FakeGraph:
        captured.update(kwargs)
        return FakeGraph(kwargs["tools"])

    resolved = ScriptedChatModel()
    seen: dict[str, Any] = {}

    def fake_init(model: str, **kwargs: Any) -> ScriptedChatModel:
        seen["model"] = model
        seen["kwargs"] = kwargs
        return resolved

    monkeypatch.setattr(factory, "create_deep_agent", fake_create)
    monkeypatch.setattr(factory, "init_chat_model", fake_init)
    result = build_agent(
        "anthropic:claude-sonnet-5-5",
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
        workspace_dir=workspace,
        max_steps=7,
    )
    assert isinstance(result.agent, FakeGraph)
    assert [tool.name for tool in result.catalog] == ["send_notification"]
    assert seen["model"] == "anthropic:claude-sonnet-5-5"
    assert captured["model"] is resolved
    assert captured["skills"] == ["/skills/"]
    assert captured["interrupt_on"]["send_notification"]["allowed_decisions"] == [
        "approve",
        "edit",
        "reject",
    ]
    assert Path(captured["backend"].cwd) == workspace.resolve()
    assert [tool.name for tool in captured["tools"]] == ["send_notification"]
    assert [type(m).__name__ for m in captured["middleware"]] == ["ThreadContextMiddleware"]
    assert captured["subagents"][0]["name"] == "general-purpose"
    assert captured["subagents"][0]["runnable"].config["recursion_limit"] == 7


# Feature 002: every tool is classified, and the approval gate is generated from the catalog
# (FR-006, FR-007, FR-011; research R8).


@asynccontextmanager
async def stub_tools(read_only: tuple[str, ...] = ("lookup",)) -> AsyncIterator[list[Any]]:
    """The stub's tools, listed and converted the way startup does it."""
    configs = {"stub": McpServerConfig(command="stub", read_only=list(read_only))}
    async with open_tool_servers(
        configs,
        connect=lambda _name, _config, _on_error_output: make_stub(),
        call_timeout=5,
        startup_timeout=5,
    ) as servers:
        yield list(servers[0].tools)


class Recorded:
    """What the factory handed to Deep Agents, captured without replacing the real build."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.orchestrator_gate: dict[str, Any] = {}
        self.subagent_gate: dict[str, Any] = {}
        self.subagent_graph: Any = None
        real_create, real_sub = factory.create_deep_agent, factory.create_sub_agent

        def create(**kwargs: Any) -> Any:
            self.orchestrator_gate = kwargs["interrupt_on"]
            return real_create(**kwargs)

        def sub(spec: dict[str, Any]) -> Any:
            self.subagent_gate = spec["interrupt_on"]
            self.subagent_graph = real_sub(spec)
            return self.subagent_graph

        monkeypatch.setattr(factory, "create_deep_agent", create)
        monkeypatch.setattr(factory, "create_sub_agent", sub)


def build(workspace: Path, delegate_tools: list[Any] | None = None) -> factory.Orchestrator:
    return build_agent(
        ScriptedChatModel(),
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
        workspace_dir=workspace,
        max_steps=Settings().max_steps,
        delegate_tools=delegate_tools or [],
    )


def test_without_tool_servers_the_catalog_is_the_fixed_table(workspace: Path) -> None:
    catalog = build(workspace).catalog
    assert [tool.name for tool in catalog] == list(OWN_TOOLS)
    assert len(catalog) == 9
    assert {tool.name: tool.classification for tool in catalog} == OWN_TOOLS
    assert all(tool.server is None and tool.server_hints is None for tool in catalog)
    assert {tool.name: list(tool.allowed_decisions) for tool in catalog if tool.gated} == {
        "send_notification": ["approve", "edit", "reject"]
    }
    assert all(tool.allowed_decisions == () for tool in catalog if not tool.gated)
    notification = catalog.get("send_notification")
    assert notification is not None
    assert set(notification.input_schema["properties"]) == {"channel", "message"}


async def test_every_compiled_tool_is_classified(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded = Recorded(monkeypatch)
    async with stub_tools() as tools:
        orchestrator = build(workspace, tools)
    compiled = set(tool_node_tools(orchestrator.agent)) | set(
        tool_node_tools(recorded.subagent_graph)
    )
    classified = {tool.name for tool in orchestrator.catalog}
    assert compiled - classified == {"execute"}
    assert {tool.name for tool in tools} <= set(tool_node_tools(recorded.subagent_graph))
    assert [tool.name for tool in orchestrator.catalog][:9] == list(OWN_TOOLS)
    assert [tool.name for tool in orchestrator.catalog][9:] == [tool.name for tool in tools]


def test_an_own_tool_missing_from_the_table_stops_the_build(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(OWN_TOOLS, "glob")
    with pytest.raises(UnclassifiedToolError, match="'glob'") as refused:
        build(workspace)
    assert refused.value.tool == "glob"


async def test_the_gate_is_exactly_the_gated_tools_on_both_agents(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded = Recorded(monkeypatch)
    async with stub_tools(read_only=("lookup", "stats")) as tools:
        catalog = build(workspace, tools).catalog
    gated = {tool.name for tool in catalog.gated()}
    assert set(recorded.orchestrator_gate) == gated
    assert set(recorded.subagent_gate) == gated
    assert recorded.orchestrator_gate == recorded.subagent_gate
    # Only what the operator listed is exempt; everything else from the server is gated.
    assert gated == {"send_notification"} | {tool.name for tool in tools} - {"lookup", "stats"}
    lookup = catalog.get("lookup")
    assert lookup is not None and lookup.classification is Classification.READ_ONLY
    assert lookup.server == "stub" and lookup.allowed_decisions == ()


async def test_a_server_label_does_not_classify(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded = Recorded(monkeypatch)
    async with stub_tools(read_only=()) as tools:
        catalog = build(workspace, tools).catalog
    for name in ("lookup", "record_note"):
        entry = catalog.get(name)
        assert entry is not None
        # The server calls both read-only; the operator listed neither.
        assert entry.server_hints is not None and entry.server_hints["read_only"] is True
        assert entry.classification is Classification.GATED
        assert name in recorded.orchestrator_gate and name in recorded.subagent_gate
    ping = catalog.get("ping")
    assert ping is not None and ping.server_hints is None


async def test_gate_entries_describe_the_tool_and_its_decisions(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded = Recorded(monkeypatch)
    async with stub_tools() as tools:
        build(workspace, tools)
    gate = recorded.orchestrator_gate
    assert gate["record_note"] == {
        "allowed_decisions": ["approve", "edit", "reject"],
        "description": "stub: Record a note on the server.",
    }
    # A tool that names no arguments has nothing an edit could change.
    assert gate["ping"]["allowed_decisions"] == ["approve", "reject"]
    assert gate["ping"]["description"].startswith("stub: Ask the server whether it is alive.")
    assert gate["send_notification"] == {
        "allowed_decisions": ["approve", "edit", "reject"],
        "description": SEND_NOTIFICATION_DESCRIPTION,
    }
