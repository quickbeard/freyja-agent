"""FR-006: one active run per thread, as an expiring lease."""

from freyja_agent.runtime.locks import InMemoryRunLocks
from tests.fakes import FakeClock


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
