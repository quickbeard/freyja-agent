"""FR-002 to FR-006: the run stream contract, continuity, the debug flag, one run per thread."""

import threading
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from freyja_agent.runtime.runtime import Runtime
from tests.fakes import FailingChatModel, ScriptedChatModel

Events = list[tuple[str, Any]]


def run(client: TestClient, thread_id: str, message: str, sse_events: Any, **params: Any) -> Events:
    with client.stream(
        "POST", f"/threads/{thread_id}/runs", json={"message": message}, params=params
    ) as response:
        assert response.status_code == 200, response.read()
        return sse_events(response.iter_lines())


def kinds(events: Events) -> list[str]:
    return [kind for kind, _ in events]


def test_stream_starts_then_updates_and_messages_then_ends(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    model = ScriptedChatModel(
        script=[
            AIMessage(content="", tool_calls=[{"id": "c1", "name": "ls", "args": {"path": "/"}}])
        ],
        final_text="Here is what I found.",
    )
    with TestClient(make_app(make_runtime(model))) as client:
        events = run(client, "t-1", "list the workspace", sse_events)

    assert events[0][0] == "start"
    assert events[0][1] == {
        "thread_id": "t-1",
        "run_id": events[0][1]["run_id"],
        "mode": "run",
        "debug": False,
    }
    assert len(events[0][1]["run_id"]) == 32
    assert events[-1] == ("end", {"status": "completed"})
    middle = kinds(events[1:-1])
    assert "update" in middle and "message" in middle
    assert "debug" not in middle
    assert set(middle) <= {"update", "message"}

    nodes = [data["node"] for kind, data in events if kind == "update"]
    assert "model" in nodes and "tools" in nodes
    tool_update = next(
        data for kind, data in events if kind == "update" and data["node"] == "tools"
    )
    assert tool_update["data"]["messages"][0]["type"] == "tool"
    assert tool_update["data"]["messages"][0]["tool_call_id"] == "c1"

    message_payloads = [data for kind, data in events if kind == "message"]
    assert message_payloads[-1]["message"]["type"] == "ai"
    assert message_payloads[-1]["message"]["content"] == "Here is what I found."
    assert message_payloads[-1]["metadata"]["langgraph_node"] == "model"


def test_start_is_emitted_before_the_model_is_called(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    model = ScriptedChatModel()
    calls_at_start: list[int] = []
    with (
        TestClient(make_app(make_runtime(model))) as client,
        client.stream("POST", "/threads/t-2/runs", json={"message": "hi"}) as response,
    ):
        lines = response.iter_lines()
        first = next(lines)
        while first == "" or first.startswith(":"):
            first = next(lines)
        assert first == "event: start"
        calls_at_start.append(model.calls)
        list(lines)
    assert calls_at_start == [0]
    assert model.calls == 1


def test_empty_message_is_rejected_and_starts_no_run(
    make_runtime: Callable[..., Runtime], make_app: Callable[[Runtime], FastAPI]
) -> None:
    model = ScriptedChatModel()
    with TestClient(make_app(make_runtime(model))) as client:
        response = client.post("/threads/t-3/runs", json={"message": ""})
    assert response.status_code == 422
    assert model.calls == 0


@pytest.mark.parametrize(
    ("request_kwargs", "location"),
    [
        pytest.param({"json": {"message": "x" * 32001}}, ["body", "message"], id="too-long"),
        pytest.param({"json": {}}, ["body", "message"], id="missing-message"),
        pytest.param({"json": {"message": 5}}, ["body", "message"], id="wrong-type"),
        pytest.param(
            {"content": b"{not json", "headers": {"content-type": "application/json"}},
            ["body", 1],
            id="invalid-json",
        ),
        pytest.param(
            {"json": {"message": "hi"}, "params": {"debug": "maybe"}},
            ["query", "debug"],
            id="debug-not-boolean",
        ),
    ],
)
def test_malformed_requests_are_rejected_and_start_no_run(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    request_kwargs: dict[str, Any],
    location: list,
) -> None:
    model = ScriptedChatModel()
    with TestClient(make_app(make_runtime(model))) as client:
        response = client.post("/threads/t-3b/runs", **request_kwargs)
        never_used = client.get("/threads/t-3b/state")
    assert response.status_code == 422
    entry = response.json()["detail"][0]
    assert entry["loc"] == location
    assert {"type", "loc", "msg"} <= set(entry)
    assert model.calls == 0
    assert never_used.status_code == 404


def test_every_offending_field_is_named_in_one_response(
    make_runtime: Callable[..., Runtime], make_app: Callable[[Runtime], FastAPI]
) -> None:
    with TestClient(make_app(make_runtime(ScriptedChatModel()))) as client:
        response = client.post("/threads/bad id/runs", json={"message": ""})
    assert response.status_code == 422
    assert [entry["loc"] for entry in response.json()["detail"]] == [
        ["path", "thread_id"],
        ["body", "message"],
    ]


def test_message_length_limit_is_inclusive(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    with TestClient(make_app(make_runtime(ScriptedChatModel()))) as client:
        events = run(client, "t-3c", "x" * 32000, sse_events)
    assert events[-1] == ("end", {"status": "completed"})


def test_undefined_fields_are_ignored_and_whitespace_is_a_message(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    model = ScriptedChatModel()
    with (
        TestClient(make_app(make_runtime(model))) as client,
        client.stream(
            "POST", "/threads/t-3d/runs", json={"message": "   ", "not_a_field": 1}
        ) as response,
    ):
        assert response.status_code == 200
        events = sse_events(response.iter_lines())
    assert events[-1] == ("end", {"status": "completed"})
    assert [m.content for m in model.seen[0] if isinstance(m, HumanMessage)] == ["   "]


def test_follow_up_sees_the_earlier_turn(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    model = ScriptedChatModel(final_text="ok")
    with TestClient(make_app(make_runtime(model))) as client:
        run(client, "t-4", "my name is Ada", sse_events)
        run(client, "t-4", "what is my name?", sse_events)
    second_turn = model.seen[1]
    humans = [m.content for m in second_turn if isinstance(m, HumanMessage)]
    assert humans == ["my name is Ada", "what is my name?"]


def test_debug_flag_adds_debug_events_without_changing_the_outcome(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    model = ScriptedChatModel(final_text="same answer")
    with TestClient(make_app(make_runtime(model))) as client:
        plain = run(client, "t-5", "hi", sse_events)
        verbose = run(client, "t-6", "hi", sse_events, debug="true")
    assert "debug" not in kinds(plain)
    assert "debug" in kinds(verbose)
    assert verbose[0][1]["debug"] is True
    assert plain[-1] == verbose[-1] == ("end", {"status": "completed"})
    assert [e for e in kinds(verbose) if e != "debug"] == kinds(plain)


def test_second_run_on_an_active_thread_is_refused(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    gate = threading.Event()
    release = threading.Event()

    class BlockingModel(ScriptedChatModel):
        def next_message(self) -> AIMessage:
            gate.set()
            release.wait(timeout=5)
            return super().next_message()

    model = BlockingModel(final_text="done")
    runtime = make_runtime(model)
    with TestClient(make_app(runtime)) as client:
        first: dict[str, Any] = {}

        def stream_first() -> None:
            first["events"] = run(client, "t-7", "slow", sse_events)

        worker = threading.Thread(target=stream_first)
        worker.start()
        assert gate.wait(timeout=5)
        second = client.post("/threads/t-7/runs", json={"message": "again"})
        release.set()
        worker.join(timeout=10)

    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "run_active"
    assert first["events"][-1] == ("end", {"status": "completed"})
    assert model.calls == 1


def test_model_failure_ends_the_stream_with_an_error_and_frees_the_thread(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    model = FailingChatModel(fail_on_call=1, error="provider unavailable")
    runtime = make_runtime(model)
    with TestClient(make_app(runtime)) as client:
        events = run(client, "t-8", "hi", sse_events)
        assert events[0][0] == "start"
        assert events[-1][0] == "error"
        assert events[-1][1]["code"] == "model_error"
        assert "provider unavailable" in events[-1][1]["message"]

        again = run(client, "t-8", "hi again", sse_events)
    assert again[-1] == ("end", {"status": "completed"})
