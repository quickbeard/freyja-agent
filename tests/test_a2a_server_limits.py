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
from tests.a2a_requests import at_rest, ended, rpc, sending
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


# Part 2: a message's form and the limits (FR-008, FR-025, FR-026a, SC-012), each refused
# before the model is asked and before a thread, a conversation, or a task is made.


def refused_before_the_model(
    client: TestClient, made: ServerApp, model: ScriptedChatModel, params: dict[str, Any]
) -> dict[str, Any]:
    before = model.calls
    answer = rpc(client, "SendMessage", params)
    assert answer.status_code == 200, answer.text
    assert "error" in answer.json(), answer.json()
    assert model.calls == before
    return answer.json()["error"]


def nothing_made(client: TestClient, made: ServerApp) -> None:
    assert client.portal.call(made.received.unreleased, 100) == []
    assert client.get("/threads").json()["total"] == 0


@pytest.mark.parametrize(
    ("message", "sentence"),
    [
        ({"parts": []}, "The message holds no text."),
        ({"parts": [{"text": ""}]}, "The message holds no text."),
        ({"parts": [{"text": " \n\t"}, {"text": "  "}]}, "The message holds no text."),
        ({"parts": [{"text": "x" * 32_001}]}, "The message is longer than 32,000 characters."),
        ({"parts": [{"text": "x" * 16_000}, {"text": "y" * 16_000}]}, None),
        (
            {"parts": [{"text": "hi"}], "taskId": "t-1"},
            "A message names no task: a task takes one message. Continue a conversation by "
            "its contextId.",
        ),
        (
            {"parts": [{"text": "hi"}], "contextId": "c" * 129},
            "The contextId is longer than 128 characters.",
        ),
    ],
)
def test_what_is_wrong_with_the_message_itself_is_refused_with_its_sentence(
    make_a2a_server_app: Callable[..., ServerApp], message: dict[str, Any], sentence: str | None
) -> None:
    model = ScriptedChatModel()
    made = make_a2a_server_app(model)
    params = {"message": {"messageId": "m-1", "role": "ROLE_USER", **message}}
    with TestClient(made.app) as client:
        if sentence is None:
            # 16,000 and 16,000 with the line break between: 32,001 characters.
            found = refused_before_the_model(client, made, model, params)
            assert found["message"] == "The message is longer than 32,000 characters."
        else:
            found = refused_before_the_model(client, made, model, params)
            assert (found["code"], found["message"]) == (-32602, sentence)
        nothing_made(client, made)


@pytest.mark.parametrize(
    "part",
    [
        {"raw": "aGVsbG8=", "mediaType": "text/plain"},
        {"url": "https://example.com/file.txt"},
        {"data": {"k": "v"}},
    ],
)
def test_a_part_that_is_not_text_is_refused_as_an_unsupported_content_type(
    make_a2a_server_app: Callable[..., ServerApp], part: dict[str, Any]
) -> None:
    model = ScriptedChatModel()
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        params = {"message": {**sending()["message"], "parts": [{"text": "hi"}, part]}}
        found = refused_before_the_model(client, made, model, params)
        nothing_made(client, made)
    assert found["code"] == -32005


def test_output_modes_without_text_are_refused_and_text_ones_taken(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = ScriptedChatModel(final_text="Yes.")
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        json_only = {**sending(), "configuration": {"acceptedOutputModes": ["application/json"]}}
        assert refused_before_the_model(client, made, model, json_only)["code"] == -32005
        for index, modes in enumerate(
            [["text/*"], ["*/*"], [], ["application/json", "text/plain"]]
        ):
            params = {
                **sending(contextId=f"c-{index}"),
                "configuration": {"acceptedOutputModes": modes},
            }
            answer = rpc(client, "SendMessage", params).json()
            assert answer["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED", modes


def test_the_longest_message_and_conversation_id_are_taken_and_parts_are_joined(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = ScriptedChatModel(final_text="Read.")
    made = make_a2a_server_app(model)
    longest = "x" * 32_000
    with TestClient(made.app) as client:
        first = rpc(client, "SendMessage", sending(longest, contextId="c" * 128)).json()
        joined = {
            "message": {
                "messageId": "m-2",
                "role": "ROLE_USER",
                "parts": [{"text": "first line"}, {"text": "second line"}],
            }
        }
        second = rpc(client, "SendMessage", joined).json()
    assert first["result"]["task"]["contextId"] == "c" * 128
    assert second["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    told = [str(message.content) for seen in model.seen for message in seen]
    assert longest in told
    assert "first line\nsecond line" in told


NOTIFY = AIMessage(
    content="",
    tool_calls=[{"id": "n1", "name": "send_notification", "args": {"channel": "#ops"}}],
)


def test_tasks_waiting_for_a_person_count_against_the_limit_until_they_end(
    make_a2a_server_app: Callable[..., ServerApp],
    sse_events: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    model = ScriptedChatModel(script=[NOTIFY, NOTIFY], final_text="Rejected, then.")
    made = make_a2a_server_app(
        model, a2a_server_caller_tasks=2, a2a_server_tasks=2, a2a_server_wait_seconds=0
    )
    with TestClient(made.app) as client:
        first = rpc(client, "SendMessage", sending("one", contextId="c-1")).json()
        second = rpc(client, "SendMessage", sending("two", contextId="c-2")).json()
        at_rest(client, made, first["result"]["task"]["id"], second["result"]["task"]["id"])
        calls = model.calls
        third = rpc(client, "SendMessage", sending("three", contextId="c-3")).json()
        assert model.calls == calls
        # A person rejects the first proposal: its task ends, and frees its place.
        thread = made.task(client, first["result"]["task"]["id"]).thread_id
        pending = client.get(f"/threads/{thread}/state").json()["pending"]
        body = {"interrupt_id": pending["interrupt_id"], "decisions": [{"type": "reject"}]}
        with client.stream("POST", f"/threads/{thread}/resume", json=body) as answer:
            sse_events(answer.iter_lines())
        ended(client, made, first["result"]["task"]["id"])
        fourth = rpc(client, "SendMessage", sending("four", contextId="c-4")).json()
    error = third["error"]
    assert (error["code"], error["message"]) == (
        -32004,
        "A limit on tasks in progress was reached. Send again when one has ended.",
    )
    assert error["data"][0]["metadata"] == {"reason": "task_limit"}
    assert fourth["result"]["task"]["status"]["state"] == "TASK_STATE_WORKING"
    refusals = [
        record.getMessage() for record in caplog.records if "was refused" in record.getMessage()
    ]
    assert refusals == [
        "A message of caller local was refused: the caller's limit on tasks in progress was reached"
    ]
