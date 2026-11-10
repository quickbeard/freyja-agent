"""The running service's shared objects: the agent and its catalog, the links to the tool
servers and the A2A agents, the run-lock and agent task stores, the delegation worker, and this
instance's id."""

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from freyja_agent.agent.catalog import RESERVED_TOOL_NAMES, Catalog
from freyja_agent.agent.factory import build_agent
from freyja_agent.config import Settings
from freyja_agent.delegates.a2a_agents import (
    AgentLink,
    AgentTransport,
    default_transport,
    open_agents,
)
from freyja_agent.delegates.a2a_tools import agent_tools
from freyja_agent.delegates.delegation_worker import DelegationWorker
from freyja_agent.delegates.mcp_servers import (
    Connect,
    ToolServer,
    default_connect,
    open_tool_servers,
)
from freyja_agent.runtime.agent_tasks import (
    AgentTaskStore,
    InMemoryAgentTaskStore,
    PostgresAgentTaskStore,
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
    # One link per configured A2A agent; empty when none is configured (feature 003).
    agents: tuple[AgentLink, ...] = ()
    # Conversations, sends, and tasks with agents (feature 003, research R8).
    agent_tasks: AgentTaskStore = field(default_factory=InMemoryAgentTaskStore)
    # Watches pending agent tasks and delivers their outcomes (feature 003, research R9).
    worker: DelegationWorker | None = None

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
        agent_tasks: AgentTaskStore | None = None,
        agent_transport: AgentTransport = default_transport,
        run_worker: bool = True,
    ) -> AsyncIterator["Runtime"]:
        """Connect the tool servers and the agents, build the agent with their tools, and yield.

        Feature 003 adds the agents, opened after the tool servers so a name shared with a
        server is refused naming the agent, and ``agent_transport``, the httpx transport each
        agent link uses: the one other seam the tests swap (research R15). The delegation
        worker is built after the agent and its loop runs while the runtime is open; it stops
        before the links close. ``run_worker=False`` builds it without its loop, for tests
        that call ``worker.tick()`` themselves.

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
            server_tools = [tool for server in servers for tool in server.tools]
            task_store = agent_tasks if agent_tasks is not None else InMemoryAgentTaskStore()
            async with open_agents(
                settings.a2a_agents,
                transport=agent_transport,
                startup_timeout=settings.a2a_startup_timeout_seconds,
                call_timeout=settings.a2a_call_timeout_seconds,
                server_names=set(settings.mcp_servers),
                taken_tool_names=RESERVED_TOOL_NAMES | {tool.name for tool in server_tools},
            ) as agents:
                orchestrator = build_agent(
                    model if model is not None else settings.model,
                    checkpointer=checkpointer,
                    store=store,
                    workspace_dir=settings.workspace_dir,
                    max_steps=settings.max_steps,
                    delegate_tools=[
                        *server_tools,
                        *(tool for link in agents for tool in agent_tools(link, task_store)),
                    ],
                    agent_tasks=task_store,
                )
                runtime = cls(
                    agent=orchestrator.agent,
                    catalog=orchestrator.catalog,
                    servers=servers,
                    agents=agents,
                    agent_tasks=task_store,
                    locks=locks,
                    instance_id=instance_id or uuid.uuid4().hex,
                    settings=settings,
                )
                runtime.worker = DelegationWorker(runtime)
                if run_worker:
                    runtime.worker.start()
                try:
                    yield runtime
                finally:
                    await runtime.worker.stop()

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        settings: Settings,
        *,
        model: str | BaseChatModel | None = None,
        instance_id: str | None = None,
        connect: Connect = default_connect,
        agent_transport: AgentTransport = default_transport,
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
            agent_tasks = PostgresAgentTaskStore(persistence.pool)
            await agent_tasks.setup()
            logger.info("Persistence ready: checkpointer, store, and run-lock table")
            async with cls.assemble(
                settings,
                checkpointer=persistence.saver,
                store=persistence.store,
                locks=locks,
                model=model,
                instance_id=instance_id,
                connect=connect,
                agent_tasks=agent_tasks,
                agent_transport=agent_transport,
            ) as runtime:
                yield runtime
        finally:
            await persistence.close()
