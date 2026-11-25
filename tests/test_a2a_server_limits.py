"""009: what bounds a caller (FR-009a, FR-020, FR-025 to FR-027).

Part 1, a named caller's files on the strict build (FR-009a, research R16). Two named callers,
``alpha`` and ``beta``, the local caller, and a console thread each work in their own place: a
named caller in ``<files dir>/<name>``, the other two in the shared workspace. A named caller
reads the skills and changes none, reaches no file outside its area, and has no shell. Runs
are started as a caller's message starts them; no caller's token is involved before request 5.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from freyja_agent.agent.caller_files import READ_ONLY, CallerFiles, NoFilesError
from freyja_agent.agent.factory import tool_node_tools, workspace_backend
from tests.conftest import ServerApp
from tests.fakes import ScriptedChatModel

DONE = AIMessage(content="Done.")
# Every tool a run of the strict build can call, with one agent configured. A tool added to
# the orchestrator is noticed here, so that one that reaches beyond a caller's area, such as
# one that reads the graph's store, is looked at before it ships (research R16).
STRICT_TOOLS = [
    "cancel_stub_task",
    "check_agent_tasks",
    "delete",
    "edit_file",
    "execute",
    "glob",
    "grep",
    "handoff_to_stub",
    "ls",
    "read_file",
    "send_notification",
    "task",
    "write_file",
]


def call(name: str, call_id: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": call_id, "name": name, "args": args}])


def calls(*found: tuple[str, str, dict[str, Any]]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": name, "args": args} for name, call_id, args in found],
    )


def results(model: ScriptedChatModel) -> dict[str, str]:
    """Every tool result the model was shown, by its call's id."""
    return {
        message.tool_call_id: str(message.content)
        for seen in model.seen
        for message in seen
        if isinstance(message, ToolMessage)
    }


@pytest.fixture
def files_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Where named callers' files are kept: beside the workspace, never inside it."""
    return tmp_path_factory.mktemp("caller-files")


@pytest.fixture
def make(
    make_a2a_server_app: Callable[..., ServerApp], files_dir: Path
) -> Callable[[ScriptedChatModel], ServerApp]:
    return lambda model: make_a2a_server_app(model, a2a_server_files_dir=str(files_dir))


def test_each_caller_s_writes_land_in_its_own_place(
    make: Callable[[ScriptedChatModel], ServerApp],
    files_dir: Path,
    workspace: Path,
    sse_events: Any,
) -> None:
    model = ScriptedChatModel(
        script=[
            call("write_file", "w1", file_path="/notes.txt", content="alpha's notes"),
            DONE,
            call("write_file", "w2", file_path="/notes.txt", content="beta's notes"),
            DONE,
            call("write_file", "w3", file_path="/local.txt", content="the local caller's"),
            DONE,
            call("write_file", "w4", file_path="/console.txt", content="the console's"),
            DONE,
        ]
    )
    made = make(model)
    with TestClient(made.app) as client:
        for caller in ("alpha", "beta", "local"):
            made.finish(client, made.accept(client, "Write it.", caller=caller))
        with client.stream("POST", "/threads/k-1/runs", json={"message": "Write."}) as answer:
            sse_events(answer.iter_lines())
    assert (files_dir / "alpha" / "notes.txt").read_text() == "alpha's notes"
    assert (files_dir / "beta" / "notes.txt").read_text() == "beta's notes"
    assert (workspace / "local.txt").read_text() == "the local caller's"
    assert (workspace / "console.txt").read_text() == "the console's"
    assert not (workspace / "notes.txt").exists()
    assert sorted(path.name for path in files_dir.iterdir()) == ["alpha", "beta"]


def test_a_named_caller_reaches_nothing_outside_its_area(
    make: Callable[[ScriptedChatModel], ServerApp], files_dir: Path, workspace: Path
) -> None:
    (files_dir / "alpha").mkdir()
    (files_dir / "alpha" / "notes.txt").write_text("alpha's notes")
    (files_dir / "beta").mkdir()
    (files_dir / "beta" / "secret.txt").write_text("beta's secret")
    (workspace / "console.txt").write_text("the console's secret")
    model = ScriptedChatModel(
        script=[
            calls(
                ("ls", "r1", {"path": "/"}),
                ("read_file", "r2", {"file_path": "/notes.txt"}),
                ("glob", "r3", {"pattern": "**/*.txt"}),
                ("grep", "r4", {"pattern": "secret"}),
                ("read_file", "r5", {"file_path": "/../beta/secret.txt"}),
                ("read_file", "r6", {"file_path": str(workspace / "console.txt")}),
                ("delete", "r8", {"file_path": "/../beta/secret.txt"}),
            ),
            DONE,
        ]
    )
    made = make(model)
    with TestClient(made.app) as client:
        made.finish(client, made.accept(client, "Look around.", caller="alpha"))
    seen = results(model)
    assert "notes.txt" in seen["r1"] and "secret" not in seen["r1"]
    assert "alpha's notes" in seen["r2"]
    assert "notes.txt" in seen["r3"] and "secret.txt" not in seen["r3"]
    everything = "\n".join(seen.values())
    assert "beta's secret" not in everything
    assert "the console's secret" not in everything
    assert (files_dir / "beta" / "secret.txt").exists()


def test_a_link_out_of_the_area_reads_nothing_and_fails_the_run(
    make: Callable[[ScriptedChatModel], ServerApp], files_dir: Path, workspace: Path
) -> None:
    """deepagents' backend raises for a path that leaves its root by a link, as it does on
    the shared workspace; the run fails and nothing is read (recorded in plan.md)."""
    (files_dir / "alpha").mkdir()
    (workspace / "console.txt").write_text("the console's secret")
    (files_dir / "alpha" / "link.txt").symlink_to(workspace / "console.txt")
    model = ScriptedChatModel(script=[call("read_file", "l1", file_path="/link.txt"), DONE])
    made = make(model)
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Read the link.", caller="alpha")
        events = made.finish(client, accepted)
        task = made.task(client, accepted.task.task_id)
    assert events[-1][0] == "error"
    assert "the console's secret" not in str(events)
    assert "the console's secret" not in "\n".join(results(model).values())
    assert (task.state, task.reason) == ("failed", "internal")


def test_the_skills_are_read_and_never_changed(
    make: Callable[[ScriptedChatModel], ServerApp], workspace: Path
) -> None:
    skill = workspace / "skills" / "case-notes" / "SKILL.md"
    before = skill.read_text()
    model = ScriptedChatModel(
        script=[
            calls(
                ("ls", "s1", {"path": "/skills/"}),
                ("read_file", "s2", {"file_path": "/skills/case-notes/SKILL.md"}),
                ("write_file", "s3", {"file_path": "/skills/new.md", "content": "mine"}),
                (
                    "edit_file",
                    "s4",
                    {
                        "file_path": "/skills/case-notes/SKILL.md",
                        "old_string": before.splitlines()[0],
                        "new_string": "changed",
                    },
                ),
            ),
            # Two changes of one path may not share a step: the delete is a step of its own.
            call("delete", "s5", file_path="/skills/case-notes/SKILL.md"),
            DONE,
        ]
    )
    made = make(model)
    with TestClient(made.app) as client:
        made.finish(client, made.accept(client, "Change a skill.", caller="alpha"))
    seen = results(model)
    assert "case-notes" in seen["s1"]
    assert before.splitlines()[0][:20] in seen["s2"]
    for refused in ("s3", "s4", "s5"):
        assert READ_ONLY in seen[refused], seen[refused]
    assert skill.read_text() == before
    assert not (workspace / "skills" / "new.md").exists()
    # The skills reach the model of a caller's thread through the route.
    assert "case-notes" in str(model.seen[0][0].content)


def test_the_subagent_of_a_named_caller_s_task_works_in_the_same_area(
    make: Callable[[ScriptedChatModel], ServerApp], files_dir: Path, workspace: Path
) -> None:
    delegate = call("task", "t1", description="Write the note.", subagent_type="general-purpose")
    model = ScriptedChatModel(
        script=[
            delegate,
            call("write_file", "w1", file_path="/sub.txt", content="by the subagent"),
            DONE,
            DONE,
        ]
    )
    made = make(model)
    with TestClient(made.app) as client:
        made.finish(client, made.accept(client, "Delegate.", caller="alpha"))
    assert (files_dir / "alpha" / "sub.txt").read_text() == "by the subagent"
    assert not (workspace / "sub.txt").exists()


def test_there_is_no_shell_on_any_thread(
    make: Callable[[ScriptedChatModel], ServerApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(
        script=[
            call("execute", "x1", command="cat /etc/hostname"),
            DONE,
            call("execute", "x2", command="cat /etc/hostname"),
            DONE,
            call("execute", "x3", command="cat /etc/hostname"),
            DONE,
        ]
    )
    made = make(model)
    with TestClient(made.app) as client:
        made.finish(client, made.accept(client, "Run it.", caller="alpha"))
        made.finish(client, made.accept(client, "Run it.", caller="local"))
        with client.stream("POST", "/threads/k-1/runs", json={"message": "Run."}) as answer:
            sse_events(answer.iter_lines())
    seen = results(model)
    for call_id in ("x1", "x2", "x3"):
        assert "Error" in seen[call_id] and "SandboxBackendProtocol" in seen[call_id]


def test_a_file_operation_without_its_caller_reads_and_writes_nothing(
    files_dir: Path, workspace: Path
) -> None:
    backend = CallerFiles(workspace_backend(workspace), files_dir, workspace / "skills")
    for operation in (
        lambda: backend.ls("/"),
        lambda: backend.write("/x.txt", "x"),
        lambda: backend.read("/x.txt"),
    ):
        with pytest.raises(NoFilesError):
            operation()
    assert not (files_dir / "x.txt").exists() and not (workspace / "x.txt").exists()


def test_the_strict_build_s_tools_are_a_known_list(
    make: Callable[[ScriptedChatModel], ServerApp],
) -> None:
    made = make(ScriptedChatModel())
    with TestClient(made.app):
        tools = sorted(tool_node_tools(made.runtime.strict_agent))
    assert tools == STRICT_TOOLS


@pytest.mark.parametrize("name", ["../beta", "Alpha", "a/b", "", 7])
def test_a_caller_s_name_that_is_no_directory_name_reaches_no_file(
    files_dir: Path, workspace: Path, name: Any
) -> None:
    from langchain_core.runnables import RunnableConfig
    from langgraph.config import get_config  # noqa: F401 - the config the backend reads

    backend = CallerFiles(workspace_backend(workspace), files_dir, workspace / "skills")
    config: RunnableConfig = {"configurable": {"freyja_caller_files": name}}

    def listing() -> Any:
        from langchain_core.runnables.config import var_child_runnable_config

        token = var_child_runnable_config.set(config)
        try:
            return backend.ls("/")
        finally:
            var_child_runnable_config.reset(token)

    if name == "":
        assert listing() is not None  # the local caller: the shared workspace
        return
    with pytest.raises(NoFilesError):
        listing()
