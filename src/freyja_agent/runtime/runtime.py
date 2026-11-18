"""The running service's shared objects: the agent and its catalog, the links to the tool
servers and the A2A agents, the run-lock, agent task, and LLM profile stores, the model
manager, the delegation worker, and this instance's id."""

import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from freyja_agent.agent.catalog import RESERVED_TOOL_NAMES, Catalog
from freyja_agent.agent.factory import build_agent, resolve_model
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
from freyja_agent.llm.active import ENVIRONMENT_PROFILE, ActiveModel, ModelManager
from freyja_agent.llm.check import CheckSlots, LastCheck
from freyja_agent.llm.providers import (
    PROVIDERS,
    EnvironmentProfile,
    ModelFactory,
    ModelSpec,
    build_model,
    environment_profile,
    environment_spec,
)
from freyja_agent.llm.store import (
    InMemoryLlmProfileStore,
    LlmProfileStore,
    PostgresLlmProfileStore,
)
from freyja_agent.runtime.agent_tasks import (
    AgentTaskStore,
    InMemoryAgentTaskStore,
    PostgresAgentTaskStore,
)
from freyja_agent.runtime.locks import PostgresRunLocks, RunLocks
from freyja_agent.runtime.persistence import open_persistence

logger = logging.getLogger(__name__)


def _given(model: BaseChatModel) -> BaseChatModel:
    """The chat model a caller handed to ``assemble``, standing for the environment's."""
    return model


def _already_built(_spec: ModelSpec) -> None:
    """Stands in for a model factory when the model is known to build."""


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
    # Saved LLM profiles and which one is active (feature 004, research R5).
    llm_profiles: LlmProfileStore = field(default_factory=InMemoryLlmProfileStore)
    # Keeps ``agent`` and ``catalog`` in step with the active profile (research R1, R6). None
    # for a runtime put together by hand, which then always runs on the model it was given.
    models: ModelManager | None = None
    # The environment's model as a read-only profile, described once at startup: what the
    # environment configures cannot change while the service runs (FR-008).
    environment: EnvironmentProfile | None = None
    # What a profile's test needs (FR-017 to FR-019b): the factory that turns values into a
    # model, the environment's values or the model that was handed over built, the tests in
    # progress on this instance, and the environment's last result, which is kept in memory
    # until the service starts again.
    model_factory: ModelFactory = build_model
    environment_model: BaseChatModel | None = None
    environment_values: ModelSpec | None = None
    environment_secrets: tuple[str, ...] = ()
    checks: CheckSlots = field(default_factory=CheckSlots)
    environment_test: LastCheck | None = None

    def environment_profile(self) -> EnvironmentProfile:
        """The environment's profile; worked out from the settings for a hand-built runtime."""
        if self.environment is None:
            self.environment = environment_profile(self.settings)
        return self.environment

    def model_to_check(self, spec: ModelSpec | None) -> BaseChatModel:
        """The model a profile's test asks: built for ``spec``, or the environment's for None.

        It is built to try nothing twice, so that the test's time limit is a limit (research
        R7). A model that was handed to the runtime built is asked as it is.
        """
        if spec is None:
            if self.environment_model is not None:
                return self.environment_model
            spec = self.environment_values or environment_spec(self.settings)
        return self.model_factory(replace(spec, max_retries=0))

    def applied_model(self) -> ActiveModel:
        """The model this instance last built its orchestrator for; reads nothing."""
        if self.models is None:
            return ActiveModel(revision=0, profile=ENVIRONMENT_PROFILE)
        return self.models.active

    async def model_for_run(self) -> ActiveModel:
        """Bring the orchestrator in step with the active profile, and say which it is.

        Called once at the start of every run. A run takes ``agent`` right after it, so it
        keeps the orchestrator it started on whatever is activated meanwhile (FR-013).
        """
        if self.models is None:
            return self.applied_model()
        orchestrator, active = await self.models.current()
        self.agent, self.catalog = orchestrator.agent, orchestrator.catalog
        return active

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
        llm_profiles: LlmProfileStore | None = None,
        model_factory: ModelFactory | None = None,
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

        Feature 004 builds the orchestrator through the model manager, for the active LLM
        profile in ``llm_profiles``, and keeps what a rebuild needs, so that a later change of
        profile reuses these links and tools. ``model_factory`` turns a profile into a chat
        model and is the seam the tests swap (004 research R18). ``model``, a string or a chat
        model, stands for the environment's model, as it always has.
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
                profile_store = (
                    llm_profiles if llm_profiles is not None else InMemoryLlmProfileStore()
                )
                spec = environment_spec(settings, model if isinstance(model, str) else None)
                provider = PROVIDERS.get(spec.provider)
                key = os.environ.get(provider.key_variable, "") if provider else ""
                if isinstance(model, BaseChatModel):
                    environment = partial(_given, model)
                elif model_factory is not None:
                    environment = partial(model_factory, spec)
                else:
                    # The path the environment's model has always taken (001 research R3).
                    environment = partial(
                        resolve_model, spec.name, openai_responses_api=spec.responses_api
                    )
                manager = ModelManager(
                    store=profile_store,
                    secret=settings.secret_key,
                    factory=model_factory or build_model,
                    environment=environment,
                    environment_secrets=(key,) if key else (),
                    build=partial(
                        build_agent,
                        checkpointer=checkpointer,
                        store=store,
                        workspace_dir=settings.workspace_dir,
                        max_steps=settings.max_steps,
                        delegate_tools=[
                            *server_tools,
                            *(tool for link in agents for tool in agent_tools(link, task_store)),
                        ],
                        # Agent tasks exist only with agents: without any, the orchestrator
                        # is what it was before feature 003 (FR-024).
                        agent_tasks=task_store if agents else None,
                        openai_responses_api=settings.openai_responses_api,
                    ),
                )
                orchestrator = await manager.load()
                # Whether the environment's model can be built is known when the manager has
                # just built it, or it was handed over built; otherwise building one says.
                given = isinstance(model, BaseChatModel)
                built = given or manager.active.profile is ENVIRONMENT_PROFILE
                described = environment_profile(
                    settings,
                    factory=_already_built if built else model_factory or build_model,
                    model=model if isinstance(model, str) else None,
                )
                if given:
                    # A model handed over needs no key from the environment.
                    described = replace(described, usable=True, unusable_reason=None)
                runtime = cls(
                    agent=orchestrator.agent,
                    catalog=orchestrator.catalog,
                    servers=servers,
                    agents=agents,
                    agent_tasks=task_store,
                    llm_profiles=profile_store,
                    models=manager,
                    environment=described,
                    model_factory=model_factory or build_model,
                    environment_model=model if given else None,
                    environment_values=spec,
                    environment_secrets=(key,) if key else (),
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
            llm_profiles = PostgresLlmProfileStore(persistence.pool)
            await llm_profiles.setup()
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
                llm_profiles=llm_profiles,
            ) as runtime:
                yield runtime
        finally:
            await persistence.close()
