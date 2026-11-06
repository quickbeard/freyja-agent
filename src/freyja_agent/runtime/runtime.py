"""The running service's shared objects: the agent and its catalog, the links to the tool
servers, the run-lock store, and this instance's id."""

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from freyja_agent.agent.catalog import Catalog
from freyja_agent.agent.factory import build_agent
from freyja_agent.config import Settings
from freyja_agent.delegates.mcp_servers import (
    Connect,
    ToolServer,
    default_connect,
    open_tool_servers,
)
from freyja_agent.runtime.locks import PostgresRunLocks, RunLocks
from freyja_agent.runtime.persistence import open_persistence

logger = logging.getLogger(__name__)


@dataclass
class Runtime:
    agent: Any
    locks: RunLocks
    instance_id: str
    settings: Settings
    # Exactly the tools the orchestrator was given, with their classification.
    catalog: Catalog
    # One link per configured tool server; empty when none is configured.
    servers: tuple[ToolServer, ...] = ()

    @classmethod
    @asynccontextmanager
    async def assemble(
        cls,
        settings: Settings,
        *,
        checkpointer: BaseCheckpointSaver,
        store: BaseStore,
        locks: RunLocks,
        model: str | BaseChatModel | None = None,
        instance_id: str | None = None,
        connect: Connect = default_connect,
    ) -> AsyncIterator["Runtime"]:
        """Connect the tool servers, build the agent with their tools, and yield the runtime.

        This is everything between persistence and serving, and the service and the tests both
        go through it: tests pass in-memory persistence and a ``connect`` that returns a stub
        server, which swaps the transport and nothing else (constitution principle III). A
        tool server that cannot be used raises here, before anything is served (FR-004); the
        links are closed on the way out (FR-005).
        """
        async with open_tool_servers(
            settings.mcp_servers,
            connect=connect,
            call_timeout=settings.mcp_call_timeout_seconds,
            startup_timeout=settings.mcp_startup_timeout_seconds,
        ) as servers:
            orchestrator = build_agent(
                model if model is not None else settings.model,
                checkpointer=checkpointer,
                store=store,
                workspace_dir=settings.workspace_dir,
                max_steps=settings.max_steps,
                delegate_tools=[tool for server in servers for tool in server.tools],
            )
            yield cls(
                agent=orchestrator.agent,
                catalog=orchestrator.catalog,
                servers=servers,
                locks=locks,
                instance_id=instance_id or uuid.uuid4().hex,
                settings=settings,
            )

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        settings: Settings,
        *,
        model: str | BaseChatModel | None = None,
        instance_id: str | None = None,
        connect: Connect = default_connect,
    ) -> AsyncIterator["Runtime"]:
        """Open persistence, assemble the runtime, and yield it; close the pool last.

        ``model``, ``instance_id``, and ``connect`` are injectable so integration tests can
        open two runtimes against one database with a scripted model. The pool closes after
        the tool servers, and also when a tool server fails to connect.
        """
        persistence = await open_persistence(settings)
        try:
            locks = PostgresRunLocks(persistence.pool, settings.run_lock_ttl_seconds)
            await locks.setup()
            logger.info("Persistence ready: checkpointer, store, and run-lock table")
            async with cls.assemble(
                settings,
                checkpointer=persistence.saver,
                store=persistence.store,
                locks=locks,
                model=model,
                instance_id=instance_id,
                connect=connect,
            ) as runtime:
                yield runtime
        finally:
            await persistence.close()
