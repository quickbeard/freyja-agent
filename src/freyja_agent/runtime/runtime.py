"""The running service's shared objects: the agent and its catalog, the links to the tool
servers, the registry of A2A agents, the run-lock, agent task, saved agent, and LLM profile
stores, the model manager, the delegation worker, and this instance's id.

Since feature 005 the orchestrator a piece of work uses is taken as a ``RunContext``: the
graph, the catalog, the active model, and the agent set, all of one build, held until the
context is released (005 research R3).

Since feature 009 there are two builds while receiving tasks from other agents is on: the
ordinary one, and a strict one for the threads callers start over A2A, on which every message
to a delegate agent waits for a person, a named caller works in a file area of its own, and
the model is told an alias in place of its thread's id. A context taken for a thread asks the
received-task store who started it, with the feature on or off, and is the strict build's
for a caller's thread (research R6).
"""

import logging
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from freyja_agent.agent.caller_files import CallerFiles
from freyja_agent.agent.catalog import RESERVED_TOOL_NAMES, Catalog
from freyja_agent.agent.factory import (
    SKILLS_SOURCE,
    Orchestrator,
    build_agent,
    resolve_model,
    workspace_backend,
)
from freyja_agent.config import Settings
from freyja_agent.delegates.a2a_agents import AgentLink, AgentTransport, default_transport
from freyja_agent.delegates.a2a_tools import agent_tools
from freyja_agent.delegates.agent_registry import AgentRegistry, AgentSet
from freyja_agent.delegates.delegation_worker import DelegationWorker
from freyja_agent.delegates.mcp_servers import (
    Connect,
    ToolServer,
    default_connect,
    open_tool_servers,
)
from freyja_agent.llm.active import (
    ENVIRONMENT_PROFILE,
    UNUSABLE,
    ActiveModel,
    ModelManager,
    unusable_reason,
)
from freyja_agent.llm.check import CheckSlots, LastCheck
from freyja_agent.llm.providers import (
    PROVIDERS,
    EnvironmentProfile,
    ModelFactory,
    ModelSpec,
    build_model,
    environment_access,
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
from freyja_agent.runtime.received_tasks import (
    Conversation,
    InMemoryReceivedTaskStore,
    PostgresReceivedTaskStore,
    ReceivedTaskStore,
)
from freyja_agent.runtime.saved_agents import (
    InMemorySavedAgentStore,
    PostgresSavedAgentStore,
    SavedAgentStore,
)

# Feature 007: the index of threads, kept through the run lease.
from freyja_agent.runtime.thread_index import (
    IndexedRunLocks,
    InMemoryThreadIndex,
    PostgresThreadIndex,
    ThreadIndex,
)

# Feature 007: runs that go on without a reader, and the stop.
from freyja_agent.runtime.unattended import SegmentedGraph, UnattendedRuns

logger = logging.getLogger(__name__)

# The two refusals of a card read that cannot start now (005 FR-014, research R15).
CARD_READ_BUSY = "This agent is already being checked. Wait for that check to end."
CARD_READ_FULL = (
    "{at_once} agent cards are already being read on this instance. Try again when one of "
    "those reads ends."
)


def _card_reads() -> CheckSlots:
    return CheckSlots(busy=CARD_READ_BUSY, full=CARD_READ_FULL)


def _given(model: BaseChatModel) -> BaseChatModel:
    """The chat model a caller handed to ``assemble``, standing for the environment's."""
    return model


def _first_then(first: list[BaseChatModel], build: Callable[[], BaseChatModel]) -> BaseChatModel:
    """The model built beforehand, the first time it is asked for; a new one after that."""
    return first.pop() if first else build()


def _already_built(_spec: ModelSpec) -> None:
    """Stands in for a model factory when the model is known to build."""


def _build(
    model: BaseChatModel,
    agents: AgentSet | None,
    *,
    server_tools: Sequence[Any],
    task_store: AgentTaskStore,
    saved_store: SavedAgentStore,
    strict: bool = False,
    files_dir: str | None = None,
    **options: Any,
) -> Orchestrator:
    """The orchestrator around ``model`` with the tool servers' tools and the set's agents'.

    Each agent's two tools take their classification from the set, which carries the
    approval choice, and a saved agent's tools are given the store they read their agent
    from before a send (005 research R7). Agent tasks exist only with agents: without any,
    the orchestrator is what it was before feature 003 (003 FR-024).

    ``strict`` builds the orchestrator of callers' threads (feature 009, research R6): every
    agent tool gated and checked for a person's decision before a send, the files of the
    caller the run is for (``CallerFiles``, under ``files_dir``), and the thread named by its
    alias.
    """
    links = agents.links if agents is not None else ()
    if strict:
        workspace = options["workspace_dir"]
        options = {
            **options,
            "backend": CallerFiles(
                workspace_backend(workspace),
                files_dir or "",
                Path(workspace) / SKILLS_SOURCE.strip("/"),
            ),
            "alias_threads": True,
        }
    return build_agent(
        model,
        delegate_tools=[
            *server_tools,
            *(
                tool
                for link in links
                for tool in agent_tools(
                    link,
                    task_store,
                    gated=agents.gated(link),
                    saved=saved_store,
                    strict=strict,
                )
            ),
        ],
        agent_tasks=task_store if links else None,
        **options,
    )


@dataclass
class RunContext:
    """One orchestrator for one piece of work (005 data-model.md "Run context").

    The graph, the catalog, the active model, and the agent set belong to one build. A run
    takes a context under its thread's lease and uses it from its first read of the thread's
    state to its last event; a read takes one for as long as it answers. The set, and with it
    every link the orchestrator's tools use, stays open until ``release``.
    """

    agent: Any
    catalog: Catalog
    active: ActiveModel
    # None for a runtime put together by hand, which has no registry.
    agents: AgentSet | None = None
    registry: AgentRegistry | None = field(default=None, repr=False)
    released: bool = False
    # Feature 009: whether the graph is the strict build's, which runs callers' threads.
    strict: bool = False

    @property
    def links(self) -> tuple[AgentLink, ...]:
        return self.agents.links if self.agents is not None else ()

    async def release(self) -> None:
        """Give the set back. A second call does nothing."""
        if self.released:
            return
        self.released = True
        if self.registry is not None and self.agents is not None:
            await self.registry.release(self.agents)


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
    # The agents this instance has tools for (feature 005, research R3). None for a runtime
    # put together by hand, which has no agents.
    registry: AgentRegistry | None = None
    # The agents an operator saved (feature 005, research R1).
    saved_agents: SavedAgentStore = field(default_factory=InMemorySavedAgentStore)
    # The card reads a person asked for that are in progress on this instance (005 FR-014).
    card_reads: CheckSlots = field(default_factory=_card_reads)
    # The agent set ``agent`` and ``catalog`` were last built for, so that what a read falls
    # back to is one build and not the tools of one set beside the agents of another.
    built_for: AgentSet | None = None
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
    # Which threads have run and where each stands (feature 007, research R6). A runtime
    # put together by hand has an index nothing writes to: its lease is not wrapped.
    threads: ThreadIndex = field(default_factory=InMemoryThreadIndex)
    # The console runs of this instance (feature 007, research R2). None for a runtime put
    # together by hand, whose runs are all passed straight to the graph.
    unattended: UnattendedRuns | None = None
    # The wrapper of the graph last built, so that every context of one build holds one graph.
    segmented: SegmentedGraph | None = field(default=None, repr=False)
    # Feature 009: the conversations and received tasks of callers (research R8), and what
    # this instance last built of the strict orchestrator, kept apart from the other build's
    # so that neither is ever answered with the other's (research R6).
    received_tasks: ReceivedTaskStore = field(default_factory=InMemoryReceivedTaskStore)
    strict_agent: Any = None
    strict_catalog: Catalog | None = None
    strict_segmented: SegmentedGraph | None = field(default=None, repr=False)

    @property
    def agents(self) -> tuple[AgentLink, ...]:
        """The links of the current set: one per agent in use on this instance."""
        return self.registry.current.links if self.registry is not None else ()

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

    def last_built(self, *, strict: bool = False) -> RunContext:
        """What this instance last built, with its last set of agents, holding nothing.

        What a read falls back to when the current set cannot be learned (research R3).
        ``strict`` asks for the strict build's, when one was built (feature 009).
        """
        if strict and self.strict_agent is not None and self.strict_catalog is not None:
            agent, catalog = self.strict_agent, self.strict_catalog
        else:
            agent, catalog = self.agent, self.catalog
        return RunContext(
            agent=agent,
            catalog=catalog,
            active=self.applied_model(),
            agents=self.built_for,
        )

    async def begin_read(self, thread_id: str | None = None) -> RunContext:
        """A context for a route that only reads: the current one, or the last one built.

        A read is answered from what this instance last had when the agents or the active
        profile cannot be read just now, as the profile list is (FR-019). Release it. A read
        of one thread is given the build that thread runs on (feature 009).
        """
        try:
            return await self.begin_run(thread_id)
        except Exception as exc:
            logger.warning(
                "The agents, the active LLM profile, or who started the thread could not be "
                "read (%s); answering "
                "from what this instance last built",
                type(exc).__name__,
            )
            strict = False
            if thread_id is not None:
                with suppress(Exception):
                    strict = await self.received_tasks.conversation_of(thread_id) is not None
            return self.last_built(strict=strict)

    async def begin_run(self, thread_id: str | None = None) -> RunContext:
        """The orchestrator for the agents and the LLM profile as they are now (research R3).

        The set of agents is refreshed, one single-row read, and held; the model manager
        rebuilds when the profile or the set moved. Whoever takes a context releases it.
        ``agent`` and ``catalog`` follow, as what this instance last built.

        Feature 009: given the thread a context is for, it asks the received-task store
        whether a caller started the thread, one single-row read, with the feature on or off,
        and gives a caller's thread the strict build. With the feature off there is no strict
        build, and the ordinary build's wrapper refuses to run such a thread (research R6). A
        lookup that fails fails the context.

        A runtime put together by hand, with no registry, gets a context from what it was
        given.
        """
        if self.models is None:
            return self.last_built()
        conversation = await self.caller_of(thread_id)
        agents: AgentSet | None = None
        if self.registry is not None:
            agents = await self.registry.refresh()
            # Held before anything else is awaited: a set nothing holds may close its links.
            self.registry.hold(agents)
        try:
            orchestrator, strict, active = await self.models.builds(agents)
            chosen = strict if conversation is not None and strict is not None else orchestrator
            # Feature 007: a console run's thread is run in segments, so that a stop is
            # applied between two steps; every other run is passed straight through (R4).
            graph = self._segmented(chosen.agent, strict=chosen is strict)
        except BaseException:
            if self.registry is not None and agents is not None:
                await self.registry.release(agents)
            raise
        self.agent, self.catalog, self.built_for = orchestrator.agent, orchestrator.catalog, agents
        if strict is not None:
            self.strict_agent, self.strict_catalog = strict.agent, strict.catalog
        return RunContext(
            agent=graph,
            catalog=chosen.catalog,
            active=active,
            agents=agents,
            registry=self.registry,
            strict=chosen is strict,
        )

    async def caller_of(self, thread_id: str | None) -> Conversation | None:
        """The conversation of a thread a caller started over A2A; None for any other."""
        if thread_id is None:
            return None
        return await self.received_tasks.conversation_of(thread_id)

    def _segmented(self, graph: Any, *, strict: bool = False) -> Any:
        """``graph`` as a run needs it: one wrapper per build, which runs a console run's
        thread in segments (feature 007, R4) and holds a caller's thread to the strict build
        (feature 009, R6)."""
        if self.unattended is None:
            return graph
        kept = self.strict_segmented if strict else self.segmented
        if kept is None or kept.graph is not graph:
            kept = SegmentedGraph(graph, self.unattended)
            kept.strict = strict
            kept.lookup = self.received_tasks.conversation_of
            if strict:
                self.strict_segmented = kept
            else:
                self.segmented = kept
        return kept

    async def model_for_run(self) -> ActiveModel:
        """Bring the orchestrator in step with the active profile, and say which it is.

        For a caller that only needs to know the model, or to have a change applied on this
        instance at once. It goes through the same refresh as a run and holds nothing.
        """
        context = await self.begin_run()
        await context.release()
        return context.active

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
        saved_agents: SavedAgentStore | None = None,
        thread_index: ThreadIndex | None = None,
        received_tasks: ReceivedTaskStore | None = None,
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

        Feature 005 obtains the agents through the registry: those of the environment and
        those in ``saved_agents``. An environment entry that is invalid in itself still
        raises here; an agent whose card cannot be read or used no longer does (005 FR-018,
        FR-020). The orchestrator is built for the registry's set, and built again when the
        set changes.

        Feature 007 keeps an index of threads: ``thread_index``, or one in memory. The
        lease it is given is wrapped so that every run that takes it and gives it back is
        told to the index, whoever starts the run (007 research R6).

        Feature 009 keeps conversations and received tasks: ``received_tasks``, or a store in
        memory. While the feature is on, the model manager builds the strict orchestrator for
        callers' threads beside the other (research R6).
        """
        index = thread_index if thread_index is not None else InMemoryThreadIndex()
        locks = IndexedRunLocks(locks, index)
        async with open_tool_servers(
            settings.mcp_servers,
            connect=connect,
            call_timeout=settings.mcp_call_timeout_seconds,
            startup_timeout=settings.mcp_startup_timeout_seconds,
        ) as servers:
            server_tools = [tool for server in servers for tool in server.tools]
            task_store = agent_tasks if agent_tasks is not None else InMemoryAgentTaskStore()
            saved_store = saved_agents if saved_agents is not None else InMemorySavedAgentStore()
            registry = AgentRegistry(
                settings.a2a_agents,
                saved=saved_store,
                transport=agent_transport,
                startup_timeout=settings.a2a_startup_timeout_seconds,
                call_timeout=settings.a2a_call_timeout_seconds,
                server_names=set(settings.mcp_servers),
                taken_tool_names=RESERVED_TOOL_NAMES | {tool.name for tool in server_tools},
            )
            try:
                agents = await registry.start()
                profile_store = (
                    llm_profiles if llm_profiles is not None else InMemoryLlmProfileStore()
                )
                spec = environment_spec(settings, model if isinstance(model, str) else None)
                provider = PROVIDERS.get(spec.provider)
                if isinstance(model, BaseChatModel):
                    environment = partial(_given, model)
                elif model_factory is not None:
                    environment = partial(model_factory, spec)
                else:
                    # The path the environment's model has always taken (001 research R3).
                    environment = partial(
                        resolve_model, spec.name, openai_responses_api=spec.responses_api
                    )
                # How the environment's model is reached is what its client holds: with the
                # LangSmith gateway on, that is the gateway and its key, with no provider key
                # at all, and such a deployment ran before feature 004 (FR-023). So the
                # client is built once here to be looked at, and the manager is handed that
                # one for its first build, not a second. A model handed over built is not
                # looked at: it needs nothing from the environment.
                first: list[BaseChatModel] = []
                if not isinstance(model, BaseChatModel):
                    build = environment
                    with suppress(Exception):  # the manager meets the failure and records it
                        first.append(build())
                    environment = partial(_first_then, first, build)
                access = environment_access(spec, first[0] if first else None)
                key = access.key
                # The credentials to take out of an error: the one that is sent, and the
                # key the client holds or the environment names where that is another.
                secrets = access.secrets
                build_options: dict[str, Any] = {
                    "server_tools": server_tools,
                    "task_store": task_store,
                    "saved_store": saved_store,
                    "checkpointer": checkpointer,
                    "store": store,
                    "workspace_dir": settings.workspace_dir,
                    "max_steps": settings.max_steps,
                    "openai_responses_api": settings.openai_responses_api,
                }
                manager = ModelManager(
                    store=profile_store,
                    secret=settings.secret_key,
                    factory=model_factory or build_model,
                    environment=environment,
                    environment_secrets=secrets,
                    # One of the three providers with no key set is not usable, whatever a
                    # client would make of it (FR-011a). A model handed over built is.
                    environment_unusable=(
                        "not_configured"
                        if provider and not key and not isinstance(model, BaseChatModel)
                        else None
                    ),
                    build=partial(_build, **build_options),
                    # Feature 009: the strict build exists only while the feature is on.
                    strict_build=(
                        partial(
                            _build,
                            **build_options,
                            strict=True,
                            files_dir=settings.a2a_server_files_dir,
                        )
                        if settings.a2a_server
                        else None
                    ),
                )
                orchestrator = await manager.load(agents)
                # Whether the environment's model can be built is known when the manager has
                # just built it, or it was handed over built; otherwise building one says.
                given = isinstance(model, BaseChatModel)
                built = given or (
                    manager.active.profile is ENVIRONMENT_PROFILE and manager.active.usable
                )
                described = environment_profile(
                    settings,
                    factory=_already_built if built else model_factory or build_model,
                    model=model if isinstance(model, str) else None,
                    access=access,
                )
                if given:
                    # A model handed over needs no key from the environment.
                    described = replace(described, usable=True, unusable_reason=None)
                runtime = cls(
                    agent=orchestrator.agent,
                    catalog=orchestrator.catalog,
                    servers=servers,
                    registry=registry,
                    built_for=agents,
                    saved_agents=saved_store,
                    agent_tasks=task_store,
                    llm_profiles=profile_store,
                    models=manager,
                    environment=described,
                    model_factory=model_factory or build_model,
                    environment_model=model if given else None,
                    environment_values=spec,
                    environment_secrets=secrets,
                    locks=locks,
                    instance_id=instance_id or uuid.uuid4().hex,
                    settings=settings,
                    threads=index,
                    received_tasks=(
                        received_tasks
                        if received_tasks is not None
                        else InMemoryReceivedTaskStore()
                    ),
                )
                if manager.strict is not None:
                    runtime.strict_agent = manager.strict.agent
                    runtime.strict_catalog = manager.strict.catalog
                # At startup, each profile that is not usable, with the reason (FR-024).
                if described.unusable_reason is not None:
                    logger.warning(
                        "LLM profile %s (%s) is not usable: %s",
                        described.name,
                        described.id,
                        UNUSABLE[described.unusable_reason],
                    )
                for record in await profile_store.list_profiles():
                    why = unusable_reason(record, settings.secret_key)
                    if why is not None:
                        logger.warning(
                            "LLM profile %s (%s) is not usable: %s",
                            record.name,
                            record.id,
                            UNUSABLE[why],
                        )
                # Feature 007: a graph a console run could not be stopped in stops startup.
                runtime.unattended = UnattendedRuns(index, runtime.instance_id)
                SegmentedGraph(orchestrator.agent, runtime.unattended)
                if manager.strict is not None:
                    SegmentedGraph(manager.strict.agent, runtime.unattended)
                runtime.worker = DelegationWorker(runtime)
                if run_worker:
                    runtime.worker.start()
                try:
                    yield runtime
                finally:
                    try:
                        # Feature 007: each console run ends at its next boundary, and the
                        # step under way is waited for; nothing is cancelled (R17).
                        await runtime.unattended.close()
                    finally:
                        await runtime.worker.stop()
            finally:
                # After the worker, whose rounds use the links.
                await registry.close()

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
            saved_agents = PostgresSavedAgentStore(persistence.pool)
            await saved_agents.setup()
            thread_index = PostgresThreadIndex(persistence.pool)
            await thread_index.setup()
            # Feature 009: made with the feature off too, so that turning it on needs no step
            # of its own and a context can always ask who started a thread.
            received_tasks = PostgresReceivedTaskStore(persistence.pool)
            await received_tasks.setup()
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
                saved_agents=saved_agents,
                thread_index=thread_index,
                received_tasks=received_tasks,
            ) as runtime:
                yield runtime
        finally:
            await persistence.close()
