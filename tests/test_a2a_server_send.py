"""009: an agent hands Freyja a task and reads the result (User Story 1, FR-001).

Part 1, before anything is served: the stand-in caller, the protocol's own client, is shown
against the stand-in agent, so that its plumbing is known before it calls Freyja; and with the
feature off and the tables empty the service is what it was, apart from asking, once for each
context taken for a thread, whether a caller started the thread (research R6).
"""

from collections.abc import Callable
from typing import Any

from a2a.types import Task
from fastapi.testclient import TestClient

from freyja_agent.runtime.received_tasks import InMemoryReceivedTaskStore
from tests.a2a_caller import HOPS_KEY, StandInCaller, as_json, message, parser
from tests.a2a_stub import make_stub
from tests.asgi_stream import StreamingASGITransport
from tests.conftest import ServerApp
from tests.fakes import ScriptedChatModel


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
