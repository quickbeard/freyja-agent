"""009: an agent hands Freyja a task and reads the result (User Story 1, FR-001).

Part 1, before anything is served: the stand-in caller, the protocol's own client, is shown
against the stand-in agent, so that its plumbing is known before it calls Freyja; and with the
feature off and the tables empty the service is what it was, apart from asking, once for each
context taken for a thread, whether a caller started the thread (research R6).
"""

import asyncio
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from importlib.metadata import version
from typing import Any

import pytest
from a2a.types import Task
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from langchain.agents.middleware.internal_call_transformer import INTERNAL_CALL_METADATA_KEY
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from pydantic import SecretStr

from freyja_agent.api import received as received_module
from freyja_agent.api.received import Ended
from freyja_agent.api.streaming import is_reply
from freyja_agent.llm.secrets import encrypt
from freyja_agent.llm.store import InMemoryLlmProfileStore
from freyja_agent.runtime.received_tasks import InMemoryReceivedTaskStore
from tests.a2a_caller import HOPS_KEY, StandInCaller, as_json, message, parser
from tests.a2a_requests import (
    ADDRESS,
    VERSION,
    at_rest,
    call,
    ended,
    kinds,
    rpc,
    sending,
    streamed,
    text_of,
)
from tests.a2a_stub import make_stub
from tests.asgi_stream import StreamingASGITransport
from tests.conftest import TEST_SECRET, ServerApp
from tests.fakes import (
    FailingChatModel,
    LabelledChatModel,
    LoopingChatModel,
    ProfileModels,
    ScriptedChatModel,
    StreamingChatModel,
)
from tests.nowhere import KEY, MARK, no_address, no_key, profile
from tests.test_allowed_hosts import REBOUND
from tests.test_allowed_hosts import _routes as host_routes
from tests.test_threads_other_origins import OTHER_ORIGIN
from tests.test_threads_other_origins import _routes as origin_routes


async def test_the_stand_in_caller_reads_a_card_and_sends_with_and_without_a_stream() -> None:
    agent = make_stub("stub")
    transport = StreamingASGITransport(agent.app)
    async with StandInCaller("http://127.0.0.1:9999", transport=transport) as caller:
        card = await caller.card()
        assert card.name == "Stub Agent stub"
        answered = await caller.send("hello there")
        events = [event.WhichOneof("payload") async for event in caller.stream("and again")]
        assert isinstance(answered, Task) and answered.id
        assert (await caller.get(answered.id)).id == answered.id
    assert [found.parts[0][1] for found in agent.record.messages] == ["hello there", "and again"]
    assert events
    assert as_json(message("x", context="c-1", hops=2))["metadata"] == {HOPS_KEY: 2}


def test_the_command_takes_the_quickstart_s_options() -> None:
    parsed = parser().parse_args(
        ["--url", "http://localhost:8000", "--context", "C", "--hops", "1", "send", "hi"]
    )
    assert (parsed.operation, parsed.value, parsed.context, parsed.hops) == ("send", "hi", "C", 1)


class Counting(InMemoryReceivedTaskStore):
    """The store, counting the lookups of who started a thread and every write."""

    def __init__(self) -> None:
        super().__init__()
        self.lookups = 0
        self.writes = 0

    async def conversation_of(self, thread_id: str) -> Any:
        self.lookups += 1
        return await super().conversation_of(thread_id)

    async def accept(self, *args: Any, **kwargs: Any) -> Any:
        self.writes += 1
        return await super().accept(*args, **kwargs)


def test_with_the_feature_off_and_no_conversation_the_service_is_what_it_was(
    make_a2a_server_app: Callable[..., ServerApp], sse_events: Any
) -> None:
    store = Counting()
    made = make_a2a_server_app(
        ScriptedChatModel(final_text="Hi."), received=store, a2a_server=False
    )
    with TestClient(made.app) as client:
        with client.stream("POST", "/threads/k-1/runs", json={"message": "hi"}) as answer:
            events = sse_events(answer.iter_lines())
        assert client.get("/threads/k-1/state").status_code == 200
        assert client.get("/threads").status_code == 200
        client.portal.call(made.runtime.worker.tick)
        assert made.app.state.received is None
        assert made.runtime.models.strict is None
    assert events[-1][0] == "end"
    assert store.writes == 0
    # One lookup for each context taken for the thread, none for the list or the worker's
    # round, which take no thread's context.
    assert store.lookups >= 1


CARD_PATH = "/.well-known/agent-card.json"
DESCRIPTION = (
    "A general-purpose orchestrator. It takes a request in plain language, works on it with "
    "its tools and the agents it can hand work to, and asks a person before any action with "
    "effects outside its workspace."
)


def ls(call_id: str = "l1", path: str = "/") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": call_id, "name": "ls", "args": {"path": path}}])


# Part 2: the card (FR-006).


def test_the_card_is_the_contract_s_and_says_nothing_of_the_deployment(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(
        ScriptedChatModel(),
        agents={"recognizable-agent": {"read_only": True}},
        servers={"recognizable-server": {"command": "stub-command"}},
        llm_profiles=InMemoryLlmProfileStore(),
        model_factory=ProfileModels(),
        secret_key=TEST_SECRET,
    )
    with TestClient(made.app) as client:
        recognizable = profile(name="recognizable-profile", model="recognizable-model")
        saved = client.post("/llm/profiles", json=recognizable).json()["id"]
        assert client.post(f"/llm/profiles/{saved}/activate").status_code == 200
        card = call(client, made, lambda caller: caller.card())
        first = client.get(CARD_PATH)
        again = client.get(CARD_PATH, headers={"x-forwarded-for": "10.0.0.9"})
    assert card.name == "Freyja"
    assert first.status_code == 200
    assert (
        first.json()
        == again.json()
        == {
            "name": "Freyja",
            "description": DESCRIPTION,
            "version": version("freyja-agent"),
            "supportedInterfaces": [
                {"url": f"{ADDRESS}/a2a", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
            ],
            "capabilities": {
                "streaming": True,
                "pushNotifications": False,
                "extendedAgentCard": False,
            },
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [
                {
                    "id": "request",
                    "name": "Take a request",
                    "description": "Send a request in plain language; the result is Freyja's reply.",
                    "tags": ["general"],
                }
            ],
        }
    )
    assert "recognizable" not in first.text
    assert "security" not in first.text


def test_the_card_gives_the_address_it_was_asked_under(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(ScriptedChatModel())
    with TestClient(made.app, base_url="http://localhost:8020") as client:
        found = client.get(CARD_PATH).json()
    assert found["supportedInterfaces"][0]["url"] == "http://localhost:8020/a2a"


# Part 3: a send with a stream (FR-017, SC-009, FR-014).


def test_a_send_with_a_stream_says_each_step_and_sends_the_reply_in_pieces_then_whole(
    make_a2a_server_app: Callable[..., ServerApp], workspace: Any
) -> None:
    (workspace / "recognizable-file.txt").write_text("x")
    model = StreamingChatModel(
        script=[ls()], final_text="There is one file.", pieces=["There is ", "one file."]
    )
    made = make_a2a_server_app(model)
    model.hold()
    with TestClient(made.app) as client:

        async def work(caller: StandInCaller) -> tuple[list[dict], Any]:
            events, during = [], None
            async for event in caller.stream("List the files."):
                events.append(as_json(event))
                if "artifactUpdate" in events[-1] and during is None:
                    # The first piece arrived while the rest of the reply is held.
                    during = await made.received.get(events[0]["task"]["id"])
                    with model.held():
                        pass
            return events, during

        events, during = call(client, made, work)
    assert during is not None and during.state == "working"
    assert kinds(events) == [
        "task",
        "statusUpdate",
        "statusUpdate",
        "artifactUpdate",
        "artifactUpdate",
        "artifactUpdate",
        "statusUpdate",
    ]
    first = events[0]["task"]
    assert first["status"]["state"] == "TASK_STATE_WORKING"
    assert text_of(events[0]) == "Working."
    assert [text_of(event) for event in events[1:3]] == ["Freyja is using: ls.", "Completed: ls."]
    pieces = [event["artifactUpdate"] for event in events[3:6]]
    # A piece is sent up to its last whitespace, so that what follows can be cleaned whole
    # with the pieces after it; the whole reply comes last.
    assert [piece["artifact"]["parts"][0]["text"] for piece in pieces] == [
        "There is ",
        "one ",
        "There is one file.",
    ]
    assert [piece.get("append", False) for piece in pieces] == [True, True, False]
    assert [piece.get("lastChunk", False) for piece in pieces] == [False, False, True]
    assert {piece["artifact"]["artifactId"] for piece in pieces} == {"reply"}
    assert events[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "message" not in events[-1]["statusUpdate"]["status"]
    # A message with no contextId is answered with one the service made.
    assert re.fullmatch(r"[0-9a-f]{32}", first["contextId"])
    assert re.fullmatch(r"[0-9a-f]{32}", first["id"])
    # What the tools step returned and the call's arguments are in no event.
    assert "recognizable-file" not in json.dumps(events)


def test_a_subagent_s_and_a_tool_s_words_are_never_sent_and_names_are_only_names(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    delegate = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "t1",
                "name": "task",
                "args": {
                    "description": "Look for the recognizable-argument.",
                    "subagent_type": "general-purpose",
                },
            }
        ],
    )
    model = StreamingChatModel(
        script=[delegate, AIMessage(content="recognizable-subagent words")],
        final_text="All done.",
    )
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        events = streamed(client, made, "Ask your helper.")
    sentences = [text_of(event) for event in events if "statusUpdate" in event]
    assert sentences[:2] == ["Freyja is using: a subagent.", "Completed: a subagent."]
    texts = json.dumps(events)
    assert "recognizable" not in texts
    assert events[-2]["artifactUpdate"]["artifact"]["parts"][0]["text"] == "All done."


def test_a_caller_that_stops_reading_leaves_the_run_going_on(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = StreamingChatModel(final_text="Half and the rest.", pieces=["Half ", "and the rest."])
    made = make_a2a_server_app(model)
    model.hold()
    with TestClient(made.app) as client:

        async def work(caller: StandInCaller) -> str:
            async for event in caller.stream("Go."):
                if event.WhichOneof("payload") == "artifact_update":
                    task_id = event.artifact_update.task_id
                    break
            with model.held():
                pass
            return task_id

        task_id = call(client, made, work)
        found = ended(client, made, task_id)
        read = call(client, made, lambda caller: caller.get(task_id))
    assert found.state == "completed"
    assert as_json(read)["artifacts"][0]["parts"][0]["text"] == "Half and the rest."


def test_a_run_that_waits_for_a_person_says_so_and_nothing_of_the_proposal(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    notify = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "n1",
                "name": "send_notification",
                "args": {"channel": "#recognizable-channel", "message": "recognizable text"},
            }
        ],
    )
    made = make_a2a_server_app(ScriptedChatModel(script=[notify]))
    with TestClient(made.app) as client:

        async def work(caller: StandInCaller) -> list[dict]:
            events = []
            async for event in caller.stream("Tell the channel."):
                events.append(as_json(event))
                if text_of(events[-1]) and text_of(events[-1]).startswith("Waiting"):
                    break
            return events

        events = call(client, made, work)
        task = made.task(client, events[0]["task"]["id"])
        pending = client.get(f"/threads/{task.thread_id}/state").json()["pending"]
    assert text_of(events[-1]) == "Waiting for a person's approval of: send_notification."
    assert events[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_WORKING"
    texts = json.dumps(events)
    assert "recognizable" not in texts
    assert pending["interrupt_id"] not in texts
    assert task.thread_id not in texts


def test_a_hand_off_is_named_by_its_agent(make_a2a_server_app: Callable[..., ServerApp]) -> None:
    handoff = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "h1",
                "name": "handoff_to_stub",
                "args": {"message": "recognizable", "context": "", "task_id": None},
            }
        ],
    )
    # An agent that requires approval: the hand-off waits for a person (010 FR-007).
    made = make_a2a_server_app(ScriptedChatModel(script=[handoff, handoff]), read_only=False)
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Hand it on.")
        made.finish(client, accepted)
        task = call(client, made, lambda caller: caller.get(accepted.task.task_id))

        async def work(caller: StandInCaller) -> list[str | None]:
            sentences = []
            async for event in caller.stream("Hand it on again.", context="c-two"):
                sentences.append(text_of(as_json(event)))
                if (sentences[-1] or "").startswith("Waiting"):
                    break
            return sentences

        sentences = call(client, made, work)
    assert text_of(as_json(task)) == "Waiting for a person's approval of: agent stub."
    assert sentences == [
        "Working.",
        "Freyja is using: agent stub.",
        "Waiting for a person's approval of: agent stub.",
    ]


def test_a_piece_is_freyja_s_own_reply_only() -> None:
    own = {"langgraph_node": "model"}
    chunk = AIMessageChunk(content="Hello")
    assert is_reply((), chunk, own)
    assert is_reply((), AIMessage(content="Hello"), own)
    # A subagent's, at any depth; another node's; a tool's; a call's arguments alone.
    assert not is_reply(("tools:abc",), chunk, own)
    assert not is_reply((), chunk, {"langgraph_node": "tools"})
    assert not is_reply((), ToolMessage(content="x", tool_call_id="c"), own)
    assert not is_reply(
        (),
        AIMessageChunk(content="", tool_call_chunks=[{"name": "ls", "args": "{}", "index": 0}]),
        own,
    )
    # A call the libraries make for themselves, as the one that summarizes.
    marked = {**own, "lc_source": "summarization", INTERNAL_CALL_METADATA_KEY: "token"}
    assert not is_reply((), chunk, marked)
    assert not is_reply((), chunk, {**own, INTERNAL_CALL_METADATA_KEY: "token"})


# Part 4: a send without a stream, and reading a task (FR-011, FR-019, R13).


def test_a_send_is_answered_with_the_task_once_it_has_ended(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(final_text="Hello there."))
    with TestClient(made.app) as client:
        answered = as_json(call(client, made, lambda caller: caller.send("Say hello.")))
    assert answered["status"]["state"] == "TASK_STATE_COMPLETED"
    assert answered["artifacts"] == [
        {"artifactId": "reply", "name": "reply", "parts": [{"text": "Hello there."}]}
    ]
    assert "history" not in answered


@pytest.mark.parametrize("how", ["limit at 0", "returnImmediately"])
def test_a_send_that_does_not_wait_is_answered_working_and_the_run_goes_on(
    make_a2a_server_app: Callable[..., ServerApp], how: str
) -> None:
    wait = 0 if how == "limit at 0" else 30
    model = StreamingChatModel(final_text="Later.")
    made = make_a2a_server_app(model, a2a_server_wait_seconds=wait)
    model.hold()
    with TestClient(made.app) as client:
        answered = as_json(
            call(
                client,
                made,
                lambda caller: caller.send("Go.", return_immediately=how == "returnImmediately"),
            )
        )
        with model.held():
            pass
        found = ended(client, made, answered["id"])
        read = as_json(call(client, made, lambda caller: caller.get(answered["id"])))
    assert answered["status"]["state"] == "TASK_STATE_WORKING"
    assert text_of(answered) == "Working."
    assert found.state == "completed"
    assert read["status"]["state"] == "TASK_STATE_COMPLETED"
    assert read["artifacts"][0]["parts"][0]["text"] == "Later."


def test_get_task_answers_as_it_stands_and_never_with_history(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(final_text="Done here."))
    with TestClient(made.app) as client:
        answered = as_json(call(client, made, lambda caller: caller.send("Do it.")))
        read = rpc(client, "GetTask", {"id": answered["id"], "historyLength": 10}).json()
    assert read["result"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert read["result"]["artifacts"][0]["parts"][0]["text"] == "Done here."
    assert "history" not in read["result"]


def failed_with(client: TestClient, made: ServerApp, text: str = "Go.") -> dict[str, Any]:
    answered = as_json(call(client, made, lambda caller: caller.send(text)))
    assert answered["status"]["state"] == "TASK_STATE_FAILED", answered
    return answered


def saved_profile(store: InMemoryLlmProfileStore) -> Any:
    """A saved, active profile whose key the service reads under ``TEST_SECRET`` only."""

    async def save() -> Any:
        saved = await store.create_profile(
            name="Saved",
            provider="openai",
            model="saved-model",
            base_url="http://127.0.0.1:9997/v1",
            api_key_encrypted=encrypt(KEY, SecretStr(TEST_SECRET)),
            key_hint=KEY[-4:],
            responses_api=False,
        )
        await store.activate(saved.id)
        return saved

    return asyncio.run(save())


def test_a_run_with_no_usable_model_fails_with_the_contract_s_reason(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    store = InMemoryLlmProfileStore()
    saved_profile(store)
    made = make_a2a_server_app(
        ScriptedChatModel(),
        llm_profiles=store,
        model_factory=ProfileModels(),
        secret_key=TEST_SECRET,
    )
    with TestClient(made.app) as client:
        first = as_json(call(client, made, lambda caller: caller.send("Hi.", context="c-1")))
    # The service starts again with another secret: the active profile's key is unreadable.
    other = Fernet.generate_key().decode()
    again = made.again(secret_key=other)
    with TestClient(again.app) as client:
        answered = as_json(call(client, again, lambda caller: caller.send("Go.", context="c-1")))
        task = again.task(client, answered["id"])
        row = client.get(f"/threads/{task.thread_id}").json()
        listed = client.get("/threads").json()["threads"]
    assert first["status"]["state"] == "TASK_STATE_COMPLETED"
    assert answered["status"]["state"] == "TASK_STATE_FAILED"
    assert text_of(answered) == "No usable model is active."
    # The thread and the list keep the run's own reason.
    assert row["status"] == "failed"
    assert row["error"]["code"] == "no_usable_model"
    assert [found["error"]["code"] for found in listed] == ["no_usable_model"]


class Failing(FailingChatModel):
    """A model whose provider fails every call."""

    def _generate(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise RuntimeError(self.error)


def test_a_model_error_fails_and_its_text_reaches_no_caller(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = Failing(error="recognizable provider trouble")
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        answered = failed_with(client, made)
        events = streamed(client, made, "Again.", context="c-other")
    assert text_of(answered) == "The model could not be used."
    assert text_of(events[-1]) == "The model could not be used."
    assert "recognizable" not in json.dumps([answered, events])


def test_the_step_limit_fails_with_its_reason(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(LoopingChatModel(), max_steps=6)
    with TestClient(made.app) as client:
        answered = failed_with(client, made)
    assert text_of(answered) == "The run reached its step limit."


class Unwritable(InMemoryReceivedTaskStore):
    async def accept(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("recognizable: the database is gone")


def test_a_message_whose_conversation_cannot_be_written_is_refused_and_nothing_starts(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    model = ScriptedChatModel()
    made = make_a2a_server_app(model, received=Unwritable())
    with TestClient(made.app) as client:
        answer = rpc(client, "SendMessage", sending())
        listed = client.get("/threads").json()
    assert answer.status_code == 200
    assert answer.json()["error"]["code"] == -32603
    assert answer.json()["error"]["message"] == "An internal error."
    assert "recognizable" not in answer.text
    assert model.calls == 0
    assert listed["total"] == 0
    # Logged by its kind.
    assert any("RuntimeError" in record.getMessage() for record in caplog.records)


def test_a_task_paused_on_a_proposal_reads_as_working_and_waiting(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    notify = AIMessage(
        content="",
        tool_calls=[
            {"id": "n1", "name": "send_notification", "args": {"channel": "#recognizable"}}
        ],
    )
    # A task waiting for a person has not ended: the send waits out its limit.
    made = make_a2a_server_app(ScriptedChatModel(script=[notify]), a2a_server_wait_seconds=0.5)
    with TestClient(made.app) as client:
        answered = as_json(call(client, made, lambda caller: caller.send("Tell them.")))
        read = as_json(call(client, made, lambda caller: caller.get(answered["id"])))
    for task in (answered, read):
        assert task["status"]["state"] == "TASK_STATE_WORKING"
        assert text_of(task) == "Waiting for a person's approval of: send_notification."
        assert "recognizable" not in json.dumps(task)


AGENT_PORT = 9431


def test_what_is_read_from_the_thread_is_cleaned_of_addresses_and_keys(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    address = f"http://127.0.0.1:{AGENT_PORT}"
    models = ProfileModels()
    models.model("saved-model").final_text = f"Ask {address}/ with {KEY} as before."
    made = make_a2a_server_app(
        ScriptedChatModel(),
        agents={"stub": {"read_only": True, "url": address}},
        llm_profiles=InMemoryLlmProfileStore(),
        model_factory=models,
        secret_key=TEST_SECRET,
    )
    with TestClient(made.app) as client:
        saved = client.post("/llm/profiles", json=profile()).json()["id"]
        assert client.post(f"/llm/profiles/{saved}/activate").status_code == 200
        answered = as_json(call(client, made, lambda caller: caller.send("Where?")))
        read = as_json(call(client, made, lambda caller: caller.get(answered["id"])))
        events = streamed(client, made, "Where again?", context="c-two")
    reply = answered["artifacts"][0]["parts"][0]["text"]
    assert reply == "Ask <address>/ with <key> as before."
    texts = [json.dumps(answered), json.dumps(read), json.dumps(events)]
    no_key(texts, MARK)
    no_address(texts, AGENT_PORT)


# Part 5: the refusals of this request, each before the model (FR-007, FR-026a).


def error_of(answer: Any) -> dict[str, Any]:
    assert answer.status_code == 200, answer.text
    return answer.json()["error"]


PUSH = {"taskId": "t", "url": "https://example.com/hook"}


@pytest.mark.parametrize(
    ("method", "params", "code"),
    [
        ("ListTasks", {}, -32004),
        ("CreateTaskPushNotificationConfig", PUSH, -32003),
        ("GetTaskPushNotificationConfig", {"taskId": "t", "id": "p"}, -32003),
        ("ListTaskPushNotificationConfigs", {"taskId": "t"}, -32003),
        ("DeleteTaskPushNotificationConfig", {"taskId": "t", "id": "p"}, -32003),
        ("GetExtendedAgentCard", {}, -32007),
        ("CancelTask", {"id": "t"}, -32004),
        ("SubscribeToTask", {"id": "t"}, -32004),
        ("GetTask", {"id": "0" * 32}, -32001),
        (
            "SendMessage",
            {**sending(), "configuration": {"taskPushNotificationConfig": PUSH}},
            -32003,
        ),
    ],
)
def test_what_is_not_offered_is_refused_as_the_contract_says(
    make_a2a_server_app: Callable[..., ServerApp], method: str, params: dict, code: int
) -> None:
    model = ScriptedChatModel()
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        found = error_of(rpc(client, method, params))
        listed = client.get("/threads").json()["total"]
    assert found["code"] == code
    assert model.calls == 0
    assert listed == 0


def test_a_request_without_the_version_is_refused_with_the_version_error(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = ScriptedChatModel()
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        found = error_of(rpc(client, "SendMessage", sending(), headers={}))
    assert found["code"] == -32009
    assert model.calls == 0


def test_a_console_thread_s_id_is_no_task_id(
    make_a2a_server_app: Callable[..., ServerApp], sse_events: Any
) -> None:
    made = make_a2a_server_app(ScriptedChatModel())
    with TestClient(made.app) as client:
        with client.stream("POST", "/threads/k-1/runs", json={"message": "hi"}) as answer:
            sse_events(answer.iter_lines())
        unused = rpc(client, "GetTask", {"id": "f" * 32}).json()
        console = rpc(client, "GetTask", {"id": "k-1"}).json()
    assert unused["error"]["code"] == console["error"]["code"] == -32001
    assert unused == console


UNAUTHORIZED = {
    "detail": {
        "code": "unauthorized",
        "message": (
            "This address takes a request only with a caller's bearer token in the "
            "Authorization header."
        ),
    }
}


def test_a_request_with_a_credential_is_refused_until_callers_can_be_named(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = ScriptedChatModel()
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        answer = rpc(client, "SendMessage", sending(), {**VERSION, "Authorization": "Bearer x" * 3})
    assert answer.status_code == 401
    assert answer.headers["www-authenticate"] == "Bearer"
    assert answer.json() == UNAUTHORIZED
    assert model.calls == 0


def big_body() -> bytes:
    body = {"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": sending("x" * 300_000)}
    return json.dumps(body).encode()


def test_a_body_above_the_limit_is_refused_by_its_length(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = ScriptedChatModel()
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        answer = client.post(
            "/a2a", content=big_body(), headers={**VERSION, "content-type": "application/json"}
        )
    assert answer.status_code == 413
    assert answer.json()["detail"]["code"] == "too_large"
    assert model.calls == 0


def test_a_body_without_a_length_is_read_no_further_than_the_limit(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(ScriptedChatModel())
    piece = 64 * 1024
    reads = 0

    async def ask() -> list[dict[str, Any]]:
        nonlocal reads
        sent: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            nonlocal reads
            reads += 1
            return {"type": "http.request", "body": b"x" * piece, "more_body": reads < 20}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/a2a",
            "raw_path": b"/a2a",
            "query_string": b"",
            "headers": [(b"host", b"testserver"), (b"a2a-version", b"1.0")],
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 50000),
        }
        await made.app(scope, receive, send)
        return sent

    with TestClient(made.app) as client:
        sent = client.portal.call(ask)
    assert sent[0]["status"] == 413
    # Four pieces fill the limit; the fifth passes it, and nothing more is read.
    assert reads == 5


def test_an_unexpected_failure_is_the_internal_error_and_logged_by_its_kind_cleaned(
    make_a2a_server_app: Callable[..., ServerApp],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    address = f"http://127.0.0.1:{AGENT_PORT}"
    made = make_a2a_server_app(
        ScriptedChatModel(), agents={"stub": {"read_only": True, "url": address}}
    )
    with TestClient(made.app) as client:

        async def broken(*_: Any, **__: Any) -> Any:
            raise ValueError(f"recognizable trouble at {address}/")

        monkeypatch.setattr(made.service(), "accept", broken)
        answer = rpc(client, "SendMessage", sending())
        streamed_answer = rpc(client, "SendStreamingMessage", sending())
    for found in (answer, streamed_answer):
        assert found.status_code == 200
        error = found.json()["error"]
        assert (error["code"], error["message"]) == (-32603, "An internal error.")
        assert "recognizable" not in found.text
    lines = [
        record.getMessage() for record in caplog.records if record.getMessage().startswith("A2A ")
    ]
    assert lines and all("ValueError" in line for line in lines)
    no_address(lines, AGENT_PORT)


def test_a_tenant_names_no_one(make_a2a_server_app: Callable[..., ServerApp]) -> None:
    made = make_a2a_server_app(ScriptedChatModel(final_text="Yes."))
    with TestClient(made.app) as client:
        answer = rpc(client, "SendMessage", {**sending(), "tenant": "artex"}).json()
        task = made.task(client, answer["result"]["task"]["id"])
    assert answer["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task.caller == "local"


# The order of the checks, one test per neighbouring pair of the contract's list.


def test_a_page_of_another_origin_is_refused_before_its_credential_is_looked_at(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(ScriptedChatModel())
    with TestClient(made.app) as client:
        answer = rpc(
            client,
            "SendMessage",
            sending(),
            {**VERSION, "Authorization": "Bearer y", "sec-fetch-site": "cross-site"},
        )
    assert answer.status_code == 403
    assert answer.json()["detail"]["code"] == "other_origin"


def test_a_credential_is_refused_before_the_size_is_looked_at(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(ScriptedChatModel())
    with TestClient(made.app) as client:
        answer = client.post(
            "/a2a", content=big_body(), headers={**VERSION, "Authorization": "Bearer y"}
        )
    assert answer.status_code == 401


def test_the_size_is_refused_before_the_version_is_looked_at(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(ScriptedChatModel())
    with TestClient(made.app) as client:
        answer = client.post("/a2a", content=big_body())
    assert answer.status_code == 413


def test_the_version_is_refused_before_the_operation(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(ScriptedChatModel())
    with TestClient(made.app) as client:
        found = error_of(rpc(client, "ListTasks", {}, headers={}))
    assert found["code"] == -32009


def test_the_operation_is_refused_before_the_message_s_form(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(ScriptedChatModel())
    params = {**sending("  "), "configuration": {"taskPushNotificationConfig": PUSH}}
    with TestClient(made.app) as client:
        found = error_of(rpc(client, "SendMessage", params))
    assert found["code"] == -32003


def paused(model_script: list[AIMessage] | None = None) -> ScriptedChatModel:
    notify = AIMessage(
        content="",
        tool_calls=[{"id": "n1", "name": "send_notification", "args": {"channel": "#ops"}}],
    )
    return ScriptedChatModel(script=model_script or [notify])


def test_the_message_s_form_is_refused_before_the_conversation(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(paused(), a2a_server_wait_seconds=0)
    with TestClient(made.app) as client:
        first = rpc(client, "SendMessage", sending("Tell them.", contextId="c-1")).json()
        found = error_of(rpc(client, "SendMessage", sending("   ", contextId="c-1")))
    assert first["result"]["task"]["status"]["state"] == "TASK_STATE_WORKING"
    assert (found["code"], found["message"]) == (-32602, "The message holds no text.")


def test_the_conversation_is_refused_before_the_limits(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    made = make_a2a_server_app(
        paused(), a2a_server_wait_seconds=0, a2a_server_caller_tasks=1, a2a_server_tasks=1
    )
    with TestClient(made.app) as client:
        rpc(client, "SendMessage", sending("Tell them.", contextId="c-1"))
        busy = error_of(rpc(client, "SendMessage", sending("More.", contextId="c-1")))
        limit = error_of(rpc(client, "SendMessage", sending("Other.", contextId="c-2")))
    assert busy["data"][0]["metadata"] == {"reason": "conversation_busy"}
    assert limit["data"][0]["metadata"] == {"reason": "task_limit"}


# Part 6: the feature off, and the routes (FR-001, FR-002, SC-002, research R3).

METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def test_with_the_feature_off_both_paths_are_unknown_paths_byte_for_byte(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    store = Counting()
    made = make_a2a_server_app(ScriptedChatModel(), received=store, a2a_server=False)
    with TestClient(made.app) as client:
        for method in METHODS:
            unknown = client.request(method, "/no-such-path", headers=VERSION)
            for path in (CARD_PATH, "/a2a"):
                found = client.request(
                    method,
                    path,
                    headers=VERSION,
                    content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "SendMessage"}),
                )
                assert found.status_code == unknown.status_code == 404, (method, path)
                assert found.content == unknown.content
                assert found.headers == unknown.headers
    assert store.writes == 0
    assert not [record for record in caplog.records if "caller" in record.getMessage().lower()]


def a2a_routes(app: Any) -> list[str]:
    paths = [getattr(route, "path", None) for route in app.router.routes]
    return [path for path in paths if path in (CARD_PATH, "/a2a")]


def test_the_routes_exist_only_while_the_lifespan_runs(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(final_text="First."))
    assert a2a_routes(made.app) == []
    with caplog.at_level(logging.INFO), TestClient(made.app) as client:
        assert sorted(a2a_routes(made.app)) == sorted([CARD_PATH, "/a2a"])
        first = made.runtime
    assert a2a_routes(made.app) == []
    # Entered again, it serves the routes of its new runtime only.
    with TestClient(made.app) as client:
        assert sorted(a2a_routes(made.app)) == sorted([CARD_PATH, "/a2a"])
        answer = rpc(client, "SendMessage", sending()).json()
        assert made.runtime is not first
    assert answer["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    on = [
        record.getMessage() for record in caplog.records if "over A2A is on" in record.getMessage()
    ]
    # Once for each time the lifespan was entered.
    assert on == 2 * [
        "Receiving tasks from other agents over A2A is on: the card and the operations are "
        "served at this address; 0 callers are named; the outside listener is off"
    ]


def test_the_routes_are_held_to_the_rule_on_other_origins(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = ScriptedChatModel()
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        changing = sorted(
            (method, path)
            for methods, path in origin_routes(made.app.routes)
            for method in methods - {"GET", "HEAD", "OPTIONS"}
        )
        assert ("POST", "/a2a") in changing
        for headers in OTHER_ORIGIN:
            answer = rpc(client, "SendMessage", sending(), {**VERSION, **headers})
            assert answer.status_code == 403, headers
            assert answer.json()["detail"]["code"] == "other_origin"
    assert model.calls == 0


@pytest.mark.parametrize("headers", REBOUND)
def test_the_routes_are_held_to_the_names_the_service_answers_for(
    make_a2a_server_app: Callable[..., ServerApp], headers: dict[str, str]
) -> None:
    model = ScriptedChatModel()
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        served = set(host_routes(made.app.routes))
        assert {("GET", CARD_PATH), ("POST", "/a2a")} <= served
        for method, path in (("GET", CARD_PATH), ("POST", "/a2a")):
            answer = client.request(method, path, headers={**VERSION, **headers})
            assert answer.status_code == 403, (method, path)
            assert answer.json()["detail"]["code"] == "unknown_host"
    assert model.calls == 0


# Part 7: what is Freyja's own reply on a caller's thread, and a thread's row (FR-023).


def replies(events: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    return [
        (data["message"]["type"], data["metadata"].get("reply", "absent"))
        for kind, data in events
        if kind == "message"
    ]


def test_each_message_event_of_a_caller_s_run_says_whether_it_is_freyja_s_reply(
    make_a2a_server_app: Callable[..., ServerApp], sse_events: Any
) -> None:
    model = StreamingChatModel(script=[ls()], final_text="One file.", pieces=["One ", "file."])
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        accepted = made.accept(client, "List.")
        events = made.finish(client, accepted)
        with client.stream("POST", "/threads/k-1/runs", json={"message": "List."}) as answer:
            console = sse_events(answer.iter_lines())
    found = [
        (data["message"]["type"], data["message"]["content"], data["metadata"]["reply"])
        for kind, data in events
        if kind == "message"
    ]
    # Only the text of Freyja's own reply is it: not the call's arguments as they are
    # written, which hold no text, and not the tools step's messages.
    assert [text for _, text, reply in found if reply] == ["One ", "file."]
    assert {kind for kind, _, reply in found if not reply} == {"ai", "tool"}
    assert {reply for _, reply in replies(console)} == {"absent"}


def test_the_continuation_a_decision_starts_has_it_too(
    make_a2a_server_app: Callable[..., ServerApp], sse_events: Any
) -> None:
    made = make_a2a_server_app(paused())
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Tell them.")
        made.finish(client, accepted)
        thread = accepted.task.thread_id
        pending = client.get(f"/threads/{thread}/state").json()["pending"]
        body = {"interrupt_id": pending["interrupt_id"], "decisions": [{"type": "approve"}]}
        with client.stream("POST", f"/threads/{thread}/resume/unattended", json=body) as answer:
            events = sse_events(answer.iter_lines())
    assert replies(events)
    found = [reply for _, reply in replies(events)]
    # Freyja's reply after the action ran is it, and the rest is not.
    assert True in found
    assert set(found) <= {True, False}


def test_with_the_feature_off_no_event_has_the_key(
    make_a2a_server_app: Callable[..., ServerApp], sse_events: Any
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(), a2a_server=False)
    with (
        TestClient(made.app) as client,
        client.stream("POST", "/threads/k-1/runs", json={"message": "Hi."}) as answer,
    ):
        events = sse_events(answer.iter_lines())
    assert replies(events)
    assert {reply for _, reply in replies(events)} == {"absent"}


def test_a_caller_s_row_says_who_started_it_and_a_console_row_says_nobody(
    make_a2a_server_app: Callable[..., ServerApp], sse_events: Any
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(final_text="Hi."))
    with TestClient(made.app) as client:
        answered = as_json(call(client, made, lambda caller: caller.send("Hello from an agent.")))
        thread = made.task(client, answered["id"]).thread_id
        with client.stream("POST", "/threads/k-1/runs", json={"message": "From a person."}) as r:
            sse_events(r.iter_lines())
        listed = {row["thread_id"]: row for row in client.get("/threads").json()["threads"]}
        one = client.get(f"/threads/{thread}").json()
        console = client.get("/threads/k-1").json()
    local = {"name": "local", "canceled": False, "configured": True}
    assert listed[thread]["caller"] == one["caller"] == local
    assert one["first_message"] == "Hello from an agent."
    assert listed["k-1"]["caller"] is None
    assert console["caller"] is None


class UnreadableCallers(InMemoryReceivedTaskStore):
    async def callers_of(self, thread_ids: Any) -> Any:
        raise RuntimeError("the table is gone")


def test_the_rows_answer_500_when_who_started_them_cannot_be_read(
    make_a2a_server_app: Callable[..., ServerApp], sse_events: Any
) -> None:
    made = make_a2a_server_app(ScriptedChatModel(), received=UnreadableCallers())
    with TestClient(made.app) as client:
        with client.stream("POST", "/threads/k-1/runs", json={"message": "Hi."}) as answer:
            sse_events(answer.iter_lines())
        listed = client.get("/threads")
        one = client.get("/threads/k-1")
    assert listed.status_code == one.status_code == 500
    assert listed.json()["detail"]["code"] == "internal"


# Found by the review of request 2: each test failed before its fix.


def appended(events: list[dict]) -> list[str]:
    return [
        event["artifactUpdate"]["artifact"]["parts"][0]["text"]
        for event in events
        if "artifactUpdate" in event and event["artifactUpdate"].get("append")
    ]


def test_an_address_and_a_key_written_over_several_pieces_are_cleaned_in_them(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    address = f"http://127.0.0.1:{AGENT_PORT}"
    half = len(KEY) // 2
    pieces = [
        "Ask http://127.0.",
        f"0.1:{AGENT_PORT}/ with {KEY[:half]}",
        f"{KEY[half:]} now.",
    ]
    model = StreamingChatModel(final_text="".join(pieces), pieces=pieces)
    made = make_a2a_server_app(
        ScriptedChatModel(),
        agents={"stub": {"read_only": True, "url": address}},
        llm_profiles=InMemoryLlmProfileStore(),
        model_factory=lambda spec: model,
        secret_key=TEST_SECRET,
    )
    with TestClient(made.app) as client:
        saved = client.post("/llm/profiles", json=profile()).json()["id"]
        assert client.post(f"/llm/profiles/{saved}/activate").status_code == 200
        events = streamed(client, made, "Where?")
    sent = appended(events)
    assert sent == ["Ask ", "<address>/ with ", "<key> "]
    no_key([json.dumps(events)], MARK)
    no_address([json.dumps(events)], AGENT_PORT)
    assert events[-2]["artifactUpdate"]["artifact"]["parts"][0]["text"] == (
        "Ask <address>/ with <key> now."
    )


def test_a_reply_is_kept_cleaned_so_another_profile_activated_later_changes_nothing(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    models = ProfileModels()
    models.model("saved-model").final_text = f"Use {KEY} please."
    made = make_a2a_server_app(
        ScriptedChatModel(),
        llm_profiles=InMemoryLlmProfileStore(),
        model_factory=models,
        secret_key=TEST_SECRET,
    )
    with TestClient(made.app) as client:
        first = client.post("/llm/profiles", json=profile()).json()["id"]
        assert client.post(f"/llm/profiles/{first}/activate").status_code == 200
        answered = as_json(call(client, made, lambda caller: caller.send("Key?")))
        other = profile(name="Other", model="other-model", api_key="sk-recognizable-other-0002")
        second = client.post("/llm/profiles", json=other).json()["id"]
        assert client.post(f"/llm/profiles/{second}/activate").status_code == 200
        # A run on the other profile, so that this instance builds for it.
        call(client, made, lambda caller: caller.send("Hi.", context="c-other"))
        read = as_json(call(client, made, lambda caller: caller.get(answered["id"])))
        kept = made.task(client, answered["id"]).result
    assert read["artifacts"][0]["parts"][0]["text"] == "Use <key> please."
    assert kept == "Use <key> please."


def test_a_tool_the_catalog_does_not_have_is_not_named_as_the_model_wrote_it(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    odd = AIMessage(
        content="", tool_calls=[{"id": "x1", "name": "recognizable_made_up_tool", "args": {}}]
    )
    made = make_a2a_server_app(ScriptedChatModel(script=[odd], final_text="Done."))
    with TestClient(made.app) as client:
        events = streamed(client, made, "Go.")
    sentences = [text_of(event) for event in events if "statusUpdate" in event]
    assert sentences[:2] == [
        "Freyja is using: a tool Freyja does not have.",
        "Completed: a tool Freyja does not have.",
    ]
    assert "recognizable" not in json.dumps(events)


def test_the_service_stopping_stops_a_run_nobody_follows_at_its_next_step(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = LabelledChatModel(script=[ls("l1"), ls("l2"), ls("l3"), ls("l4")], final_text="Done.")
    made = make_a2a_server_app(model)
    model.hold()
    with TestClient(made.app) as client:
        answer = rpc(
            client, "SendMessage", {**sending("Go."), "configuration": {"returnImmediately": True}}
        ).json()
        assert answer["result"]["task"]["status"]["state"] == "TASK_STATE_WORKING"
        assert model._reached.wait(5)
        # The service stops while the model is called; the call returns once shutdown began.
        threading.Timer(0.3, model._release.set).start()
    # The step under way completed; no other started.
    assert model.calls == 1


def test_a_task_id_the_service_could_not_have_made_is_not_looked_up(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    store = Counting()
    looked: list[str] = []
    original = store.get

    async def get(task_id: str, caller: str | None = None) -> Any:
        looked.append(task_id)
        return await original(task_id, caller)

    store.get = get  # type: ignore[method-assign]
    made = make_a2a_server_app(ScriptedChatModel(), received=store)
    with TestClient(made.app) as client:
        found = [rpc(client, "GetTask", {"id": bad}).json() for bad in ("x\u0000y", "T" * 32)]
    assert [answer["error"]["code"] for answer in found] == [-32001, -32001]
    assert looked == []


def test_a_follower_waiting_on_a_person_is_woken_when_the_decision_s_run_ends(
    make_a2a_server_app: Callable[..., ServerApp],
    monkeypatch: pytest.MonkeyPatch,
    sse_events: Any,
) -> None:
    monkeypatch.setattr(received_module, "FOLLOW_SECONDS", 30)
    made = make_a2a_server_app(paused(), a2a_server_wait_seconds=0)
    with TestClient(made.app) as client:
        task_id = rpc(client, "SendMessage", sending("Tell them.")).json()["result"]["task"]["id"]
        at_rest(client, made, task_id)
        thread = made.task(client, task_id).thread_id

        async def follow() -> tuple[str, float]:
            # The task waits for a person: the follower has no run to read, and waits.
            async for change in made.service().follow(await made.received.get(task_id)):
                if isinstance(change, Ended):
                    return change.view.state, time.monotonic()
            raise AssertionError("the follower ended without the task")

        following = client.portal.start_task_soon(follow)
        time.sleep(0.3)
        pending = client.get(f"/threads/{thread}/state").json()["pending"]
        body = {"interrupt_id": pending["interrupt_id"], "decisions": [{"type": "approve"}]}
        with client.stream("POST", f"/threads/{thread}/resume/unattended", json=body) as answer:
            sse_events(answer.iter_lines())
        decided = time.monotonic()
        state, at = following.result(timeout=10)
    assert state == "completed"
    # Woken by the run's end, not at its next look 30 seconds later.
    assert at - decided < 5


def test_a_stream_closed_before_it_follows_leaves_the_run_going_on(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    model = LabelledChatModel(final_text="Done.")
    made = make_a2a_server_app(model)
    model.hold()
    with TestClient(made.app) as client:

        async def work(caller: StandInCaller) -> str:
            async for event in caller.stream("Go."):
                return event.task.id
            raise AssertionError("no event")

        task_id = call(client, made, work)
        with model.held():
            pass
        found = ended(client, made, task_id)
    assert found.state == "completed"
