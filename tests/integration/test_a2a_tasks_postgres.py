"""003 research R8, R9: the agent task store's contract against a real database.

The tests are the ones in ``tests/test_agent_task_store.py``; only the fixtures differ. Claims
expire by real time here, so ``advance`` waits.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest

from freyja_agent.config import Settings
from freyja_agent.runtime.agent_tasks import AgentTaskStore, PostgresAgentTaskStore
from freyja_agent.runtime.persistence import open_persistence
from tests.test_agent_task_store import (  # noqa: F401 - collected here against Postgres
    Advance,
    test_a_conversation_is_kept_by_its_first_context,
    test_a_pending_task_is_claimed_by_one_instance_until_the_claim_expires,
    test_a_send_is_recorded_once_and_finished,
    test_an_unknown_column_is_refused,
    test_follow_ups_are_set_and_cleared_per_thread,
    test_ready_outcomes_wait_for_delivery_once,
    test_tasks_are_listed_oldest_first_and_never_dropped,
)


@pytest.fixture
async def store(settings: Settings) -> AsyncIterator[AgentTaskStore]:
    persistence = await open_persistence(settings)
    try:
        store = PostgresAgentTaskStore(persistence.pool)
        await store.setup()
        await store.setup()  # idempotent
        yield store
    finally:
        await persistence.close()


@pytest.fixture
def advance() -> Advance:
    async def _advance(seconds: float) -> None:
        await asyncio.sleep(seconds)

    return _advance
