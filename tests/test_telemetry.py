"""Telemetry: the spans and counts of a run, off by default (``runtime/telemetry.py``).

Part 1, off: without ``FREYJA_OTEL_ENDPOINT`` nothing of OpenTelemetry's SDK is made, no
thread is started, and the graph is the one that runs with telemetry on.

Part 2, the two settings.

Part 3, what is recorded: one span per run, per model call, and per tool call, under one
trace, with names and outcomes for attributes; and the counts and durations.

Part 4, what telemetry leaves alone: the process's own providers, OpenTelemetry's current
span, and so everything a model or a tool server is sent.

Part 5, the collector: what the settings configure is sent to the endpoint's two paths when
the service stops, whatever the ``OTEL_*`` variables say.

That none of it is content is ``tests/test_telemetry_nowhere.py``'s to show.
"""

import asyncio
import logging
import re
import threading
import time
from collections.abc import AsyncIterator, Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.errors import GraphInterrupt, ParentCommand
from langgraph.types import Command
from opentelemetry import metrics as metrics_api
from opentelemetry import propagate
from opentelemetry import trace as trace_api
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.trace import SpanKind, StatusCode
from pydantic import Field, ValidationError

from freyja_agent.agent.telemetry import TelemetryMiddleware
from freyja_agent.api import streaming
from freyja_agent.config import Settings
from freyja_agent.llm.guard import ModelError, guard_model
from freyja_agent.runtime import telemetry as telemetry_module
from freyja_agent.runtime.runtime import Runtime
from freyja_agent.runtime.telemetry import (
    CALL_OUTCOME,
    ERROR_CODE,
    ERROR_TYPE,
    MODEL_CALLER,
    MODEL_CALLS,
    MODEL_ERRORS,
    MODEL_NAME,
    MODEL_PROVIDER,
    MODEL_SPAN,
    PROFILE_ID,
    PROFILE_SOURCE,
    RUN_CONSOLE,
    RUN_DURATION,
    RUN_ID,
    RUN_MODE,
    RUN_OUTCOME,
    RUN_SPAN,
    RUN_STRICT,
    RUNS_ENDED,
    RUNS_STARTED,
    THREAD_ID,
    TOOL_AGENT,
    TOOL_CALLS,
    TOOL_CLASSIFICATION,
    TOOL_NAME,
    TOOL_SERVER,
    TOOL_SPAN,
    WORKER_ROUND_DURATION,
    WORKER_THREADS_WAITING,
    Telemetry,
    ToolLabels,
)
from tests.conftest import A2aApp, LlmApp, McpApp, ServerApp
from tests.fakes import (
    FailingChatModel,
    LabelledChatModel,
    LoopingChatModel,
    ScriptedChatModel,
    StreamingChatModel,
)
from tests.mcp_stub import make_stub
from tests.telemetry_capture import SERVICE, Captured
from tests.unattended_helpers import Reader, idle, ls
from tests.unattended_helpers import stream as read

Events = list[tuple[str, Any]]
SRC = Path(__file__).resolve().parent.parent / "src" / "freyja_agent"
CONSOLE_RUN = "/threads/{}/runs/unattended"
NOTIFY = AIMessage(
    content="",
    tool_calls=[
        {"id": "n1", "name": "send_notification", "args": {"channel": "#ops", "message": "hi"}}
    ],
)
TASK = AIMessage(
    content="",
    tool_calls=[
        {
            "id": "t1",
            "name": "task",
            "args": {"description": "Look it up.", "subagent_type": "general-purpose"},
        }
    ],
)
# The environment's model as the fixtures leave it: FREYJA_MODEL's default.
PROVIDER, MODEL = "anthropic", "claude-sonnet-5-5"


def call(name: str, call_id: str = "c1", **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": call_id, "name": name, "args": args}])


def run(client: TestClient, thread: str, message: str = "go") -> Events:
    return read(client, f"/threads/{thread}/runs", {"message": message})


def resume(client: TestClient, thread: str, decision: dict[str, Any]) -> Events:
    pending = client.get(f"/threads/{thread}/state").json()["pending"]
    body = {"interrupt_id": pending["interrupt_id"], "decisions": [decision]}
    return read(client, f"/threads/{thread}/resume", body)


def one(spans: list[ReadableSpan]) -> ReadableSpan:
    assert len(spans) == 1, [span.name for span in spans]
    return spans[0]


def attributes(span: ReadableSpan) -> dict[str, Any]:
    return dict(span.attributes or {})


def one_point(captured: Captured, metric: str) -> Any:
    points = captured.points(metric)
    assert len(points) == 1, points
    return points[0]


def parent_of(span: ReadableSpan) -> int | None:
    return span.parent.span_id if span.parent is not None else None


def otel_threads() -> list[str]:
    return [thread.name for thread in threading.enumerate() if thread.name.startswith("Otel")]


# Part 1: off.


def test_telemetry_is_off_unless_a_collector_is_named() -> None:
    settings = Settings(_env_file=None)

    assert settings.otel_endpoint is None
    assert settings.otel_service_name == "freyja"
    assert Telemetry.configured(None, "freyja").on is False


def test_off_nothing_of_the_sdk_is_made_and_no_thread_is_started(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    made = make_mcp_app(ScriptedChatModel(script=[ls("l1")], final_text="Done."), {})
    with TestClient(made.app) as client:
        events = run(client, "t-1")
        telemetry = made.runtime.telemetry
        threads = otel_threads()

    assert events[-1] == ("end", {"status": "completed"})
    assert telemetry.on is False
    # The API's own tracer and meter, which record nothing.
    assert type(telemetry.tracer) is trace_api.NoOpTracer
    assert type(telemetry.meter) is metrics_api.NoOpMeter
    assert threads == []
    # Shutting it down has nothing to do.
    telemetry.close()


def test_the_graph_holds_the_recording_middleware_whether_telemetry_is_on_or_off(
    make_runtime: Callable[..., Runtime],
) -> None:
    """One code path (constitution principle III): off is a tracer that does nothing."""

    def hooks(runtime: Runtime) -> list[str]:
        return sorted(name for name in runtime.agent.nodes if "Telemetry" in name)

    off = make_runtime(ScriptedChatModel())
    on = make_runtime(ScriptedChatModel(), telemetry=Captured().telemetry)

    # It wraps calls and adds no step of its own, so no node is its.
    assert hooks(off) == hooks(on) == []
    assert sorted(off.agent.nodes) == sorted(on.agent.nodes)


# Part 2: the two settings.


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:4318",
        "https://collector.example.com",
        "https://collector.example.com:4318/otlp",
        "http://[::1]:4318/",
    ],
)
def test_a_collector_s_address_is_taken(endpoint: str) -> None:
    assert Settings(otel_endpoint=endpoint).otel_endpoint == endpoint


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_endpoint_means_off(blank: str) -> None:
    assert Settings(otel_endpoint=blank).otel_endpoint is None


@pytest.mark.parametrize(
    "endpoint",
    [
        "collector.example.com:4318",
        "grpc://collector.example.com:4317",
        "http://",
        "https://user:sk-recognizable-key-0001@collector.example.com",
        "https://sk-recognizable-key-0001@collector.example.com",
        "https://collector.example.com/?key=sk-recognizable-key-0001",
        "https://collector.example.com/#sk-recognizable-key-0001",
        "https://collector.example.com:0",
        "https://collector.example.com:port",
        "https://[collector",
    ],
)
def test_an_address_that_is_no_collector_s_stops_startup_without_being_repeated(
    endpoint: str,
) -> None:
    with pytest.raises(ValidationError) as refused:
        Settings(otel_endpoint=endpoint)

    text = str(refused.value)
    assert "otel_endpoint" in text
    assert "must be a collector's address" in text
    # Nothing of the refused value: a credential may have been put in it.
    assert "recognizable" not in text and "collector.example.com" not in text


@pytest.mark.parametrize("name", ["", "freyja service", "freyja/prod", "x" * 65])
def test_a_service_name_is_a_short_plain_name(name: str) -> None:
    with pytest.raises(ValidationError):
        Settings(otel_service_name=name)
    assert Settings(otel_service_name="freyja-prod.eu_1").otel_service_name == "freyja-prod.eu_1"


# Part 3: what is recorded.


def test_a_run_is_one_trace_of_its_model_calls_and_tool_calls(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    model = ScriptedChatModel(script=[ls("l1")], final_text="Here it is.")
    made = make_mcp_app(model, {}, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        events = run(client, "t-1", "List the workspace.")

    root = one(captured.spans(RUN_SPAN))
    # Under the request that started it, and nothing else (``test_telemetry_requests.py``).
    request = one([span for span in captured.spans() if span.kind is SpanKind.SERVER])
    assert parent_of(root) == request.context.span_id
    assert root.kind is SpanKind.INTERNAL
    assert root.status.status_code is StatusCode.UNSET
    assert attributes(root) == {
        RUN_ID: events[0][1]["run_id"],
        THREAD_ID: "t-1",
        RUN_MODE: "run",
        RUN_CONSOLE: False,
        RUN_STRICT: False,
        PROFILE_ID: "environment",
        PROFILE_SOURCE: "environment",
        MODEL_PROVIDER: PROVIDER,
        MODEL_NAME: MODEL,
        RUN_OUTCOME: "end",
    }
    calls = captured.spans(MODEL_SPAN)
    assert len(calls) == 2
    for span in calls:
        assert span.kind is SpanKind.CLIENT
        assert attributes(span) == {
            MODEL_CALLER: "agent",
            MODEL_PROVIDER: PROVIDER,
            MODEL_NAME: MODEL,
            CALL_OUTCOME: "ok",
        }
    tool = one(captured.spans(TOOL_SPAN))
    assert attributes(tool) == {
        TOOL_NAME: "ls",
        TOOL_CLASSIFICATION: "read_only",
        CALL_OUTCOME: "ok",
    }
    # One trace, every call under the run, in the order they were made.
    spans = captured.spans()
    assert {span.context.trace_id for span in spans} == {root.context.trace_id}
    assert [parent_of(span) for span in [*calls, tool]] == [root.context.span_id] * 3
    assert [span.name for span in spans] == [
        MODEL_SPAN,
        TOOL_SPAN,
        MODEL_SPAN,
        RUN_SPAN,
        request.name,
    ]
    assert request.name == "POST /threads/{thread_id}/runs"
    # No span holds an event or a link, which could carry text.
    assert all(not span.events and not span.links for span in spans)
    assert dict(root.resource.attributes) == {"service.name": SERVICE}


def test_a_run_s_counts_and_its_duration(make_mcp_app: Callable[..., McpApp]) -> None:
    captured = Captured()
    model = ScriptedChatModel(script=[ls("l1"), ls("l2")], final_text="Done.")
    made = make_mcp_app(model, {}, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        run(client, "t-1")
        run(client, "t-2")

    labels = {RUN_MODE: "run", RUN_CONSOLE: False, RUN_STRICT: False}
    assert [(p.attributes, p.value) for p in captured.points(RUNS_STARTED)] == [(labels, 2)]
    assert [(p.attributes, p.value) for p in captured.points(RUNS_ENDED)] == [
        ({**labels, RUN_OUTCOME: "end"}, 2)
    ]
    (duration,) = captured.points(RUN_DURATION)
    assert duration.attributes == {RUN_MODE: "run", RUN_OUTCOME: "end"}
    assert duration.value == 2 and duration.total is not None and 0 < duration.total < 60
    assert [(p.attributes, p.value) for p in captured.points(MODEL_CALLS)] == [
        (
            {
                MODEL_CALLER: "agent",
                MODEL_PROVIDER: PROVIDER,
                MODEL_NAME: MODEL,
                CALL_OUTCOME: "ok",
            },
            4,
        )
    ]
    assert [(p.attributes, p.value) for p in captured.points(TOOL_CALLS)] == [
        ({TOOL_NAME: "ls", TOOL_CLASSIFICATION: "read_only", CALL_OUTCOME: "ok"}, 2)
    ]
    assert captured.points(MODEL_ERRORS) == []
    # No point of any metric names a run or a thread.
    for point in captured.points():
        assert RUN_ID not in point.attributes and THREAD_ID not in point.attributes


def test_a_subagent_s_calls_are_under_the_task_call_that_started_it(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    model = ScriptedChatModel(
        script=[TASK, call("lookup", key="alpha"), AIMessage(content="It is value-of-alpha.")],
        final_text="Found it.",
    )
    servers = {"stub": {"command": "stub-command", "read_only": ["lookup"]}}
    made = make_mcp_app(model, servers, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        events = run(client, "t-1")

    assert events[-1] == ("end", {"status": "completed"})
    root = one(captured.spans(RUN_SPAN))
    tools = {attributes(span)[TOOL_NAME]: span for span in captured.spans(TOOL_SPAN)}
    assert sorted(tools) == ["lookup", "task"]
    assert parent_of(tools["task"]) == root.context.span_id
    assert attributes(tools["task"])[TOOL_CLASSIFICATION] == "workspace"
    # The subagent's tool call, and its two model calls, belong to the task call.
    assert parent_of(tools["lookup"]) == tools["task"].context.span_id
    assert attributes(tools["lookup"]) == {
        TOOL_NAME: "lookup",
        TOOL_CLASSIFICATION: "read_only",
        TOOL_SERVER: "stub",
        CALL_OUTCOME: "ok",
    }
    parents = [parent_of(span) for span in captured.spans(MODEL_SPAN)]
    assert sorted(parents, key=str) == sorted(
        [root.context.span_id] * 2 + [tools["task"].context.span_id] * 2, key=str
    )
    assert {span.context.trace_id for span in captured.spans()} == {root.context.trace_id}


def test_a_tool_server_s_failure_is_an_error_of_the_call_and_not_of_the_run(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    model = ScriptedChatModel(script=[call("fail", reason="disk full")], final_text="It failed.")
    servers = {"stub": {"command": "stub-command", "read_only": ["fail"]}}
    made = make_mcp_app(model, servers, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        events = run(client, "t-1")

    assert events[-1] == ("end", {"status": "completed"})
    tool = one(captured.spans(TOOL_SPAN))
    assert attributes(tool) == {
        TOOL_NAME: "fail",
        TOOL_CLASSIFICATION: "read_only",
        TOOL_SERVER: "stub",
        CALL_OUTCOME: "error",
    }
    # The failure came back as a result: there is no exception to name, and no text of it.
    assert (tool.status.status_code, tool.status.description) == (StatusCode.ERROR, None)
    assert attributes(one(captured.spans(RUN_SPAN)))[RUN_OUTCOME] == "end"
    assert captured.count(TOOL_CALLS, **{TOOL_NAME: "fail", CALL_OUTCOME: "error"}) == 1


def test_a_tool_a_model_made_up_is_named_unknown_and_never_by_what_the_model_wrote(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    model = ScriptedChatModel(
        script=[call("zebra_made_up_tool", detail="zebra-argument")], final_text="Sorry."
    )
    made = make_mcp_app(model, {}, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        events = run(client, "t-1")

    assert events[-1] == ("end", {"status": "completed"})
    tool = one(captured.spans(TOOL_SPAN))
    assert attributes(tool) == {
        TOOL_NAME: "unknown",
        TOOL_CLASSIFICATION: "unknown",
        CALL_OUTCOME: "error",
    }
    assert not any("zebra" in text for text in captured.texts())


def test_a_gated_call_is_recorded_when_a_person_approved_it_and_not_before(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    model = ScriptedChatModel(script=[NOTIFY], final_text="Sent.")
    made = make_mcp_app(model, {}, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        paused = run(client, "t-1")
        before = [attributes(span) for span in captured.spans(TOOL_SPAN)]
        resumed = resume(client, "t-1", {"type": "approve"})

    assert paused[-1][0] == "interrupt" and resumed[-1] == ("end", {"status": "completed"})
    assert before == []
    first, second = captured.spans(RUN_SPAN)
    assert (attributes(first)[RUN_MODE], attributes(first)[RUN_OUTCOME]) == ("run", "interrupt")
    assert first.status.status_code is StatusCode.UNSET
    assert (attributes(second)[RUN_MODE], attributes(second)[RUN_OUTCOME]) == ("resume", "end")
    assert first.context.trace_id != second.context.trace_id
    tool = one(captured.spans(TOOL_SPAN))
    assert parent_of(tool) == second.context.span_id
    assert attributes(tool) == {
        TOOL_NAME: "send_notification",
        TOOL_CLASSIFICATION: "gated",
        CALL_OUTCOME: "ok",
    }
    assert captured.count(RUNS_ENDED, **{RUN_MODE: "run", RUN_OUTCOME: "interrupt"}) == 1
    assert captured.count(RUNS_ENDED, **{RUN_MODE: "resume", RUN_OUTCOME: "end"}) == 1


def test_a_rejected_call_is_no_tool_call(make_mcp_app: Callable[..., McpApp]) -> None:
    captured = Captured()
    model = ScriptedChatModel(script=[NOTIFY], final_text="Not sent.")
    made = make_mcp_app(model, {}, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        run(client, "t-1")
        resumed = resume(client, "t-1", {"type": "reject", "message": "zebra-reason"})

    assert resumed[-1] == ("end", {"status": "completed"})
    assert captured.spans(TOOL_SPAN) == []
    assert not any("zebra" in text for text in captured.texts())


def test_a_subagent_s_proposal_interrupts_the_task_call(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    model = ScriptedChatModel(script=[TASK, NOTIFY], final_text="Done.")
    made = make_mcp_app(model, {}, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        paused = run(client, "t-1")

    assert paused[-1][0] == "interrupt"
    task = one(captured.spans(TOOL_SPAN))
    # The run paused inside the call: that is no error of the tool's.
    assert attributes(task) == {
        TOOL_NAME: "task",
        TOOL_CLASSIFICATION: "workspace",
        CALL_OUTCOME: "interrupted",
    }
    assert task.status.status_code is StatusCode.UNSET
    assert ERROR_TYPE not in attributes(task)


def test_a_hand_off_is_a_tool_call_that_names_its_agent(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    captured = Captured()
    model = ScriptedChatModel(
        script=[call("handoff_to_hello", message="Say hello to zebra.")], final_text="Done."
    )
    made = make_a2a_app(model, {"hello": {"read_only": True}}, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        events = run(client, "t-1")

    assert events[-1] == ("end", {"status": "completed"})
    assert len(made.stubs["hello"].record.messages) == 1
    tool = one(captured.spans(TOOL_SPAN))
    assert attributes(tool) == {
        TOOL_NAME: "handoff_to_hello",
        TOOL_CLASSIFICATION: "read_only",
        TOOL_AGENT: "hello",
        CALL_OUTCOME: "ok",
    }
    assert parent_of(tool) == one(captured.spans(RUN_SPAN)).context.span_id


def test_a_model_s_failure_names_the_error_s_class_and_the_run_s_code(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    model = FailingChatModel(fail_on_call=1, error="zebra provider said no")
    made = make_mcp_app(model, {}, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        events = run(client, "t-1")

    assert events[-1][1]["code"] == "model_error"
    failed = one(captured.spans(MODEL_SPAN))
    assert attributes(failed) == {
        MODEL_CALLER: "agent",
        MODEL_PROVIDER: PROVIDER,
        MODEL_NAME: MODEL,
        CALL_OUTCOME: "error",
        ERROR_TYPE: "RuntimeError",
    }
    # The class, never the text.
    assert (failed.status.status_code, failed.status.description) == (
        StatusCode.ERROR,
        "RuntimeError",
    )
    root = one(captured.spans(RUN_SPAN))
    assert (attributes(root)[RUN_OUTCOME], attributes(root)[ERROR_CODE]) == ("error", "model_error")
    assert (root.status.status_code, root.status.description) == (StatusCode.ERROR, "model_error")
    assert [(p.attributes, p.value) for p in captured.points(MODEL_ERRORS)] == [
        ({ERROR_CODE: "model_error"}, 1)
    ]
    assert captured.count(RUNS_ENDED, **{RUN_OUTCOME: "error", ERROR_CODE: "model_error"}) == 1
    assert captured.count(MODEL_CALLS, **{CALL_OUTCOME: "error"}) == 1
    assert not any("zebra" in text for text in captured.texts())


def test_a_run_turned_away_for_want_of_a_model_is_recorded_and_calls_nothing(
    make_llm_app: Callable[..., LlmApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    captured = Captured()
    made = make_llm_app(model="openai:env-model", telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        events = run(client, "t-1")

    assert events[-1][1]["code"] == "no_usable_model"
    root = one(captured.spans(RUN_SPAN))
    assert captured.spans(MODEL_SPAN) == captured.spans(TOOL_SPAN) == []
    found = attributes(root)
    assert (found[RUN_OUTCOME], found[ERROR_CODE]) == ("error", "no_usable_model")
    assert (found[MODEL_PROVIDER], found[MODEL_NAME]) == ("openai", "env-model")
    assert captured.count(MODEL_ERRORS, **{ERROR_CODE: "no_usable_model"}) == 1
    assert captured.points(MODEL_CALLS) == []


def test_a_saved_profile_s_run_names_its_provider_and_model(
    make_llm_app: Callable[..., LlmApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-environment-key-9f2c")
    captured = Captured()
    made = make_llm_app(model="openai:env-model", telemetry=captured.telemetry)
    body = {
        "name": "Zebra profile",
        "provider": "openrouter",
        "model": "saved-model",
        "api_key": "sk-recognizable-key-0001",
    }
    with TestClient(made.app) as client:
        saved = client.post("/llm/profiles", json=body)
        assert saved.status_code == 201, saved.text
        profile = saved.json()["id"]
        assert client.post(f"/llm/profiles/{profile}/activate").status_code == 200
        events = run(client, "t-1")

    assert events[-1] == ("end", {"status": "completed"})
    found = attributes(one(captured.spans(RUN_SPAN)))
    assert (found[PROFILE_ID], found[PROFILE_SOURCE]) == (profile, "saved")
    assert (found[MODEL_PROVIDER], found[MODEL_NAME]) == ("openrouter", "saved-model")
    call_attributes = attributes(one(captured.spans(MODEL_SPAN)))
    assert (call_attributes[MODEL_PROVIDER], call_attributes[MODEL_NAME]) == (
        "openrouter",
        "saved-model",
    )
    # The profile's name is an operator's free text, and its key is a key: neither is sent.
    texts = captured.texts()
    assert not any("Zebra" in text or "recognizable" in text for text in texts)


def test_a_run_stopped_at_the_step_limit_says_so(make_mcp_app: Callable[..., McpApp]) -> None:
    captured = Captured()
    made = make_mcp_app(LoopingChatModel(), {}, max_steps=4, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        events = run(client, "t-1")

    assert events[-1][1]["code"] == "step_limit"
    found = attributes(one(captured.spans(RUN_SPAN)))
    assert (found[RUN_OUTCOME], found[ERROR_CODE]) == ("error", "step_limit")
    # The step limit is the run's, not the model's.
    assert captured.points(MODEL_ERRORS) == []


def test_a_run_whose_reader_leaves_is_cancelled(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    made = make_mcp_app(ScriptedChatModel(), {}, telemetry=captured.telemetry)

    async def start_and_leave(runtime: Runtime) -> None:
        owner = streaming.lease_owner(runtime, "run-1")
        assert await runtime.locks.acquire("t-1", owner)
        context = await streaming.take_context(runtime, "t-1", "run-1")
        assert context is not None
        # As what it is, an async generator, which the reader that leaves closes.
        events: Any = streaming.run_stream(
            runtime,
            "t-1",
            context=context,
            input={"messages": [("user", "hello")]},
            mode="run",
            debug=False,
            run_id="run-1",
        )
        first = await anext(events)
        assert first.event == "start"
        await events.aclose()

    with TestClient(made.app) as client:
        client.portal.call(start_and_leave, made.runtime)
        # The lease was given back: another run can take it.
        free = client.portal.call(made.runtime.locks.acquire, "t-1", "another-run")

    assert free is True
    found = attributes(one(captured.spans(RUN_SPAN)))
    assert found[RUN_OUTCOME] == "cancelled" and ERROR_CODE not in found
    assert captured.count(RUNS_ENDED, **{RUN_OUTCOME: "cancelled"}) == 1


def test_a_run_for_which_no_orchestrator_could_be_taken_is_recorded(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    made = make_mcp_app(ScriptedChatModel(), {}, telemetry=captured.telemetry)

    async def nothing_runs(runtime: Runtime) -> list[str | None]:
        owner = streaming.lease_owner(runtime, "run-1")
        assert await runtime.locks.acquire("t-1", owner)
        events = streaming.no_context_stream(
            runtime, "t-1", mode="run", debug=False, run_id="run-1"
        )
        return [event.event async for event in events]

    with TestClient(made.app) as client:
        kinds = client.portal.call(nothing_runs, made.runtime)

    assert kinds == ["start", "error"]
    found = attributes(one(captured.spans()))
    assert (found[RUN_ID], found[RUN_OUTCOME], found[ERROR_CODE]) == ("run-1", "error", "internal")


class _Broken:
    """A tracer and a meter whose every use fails, in a provider's words, at one place."""

    def __init__(self, at: str) -> None:
        self.at = at

    def _fail(self, *_: Any, **__: Any) -> Any:
        raise RuntimeError("the zebra collector at http://127.0.0.1:9731 said no")

    # The tracer: starting a span fails, or the span it gives fails as it is ended.
    def start_span(self, *args: Any, **kwargs: Any) -> Any:
        if self.at == "start":
            self._fail()
        span = trace_api.NoOpTracer().start_span(*args, **kwargs)
        if self.at == "end":
            return SimpleNamespace(
                set_attributes=span.set_attributes,
                set_status=span.set_status,
                end=self._fail,
                get_span_context=span.get_span_context,
            )
        return span

    # The meter: an instrument is made, and fails when it is used.
    def create_counter(self, *_: Any, **__: Any) -> Any:
        ok = self.at != "count"
        return SimpleNamespace(
            add=(lambda *a, **k: None) if ok else self._fail,
            record=(lambda *a, **k: None) if ok else self._fail,
            set=(lambda *a, **k: None) if ok else self._fail,
        )

    create_histogram = create_gauge = create_counter

    # An instrument that is read and not used: making one never fails.
    def create_observable_gauge(self, *_: Any, **__: Any) -> Any:
        return SimpleNamespace()


@pytest.mark.parametrize("at", ["start", "end", "count"])
def test_telemetry_that_fails_changes_nothing_a_run_does(
    make_mcp_app: Callable[..., McpApp], caplog: pytest.LogCaptureFixture, at: str
) -> None:
    """Above all it never fails a step after its tool ran: the step would run again."""
    # A stand-in for the tracer and the meter both, which fails where the test says.
    broken: Any = _Broken(at)
    telemetry = Telemetry(broken, broken, providers=("on",))
    model = ScriptedChatModel(script=[ls("l1"), call("record_note", note="once")], final_text="Ok.")
    server = make_stub("work")
    made = make_mcp_app(
        model,
        {"work": {"command": "stub", "read_only": []}},
        stubs={"work": server},
        telemetry=telemetry,
    )
    with TestClient(made.app) as client:
        paused = run(client, "t-1")
        resumed = resume(client, "t-1", {"type": "approve"})
        state = client.get("/threads/t-1/state").json()
        again = run(client, "t-1")

    assert paused[-1][0] == "interrupt"
    assert resumed[-1] == again[-1] == ("end", {"status": "completed"})
    # The approved action ran once, and its result is the tool's own.
    assert server.notes == [{"note": "once", "priority": 0}]
    results = [message for message in state["messages"] if message["type"] == "tool"]
    assert len(results) == 2 and results[-1]["content"] == "recorded note 1: once"
    assert state["run_active"] is False
    # Said once, by the failure's kind, however many spans and counts it cost.
    lines = [record.getMessage() for record in caplog.records if "Telemetry" in record.message]
    assert lines == [
        "Telemetry: a span or a count could not be recorded (RuntimeError). Runs are not "
        "affected; further failures are not logged."
    ]
    assert "zebra" not in caplog.text and "9731" not in caplog.text


def _request(tool: str | None) -> Any:
    """A tool call as the tool node hands it to a middleware; ``None`` for no such tool."""
    held = SimpleNamespace(name=tool) if tool is not None else None
    return SimpleNamespace(tool=held, tool_call={"name": "zebra_written_by_a_model", "args": {}})


def _model_request() -> Any:
    """A model call as a middleware is handed it; the middleware reads nothing of it."""
    return SimpleNamespace()


def _replying(_request: Any) -> Any:
    return "reply"


def _middleware(captured: Captured) -> TelemetryMiddleware:
    labels = {"lookup": ToolLabels("lookup", "read_only", server="work")}
    return TelemetryMiddleware(captured.telemetry, labels)


@pytest.mark.parametrize(
    ("raised", "outcome", "status"),
    [
        (GraphInterrupt(), "interrupted", StatusCode.UNSET),
        # Another signal the graph passes up through a call is its control flow.
        (ParentCommand(Command()), "ok", StatusCode.UNSET),
        (asyncio.CancelledError(), "cancelled", StatusCode.UNSET),
        (ValueError("zebra"), "error", StatusCode.ERROR),
    ],
)
async def test_how_a_call_that_raised_is_named(
    raised: BaseException, outcome: str, status: StatusCode
) -> None:
    captured = Captured()
    middleware = _middleware(captured)

    async def handler(_request: Any) -> Any:
        raise raised

    with pytest.raises(type(raised)):
        await middleware.awrap_tool_call(_request("lookup"), handler)
    with pytest.raises(type(raised)):
        await middleware.awrap_model_call(_model_request(), handler)

    for span in captured.spans():
        found = attributes(span)
        assert found[CALL_OUTCOME] == outcome
        assert span.status.status_code is status
        assert found.get(ERROR_TYPE) == ("ValueError" if outcome == "error" else None)
    assert [span.name for span in captured.spans()] == [TOOL_SPAN, MODEL_SPAN]
    assert not any("zebra" in text for text in captured.texts())


def test_a_graph_run_without_asyncio_is_recorded_the_same() -> None:
    """The service only runs its graph asynchronously; the other hooks keep the middleware
    whole for whoever runs it another way, such as a test."""
    captured = Captured()
    middleware = _middleware(captured)
    answer = ToolMessage(content="zebra-result", tool_call_id="c1")
    refusal = ToolMessage(content="zebra-refusal", tool_call_id="c2", status="error")

    assert middleware.wrap_model_call(_model_request(), _replying) == "reply"
    assert middleware.wrap_tool_call(_request("lookup"), lambda _request: answer) is answer
    assert middleware.wrap_tool_call(_request(None), lambda _request: refusal) is refusal
    # A tool the build was not given is named unknown, like one that does not exist.
    assert middleware.wrap_tool_call(_request("another"), lambda _request: answer) is answer

    def failing(_request: Any) -> Any:
        raise ValueError("zebra")

    with pytest.raises(ValueError, match="zebra"):
        middleware.wrap_model_call(_model_request(), failing)
    with pytest.raises(ValueError, match="zebra"):
        middleware.wrap_tool_call(_request("lookup"), failing)

    tools = [
        (found[TOOL_NAME], found.get(TOOL_SERVER), found[CALL_OUTCOME])
        for found in captured.attributes(TOOL_SPAN)
    ]
    assert tools == [
        ("lookup", "work", "ok"),
        ("unknown", None, "error"),
        ("unknown", None, "ok"),
        ("lookup", "work", "error"),
    ]
    models = [found[CALL_OUTCOME] for found in captured.attributes(MODEL_SPAN)]
    assert models == ["ok", "error"]
    assert not any("zebra" in text for text in captured.texts())


def test_a_console_run_is_one_run_however_many_segments_it_is_run_in(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    model = ScriptedChatModel(script=[ls("l1"), ls("l2")], final_text="Done.")
    made = make_mcp_app(model, {}, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        events = read(client, CONSOLE_RUN.format("t-1"), {"message": "List twice."})
        idle(client, "t-1")

    assert events[-1] == ("end", {"status": "completed"})
    root = one(captured.spans(RUN_SPAN))
    found = attributes(root)
    assert (found[RUN_MODE], found[RUN_CONSOLE], found[RUN_OUTCOME]) == ("run", True, "end")
    # Three model steps and two tool steps, each its own segment, all under the one run.
    assert len(captured.spans(MODEL_SPAN)) == 3 and len(captured.spans(TOOL_SPAN)) == 2
    calls = captured.spans(MODEL_SPAN) + captured.spans(TOOL_SPAN)
    assert {parent_of(span) for span in calls} == {root.context.span_id}
    assert captured.count(RUNS_STARTED, **{RUN_CONSOLE: True}) == 1


def test_a_console_run_that_a_stop_ended_is_recorded_as_stopped(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    model = LabelledChatModel(script=[ls("l1"), ls("l2")], final_text="Done.")
    made = make_mcp_app(model, {}, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        model.hold()
        reader = Reader(client, CONSOLE_RUN.format("t-1"), {"message": "List."})
        reader.start()
        with model.held():
            assert client.post("/threads/t-1/stop").status_code == 202
        events = reader.finish()
        idle(client, "t-1")

    assert events[-1] == ("end", {"status": "stopped"})
    found = attributes(one(captured.spans(RUN_SPAN)))
    assert (found[RUN_CONSOLE], found[RUN_OUTCOME]) == (True, "stopped")
    # The model call under way finished; the tool it asked for never started.
    assert len(captured.spans(MODEL_SPAN)) == 1 and captured.spans(TOOL_SPAN) == []
    assert captured.count(RUNS_ENDED, **{RUN_OUTCOME: "stopped"}) == 1


def test_a_caller_s_task_is_a_console_run_on_the_strict_build(
    make_a2a_server_app: Callable[..., ServerApp],
) -> None:
    captured = Captured()
    model = ScriptedChatModel(script=[ls("l1")], final_text="Hello, caller.")
    made = make_a2a_server_app(model, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        accepted = made.accept(client, "Say hello.")
        made.finish(client, accepted)
        thread = accepted.task.thread_id

    root = one(captured.spans(RUN_SPAN))
    found = attributes(root)
    assert (found[RUN_STRICT], found[RUN_CONSOLE], found[RUN_OUTCOME]) == (True, True, "end")
    # Telemetry is the operator's, as the log is: it names the thread by its id.
    assert found[THREAD_ID] == thread
    assert {parent_of(span) for span in captured.spans() if span is not root} == {
        root.context.span_id
    }
    assert captured.count(RUNS_ENDED, **{RUN_STRICT: True}) == 1


def test_a_background_run_and_the_worker_s_rounds_are_recorded(
    make_a2a_app: Callable[..., A2aApp],
) -> None:
    from tests.test_a2a_pending import TASK as PENDING_TASK
    from tests.test_a2a_pending import handoff, pending_app, stub, tick, wait_for

    captured = Captured()
    agent = stub()
    model = ScriptedChatModel(script=[handoff("hold: report")], final_text="The report is ready.")
    made = pending_app(make_a2a_app, model, agent, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        assert run(client, "b-1")[-1] == ("end", {"status": "completed"})
        tick(client, made.runtime)
        nothing_ready = captured.points(WORKER_THREADS_WAITING)
        agent.release()
        wait_for(client, made.runtime, PENDING_TASK, "completed")
        tick(client, made.runtime)
        one_ready = captured.points(WORKER_THREADS_WAITING)
        tick(client, made.runtime)
        delivered = captured.points(WORKER_THREADS_WAITING)

    assert [point.value for point in nothing_ready] == [0]
    # The round that recorded the outcome found its thread waiting, and delivered to it.
    assert [point.value for point in one_ready] == [1]
    assert [point.value for point in delivered] == [0]
    assert all(point.attributes == {} for point in delivered)
    (rounds,) = captured.points(WORKER_ROUND_DURATION)
    assert rounds.attributes == {} and rounds.value == 3
    modes = [attributes(span)[RUN_MODE] for span in captured.spans(RUN_SPAN)]
    assert modes == ["run", "background"]
    background = captured.spans(RUN_SPAN)[1]
    assert attributes(background)[RUN_OUTCOME] == "end"
    assert captured.count(RUNS_ENDED, **{RUN_MODE: "background", RUN_OUTCOME: "end"}) == 1


def test_the_worker_s_own_loop_times_its_rounds(make_a2a_app: Callable[..., A2aApp]) -> None:
    captured = Captured()
    made = make_a2a_app(
        ScriptedChatModel(),
        {"hello": {"read_only": True}},
        run_worker=True,
        a2a_poll_interval_seconds=0.05,
        telemetry=captured.telemetry,
    )
    with TestClient(made.app):
        deadline = threading.Event()
        for _ in range(100):
            if captured.points(WORKER_ROUND_DURATION):
                break
            deadline.wait(0.05)

    (rounds,) = captured.points(WORKER_ROUND_DURATION)
    assert rounds.value >= 1


# Part 4: what telemetry leaves alone.


class WatchingModel(ScriptedChatModel):
    """A scripted model that notes whether OpenTelemetry had a current span as it was called."""

    current: list[bool] = Field(default_factory=list)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.current.append(trace_api.get_current_span().get_span_context().is_valid)
        return super()._generate(messages, stop, run_manager, **kwargs)


def test_nothing_a_model_or_a_tool_server_receives_changes_with_telemetry_on(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    """The MCP SDK writes the current trace's id into every request, and a provider's client
    into a header, whenever OpenTelemetry has a current span. Freyja never sets one."""
    carried: list[dict[str, str]] = []

    def probe(key: str) -> str:
        """Note what trace context reached this server with the call."""
        carrier: dict[str, str] = {}
        propagate.inject(carrier)
        carried.append(carrier)
        return f"probed {key}"

    captured = Captured()
    model = WatchingModel(script=[call("probe", key="alpha")], final_text="Done.")
    stub = make_stub("work", only=[], extra={"probe": probe})
    servers = {"work": {"command": "stub", "read_only": ["probe"]}}
    made = make_mcp_app(model, servers, stubs={"work": stub}, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        events = run(client, "t-1")

    assert events[-1] == ("end", {"status": "completed"})
    # Recorded, so telemetry was on and the calls were made under its spans.
    assert len(captured.spans(MODEL_SPAN)) == 2 and len(captured.spans(TOOL_SPAN)) == 1
    assert model.current == [False, False]
    assert carried == [{}]


def test_the_probe_does_see_a_trace_context_a_client_sends() -> None:
    """The control of the test above: with a current span of OpenTelemetry's, the same
    probe finds it, so an empty carrier there means none was sent."""
    span = trace_api.NonRecordingSpan(
        trace_api.SpanContext(
            trace_id=0x1234567890ABCDEF1234567890ABCDEF,
            span_id=0x1234567890ABCDEF,
            is_remote=False,
            trace_flags=trace_api.TraceFlags(trace_api.TraceFlags.SAMPLED),
        )
    )
    carrier: dict[str, str] = {}
    with trace_api.use_span(span):
        propagate.inject(carrier)

    assert carrier["traceparent"].startswith("00-1234567890abcdef1234567890abcdef-")


def test_the_process_s_own_providers_are_left_alone(make_mcp_app: Callable[..., McpApp]) -> None:
    """A library that traces itself through the global provider stays as it was."""
    captured = Captured()
    made = make_mcp_app(ScriptedChatModel(), {}, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        run(client, "t-1")

    assert captured.spans(RUN_SPAN)
    assert not isinstance(trace_api.get_tracer_provider(), TracerProvider)
    assert not isinstance(metrics_api.get_meter_provider(), MeterProvider)


def test_one_module_of_the_service_imports_opentelemetry() -> None:
    importing = re.compile(r"^\s*(?:from|import)\s+opentelemetry\b", re.MULTILINE)
    found = sorted(
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if importing.search(path.read_text())
    )

    assert found == ["runtime/telemetry.py"]


def test_every_name_telemetry_sends_is_in_its_own_namespace() -> None:
    names = {
        name: value
        for name, value in vars(telemetry_module).items()
        if name.isupper() and isinstance(value, str) and "." in value
    }

    assert len(names) > 20
    # A request's names and an error's class are OpenTelemetry's own conventions.
    assert {value for value in names.values() if not value.startswith("freyja.")} == {
        "error.type",
        "http.request.method",
        "http.route",
        "http.response.status_code",
        "http.server.request.duration",
    }


# Part 5: the collector.


class Collector:
    """A stand-in collector on the loopback interface that keeps what it is sent."""

    def __init__(self) -> None:
        self.received: list[tuple[str, dict[str, str], bytes]] = []
        received = self.received

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("content-length", "0")))
                received.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                self.send_response(200)
                self.send_header("content-length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def address(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self) -> "Collector":
        self.thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def bodies(self, path: str) -> bytes:
        return b"".join(body for sent_to, _headers, body in self.received if sent_to == path)


def test_what_was_recorded_is_sent_to_the_collector_s_two_paths_when_the_service_stops(
    make_mcp_app: Callable[..., McpApp],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="freyja_agent")
    # What the SDK would take from the environment if it were left to: none of it is used.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9/elsewhere")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://127.0.0.1:9/elsewhere")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "zebra-service")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "deployment=zebra-attribute")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "always_off")
    # The exporter's HTTP library would send a request for loopback through a proxy.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    model = ScriptedChatModel(script=[ls("l1")], final_text="Done.")
    with Collector() as collector:
        made = make_mcp_app(
            model, {}, otel_endpoint=f"{collector.address}/", otel_service_name="freyja-wire"
        )
        with TestClient(made.app) as client:
            assert run(client, "t-1")[-1] == ("end", {"status": "completed"})
            telemetry = made.runtime.telemetry
            assert telemetry.on is True
            running = otel_threads()
        # Shut down with the runtime: what it held was sent, and its threads are gone.
        assert telemetry.on is False
        assert otel_threads() == []
        paths = sorted({path for path, _headers, _body in collector.received})
        traces, metrics = collector.bodies("/v1/traces"), collector.bodies("/v1/metrics")
        headers = [sent for _path, sent, _body in collector.received]

    assert running, "no thread of the SDK ran while telemetry was on"
    assert paths == ["/v1/metrics", "/v1/traces"]
    for name in (RUN_SPAN, MODEL_SPAN, TOOL_SPAN, "freyja-wire", "t-1"):
        assert name.encode() in traces, name
    for name in (RUNS_STARTED, RUNS_ENDED, RUN_DURATION, MODEL_CALLS, TOOL_CALLS, "freyja-wire"):
        assert name.encode() in metrics, name
    assert b"zebra" not in traces + metrics
    assert all(sent["content-type"] == "application/x-protobuf" for sent in headers)
    # No credential is sent: Freyja has none for a collector.
    assert all("authorization" not in sent for sent in headers)
    # The log says that telemetry is on and not where it is sent.
    assert "Telemetry is on" in caplog.text
    assert str(collector.server.server_address[1]) not in caplog.text


def test_closing_sends_the_spans_a_batch_still_holds() -> None:
    captured = Captured(batch=True)
    span = captured.telemetry.run(
        thread_id="t-1",
        run_id="run-1",
        mode="run",
        console=False,
        strict=False,
        profile_id="environment",
        profile_source="environment",
        provider=None,
        model=None,
    )
    span.end("end")
    # Ending twice changes nothing.
    span.end("error", "internal")
    waiting = captured.spans()

    captured.telemetry.close()

    assert waiting == []
    found = attributes(one(captured.spans(RUN_SPAN)))
    assert found[RUN_OUTCOME] == "end" and MODEL_PROVIDER not in found
    assert captured.telemetry.on is False
    # A second close has nothing left to shut down.
    captured.telemetry.close()


def test_a_shutdown_that_fails_is_logged_by_its_kind_and_goes_on(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = Captured()
    providers: Any = captured.telemetry._providers
    tracers, meters = providers
    closed: list[str] = []

    def broken() -> None:
        raise RuntimeError("zebra collector at http://127.0.0.1:9731 refused")

    monkeypatch.setattr(tracers, "shutdown", broken)
    monkeypatch.setattr(meters, "shutdown", lambda: closed.append("meters"))

    captured.telemetry.close()

    assert closed == ["meters"]
    assert "Telemetry: shutting down failed (RuntimeError)" in caplog.text
    assert "zebra" not in caplog.text and "9731" not in caplog.text


def test_telemetry_handed_to_a_runtime_is_its_maker_s_to_shut_down(
    make_app: Callable[[Runtime], FastAPI], make_mcp_app: Callable[..., McpApp]
) -> None:
    captured = Captured()
    made = make_mcp_app(ScriptedChatModel(), {}, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        run(client, "t-1")

    assert captured.telemetry.on is True
    captured.telemetry.close()


def test_a_call_made_outside_any_run_is_a_trace_of_its_own() -> None:
    captured = Captured()
    with captured.telemetry.model_call() as made:
        assert made.outcome is None

    span = one(captured.spans(MODEL_SPAN))
    assert span.parent is None
    assert attributes(span) == {MODEL_CALLER: "agent", CALL_OUTCOME: "ok"}


# Part 6: what a span belongs under, and the bound on a value.


def _scope() -> Any:
    return telemetry_module._CURRENT.get()


def _run_span(captured: Captured) -> Any:
    return captured.telemetry.run(
        thread_id="t-1",
        run_id="r-1",
        mode="run",
        console=False,
        strict=False,
        profile_id="environment",
        profile_source="environment",
        provider=PROVIDER,
        model=MODEL,
    )


async def test_a_scope_says_what_it_is_and_a_run_s_is_current_only_while_the_graph_is_asked() -> (
    None
):
    captured = Captured()
    telemetry = captured.telemetry
    run_span = _run_span(captured)
    seen: list[Any] = []

    async def graph() -> Any:
        seen.append(_scope())
        with telemetry.tool_call(ToolLabels("lookup", "read_only")):
            seen.append(_scope())
        with telemetry.model_call():
            seen.append(_scope())
        yield "item"

    between = [_scope() async for _ in run_span.within(graph())]
    run_span.end("end")

    # Never across a ``yield``: whoever reads the stream is in no run.
    assert between == [None] and _scope() is None
    of_run, of_tool, of_model = seen
    assert (of_run.kind, of_run.in_run, of_run.model_call) == ("run", True, False)
    assert (of_tool.kind, of_tool.in_run, of_tool.model_call) == ("call", True, False)
    assert (of_model.kind, of_model.in_run, of_model.model_call) == ("call", True, True)


def test_a_call_outside_any_run_is_in_no_run() -> None:
    captured = Captured()
    with captured.telemetry.tool_call(ToolLabels("lookup", "read_only")):
        scope = _scope()

    assert (scope.kind, scope.in_run, scope.model_call) == ("call", False, False)


def test_a_value_that_is_text_is_cut_on_a_span_and_on_a_metric() -> None:
    """The SDK's own limit holds for a span; a metric's value is cut here (FR-030)."""
    captured = Captured()
    long = "x" * 300
    with captured.telemetry.tool_call(ToolLabels(long, "read_only", server=long)):
        pass

    span = attributes(one(captured.spans(TOOL_SPAN)))
    point = one_point(captured, TOOL_CALLS)
    for found in (span, point.attributes):
        assert found[TOOL_NAME] == found[TOOL_SERVER] == "x" * 256
        assert found[TOOL_CLASSIFICATION] == "read_only"


# Part 7: who made a call of the model. The orchestrator's own and a subagent's are recorded
# by the middleware; one a library makes by itself, by the guarded model; a profile's test's,
# by the test.

KEY = "sk-recognizable-key-0001"


def _guarded(model: Any, captured: Captured, *secrets: str) -> Any:
    return guard_model(model, secrets, record=captured.telemetry.model_request)


async def _in_a_run(captured: Captured, steps: Callable[[], Any]) -> ReadableSpan:
    """Do ``steps`` as the graph of one run does its own, and give the run's span."""
    run_span = _run_span(captured)

    async def graph() -> Any:
        await steps()
        yield "item"

    async for _ in run_span.within(graph()):
        pass
    run_span.end("end")
    return one(captured.spans(RUN_SPAN))


def test_a_call_the_middleware_records_is_the_agent_s(make_mcp_app: Callable[..., McpApp]) -> None:
    captured = Captured()
    made = make_mcp_app(ScriptedChatModel(script=[ls("l1")]), {}, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        run(client, "t-1")

    spans = captured.spans(MODEL_SPAN)
    assert [attributes(span)[MODEL_CALLER] for span in spans] == ["agent", "agent"]
    assert captured.count(MODEL_CALLS, **{MODEL_CALLER: "agent"}) == 2
    assert captured.count(MODEL_CALLS) == 2


async def test_a_request_a_library_makes_by_itself_within_a_run_is_recorded_once() -> None:
    captured = Captured()
    telemetry = captured.telemetry
    model = _guarded(ScriptedChatModel(), captured)

    async def steps() -> None:
        # A library asks the model by itself, as the summary of a long conversation is asked.
        await model.ainvoke("summarize")
        # The orchestrator's own call, which the middleware records: no second span.
        with telemetry.model_call():
            await model.ainvoke("go")
        # From inside a tool call, such as a subagent's own library.
        with telemetry.tool_call(ToolLabels("task", "workspace")):
            await model.ainvoke("summarize")

    of_run = await _in_a_run(captured, steps)
    # Outside any run nothing is recorded, also from inside a call that is in no run: nobody
    # could say which run it belongs to.
    await model.ainvoke("outside")
    with telemetry.tool_call(ToolLabels("lookup", "read_only")):
        await model.ainvoke("outside")

    spans = captured.spans(MODEL_SPAN)
    assert [attributes(span)[MODEL_CALLER] for span in spans] == ["library", "agent", "library"]
    by_itself = spans[0]
    assert parent_of(by_itself) == of_run.context.span_id
    assert by_itself.kind is SpanKind.CLIENT
    assert attributes(by_itself) == {
        MODEL_CALLER: "library",
        MODEL_PROVIDER: PROVIDER,
        MODEL_NAME: MODEL,
        CALL_OUTCOME: "ok",
    }
    in_the_run, _outside = captured.spans(TOOL_SPAN)
    assert parent_of(spans[2]) == in_the_run.context.span_id
    assert captured.count(MODEL_CALLS, **{MODEL_CALLER: "library", CALL_OUTCOME: "ok"}) == 2
    assert captured.count(MODEL_CALLS, **{MODEL_CALLER: "agent"}) == 1
    assert model.calls == 5


async def test_a_library_s_request_that_fails_is_an_error_by_the_class_of_what_was_raised() -> None:
    captured = Captured()
    held = _guarded(FailingChatModel(fail_on_call=1, error=f"no: {KEY}"), captured, KEY)
    plain = _guarded(FailingChatModel(fail_on_call=1, error="no"), captured, KEY)

    async def steps() -> None:
        # The guard raises its own error in place of one that held the key.
        with pytest.raises(ModelError):
            await held.ainvoke("summarize")
        with pytest.raises(RuntimeError):
            await plain.ainvoke("summarize")

    await _in_a_run(captured, steps)

    spans = captured.spans(MODEL_SPAN)
    assert [attributes(span)[ERROR_TYPE] for span in spans] == ["ModelError", "RuntimeError"]
    assert all(attributes(span)[CALL_OUTCOME] == "error" for span in spans)
    assert all(span.status.status_code is StatusCode.ERROR for span in spans)
    assert not any("recognizable" in text for text in captured.texts())


class _StreamsInside(ScriptedChatModel):
    """A model whose ``_agenerate`` streams through its own ``_astream``, as a provider's
    client does when it was built to stream."""

    async def _astream(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> AsyncIterator[ChatGenerationChunk]:
        self.calls += 1
        yield ChatGenerationChunk(message=AIMessageChunk(content="Done."))

    async def _agenerate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        pieces = [piece async for piece in self._astream(messages, stop, run_manager, **kwargs)]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=pieces[0].text))])


async def test_a_library_s_request_is_one_call_however_the_model_streams() -> None:
    captured = Captured()
    inside = _guarded(_StreamsInside(), captured)
    streaming = _guarded(StreamingChatModel(pieces=["Do", "ne."]), captured)

    async def steps() -> None:
        await inside.ainvoke("summarize")
        pieces = [chunk.text async for chunk in inside.astream("summarize")]
        assert "".join(pieces) == "Done."
        # Left before its end: the request is recorded as given up, not left open.
        stream = streaming.astream("summarize")
        assert (await stream.__anext__()).text == "Do"
        await stream.aclose()

    await _in_a_run(captured, steps)

    outcomes = [attributes(span)[CALL_OUTCOME] for span in captured.spans(MODEL_SPAN)]
    assert outcomes == ["ok", "ok", "cancelled"]
    # No scope of a request was left behind by a stream that was read in pieces.
    assert _scope() is None


def test_a_model_the_service_guards_without_telemetry_records_nothing() -> None:
    captured = Captured()
    model = guard_model(ScriptedChatModel(), ())

    assert model.invoke("go").text == "Done."
    assert captured.spans() == []


class _SmallWindow(ScriptedChatModel):
    """A model that says its context is small: large enough for the orchestrator's own prompt
    and tools, and small enough that a second long message has the first one summarized."""

    profile: Any = Field(default_factory=lambda: {"max_input_tokens": 16000})


def test_the_summary_of_a_long_conversation_is_in_the_run_s_trace_as_a_library_s_call(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    captured = Captured()
    model = _SmallWindow(final_text="Noted.")
    made = make_mcp_app(model, {}, telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        first = run(client, "t-1", "zebra " * 5000)
        second = run(client, "t-1", "zebra " * 5000)

    assert first[-1] == second[-1] == ("end", {"status": "completed"})
    spans = captured.spans(MODEL_SPAN)
    # The second run summarizes the first message before it asks the model its own question.
    assert [attributes(span)[MODEL_CALLER] for span in spans] == ["agent", "library", "agent"]
    earlier, later = captured.spans(RUN_SPAN)
    # In its run's trace, under the run: nothing of the agent wraps the library's own request.
    assert parent_of(spans[0]) == earlier.context.span_id
    assert parent_of(spans[1]) == parent_of(spans[2]) == later.context.span_id
    assert captured.count(MODEL_CALLS, **{MODEL_CALLER: "library"}) == 1
    assert model.calls == 3
    assert not any("zebra" in text for text in captured.texts())


def _draft(**values: Any) -> dict[str, Any]:
    return {"provider": "openrouter", "model": "zebra-model", "api_key": KEY, **values}


def test_a_profile_s_test_is_two_calls_of_the_model_marked_as_a_test_s(
    make_llm_app: Callable[..., LlmApp], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-environment-key-9f2c")
    captured = Captured()
    made = make_llm_app(model="openai:env-model", telemetry=captured.telemetry)
    with TestClient(made.app) as client:
        saved = client.post("/llm/profiles", json=_draft(name="Zebra profile"))
        profile = saved.json()["id"]
        of_saved = client.post(f"/llm/profiles/{profile}/test").json()
        of_environment = client.post("/llm/profiles/environment/test").json()
        of_values = client.post("/llm/test", json=_draft(model="written-by-a-request")).json()

    assert of_saved["ok"] and of_environment["ok"] and of_values["ok"]
    found = [attributes(span) for span in captured.spans(MODEL_SPAN)]
    # Two requests a test, each named by what the test asks: a listed profile's provider and
    # model, and for values nobody saved the provider alone.
    saved_call = {
        MODEL_CALLER: "test",
        MODEL_PROVIDER: "openrouter",
        MODEL_NAME: "zebra-model",
        CALL_OUTCOME: "ok",
    }
    environment_call = {**saved_call, MODEL_PROVIDER: "openai", MODEL_NAME: "env-model"}
    values_call = {MODEL_CALLER: "test", MODEL_PROVIDER: "openrouter", CALL_OUTCOME: "ok"}
    assert found == [saved_call] * 2 + [environment_call] * 2 + [values_call] * 2
    assert captured.count(MODEL_CALLS, **{MODEL_CALLER: "test"}) == 6
    # A test is no run.
    assert captured.spans(RUN_SPAN) == [] and captured.points(RUNS_STARTED) == []
    texts = captured.texts()
    assert not any("written-by-a-request" in text or "recognizable" in text for text in texts)


def test_a_profile_s_test_that_fails_or_runs_out_of_time_is_recorded_and_answers_as_before(
    make_llm_app: Callable[..., LlmApp],
) -> None:
    captured = Captured()
    made = make_llm_app(
        model="openai:env-model", telemetry=captured.telemetry, llm_test_timeout_seconds=0.2
    )
    made.models.models["refuses"] = FailingChatModel(fail_on_call=2, error=f"401: {KEY}")
    held = made.models.model("never-answers")
    with TestClient(made.app) as client:
        refused = client.post("/llm/test", json=_draft(model="refuses")).json()
        held.hold()
        late = client.post("/llm/test", json=_draft(model="never-answers")).json()
        with held.held():
            pass  # let the held call go, now that the test gave up on it

    assert (refused["ok"], refused["step"]) == (False, "conversation")
    assert (late["ok"], late["step"]) == (False, "first_message")
    found = [attributes(span) for span in captured.spans(MODEL_SPAN)]
    assert [(call[CALL_OUTCOME], call.get(ERROR_TYPE)) for call in found] == [
        ("ok", None),
        ("error", "RuntimeError"),
        ("cancelled", None),
    ]
    assert not any("recognizable" in text for text in captured.texts())


def test_no_metric_names_a_run_or_a_thread_and_each_instance_counts_its_own(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    one_instance, another = Captured(), Captured()
    first = make_mcp_app(ScriptedChatModel(), {}, telemetry=one_instance.telemetry)
    second = make_mcp_app(ScriptedChatModel(), {}, telemetry=another.telemetry)
    with TestClient(first.app) as client:
        run(client, "t-1")
        run(client, "t-2")
    with TestClient(second.app) as client:
        run(client, "t-3")

    for captured in (one_instance, another):
        for point in captured.points():
            assert RUN_ID not in point.attributes and THREAD_ID not in point.attributes
    assert one_instance.count(RUNS_ENDED) == 2 and another.count(RUNS_ENDED) == 1


def test_every_trace_is_kept_and_no_setting_keeps_a_share_of_them() -> None:
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON

    captured = Captured()
    providers: Any = captured.telemetry._providers

    assert providers[0].sampler is ALWAYS_ON
    assert not [name for name in Settings.model_fields if "sampl" in name or "ratio" in name]


# Part 8: telemetry that fails, a collector that never answers, and what others receive, for
# what feature 011 added to the runs: a library's own call, a profile's test, and a request.


@pytest.mark.parametrize("at", ["start", "end", "count"])
async def test_telemetry_that_fails_changes_no_request_to_the_model(at: str) -> None:
    broken: Any = _Broken(at)
    telemetry = Telemetry(broken, broken, providers=("on",))
    model = guard_model(ScriptedChatModel(), (), record=telemetry.model_request)
    failing = guard_model(
        FailingChatModel(fail_on_call=1, error="no"), (), record=telemetry.model_request
    )
    run_span = telemetry.run(
        thread_id="t-1",
        run_id="r-1",
        mode="run",
        console=False,
        strict=False,
        profile_id="environment",
        profile_source="environment",
        provider=PROVIDER,
        model=MODEL,
    )
    answers: list[str] = []

    async def graph() -> AsyncIterator[str]:
        answers.append((await model.ainvoke("summarize")).text)
        answers.extend([chunk.text async for chunk in model.astream("summarize")])
        # The model's own error is what is raised, not telemetry's.
        with pytest.raises(RuntimeError, match=r"^no$"):
            await failing.ainvoke("summarize")
        yield "item"

    assert [item async for item in run_span.within(graph())] == ["item"]
    run_span.end("end")

    assert "".join(answers) == "Done.Done."
    assert _scope() is None


@pytest.mark.parametrize("at", ["start", "end", "count"])
def test_telemetry_that_fails_changes_no_test_of_a_profile(
    make_llm_app: Callable[..., LlmApp], monkeypatch: pytest.MonkeyPatch, at: str
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-environment-key-9f2c")
    broken: Any = _Broken(at)
    made = make_llm_app(
        model="openai:env-model", telemetry=Telemetry(broken, broken, providers=("on",))
    )
    with TestClient(made.app) as client:
        answered = client.post("/llm/profiles/environment/test")

    assert answered.status_code == 200 and answered.json()["ok"] is True
    assert made.models.model("env-model").calls == 2


class _NeverAnswers(BaseHTTPRequestHandler):
    """A collector that takes a request and does not answer it before it is let go."""

    held = threading.Event()
    asked = threading.Event()

    def do_POST(self) -> None:
        type(self).asked.set()
        type(self).held.wait(30)
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return None


def test_a_collector_that_never_answers_delays_no_run(
    make_mcp_app: Callable[..., McpApp],
) -> None:
    """SC-005: sending is off the path of a run, so a run does not wait for an export."""
    _NeverAnswers.held.clear()
    _NeverAnswers.asked.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _NeverAnswers)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_address[1]}"
        telemetry = Telemetry.configured(endpoint, "freyja-test")
        try:
            script = [ls("l1")]
            with_it = make_mcp_app(ScriptedChatModel(script=script), {}, telemetry=telemetry)
            without = make_mcp_app(ScriptedChatModel(script=script), {})
            kinds = []
            for made in (with_it, without):
                with TestClient(made.app) as client:
                    began = time.monotonic()
                    events = run(client, "t-1")
                    took = time.monotonic() - began
                    kinds.append([kind for kind, _data in events])
                    assert took < telemetry_module.EXPORT_TIMEOUT_SECONDS
            # The same events in the same order as with telemetry off.
            assert kinds[0] == kinds[1] and kinds[0][-1] == "end"
            # The collector was asked, off the run's path, and has not answered.
            providers: Any = telemetry._providers
            threading.Thread(target=providers[0].force_flush, args=(100,), daemon=True).start()
            assert _NeverAnswers.asked.wait(10)
        finally:
            _NeverAnswers.held.set()
            telemetry.close()
    finally:
        server.shutdown()
        server.server_close()


def test_the_spans_that_wait_to_be_sent_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-036: the SDK's queue is given its bound here, past which it drops a span and
    makes nobody wait; nothing is left to its own default or to an ``OTEL_*`` variable."""
    from opentelemetry.sdk.trace import export

    given: dict[str, Any] = {}

    class Recording(export.BatchSpanProcessor):
        def __init__(self, exporter: Any, **kwargs: Any) -> None:
            given.update(kwargs)
            super().__init__(exporter, **kwargs)

    monkeypatch.setattr(export, "BatchSpanProcessor", Recording)
    monkeypatch.setenv("OTEL_BSP_MAX_QUEUE_SIZE", "999999")
    captured = Captured(batch=True)
    captured.telemetry.close()

    assert given["max_queue_size"] == telemetry_module.SPAN_QUEUE_SIZE == 2048
    assert given["max_export_batch_size"] <= given["max_queue_size"]
