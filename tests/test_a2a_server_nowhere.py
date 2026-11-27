"""009: what a caller is never given (FR-018, FR-028).

Part 1, the alias (research R6): on a caller's thread the model, and the subagent's, is told
``c-`` and the first 16 hexadecimal characters of the SHA-256 of the thread's id in place of
the id, so a caller that asks Freyja for its thread's id learns nothing it can use. A console
thread's model is told the id, as before.

Part 2, no token anywhere (FR-028, SC-006): with two named callers' recognizable tokens,
through every operation and refusal at both addresses, nothing the service answers, streams,
keeps, or logs holds either; and refused credentials are warned of sparingly.

Part 3, what a caller is never given (FR-018, FR-019, SC-008): two named callers, one at each
address, through every operation and refusal and through tasks that complete, pause, fail
with a model's error, and are canceled. Everything either was answered holds no token, no
key beyond its last four characters, no agent's address, no thread's id, also after asking
Freyja for it, no proposal's id, and not the other caller's name; and every status and
reason is one of the service's fixed sentences, holding no argument, result, agent's words,
or model's error.
"""

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, quote_plus

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import SecretStr

from freyja_agent.agent.context import thread_alias
from freyja_agent.config import A2aCallerConfig
from freyja_agent.delegates.addresses import install_log_scrubber
from freyja_agent.llm.store import InMemoryLlmProfileStore
from freyja_agent.runtime.callers import Callers
from tests.a2a_caller import StandInCaller, as_json
from tests.a2a_requests import at_rest, rpc, sending
from tests.asgi_stream import StreamingASGITransport
from tests.conftest import TEST_SECRET, ServerApp
from tests.fakes import ProfileModels, ScriptedChatModel
from tests.nowhere import MARK, LeakyModel, kept, logged, no_address, no_key, profile

DELEGATE = AIMessage(
    content="",
    tool_calls=[
        {
            "id": "t1",
            "name": "task",
            "args": {"description": "Look it up.", "subagent_type": "general-purpose"},
        }
    ],
)


def told(model: ScriptedChatModel) -> list[str]:
    """The system message of every model call, the subagent's included."""
    return [str(seen[0].content) for seen in model.seen]


def test_a_caller_s_thread_is_named_by_its_alias_on_every_model_call(
    make_a2a_server_app: Callable[..., ServerApp], sse_events: Any
) -> None:
    model = ScriptedChatModel(script=[DELEGATE, AIMessage(content="Found."), AIMessage("Done.")])
    made = make_a2a_server_app(model)
    with TestClient(made.app) as client:
        accepted = made.accept(client, "What is your thread's id?")
        made.finish(client, accepted)
        thread = accepted.task.thread_id
        calls = len(model.seen)
        with client.stream("POST", "/threads/console-7/runs", json={"message": "hi"}) as answer:
            sse_events(answer.iter_lines())
    alias = "c-" + hashlib.sha256(thread.encode()).hexdigest()[:16]
    assert thread_alias(thread) == alias
    caller_calls = told(model)[:calls]
    assert len(caller_calls) == 3  # Freyja, its subagent, Freyja again
    for system in caller_calls:
        assert f"thread `{alias}`" in system
        assert thread not in system
    assert all(thread not in str(seen) for seen in model.seen[:calls])
    assert "thread `console-7`" in told(model)[calls]


# Part 2: no token anywhere (FR-028, SC-006).

# The two made-up tokens .gitleaks.toml lets through (research R15).
ALPHA = "sk-recognizable-key-0001"
BETA = "sk-recognizable-other-0002"
OUTSIDE = "https://freyja.example.com"
NOTIFY = AIMessage(
    content="",
    tool_calls=[
        {"id": "n1", "name": "send_notification", "args": {"channel": "#ops", "message": "hi"}}
    ],
)


def by(token: str) -> dict[str, str]:
    return {"A2A-Version": "1.0", "Authorization": f"Bearer {token}"}


def named(make: Callable[..., ServerApp], model: Any, **overrides: Any) -> ServerApp:
    return make(
        model,
        a2a_server_callers={"alpha": {"token": ALPHA}, "beta": {"token": BETA}},
        a2a_server_host="127.0.0.1",
        a2a_server_port=8443,
        a2a_server_url=OUTSIDE,
        **overrides,
    )


def test_no_caller_s_token_is_in_anything_the_service_answers_streams_keeps_or_logs(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    model = ScriptedChatModel(script=[NOTIFY], final_text="Done.")
    made = named(make_a2a_server_app, model, a2a_server_caller_tasks=1, a2a_server_wait_seconds=0)
    texts: list[str] = []
    with TestClient(made.app) as client:

        def own(method: str, params: dict[str, Any], token: str | None) -> Any:
            headers = by(token) if token else {"A2A-Version": "1.0"}
            answer = rpc(client, method, params, headers)
            texts.append(answer.text)
            return answer

        async def outside(work: Callable[[StandInCaller], Any], token: str) -> Any:
            transport = StreamingASGITransport(made.outside)
            async with StandInCaller(OUTSIDE, transport=transport, token=token) as caller:
                return await work(caller)

        # A task waiting for a person, sent with the token pasted into the message.
        paused = own(
            "SendMessage", sending(f"Tell #ops. My token is {ALPHA}.", contextId="c-1"), ALPHA
        )
        task_id = paused.json()["result"]["task"]["id"]
        at_rest(client, made, task_id)
        # A refused limit, a read, a subscription that is refused, and a cancel.
        own("SendMessage", sending("Another.", contextId="c-2"), ALPHA)
        own("GetTask", {"id": task_id}, ALPHA)
        own("CancelTask", {"id": task_id}, ALPHA)
        own("SubscribeToTask", {"id": task_id}, ALPHA)
        own("CancelTask", {"id": task_id}, ALPHA)
        own("GetTask", {"id": task_id}, BETA)
        own("SendMessage", sending("Wrong."), "sk-recognizable-wrong-0003")
        own("SendMessage", sending("Local."), None)
        at_rest(client, made, task_id)
        # Through the outside listener: a stream, a card, and refusals.
        events = client.portal.call(
            outside,
            lambda caller: _all(caller.stream("Say hello.", context="c-9")),
            BETA,
        )
        texts.extend(json.dumps(event) for event in events)

        async def raw() -> list[str]:
            transport = StreamingASGITransport(made.outside)
            async with httpx.AsyncClient(transport=transport, base_url=OUTSIDE) as http:
                found = [
                    await http.get("/.well-known/agent-card.json"),
                    await http.post(
                        "/a2a",
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "GetTask",
                            "params": {"id": task_id},
                        },
                        headers=by("sk-recognizable-wrong-0003"),
                    ),
                    await http.post(f"/a2a?token={ALPHA}", json={}, headers={"A2A-Version": "1.0"}),
                ]
                return [answer.text for answer in found]

        texts.extend(client.portal.call(raw))
        thread = made.task(client, task_id).thread_id
        for path in ("/threads", f"/threads/{thread}", f"/threads/{thread}/state"):
            texts.append(client.get(path).text)
        # A line of the web server's access log, as uvicorn writes one.
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d', "127.0.0.1:50000", "POST", f"/a2a?token={ALPHA}", "1.1", 401
        )
    told = [
        str(message.content)
        for seen in model.seen
        for message in seen
        if isinstance(message, HumanMessage)
    ]
    assert any("My token is" in text for text in told)
    lines = logged(caplog)
    no_key(texts + kept(made.a2a.saver) + lines + told, ALPHA, BETA)
    access = [line for line in lines if "/a2a?token=" in line]
    assert access, "the access log line was not written"
    # One line each, naming the caller.
    assert any(f"Caller alpha: received task {task_id} accepted" in line for line in lines)
    assert any(f"Caller alpha canceled received task {task_id}" in line for line in lines)
    assert any("caller alpha was refused" in line for line in lines)
    assert any("refused for its credential" in line for line in lines)


async def _all(events: Any) -> list[dict]:
    return [as_json(event) async for event in events]


def test_refused_credentials_are_warned_of_once_then_at_most_once_in_ten_seconds_for_each_address(
    make_a2a_server_app: Callable[..., ServerApp], caplog: pytest.LogCaptureFixture
) -> None:
    made = named(make_a2a_server_app, ScriptedChatModel())
    now = [1000.0]
    with TestClient(made.app) as client:
        made.callers.clock = lambda: now[0]

        async def outside_refused() -> None:
            transport = StreamingASGITransport(made.outside)
            async with httpx.AsyncClient(transport=transport, base_url=OUTSIDE) as http:
                await http.post("/a2a", json={}, headers={"A2A-Version": "1.0"})

        with caplog.at_level(logging.WARNING, logger="freyja_agent"):
            for _ in range(100):
                rpc(client, "GetTask", {"id": "0" * 32}, by("sk-recognizable-wrong-0003"))
            first = warnings(caplog)
            for _ in range(100):
                client.portal.call(outside_refused)
            both = warnings(caplog)
            now[0] += 10
            rpc(client, "GetTask", {"id": "0" * 32}, by("sk-recognizable-wrong-0003"))
            client.portal.call(outside_refused)
            later = warnings(caplog)
    assert len(first) == 1 and "the service's own address" in first[0]
    assert len(both) == 2 and "the outside listener" in both[1]
    assert len(later) == 4
    assert "100 requests" in later[2] and "the service's own address" in later[2]
    assert "100 requests" in later[3] and "the outside listener" in later[3]


def warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """The warnings of refused credentials, in order."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and "credential" in record.getMessage()
    ]


def _lower_hex(text: str) -> str:
    return re.sub(r"%[0-9A-F]{2}", lambda found: found.group(0).lower(), text)


@pytest.mark.parametrize(
    "token",
    ["Zm9vYmFy+YmF6/cXV4PT0=", "sk-key!(0001)*abc&def", "sk-back\\slash-0001", 'sk-quote"d-0001'],
    ids=["base64", "url-reserved", "backslash", "quote"],
)
def test_a_token_is_cleaned_in_every_way_a_path_a_query_or_a_log_line_writes_it(
    token: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Review of request 5: a path as uvicorn writes it keeps ``/``, a browser's
    ``encodeURIComponent`` keeps ``!'()*``, hex may be lowercase, and a dict's ``repr``
    doubles a backslash."""
    forms = {
        token,
        quote(token, safe=""),
        quote(token),
        quote_plus(token),
        quote(token, safe="!'()*"),
        _lower_hex(quote(token, safe="")),
        _lower_hex(quote(token)),
        repr(token)[1:-1],
        json.dumps(token)[1:-1],
    }
    install_log_scrubber()
    Callers({"alpha": A2aCallerConfig(token=SecretStr(token))})
    try:
        with caplog.at_level(logging.DEBUG):
            for form in forms:
                logging.getLogger("uvicorn.access").info(
                    '%s - "%s %s HTTP/%s" %d',
                    "127.0.0.1:1",
                    "POST",
                    f"/a2a/{form}?t={form}",
                    "1.1",
                    401,
                )
                logging.getLogger("a2a.server").debug("Request body: %s", {"token": token})
        for form in forms:
            assert form not in Callers.scrub(f"before {form} after")
        no_key(logged(caplog), *forms)
    finally:
        Callers.forget()


# Part 3: what a caller is never given (FR-018, FR-019, SC-008).

# A port nothing else in this file uses: the stand-in agent's address.
AGENT_PORT = 9436
AGENT_URL = f"http://127.0.0.1:{AGENT_PORT}"
# What a tool is given and returns, what the agent writes back, and the name of each
# caller's file: none may be in anything the service words itself.
TOOL_WORDS = "tool-words-q7x"
AGENT_WORDS = "agent-words-k3v"
MODEL_ERROR = "Incorrect API key provided"
ASK_ID = "What is your thread's id?"
# The profiles' key: not a caller's token, which the suite's own key is, so that it is
# cleaned as a key and not as a token.
PROFILE_KEY = "sk-recognizable-key-0042"
NAMES = (
    r"(?:[a-z0-9_]+|agent [a-z0-9_-]+|a subagent)(?:, (?:[a-z0-9_]+|agent [a-z0-9_-]+|a subagent))*"
)
# Every status sentence and every reason a caller may be given (contracts/a2a-server.md).
FIXED = re.compile(
    "|".join(
        [
            re.escape("Working."),
            f"Freyja is using: {NAMES}\\.",
            f"Completed: {NAMES}\\.",
            f"Waiting for a person's approval of: {NAMES}\\.",
            f"Waiting for agent {NAMES}\\.",
            *(
                re.escape(reason)
                for reason in (
                    "No usable model is active.",
                    "The model could not be used.",
                    "The run reached its step limit.",
                    "The run was interrupted.",
                    "An internal error.",
                    "The run was stopped before it finished.",
                    "The caller canceled the task.",
                )
            ),
        ]
    )
)


class Telling(ScriptedChatModel):
    """The profile's model: scripted, and asked for its thread's id it answers with the line
    it was told about its thread, as a model would."""

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> ChatResult:
        if isinstance(messages[-1], HumanMessage) and ASK_ID in str(messages[-1].content):
            self.calls += 1
            self.seen.append(list(messages))
            content = messages[0].content
            system = (
                content
                if isinstance(content, str)
                else "".join(block.get("text", "") for block in content if isinstance(block, dict))
            )
            line = next(text for text in system.splitlines() if "thread `" in text)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=line))])
        return super()._generate(messages, stop, run_manager, **kw)


def tool_calls(*found: tuple[str, str, dict[str, Any]]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": name, "args": args} for name, call_id, args in found],
    )


def one_task_s_script(name: str) -> list[AIMessage]:
    """What the model does for one caller: a task that writes and reads a file and hands
    work to the agent, then completes with a reply that names a key and an address; then a
    task that proposes a notification."""
    path = f"/{name}-only-z5.txt"
    return [
        tool_calls(("write_file", f"{name}-w", {"file_path": path, "content": TOOL_WORDS})),
        tool_calls(
            ("read_file", f"{name}-r", {"file_path": path}),
            ("ls", f"{name}-l", {"path": "/"}),
            ("handoff_to_stub", f"{name}-h", {"message": f"Look into {AGENT_WORDS}."}),
        ),
        AIMessage(content=f"Noted. Ask {AGENT_URL}/ with {PROFILE_KEY} as before."),
        tool_calls(("send_notification", f"{name}-n", {"channel": "#ops", "message": TOOL_WORDS})),
    ]


class Answered:
    """One named caller's raw JSON-RPC requests at one address, and everything it was
    answered, each answer and each stream as its text."""

    def __init__(self, app: Any, base: str, token: str) -> None:
        self.app, self.base, self.token = app, base, token
        self.texts: list[str] = []
        self.answers: list[Any] = []

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=StreamingASGITransport(self.app), base_url=self.base)

    async def card(self) -> None:
        async with self._http() as http:
            answer = await http.get("/.well-known/agent-card.json")
        self.texts.append(answer.text)

    async def rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        async with self._http() as http:
            answer = await http.post("/a2a", json=body, headers=by(self.token))
        self.texts.append(answer.text)
        self.answers.append(answer.json())
        return answer.json()

    async def stream(
        self, method: str, params: dict[str, Any], first: asyncio.Event | None = None
    ) -> list[dict[str, Any]]:
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        events: list[dict[str, Any]] = []
        lines: list[str] = []
        async with (
            self._http() as http,
            http.stream("POST", "/a2a", json=body, headers=by(self.token)) as answer,
        ):
            async for line in answer.aiter_lines():
                lines.append(line)
                if line.startswith("data:"):
                    events.append(json.loads(line.removeprefix("data:")))
                    if first is not None:
                        first.set()
                elif line.startswith("{"):
                    events.append(json.loads(line))
        self.texts.append("\n".join(lines))
        self.answers.extend(events)
        return events


def statuses(value: Any) -> list[str]:
    """The status messages alone: sentences and reasons."""
    if isinstance(value, dict):
        status = value.get("status")
        own = []
        if isinstance(status, dict):
            own = [part.get("text", "") for part in (status.get("message") or {}).get("parts", [])]
        return own + [text for item in value.values() for text in statuses(item)]
    if isinstance(value, list):
        return [text for item in value for text in statuses(item)]
    return []


def task_of(answer: dict[str, Any]) -> dict[str, Any]:
    """The task of an answer, or of a stream's event: a task or a status update."""
    result = answer["result"]
    return result.get("task") or result.get("statusUpdate") or result


def test_a_caller_is_given_nothing_of_what_it_must_never_be_given(
    make_a2a_server_app: Callable[..., ServerApp], tmp_path_factory: pytest.TempPathFactory
) -> None:
    files = tmp_path_factory.mktemp("caller-files")
    (files / "beta").mkdir()
    (files / "beta" / "beta-own-z5.txt").write_text(TOOL_WORDS)
    models = ProfileModels()
    models.models["saved-model"] = Telling(
        script=[*one_task_s_script("alpha"), *one_task_s_script("beta")], final_text="Done."
    )
    models.models["leaky-model"] = LeakyModel()
    made = named(
        make_a2a_server_app,
        ScriptedChatModel(),
        agents={"stub": {"read_only": True, "url": AGENT_URL}},
        llm_profiles=InMemoryLlmProfileStore(),
        model_factory=models,
        secret_key=TEST_SECRET,
        a2a_server_files_dir=str(files),
        a2a_server_caller_tasks=1,
        a2a_server_wait_seconds=10,
    )
    tasks: dict[str, list[str]] = {"alpha": [], "beta": []}
    proposals: list[str] = []
    replies: dict[str, str] = {}
    with TestClient(made.app) as client:
        saved = client.post("/llm/profiles", json=profile(api_key=PROFILE_KEY)).json()["id"]
        leaky = client.post(
            "/llm/profiles", json=profile(name="Leaky", model="leaky-model", api_key=PROFILE_KEY)
        )
        assert client.post(f"/llm/profiles/{saved}/activate").status_code == 200
        callers = {
            # One at the outside listener, the other at the service's own address.
            "alpha": Answered(made.outside, OUTSIDE, ALPHA),
            "beta": Answered(made.app, "http://testserver", BETA),
        }
        for name, caller in callers.items():
            go = client.portal.call
            go(caller.card)
            # A task that completes, sent with a stream, then read, and refused a
            # subscription and a cancel since it has ended.
            events = go(caller.stream, "SendStreamingMessage", sending("Write it.", contextId="w"))
            done = events[0]["result"]["task"]["id"]
            tasks[name].append(done)
            assert task_of(events[-1])["status"]["state"] == "TASK_STATE_COMPLETED", events[-1]
            got = go(caller.rpc, "GetTask", {"id": done})
            replies[name] = got["result"]["artifacts"][0]["parts"][0]["text"]
            assert go(caller.rpc, "SubscribeToTask", {"id": done})["error"]["code"] == -32004
            assert go(caller.rpc, "CancelTask", {"id": done})["error"]["code"] == -32002
            # A task that pauses on a proposal; the conversation and the limit refuse more.
            paused = go(
                caller.rpc,
                "SendMessage",
                {
                    **sending("Tell #ops.", contextId="p"),
                    "configuration": {"returnImmediately": True},
                },
            )
            paused_id = task_of(paused)["id"]
            tasks[name].append(paused_id)
            at_rest(client, made, paused_id)
            thread = made.task(client, paused_id).thread_id
            proposals.append(
                client.get(f"/threads/{thread}/state").json()["pending"]["interrupt_id"]
            )
            got = go(caller.rpc, "GetTask", {"id": paused_id})
            assert "send_notification" in statuses(got)[0]
            busy = go(caller.rpc, "SendMessage", sending("More.", contextId="p"))
            assert busy["error"]["data"][0]["metadata"] == {"reason": "conversation_busy"}
            limit = go(caller.rpc, "SendMessage", sending("Other.", contextId="o"))
            assert limit["error"]["data"][0]["metadata"] == {"reason": "task_limit"}

            # Followed by a subscription, and canceled by its caller.
            async def follow_and_cancel(
                caller: Answered = caller, paused_id: str = paused_id
            ) -> list[dict[str, Any]]:
                first = asyncio.Event()
                follower = asyncio.create_task(
                    caller.stream("SubscribeToTask", {"id": paused_id}, first)
                )
                async with asyncio.timeout(10):
                    await first.wait()
                    await caller.rpc("CancelTask", {"id": paused_id})
                    return await follower

            followed = go(follow_and_cancel)
            assert task_of(followed[-1])["status"]["state"] == "TASK_STATE_CANCELED"
            # A task that fails with a model's error that holds a recognizable text.
            assert client.post(f"/llm/profiles/{leaky.json()['id']}/activate").status_code == 200
            failed = go(caller.rpc, "SendMessage", sending("This one fails.", contextId="f"))
            assert client.post(f"/llm/profiles/{saved}/activate").status_code == 200
            tasks[name].append(task_of(failed)["id"])
            assert statuses(failed) == ["The model could not be used."]
            # Asked for its thread's id, Freyja says what it was told.
            asked = go(caller.rpc, "SendMessage", sending(ASK_ID, contextId="q"))
            tasks[name].append(task_of(asked)["id"])
            answer = task_of(asked)["artifacts"][0]["parts"][0]["text"]
            alias = thread_alias(made.task(client, task_of(asked)["id"]).thread_id)
            assert answer.startswith(f"You are working on thread `{alias}`."), answer
            # The refusals of a message itself, and the operations that are not offered.
            for params in (
                sending("hi", metadata={"freyja.hops": -1}),
                sending("hi", metadata={"freyja.hops": 3}),
                sending(" "),
                sending("hi", taskId=done),
            ):
                assert go(caller.rpc, "SendMessage", params)["error"]["code"] == -32602
            go(caller.stream, "SendStreamingMessage", sending("hi", metadata={"freyja.hops": 3}))
            go(caller.rpc, "ListTasks", {})
            go(caller.rpc, "CreateTaskPushNotificationConfig", {"taskId": done, "url": OUTSIDE})
            go(caller.rpc, "GetExtendedAgentCard", {})
        # Each caller asks for the other's tasks.
        for name, other in (("alpha", "beta"), ("beta", "alpha")):
            for task_id in tasks[other]:
                for method in ("GetTask", "CancelTask", "SubscribeToTask"):
                    found = client.portal.call(callers[name].rpc, method, {"id": task_id})
                    assert found["error"]["code"] == -32001
        threads = [made.task(client, t).thread_id for found in tasks.values() for t in found]
    # The completed task's reply names the key and the address only as cleaned.
    assert replies == dict.fromkeys(callers, "Noted. Ask <address>/ with <key> as before.")
    # The runs were shown every text the searches look for: what a tool returned, a listing
    # of each caller's files, and what the agent wrote back.
    shown = {
        message.tool_call_id: str(message.content)
        for seen in models.models["saved-model"].seen
        for message in seen
        if isinstance(message, ToolMessage)
    }
    assert TOOL_WORDS in shown["alpha-r"] and TOOL_WORDS in shown["beta-r"]
    assert "alpha-only-z5.txt" in shown["alpha-l"]
    assert "beta-own-z5.txt" in shown["beta-l"] and "beta-only-z5.txt" in shown["beta-l"]
    assert AGENT_WORDS in shown["alpha-h"] and AGENT_WORDS in shown["beta-h"]
    assert len(made.stub.record.messages) == 2
    assert all(AGENT_WORDS in message.text for message in made.stub.record.messages)
    assert (files / "alpha" / "alpha-only-z5.txt").read_text() == TOOL_WORDS
    assert len(set(threads)) == 8
    for name, other in (("alpha", "beta"), ("beta", "alpha")):
        texts = callers[name].texts
        no_key(texts, MARK, MODEL_ERROR, *threads, *proposals, other)
        no_address(texts, AGENT_PORT)
        # What a tool was given and returned, file names a listing returned, and what the
        # agent wrote are in nothing a caller was sent: no status, reason, error, piece of the
        # reply, or reply.
        no_key(texts, TOOL_WORDS, AGENT_WORDS, "only-z5", "own-z5", "Look into")
        sentences = [text for answer in callers[name].answers for text in statuses(answer)]
        assert sentences, name
        for sentence in sentences:
            assert FIXED.fullmatch(sentence), sentence
