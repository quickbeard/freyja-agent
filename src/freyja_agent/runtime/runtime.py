"""The running service's shared objects: the agent, the run-lock store, and this instance's id."""

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from freyja_agent.agent.factory import build_agent
from freyja_agent.config import Settings
from freyja_agent.runtime.locks import PostgresRunLocks, RunLocks
from freyja_agent.runtime.persistence import open_persistence

logger = logging.getLogger(__name__)


@dataclass
class Runtime:
    agent: Any
    locks: RunLocks
    instance_id: str
    settings: Settings

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        settings: Settings,
        *,
        model: str | BaseChatModel | None = None,
        instance_id: str | None = None,
    ) -> AsyncIterator["Runtime"]:
        """Open persistence, build the agent, and yield the runtime; close the pool on exit.

        ``model`` and ``instance_id`` are injectable so integration tests can open two
        runtimes against one database with a scripted model.
        """
        persistence = await open_persistence(settings)
        try:
            locks = PostgresRunLocks(persistence.pool, settings.run_lock_ttl_seconds)
            await locks.setup()
            logger.info("Persistence ready: checkpointer, store, and run-lock table")
            agent = build_agent(
                model if model is not None else settings.model,
                checkpointer=persistence.saver,
                store=persistence.store,
                workspace_dir=settings.workspace_dir,
            )
            yield cls(
                agent=agent,
                locks=locks,
                instance_id=instance_id or uuid.uuid4().hex,
                settings=settings,
            )
        finally:
            await persistence.close()
