"""FR-006: one active run per thread, as an expiring lease."""

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from freyja_agent.runtime.locks import InMemoryRunLocks
from freyja_agent.runtime.runtime import Runtime
from tests.fakes import FailingChatModel, FakeClock, ScriptedChatModel


def make_locks(clock: FakeClock, ttl: float = 60) -> InMemoryRunLocks:
    return InMemoryRunLocks(ttl, clock=clock.now)


async def test_acquire_is_exclusive_while_unexpired(clock: FakeClock) -> None:
    locks = make_locks(clock)
    assert await locks.acquire("t-1", "a:1") is True
    assert await locks.acquire("t-1", "b:1") is False
    assert await locks.is_active("t-1") is True
    assert await locks.acquire("t-2", "b:1") is True


async def test_release_frees_the_thread(clock: FakeClock) -> None:
    locks = make_locks(clock)
    await locks.acquire("t-1", "a:1")
    await locks.release("t-1", "a:1")
    assert await locks.is_active("t-1") is False
    assert await locks.acquire("t-1", "b:1") is True


async def test_release_with_wrong_owner_is_a_no_op(clock: FakeClock) -> None:
    locks = make_locks(clock)
    await locks.acquire("t-1", "a:1")
    await locks.release("t-1", "b:1")
    assert await locks.is_active("t-1") is True


async def test_expiry_lets_a_new_owner_take_over(clock: FakeClock) -> None:
    locks = make_locks(clock, ttl=60)
    await locks.acquire("t-1", "a:1")
    clock.advance(59)
    assert await locks.acquire("t-1", "b:1") is False
    clock.advance(2)
    assert await locks.is_active("t-1") is False
    assert await locks.acquire("t-1", "b:1") is True


async def test_renew_moves_the_expiry_forward(clock: FakeClock) -> None:
    locks = make_locks(clock, ttl=60)
    await locks.acquire("t-1", "a:1")
    clock.advance(50)
    await locks.renew("t-1", "a:1")
    clock.advance(50)
    assert await locks.is_active("t-1") is True
    assert await locks.acquire("t-1", "b:1") is False


async def test_renew_by_wrong_owner_does_not_extend(clock: FakeClock) -> None:
    locks = make_locks(clock, ttl=60)
    await locks.acquire("t-1", "a:1")
    clock.advance(50)
    await locks.renew("t-1", "b:1")
    clock.advance(20)
    assert await locks.is_active("t-1") is False


# API-level: the lease is released on every exit path of a stream (research R12).


async def _active(runtime: Runtime, thread_id: str) -> bool:
    return await runtime.locks.is_active(thread_id)


def test_lease_is_released_after_a_completed_run(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    runtime = make_runtime(ScriptedChatModel())
    with TestClient(make_app(runtime)) as client:
        with client.stream("POST", "/threads/l-1/runs", json={"message": "hi"}) as response:
            assert sse_events(response.iter_lines())[-1][0] == "end"
        assert client.portal.call(_active, runtime, "l-1") is False


def test_lease_is_released_after_an_error(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    sse_events: Any,
) -> None:
    runtime = make_runtime(FailingChatModel(fail_on_call=1))
    with TestClient(make_app(runtime)) as client:
        with client.stream("POST", "/threads/l-2/runs", json={"message": "hi"}) as response:
            assert sse_events(response.iter_lines())[-1][0] == "error"
        assert client.portal.call(_active, runtime, "l-2") is False


def test_lease_is_released_when_the_client_disconnects_early(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
) -> None:
    script = [
        AIMessage(content="", tool_calls=[{"id": f"c{i}", "name": "ls", "args": {"path": "/"}}])
        for i in range(5)
    ]
    runtime = make_runtime(ScriptedChatModel(script=script))
    with TestClient(make_app(runtime)) as client:
        with client.stream("POST", "/threads/l-3/runs", json={"message": "hi"}) as response:
            lines = response.iter_lines()
            for line in lines:
                if line == "event: update":
                    break
            # Leaving the block closes the connection while the run is still streaming.
        assert client.portal.call(_active, runtime, "l-3") is False
