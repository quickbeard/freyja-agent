"""009: what bounds a caller (FR-009a, FR-020, FR-025 to FR-027).

Part 1, a named caller's files on the strict build (FR-009a, research R16). Two named callers,
``alpha`` and ``beta``, the local caller, and a console thread each work in their own place: a
named caller in ``<files dir>/<name>``, the other two in the shared workspace. A named caller
reads the skills and changes none, reaches no file outside its area, and has no shell. Runs
are started as a caller's message starts them; no caller's token is involved before request 5.

Part 2, a message's form and the limits. Part 3, isolation through A2A (FR-009a, FR-020,
SC-007): two named callers, by their tokens, reach nothing of each other's or of a console
thread's, and work in their own files.

Part 4, the hand-over count (FR-027, research R17): a message's count is kept with its task,
one of another form or at the limit is refused before the model, and every message Freyja
sends to an agent from a caller's thread carries the conversation's count plus one, under a
message id that names the thread by its alias.
"""

import itertools
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from freyja_agent.agent.caller_files import READ_ONLY, CallerFiles, NoFilesError
from freyja_agent.agent.context import thread_alias
from freyja_agent.agent.factory import tool_node_tools, workspace_backend
from freyja_agent.config import Settings
from tests.a2a_requests import VERSION, at_rest, ended, rpc, sending
from tests.a2a_stub import make_stub
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


# Part 3: isolation through A2A, with two named callers' tokens (FR-009a, FR-020, SC-007).

# The two made-up tokens .gitleaks.toml lets through (research R15).
ALPHA = "sk-recognizable-key-0001"
BETA = "sk-recognizable-other-0002"
NAMED = {"alpha": {"token": ALPHA}, "beta": {"token": BETA}}
# A console thread whose id has the form of a received task's id.
CONSOLE = "0123456789abcdef0123456789abcdef"


def by(token: str) -> dict[str, str]:
    return {"A2A-Version": "1.0", "Authorization": f"Bearer {token}"}


def test_one_named_caller_reaches_nothing_of_another_s_tasks_or_of_a_console_thread(
    make_a2a_server_app: Callable[..., ServerApp], sse_events: Any
) -> None:
    made = make_a2a_server_app(
        ScriptedChatModel(script=[NOTIFY], final_text="Hi."), a2a_server_callers=NAMED
    )
    with TestClient(made.app) as client:
        theirs = rpc(
            client,
            "SendMessage",
            {
                **sending("Tell #ops.", contextId="shared"),
                "configuration": {"returnImmediately": True},
            },
            by(BETA),
        ).json()["result"]["task"]["id"]
        at_rest(client, made, theirs)
        with client.stream("POST", f"/threads/{CONSOLE}/runs", json={"message": "hi"}) as answer:
            sse_events(answer.iter_lines())
        before = made.task(client, theirs)
        answers = {
            (method, target): rpc(client, method, {"id": target}, by(ALPHA)).content
            for method in ("GetTask", "CancelTask", "SubscribeToTask")
            for target in (theirs, CONSOLE, "f" * 32)
        }
        mine = rpc(client, "SendMessage", sending("hello", contextId="shared"), by(ALPHA)).json()
        after = made.task(client, theirs)
        own = made.task(client, mine["result"]["task"]["id"])
        pending = client.get(f"/threads/{before.thread_id}/state").json()["pending"]
    for method in ("GetTask", "CancelTask", "SubscribeToTask"):
        never = answers[(method, "f" * 32)]
        assert b"-32001" in never
        assert answers[(method, theirs)] == never
        assert answers[(method, CONSOLE)] == never
    # The other caller's task and its proposal are as they were.
    assert (after.state, after.reject_attempts) == (before.state, 0)
    assert pending is not None
    # A conversation id is the caller's own: the same one starts a conversation of alpha's.
    assert (own.caller, own.context_id) == ("alpha", "shared")
    assert own.thread_id != before.thread_id


def test_through_a2a_each_named_caller_works_in_its_own_files(
    make_a2a_server_app: Callable[..., ServerApp],
    files_dir: Path,
    workspace: Path,
    sse_events: Any,
) -> None:
    model = ScriptedChatModel(
        script=[
            call("write_file", "w1", file_path="/alpha-recognizable.txt", content="alpha's text"),
            DONE,
            call("write_file", "w2", file_path="/beta-recognizable.txt", content="beta's text"),
            DONE,
            call("write_file", "w3", file_path="/console-recognizable.txt", content="console's"),
            DONE,
            calls(
                ("ls", "l1", {"path": "/"}),
                ("read_file", "l2", {"file_path": "/../beta/beta-recognizable.txt"}),
                ("delete", "l3", {"file_path": "/skills"}),
                ("execute", "l4", {"command": "cat /etc/hostname"}),
            ),
            DONE,
        ]
    )
    made = make_a2a_server_app(model, a2a_server_callers=NAMED, a2a_server_files_dir=str(files_dir))
    with TestClient(made.app) as client:
        rpc(client, "SendMessage", sending("Write.", contextId="a"), by(ALPHA))
        rpc(client, "SendMessage", sending("Write.", contextId="b"), by(BETA))
        with client.stream("POST", "/threads/k-1/runs", json={"message": "Write."}) as answer:
            sse_events(answer.iter_lines())
        rpc(client, "SendMessage", sending("Look.", contextId="a"), by(ALPHA))
    seen = results(model)
    assert (files_dir / "alpha" / "alpha-recognizable.txt").read_text() == "alpha's text"
    assert (files_dir / "beta" / "beta-recognizable.txt").read_text() == "beta's text"
    assert (workspace / "console-recognizable.txt").read_text() == "console's"
    assert "alpha-recognizable.txt" in seen["l1"]
    assert "beta" not in seen["l1"] and "console" not in seen["l1"]
    assert "beta's text" not in seen["l2"]
    assert (workspace / "skills").is_dir()
    assert "error" in seen["l4"].lower() or "not" in seen["l4"].lower()


def test_the_total_limit_with_two_named_callers_refuses_with_the_same_sentence(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    model = ScriptedChatModel(script=[NOTIFY, NOTIFY])
    made = make_a2a_server_app(
        model,
        a2a_server_callers=NAMED,
        a2a_server_caller_tasks=1,
        a2a_server_tasks=2,
        a2a_server_wait_seconds=0,
    )
    with TestClient(made.app) as client:
        one = rpc(client, "SendMessage", sending("one", contextId="c-1"), by(ALPHA)).json()
        two = rpc(client, "SendMessage", sending("two", contextId="c-2"), by(BETA)).json()
        at_rest(client, made, one["result"]["task"]["id"], two["result"]["task"]["id"])
        third = rpc(client, "SendMessage", sending("three", contextId="c-3")).json()
    error = third["error"]
    assert (error["code"], error["message"]) == (
        -32004,
        "A limit on tasks in progress was reached. Send again when one has ended.",
    )
    assert error["data"][0]["metadata"] == {"reason": "task_limit"}
    assert [r.getMessage() for r in caplog.records if "was refused" in r.getMessage()] == [
        "A message of caller local was refused: the total limit on tasks in progress was reached"
    ]


# Part 4: the hand-over count (FR-027, research R17), on messages in and out.

HOPS = "freyja.hops"
FORM = "The hand-over count is not a whole number of zero or more."
TOO_MANY = "The message has passed through too many agents."


@pytest.mark.parametrize(
    ("metadata", "kept"),
    [(None, 0), ({"other": "x"}, 0), ({HOPS: 0}, 0), ({HOPS: 2}, 2), ({HOPS: 2.0}, 2)],
)
def test_a_message_s_hand_over_count_is_kept_with_its_task(
    make_a2a_server_app: Callable[..., ServerApp], metadata: dict[str, Any] | None, kept: int
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(final_text="Hi."))
    extra = {} if metadata is None else {"metadata": metadata}
    with TestClient(made.app) as client:
        answer = rpc(client, "SendMessage", sending("hello", **extra)).json()
        task = made.task(client, answer["result"]["task"]["id"])
    assert answer["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task.hops == kept


@pytest.mark.parametrize(
    ("value", "sentence"),
    [
        (-1, FORM),
        (1.5, FORM),
        # Not whole and past the limit: its form is judged first.
        (4.5, FORM),
        ("1", FORM),
        (True, FORM),
        ([1], FORM),
        (None, FORM),
        ({"n": 1}, FORM),
        (3, TOO_MANY),
        (3.0, TOO_MANY),
        (1e20, TOO_MANY),
    ],
)
def test_a_count_of_another_form_or_at_the_limit_is_refused_before_the_model(
    make_a2a_server_app: Callable[..., ServerApp], value: Any, sentence: str
) -> None:
    model = ScriptedChatModel()
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        params = sending("hello", metadata={HOPS: value})
        found = refused_before_the_model(client, made, model, params)
        nothing_made(client, made)
    assert (found["code"], found["message"]) == (-32602, sentence)


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity"])
def test_a_count_that_is_no_finite_number_is_refused_for_its_form(
    make_a2a_server_app: Callable[..., ServerApp], raw: str
) -> None:
    """Python's JSON parser takes these, which only raw JSON text can carry."""
    model = ScriptedChatModel()
    made = make_a2a_server_app(model)
    body = {"jsonrpc": "2.0", "id": 7, "method": "SendMessage"}
    body["params"] = sending("hello", metadata={HOPS: "RAW"})
    text = json.dumps(body).replace('"RAW"', raw)
    with TestClient(made.app) as client:
        answer = client.post(
            "/a2a", content=text, headers={**VERSION, "Content-Type": "application/json"}
        )
        nothing_made(client, made)
    assert (answer.json()["error"]["code"], answer.json()["error"]["message"]) == (-32602, FORM)
    assert model.calls == 0


def test_the_count_is_checked_after_the_message_s_other_parts(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = ScriptedChatModel()
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        found = refused_before_the_model(
            client, made, model, sending("hello", contextId="c" * 129, metadata={HOPS: -1})
        )
    assert found["message"] == "The contextId is longer than 128 characters."


def test_the_limit_is_the_setting_s(make_a2a_server_app: Callable[..., ServerApp]) -> None:
    model = ScriptedChatModel(final_text="Hi.")
    made = make_a2a_server_app(model, a2a_server_max_hops=5)
    with TestClient(made.app) as client:
        four = rpc(client, "SendMessage", sending("hi", contextId="c-4", metadata={HOPS: 4})).json()
        five = refused_before_the_model(
            client, made, model, sending("hi", contextId="c-5", metadata={HOPS: 5})
        )
    assert four["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert (five["code"], five["message"]) == (-32602, TOO_MANY)


@pytest.mark.parametrize("value", [0, 17, -1])
def test_the_limit_of_hand_overs_is_1_to_16_and_3_by_default(workspace: Path, value: int) -> None:
    assert Settings(workspace_dir=str(workspace)).a2a_server_max_hops == 3
    for bound in (1, 16):
        on = Settings(workspace_dir=str(workspace), a2a_server=True, a2a_server_max_hops=bound)
        assert on.a2a_server_max_hops == bound
    with pytest.raises(ValidationError) as caught:
        Settings(workspace_dir=str(workspace), a2a_server=True, a2a_server_max_hops=value)
    assert "FREYJA_A2A_SERVER_MAX_HOPS must be from 1 to 16" in str(caught.value)
    # With the feature off the setting is not used, and not checked.
    assert Settings(workspace_dir=str(workspace), a2a_server_max_hops=value).a2a_server is False


def handoff(message: str, call_id: str, task_id: str | None = None) -> AIMessage:
    args = {"message": message, **({"task_id": task_id} if task_id else {})}
    return call("handoff_to_stub", call_id, **args)


def test_every_message_from_a_caller_s_thread_carries_the_conversation_s_count_plus_one(
    make_a2a_server_app: Callable[..., ServerApp], sse_events: Any
) -> None:
    numbers = itertools.count(1)
    stub = make_stub("stub", task_ids=lambda: f"task-{next(numbers)}")
    model = ScriptedChatModel(
        script=[
            # A hand-off whose task asks a question, then the answer to it, then a follow-up
            # in the agent's conversation, canceled in the same run; then a console thread's.
            handoff("ask: plan the release", "h1"),
            AIMessage(content="The agent asks which date."),
            handoff("Friday at 10.", "h2", task_id="task-1"),
            AIMessage(content="Answered."),
            handoff("ask: plan another", "h3"),
            call("cancel_stub_task", "x1", task_id="task-2"),
            AIMessage(content="Canceled."),
            handoff("Hello from the console.", "h4"),
            AIMessage(content="Sent."),
        ]
    )
    # The agent runs without approval, and the agents' mode is Auto: each is sent at once.
    made = make_a2a_server_app(model, stubs={"stub": stub})
    with TestClient(made.app) as client:

        def send(text: str, hops: int | None) -> dict[str, Any]:
            extra = {} if hops is None else {"metadata": {HOPS: hops}}
            answer = rpc(client, "SendMessage", sending(text, contextId="c-hops", **extra))
            return answer.json()["result"]["task"]

        tasks = [
            send("Plan the release with the stub.", 1),
            send("Friday at 10.", None),
            send("Plan another, then cancel it.", 2),
        ]
        with client.stream("POST", "/threads/k-console/runs", json={"message": "Hi."}) as answer:
            sse_events(answer.iter_lines())
        thread = made.task(client, tasks[0]["id"]).thread_id
        # The send ledger, where a cancel's message id is kept.
        sends = list(made.a2a.store._sends.values())
    assert [task["status"]["state"] for task in tasks] == ["TASK_STATE_COMPLETED"] * 3
    record = stub.record
    assert [(message.text, message.hops) for message in record.messages] == [
        ("ask: plan the release", 2),
        # After a message that carried no count, and one that carried another.
        ("Friday at 10.", 1),
        ("ask: plan another", 3),
        ("Hello from the console.", None),
    ]
    # The answer continued the agent's task, and the follow-up its conversation.
    assert record.messages[1].task_id == "task-1"
    assert record.messages[2].context_id == record.messages[0].context_id
    # A cancel is a request about a task, and starts no work: it carries no count.
    assert (record.cancels, record.cancel_hops) == (["task-2"], [None])
    alias = thread_alias(thread)
    for message in record.messages[:3]:
        assert alias in message.message_id
        assert thread not in message.message_id
    (cancel,) = [found for found in sends if found.kind == "cancel"]
    assert alias in cancel.message_id
    assert thread not in cancel.message_id
    # A console thread's message is sent as before.
    assert "k-console" in record.messages[3].message_id


def test_a_hand_off_a_person_approves_carries_the_count_plus_one(
    make_a2a_server_app: Callable[..., ServerApp], sse_events: Any
) -> None:
    """Through both routes that carry a decision: with the agents' mode on Manual, a hand-off
    waits for a person, as the quickstart walks it."""
    stub = make_stub("stub")
    model = ScriptedChatModel(
        script=[handoff("say hello", "h1"), AIMessage(content="Sent."), handoff("again", "h2")],
        final_text="Sent again.",
    )
    made = make_a2a_server_app(model, stubs={"stub": stub})
    with TestClient(made.app) as client:
        assert client.put("/agents/mode", json={"mode": "manual"}).status_code == 200
        threads = []
        for index, route in enumerate(("resume", "resume/unattended")):
            params = {
                **sending("Hand it over.", contextId=f"c-{index}", metadata={HOPS: index + 1}),
                "configuration": {"returnImmediately": True},
            }
            task_id = rpc(client, "SendMessage", params).json()["result"]["task"]["id"]
            at_rest(client, made, task_id)
            thread = made.task(client, task_id).thread_id
            threads.append(thread)
            pending = client.get(f"/threads/{thread}/state").json()["pending"]
            body = {"interrupt_id": pending["interrupt_id"], "decisions": [{"type": "approve"}]}
            with client.stream("POST", f"/threads/{thread}/{route}", json=body) as answer:
                assert answer.status_code == 200, answer.read()
                sse_events(answer.iter_lines())
            ended(client, made, task_id)
    assert [(message.text, message.hops) for message in stub.record.messages] == [
        ("say hello", 2),
        ("again", 3),
    ]
    for message, thread in zip(stub.record.messages, threads, strict=True):
        assert thread_alias(thread) in message.message_id
        assert thread not in message.message_id


def test_the_run_that_reports_an_agent_s_outcome_carries_the_count_plus_one(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    stub = make_stub("stub")
    model = ScriptedChatModel(
        script=[
            handoff("hold", "h1"),
            AIMessage(content="Waiting for the agent."),
            # The worker's run that reports the outcome hands work over again.
            handoff("Thanks, one more.", "h2"),
        ],
        final_text="Done.",
    )
    made = make_a2a_server_app(model, stubs={"stub": stub}, a2a_call_timeout_seconds=0.3)
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Ask the agent.", context="c-w", hops=1)
        made.finish(client, accepted)
        assert len(stub.record.messages) == 1
        stub.release()
        client.portal.call(made.runtime.worker.tick)
        client.portal.call(made.runtime.worker.tick)
    thread = accepted.task.thread_id
    assert [(message.text, message.hops) for message in stub.record.messages] == [
        ("hold", 2),
        ("Thanks, one more.", 2),
    ]
    assert all(thread_alias(thread) in message.message_id for message in stub.record.messages)


def test_a_subagent_s_hand_off_carries_the_count_plus_one(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    stub = make_stub("stub")
    model = ScriptedChatModel(
        script=[
            call("task", "t1", description="Ask the stub.", subagent_type="general-purpose"),
            handoff("From the subagent.", "h1"),
            AIMessage(content="The subagent is done."),
        ],
        final_text="Done.",
    )
    made = make_a2a_server_app(model, stubs={"stub": stub})
    with TestClient(made.app) as client:
        answer = rpc(client, "SendMessage", sending("Delegate.", metadata={HOPS: 2})).json()
        thread = made.task(client, answer["result"]["task"]["id"]).thread_id
    assert [(message.text, message.hops) for message in stub.record.messages] == [
        ("From the subagent.", 3)
    ]
    assert thread_alias(thread) in stub.record.messages[0].message_id
    assert thread not in stub.record.messages[0].message_id
