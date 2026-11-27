"""Telemetry holds names and never content (``runtime/telemetry.py``).

Built like the other proofs that nothing gets out (``tests/nowhere.py``), for what a
collector is sent. With telemetry on, one service is driven through what a run can be: a
message that holds a recognizable key, a caller's token, and recognizable text; a gated call
of a tool server's tool with a recognizable argument, approved; a hand-off to an agent at a
recognizable address, approved, whose reply repeats the message; a tool that fails with its
reason; a tool a model made up; a model whose error repeats the key and the address; a
rejection with a reason; and a caller's task on the strict build, sent with the caller's
token and holding it. Then every span and every metric point is searched for each of them.

The second half does not search for what is known to be wrong but holds every attribute to
what is known to be right: each key is one of the module's, and each value is an id the
service made, a name the deployment configured, or one of the service's own words. A new
span, metric, or attribute is added to ``ALLOWED``, or the test fails.
"""

import re
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from freyja_agent.runtime import telemetry as names
from tests.a2a_requests import at_rest, rpc, sending
from tests.conftest import ServerApp
from tests.fakes import FailingChatModel
from tests.mcp_stub import make_stub
from tests.nowhere import no_address, no_key
from tests.telemetry_capture import Captured
from tests.unattended_helpers import stream

# The two made-up keys .gitleaks.toml lets through: an API key and a caller's token.
KEY = "sk-recognizable-key-0001"
CALLER_KEY = "sk-recognizable-other-0002"
MARK = "recognizable"
# In no name the service or the deployment gives anything: found, it is content.
CONTENT = "zebra"
# A port nothing else in the suite uses, searched for as the other proofs search.
AGENT_PORT = 9747
AGENT_URL = f"http://127.0.0.1:{AGENT_PORT}"
OUTSIDE = "https://freyja.example.com"

PROVIDER, MODEL = "anthropic", "claude-sonnet-5-5"
OUTCOMES = {"end", "interrupt", "error", "stopped", "cancelled"}
CODES = {"model_error", "no_usable_model", "step_limit", "internal", "approval_now_required"}
CLASSIFICATIONS = {"read_only", "workspace", "gated", "unknown"}
CALL_OUTCOMES = {"ok", "error", "interrupted", "cancelled"}
CLASS_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
HEX_ID = re.compile(r"[0-9a-f]{32}")


def call(name: str, call_id: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": call_id, "name": name, "args": args}])


def run(client: TestClient, thread: str, message: str) -> list[tuple[str, Any]]:
    return stream(client, f"/threads/{thread}/runs", {"message": message})


def decide(client: TestClient, thread: str, decision: dict[str, Any]) -> list[tuple[str, Any]]:
    pending = client.get(f"/threads/{thread}/state").json()["pending"]
    body = {"interrupt_id": pending["interrupt_id"], "decisions": [decision]}
    return stream(client, f"/threads/{thread}/resume", body)


def driven(make: Callable[..., ServerApp]) -> tuple[Captured, FailingChatModel, dict[str, Any]]:
    """One service with telemetry on, driven through every kind of run and call."""
    captured = Captured()
    model = FailingChatModel(
        script=[
            call("record_note", "c1", note=f"{CONTENT}-argument for {KEY}"),
            call("handoff_to_stub", "c2", message=f"Greet the {CONTENT}-handoff at {AGENT_URL}."),
            call("fail", "c3", reason=f"{CONTENT}-reason"),
            call(f"{CONTENT}_made_up_tool", "c4", detail=f"{CONTENT}-detail"),
            AIMessage(content=f"A {CONTENT}-reply."),
            call("send_notification", "c5", channel=f"#{CONTENT}", message=f"{CONTENT}-notice"),
            AIMessage(content=f"Not sent, {CONTENT}."),
            call("ls", "c6", path="/"),
        ],
        # The eighth call is the model's own failure, in a provider's words.
        fail_on_call=8,
        error=f"401: Incorrect API key provided: {KEY}. See {AGENT_URL}/v1 ({CONTENT}-error)",
        final_text=f"Hello, {CONTENT}-caller.",
    )
    server = make_stub("work")
    made = make(
        model,
        agents={"stub": {"url": AGENT_URL, "read_only": False}},
        servers={"work": {"command": "stub", "read_only": ["fail"]}},
        server_stubs={"work": server},
        a2a_server_callers={"alpha": {"token": CALLER_KEY}},
        a2a_server_host="127.0.0.1",
        a2a_server_port=8443,
        a2a_server_url=OUTSIDE,
        a2a_server_wait_seconds=0,
        telemetry=captured.telemetry,
    )
    seen: dict[str, Any] = {}
    with TestClient(made.app) as client:
        # A gated call of a tool server's tool, then a hand-off, each approved.
        first = run(client, "t-1", f"Note the {CONTENT}-message. My key is {KEY} and {CALLER_KEY}.")
        second = decide(client, "t-1", {"type": "approve"})
        third = decide(client, "t-1", {"type": "approve"})
        # A proposal rejected with a reason.
        fourth = run(client, "t-1", f"Tell the {CONTENT} channel.")
        fifth = decide(client, "t-1", {"type": "reject", "message": f"{CONTENT}-refusal"})
        # The model's own failure.
        sixth = run(client, "t-2", f"Another {CONTENT}-message.")
        # A caller's task, sent with its token and holding it.
        sent = rpc(
            client,
            "SendMessage",
            sending(f"A {CONTENT}-task. My token is {CALLER_KEY}.", contextId="c-1"),
            {"A2A-Version": "1.0", "Authorization": f"Bearer {CALLER_KEY}"},
        )
        task_id = sent.json()["result"]["task"]["id"]
        at_rest(client, made, task_id)
        seen["caller_thread"] = made.task(client, task_id).thread_id
        assert made.runtime.worker is not None
        client.portal.call(made.runtime.worker.tick)
        seen["texts"] = captured.texts()
        seen["catalog"] = {tool.name for tool in made.runtime.catalog}
    seen["ends"] = [events[-1] for events in (first, second, third, fourth, fifth, sixth)]
    seen["run_ids"] = [
        events[0][1]["run_id"] for events in (first, second, third, fourth, fifth, sixth)
    ]
    seen["notes"] = server.notes
    seen["agent"] = made.stub.record.messages
    return captured, model, seen


@pytest.fixture
def everything(
    make_a2a_server_app: Callable[..., ServerApp],
) -> tuple[Captured, FailingChatModel, dict[str, Any]]:
    return driven(make_a2a_server_app)


def test_what_was_driven_is_what_the_proof_is_about(
    everything: tuple[Captured, FailingChatModel, dict[str, Any]],
) -> None:
    """The search below means something only if all of it happened, and with the content."""
    captured, model, seen = everything

    kinds = [kind for kind, _data in seen["ends"]]
    assert kinds == ["interrupt", "interrupt", "end", "interrupt", "end", "error"]
    assert seen["ends"][-1][1]["code"] == "model_error"
    # The content reached the tool server, the agent, and the model: it was there to leak.
    assert seen["notes"] == [{"note": f"{CONTENT}-argument for {KEY}", "priority": 0}]
    assert len(seen["agent"]) == 1
    humans = [
        str(message.content)
        for messages in model.seen
        for message in messages
        if isinstance(message, HumanMessage)
    ]
    assert any(KEY in text and CALLER_KEY in text for text in humans)
    results = [
        str(message.content)
        for messages in model.seen
        for message in messages
        if isinstance(message, ToolMessage)
    ]
    assert any(f"{CONTENT}-reason" in text for text in results)
    assert any(f"{CONTENT}-handoff" in text for text in results)
    # And every kind of span and metric was recorded.
    runs = captured.attributes(names.RUN_SPAN)
    assert {found[names.RUN_MODE] for found in runs} == {"run", "resume"}
    assert {found[names.RUN_OUTCOME] for found in runs} == {"end", "interrupt", "error"}
    assert {found[names.RUN_STRICT] for found in runs} == {True, False}
    assert {found[names.RUN_CONSOLE] for found in runs} == {True, False}
    tools = {found[names.TOOL_NAME] for found in captured.attributes(names.TOOL_SPAN)}
    assert tools == {"record_note", "handoff_to_stub", "fail", "unknown", "ls"}
    assert {found[names.CALL_OUTCOME] for found in captured.attributes(names.MODEL_SPAN)} == {
        "ok",
        "error",
    }
    metrics = {point.metric for point in captured.points()}
    assert metrics == {
        names.RUNS_STARTED,
        names.RUNS_ENDED,
        names.RUN_DURATION,
        names.MODEL_CALLS,
        names.MODEL_ERRORS,
        names.TOOL_CALLS,
        names.WORKER_ROUND_DURATION,
        names.WORKER_THREADS_WAITING,
    }


def test_no_key_token_address_or_text_of_a_run_is_in_any_span_or_metric(
    everything: tuple[Captured, FailingChatModel, dict[str, Any]],
) -> None:
    _captured, _model, seen = everything
    texts = seen["texts"]

    assert len(texts) > 100
    # The key and the caller's token.
    no_key(texts, MARK, "sk-")
    # The agent's address.
    no_address(texts, AGENT_PORT)
    # A message, an argument, a result, a reply, a reason, an error's text, a made-up name.
    no_key(texts, CONTENT)
    # The other address the service knows: where callers reach it.
    no_key(texts, "freyja.example.com")


def allowed(seen: dict[str, Any]) -> dict[str, Callable[[Any], bool]]:
    """What each attribute may be: an id the service made, a configured name, or one of the
    service's own words. A new attribute is added here, with the rule that keeps it a name."""
    threads = {"t-1", "t-2", seen["caller_thread"]}
    tools = seen["catalog"] | {"unknown"}
    return {
        names.RUN_ID: lambda value: bool(HEX_ID.fullmatch(value)),
        names.THREAD_ID: lambda value: value in threads,
        names.RUN_MODE: lambda value: value in {"run", "resume", "background"},
        names.RUN_CONSOLE: lambda value: isinstance(value, bool),
        names.RUN_STRICT: lambda value: isinstance(value, bool),
        names.RUN_OUTCOME: lambda value: value in OUTCOMES,
        names.ERROR_CODE: lambda value: value in CODES,
        names.PROFILE_ID: lambda value: value == "environment",
        names.PROFILE_SOURCE: lambda value: value in {"environment", "saved"},
        names.MODEL_PROVIDER: lambda value: value == PROVIDER,
        names.MODEL_NAME: lambda value: value == MODEL,
        names.MODEL_CALLER: lambda value: value in {"agent", "library", "test"},
        names.TOOL_NAME: lambda value: value in tools,
        names.TOOL_CLASSIFICATION: lambda value: value in CLASSIFICATIONS,
        names.TOOL_SERVER: lambda value: value == "work",
        names.TOOL_AGENT: lambda value: value == "stub",
        names.CALL_OUTCOME: lambda value: value in CALL_OUTCOMES,
        names.ERROR_TYPE: lambda value: bool(CLASS_NAME.fullmatch(value)),
    }


def test_every_attribute_is_one_the_proof_knows_and_holds_a_name(
    everything: tuple[Captured, FailingChatModel, dict[str, Any]],
) -> None:
    captured, _model, seen = everything
    rules = allowed(seen)

    found: list[tuple[str, str, Any]] = []
    for span in captured.spans():
        assert span.name in {names.RUN_SPAN, names.MODEL_SPAN, names.TOOL_SPAN}
        assert not span.events and not span.links
        # A status says nothing but one of the service's codes or an exception's class.
        description = span.status.description
        assert description is None or description in CODES or CLASS_NAME.fullmatch(description)
        found.extend((span.name, key, value) for key, value in (span.attributes or {}).items())
        assert dict(span.resource.attributes) == {"service.name": "freyja-test"}
    for point in captured.points():
        # A count is kept by names alone: an id would make one series per run.
        assert names.RUN_ID not in point.attributes and names.THREAD_ID not in point.attributes
        found.extend((point.metric, key, value) for key, value in point.attributes.items())

    assert found
    for where, key, value in found:
        assert key in rules, f"{where} has an attribute this proof does not know: {key}"
        assert rules[key](value), f"{where}: {key} holds {value!r}"
    # Every rule was used, so none is left over from an attribute that no longer exists.
    assert {key for _where, key, _value in found} == set(rules)
    # The run ids are the ones the streams named.
    recorded = {value for _where, key, value in found if key == names.RUN_ID}
    assert set(seen["run_ids"]) <= recorded
