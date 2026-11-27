import asyncio
import functools
import json
import os
import re
import shutil
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import pytest

# Settings has no default for the database URI. Give the suite one before any test
# imports the app; no database is contacted.
os.environ.setdefault("FREYJA_DATABASE_URI", "postgresql://localhost:5432/freyja_test")
# FastAPI's test client asks for the host ``testserver``, a name the service answers for
# only when it is listed (api/hosts.py). Set, not defaulted: a list the environment already
# holds, as when the suite is run with ``--env-file .env``, would refuse every request.
os.environ["FREYJA_ALLOWED_HOSTS"] = '["testserver"]'

from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from freyja_agent.agent.factory import build_agent
from freyja_agent.api.app import create_app
from freyja_agent.config import Settings, canonical_address, destination
from freyja_agent.delegates.a2a_agents import default_transport
from freyja_agent.delegates.addresses import SCRUBBER
from freyja_agent.delegates.agent_cards import card_details
from freyja_agent.llm.providers import ModelFactory
from freyja_agent.llm.store import InMemoryLlmProfileStore, LlmProfileStore
from freyja_agent.runtime.agent_tasks import InMemoryAgentTaskStore
from freyja_agent.runtime.locks import InMemoryRunLocks
from freyja_agent.runtime.received_tasks import InMemoryReceivedTaskStore, ReceivedTaskStore
from freyja_agent.runtime.runtime import Runtime
from freyja_agent.runtime.saved_agents import InMemorySavedAgentStore, SavedAgentRecord
from freyja_agent.runtime.telemetry import Telemetry
from freyja_agent.runtime.thread_index import IndexedRunLocks, InMemoryThreadIndex, ThreadIndex
from tests.a2a_stub import StubAgent
from tests.a2a_stub import make_stub as make_a2a_stub
from tests.asgi_stream import StreamingASGITransport
from tests.fakes import FakeClock, ProfileModels
from tests.mcp_stub import StubServer, make_stub

REPO_SKILLS = Path(__file__).resolve().parent.parent / "workspace" / "skills"
# The secret the suite stores keys under (feature 004). Made afresh for every test session.
TEST_SECRET = Fernet.generate_key().decode()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A throwaway workspace holding a copy of the committed skills."""
    shutil.copytree(REPO_SKILLS, tmp_path / "skills")
    return tmp_path


@pytest.fixture
def make_runtime(workspace: Path, clock: FakeClock) -> Callable[..., Runtime]:
    """Build a Runtime through the real factory with in-memory persistence.

    Pass ``saver``, ``store``, or ``locks`` from an earlier runtime to simulate a second
    instance sharing the same database.
    """

    def _make(
        model: BaseChatModel,
        *,
        instance_id: str = "instance-a",
        saver: InMemorySaver | None = None,
        store: InMemoryStore | None = None,
        locks: InMemoryRunLocks | None = None,
        telemetry: Telemetry | None = None,
        **overrides: Any,
    ) -> Runtime:
        settings = Settings(workspace_dir=str(workspace), **overrides)
        saver = saver or InMemorySaver()
        store = store or InMemoryStore()
        locks = locks or InMemoryRunLocks(settings.run_lock_ttl_seconds, clock=clock.now)
        telemetry = telemetry or Telemetry.off()
        orchestrator = build_agent(
            model,
            checkpointer=saver,
            store=store,
            workspace_dir=settings.workspace_dir,
            max_steps=settings.max_steps,
            telemetry=telemetry,
        )
        return Runtime(
            agent=orchestrator.agent,
            catalog=orchestrator.catalog,
            locks=locks,
            instance_id=instance_id,
            settings=settings,
            telemetry=telemetry,
        )

    return _make


@asynccontextmanager
async def _never_connects() -> AsyncIterator[Any]:
    """A transport whose server never answers, so only a time limit ends the attempt."""
    await anyio.sleep_forever()
    yield None  # pragma: no cover


@dataclass
class StubLink:
    """What ``connect`` hands to ``mcp.Client`` for one configured server.

    By default that is the stub itself, connected inside the test process. ``down`` makes the
    next connection attempt fail the way a server that is not running does; ``hang`` makes it
    never answer. Set either back to False to let the server recover.
    """

    stub: StubServer
    down: bool = False
    hang: bool = False
    connections: int = 0
    # What the failed connection says, for tests of what may appear in errors and logs.
    down_reason: str = "the stub server is down"

    def target(self) -> Any:
        self.connections += 1
        if self.down:
            raise ConnectionRefusedError(self.down_reason)
        if self.hang:
            return _never_connects()
        return self.stub


@dataclass
class McpApp:
    """An app assembled the production way, with stub tool servers behind it."""

    app: FastAPI
    settings: Settings
    links: dict[str, StubLink]
    # Filled when the app's lifespan starts.
    runtimes: list[Runtime] = field(default_factory=list)

    @property
    def stubs(self) -> dict[str, StubServer]:
        return {name: link.stub for name, link in self.links.items()}

    @property
    def runtime(self) -> Runtime:
        return self.runtimes[-1]


@pytest.fixture
def make_mcp_app(workspace: Path, clock: FakeClock) -> Callable[..., McpApp]:
    """Build an app whose runtime is assembled by ``Runtime.assemble``, as in production.

    Persistence is in memory and ``connect`` returns stub servers, so ``mcp.Client`` connects
    inside the test process: listing, conversion, classification, gating, calling, and the
    routes are the production code (research R13). ``servers`` is the configuration, in the
    shape of ``FREYJA_MCP_SERVERS``; ``stubs`` supplies a stub per server name, and a server
    without one gets ``make_stub()``. Pass ``saver``, ``store``, or ``locks`` from an earlier
    app's runtime to simulate a restart with another configuration.
    """

    def _make(
        model: BaseChatModel,
        servers: dict[str, dict[str, Any]],
        *,
        stubs: dict[str, StubServer] | None = None,
        instance_id: str = "instance-a",
        saver: InMemorySaver | None = None,
        store: InMemoryStore | None = None,
        locks: InMemoryRunLocks | None = None,
        # Feature 007: an index shared with an earlier app, for a second instance.
        thread_index: ThreadIndex | None = None,
        telemetry: Telemetry | None = None,
        **overrides: Any,
    ) -> McpApp:
        settings = Settings(workspace_dir=str(workspace), mcp_servers=servers, **overrides)
        links = {name: StubLink((stubs or {}).get(name) or make_stub(name)) for name in servers}
        checkpointer = saver or InMemorySaver()
        memory = store or InMemoryStore()
        lock_store = locks or InMemoryRunLocks(settings.run_lock_ttl_seconds, clock=clock.now)

        def connect(name: str, _config: Any, _on_error_output: Any) -> Any:
            return links[name].target()

        made = McpApp(app=FastAPI(), settings=settings, links=links)

        @asynccontextmanager
        async def open_runtime(_settings: Settings) -> AsyncIterator[Runtime]:
            async with Runtime.assemble(
                settings,
                checkpointer=checkpointer,
                store=memory,
                locks=lock_store,
                model=model,
                instance_id=instance_id,
                connect=connect,
                run_worker=False,
                thread_index=thread_index,
                telemetry=telemetry,
            ) as runtime:
                made.runtimes.append(runtime)
                yield runtime

        made.app = create_app(open_runtime=open_runtime)
        return made

    return _make


@dataclass
class A2aApp:
    """An app assembled the production way, with stand-in A2A agents behind it (003)."""

    app: FastAPI
    settings: Settings
    stubs: dict[str, StubAgent]
    # One streaming transport per agent; records requests and can play a stopped agent.
    transports: dict[str, StreamingASGITransport]
    store: InMemoryAgentTaskStore
    # Opens this app's runtime; a test enters it in another app's event loop to run a second
    # instance beside it (``TestClient.portal.wrap_async_context_manager``).
    open_runtime: Callable[[Settings], Any] = lambda _settings: nullcontext()
    runtimes: list[Runtime] = field(default_factory=list)
    # The agents an operator saved (feature 005); shared between apps as ``store`` is.
    saved_agents: InMemorySavedAgentStore = field(default_factory=InMemorySavedAgentStore)
    # The agents the store was seeded with before the app started, by name.
    saved: dict[str, SavedAgentRecord] = field(default_factory=dict)
    # Stand-ins found by where an address leads: every saved agent's, and any added with
    # ``add_stub``. Shared between apps, like the stores.
    at: dict[str, tuple[StubAgent, StreamingASGITransport]] = field(default_factory=dict)
    # The checkpointer and the lease of the app, to pass on to a second app (feature 009).
    saver: InMemorySaver | None = None
    locks: InMemoryRunLocks | None = None

    @property
    def runtime(self) -> Runtime:
        return self.runtimes[-1]

    def add_stub(self, url: str, stub: StubAgent) -> StreamingASGITransport:
        """Put ``stub`` at ``url``: whatever is asked for at that address reaches it."""
        transport = StreamingASGITransport(stub.app)
        self.at[destination(canonical_address(url))] = (stub, transport)
        return transport

    def stub_at(self, url: str) -> StubAgent:
        return self.at[destination(canonical_address(url))][0]

    def transport_at(self, url: str) -> StreamingASGITransport:
        return self.at[destination(canonical_address(url))][1]


def saved_card(stub: StubAgent) -> dict[str, Any]:
    """The card details the service would keep for ``stub``: cut and cleaned (005 FR-002)."""
    return card_details(stub.card, SCRUBBER.scrub).to_dict()


@pytest.fixture
def make_a2a_app(workspace: Path, clock: FakeClock) -> Callable[..., A2aApp]:
    """Build an app whose runtime is assembled by ``Runtime.assemble``, with stand-in agents.

    ``agents`` is the configuration in the shape of ``FREYJA_A2A_AGENTS``; an entry may omit
    ``url`` and gets ``http://127.0.0.1:9999``. ``stubs`` supplies a stand-in per agent name, and
    an agent without one gets ``make_stub(name)``. Each agent's link reaches its stand-in through
    the streaming in-process transport, so the card read, the checks, the tools, the gate, and
    the routes are the production code (003 research R16). Pass ``store``, ``saver``, or
    ``locks`` from an earlier app to simulate a restart or a second instance. The delegation
    worker is built but its loop is not started unless ``run_worker`` is true: tests call
    ``runtime.worker.tick()`` themselves.

    Feature 005 adds saved agents (005 research R20). ``saved`` seeds the saved-agent store
    before the app starts: each entry is an agent's name with ``url``, and optionally
    ``read_only`` and ``stub``, and is saved with the card details of its stand-in.
    ``saved_agents`` is the store, and ``at`` the stand-ins by address, to pass on from an
    earlier app for a second instance or a restart. A stand-in given through ``saved`` or
    through ``A2aApp.add_stub`` is found by where the address asked for leads; any other by
    its agent's name, as before, since every environment agent of the fixture has one
    address.
    """

    def _make(
        model: BaseChatModel,
        agents: dict[str, dict[str, Any]],
        *,
        stubs: dict[str, StubAgent] | None = None,
        servers: dict[str, dict[str, Any]] | None = None,
        server_stubs: dict[str, StubServer] | None = None,
        instance_id: str = "instance-a",
        saver: InMemorySaver | None = None,
        store: InMemoryAgentTaskStore | None = None,
        locks: InMemoryRunLocks | None = None,
        run_worker: bool = False,
        network: bool = False,
        llm_profiles: LlmProfileStore | None = None,
        model_factory: ModelFactory | None = None,
        saved_agents: InMemorySavedAgentStore | None = None,
        saved: dict[str, dict[str, Any]] | None = None,
        at: dict[str, tuple[StubAgent, StreamingASGITransport]] | None = None,
        received_tasks: ReceivedTaskStore | None = None,
        thread_index: ThreadIndex | None = None,
        listen: bool = False,
        telemetry: Telemetry | None = None,
        **overrides: Any,
    ) -> A2aApp:
        entries = {
            name: {"url": "http://127.0.0.1:9999", **entry} for name, entry in agents.items()
        }
        settings = Settings(
            workspace_dir=str(workspace),
            a2a_agents=entries,
            mcp_servers=servers or {},
            **overrides,
        )
        stand_ins = {name: (stubs or {}).get(name) or make_a2a_stub(name) for name in agents}
        transports = {name: StreamingASGITransport(stub.app) for name, stub in stand_ins.items()}
        server_links = {
            name: StubLink((server_stubs or {}).get(name) or make_stub(name))
            for name in servers or {}
        }
        task_store = store or InMemoryAgentTaskStore(clock=clock.now)
        checkpointer = saver or InMemorySaver()
        lock_store = locks or InMemoryRunLocks(settings.run_lock_ttl_seconds, clock=clock.now)
        made = A2aApp(
            app=FastAPI(),
            settings=settings,
            stubs=stand_ins,
            transports=transports,
            store=task_store,
            saved_agents=saved_agents or InMemorySavedAgentStore(task_store, clock=clock.now),
            at=at if at is not None else {},
            saver=checkpointer,
            locks=lock_store,
        )
        for name, entry in (saved or {}).items():
            stub = entry.get("stub") or make_a2a_stub(name)
            made.add_stub(entry["url"], stub)
            # The store keeps nothing that belongs to an event loop, so it can be filled here.
            made.saved[name] = asyncio.run(
                made.saved_agents.add(
                    name=name,
                    url=entry["url"],
                    card=saved_card(stub),
                    read_only=entry.get("read_only", False),
                )
            )

        def transport_for(name: str, url: str) -> Any:
            if network:
                # The production transport, to agents served on loopback
                # (tests/test_a2a_transport.py only).
                return default_transport(name, url)
            try:
                found = made.at.get(destination(canonical_address(url)))
            except ValueError:  # an environment entry in a spelling the saved form refuses
                found = None
            return found[1] if found is not None else transports[name]

        @asynccontextmanager
        async def open_runtime(_settings: Settings) -> AsyncIterator[Runtime]:
            async with Runtime.assemble(
                settings,
                checkpointer=checkpointer,
                store=InMemoryStore(),
                locks=lock_store,
                model=model,
                instance_id=instance_id,
                connect=lambda name, _config, _on_error_output: server_links[name].target(),
                agent_tasks=task_store,
                agent_transport=transport_for,
                run_worker=run_worker,
                # Feature 004: a test may give stand-in agents and LLM profiles together.
                llm_profiles=llm_profiles,
                model_factory=model_factory,
                saved_agents=made.saved_agents,
                # Feature 009: given by ``make_a2a_server_app``, and shared with a second app.
                received_tasks=received_tasks,
                thread_index=thread_index,
                telemetry=telemetry,
            ) as runtime:
                made.runtimes.append(runtime)
                yield runtime

        made.open_runtime = open_runtime
        # Feature 009: the outside listener is bound only where a test asks (``listen``);
        # otherwise its application is driven in-process as ``ServerApp.outside``.
        made.app = create_app(open_runtime=open_runtime, listen=listen)
        return made

    return _make


@dataclass
class ServerApp:
    """An app with feature 009 on, assembled as ``make_a2a_app`` assembles one (research R21).

    ``a2a`` is that app, with its stand-in agents and stores. ``received`` is the store of
    conversations and received tasks and ``index`` the index of threads, both shared with
    an app made by ``again``, which stands for a restart or a second instance on the same
    database; ``again`` takes the same settings as the fixture, the feature's switch
    included. The scripted model is ``model``.

    Request 5 adds the outside application (``outside``) and ``callers``.
    """

    a2a: A2aApp
    model: BaseChatModel
    received: InMemoryReceivedTaskStore
    index: ThreadIndex
    again: Callable[..., "ServerApp"]

    @property
    def app(self) -> FastAPI:
        return self.a2a.app

    @property
    def runtime(self) -> Runtime:
        return self.a2a.runtime

    @property
    def stub(self) -> StubAgent:
        return next(iter(self.a2a.stubs.values()))

    def service(self) -> Any:
        """The ``ReceivedTasks`` of the running app."""
        return self.app.state.received

    @property
    def outside(self) -> Any:
        """The outside listener's application, while the app runs with the listener set
        (request 5); driven in-process unless the app was made with ``listen=True``."""
        return self.app.state.a2a_outside

    @property
    def callers(self) -> Any:
        """The ``Callers`` of the running app (request 5)."""
        return self.app.state.callers

    def accept(
        self,
        client: Any,
        text: str = "hello",
        *,
        caller: str = "local",
        context: str = "c-1",
        hops: int = 0,
    ) -> Any:
        """Accept a caller's message through the service, as the A2A routes will."""
        return client.portal.call(
            functools.partial(self.service().accept, caller, context, text, hops=hops)
        )

    def finish(self, client: Any, accepted: Any) -> list[tuple[str, Any]]:
        """Read the run an accepted message started to its end, then wait for the look that
        its lease's release started; the run's events as ``(kind, data)``."""
        events = client.portal.call(read_events, accepted.events)
        client.portal.call(self.service().close)
        return events

    def task(self, client: Any, task_id: str) -> Any:
        return client.portal.call(self.received.get, task_id)


async def read_events(events: Any) -> list[tuple[str, Any]]:
    """Every event of a run's stream as ``(kind, data)``."""
    found = []
    async for event in events:
        found.append((event.event, json.loads(event.data) if event.data else None))
    return found


@pytest.fixture
def make_a2a_server_app(
    make_a2a_app: Callable[..., A2aApp], clock: FakeClock
) -> Callable[..., ServerApp]:
    """Build an app the production way with feature 009 on, one stand-in agent, ``stub``,
    which runs without approval unless ``read_only=False`` is given, and the scripted model.

    Any setting of the feature, and ``a2a_server`` itself, is given as a keyword. ``agents``
    replaces the one stand-in agent. Every store is in memory; ``again`` makes an app on
    the same stores, for a restart or a second instance.
    """

    def _make(
        model: BaseChatModel,
        *,
        agents: dict[str, dict[str, Any]] | None = None,
        read_only: bool = True,
        received: InMemoryReceivedTaskStore | None = None,
        index: ThreadIndex | None = None,
        shared: A2aApp | None = None,
        instance_id: str = "instance-a",
        **overrides: Any,
    ) -> ServerApp:
        overrides.setdefault("a2a_server", True)
        store = received if received is not None else InMemoryReceivedTaskStore()
        threads = index if index is not None else InMemoryThreadIndex()
        sharing: dict[str, Any] = {}
        if shared is not None:
            sharing = {
                "saver": shared.saver,
                "store": shared.store,
                "locks": shared.locks,
                "saved_agents": shared.saved_agents,
                "at": shared.at,
            }
        made = make_a2a_app(
            model,
            agents if agents is not None else {"stub": {"read_only": read_only}},
            received_tasks=store,
            thread_index=threads,
            instance_id=instance_id,
            **sharing,
            **overrides,
        )

        def again(other: BaseChatModel | None = None, **changed: Any) -> ServerApp:
            return _make(
                other if other is not None else model,
                agents=agents,
                read_only=read_only,
                received=store,
                index=threads,
                shared=made,
                instance_id=changed.pop("instance_id", "instance-b"),
                **{**overrides, **changed},
            )

        return ServerApp(made, model, store, threads, again)

    return _make


@dataclass
class LlmApp:
    """An app assembled the production way, with LLM profiles behind it (feature 004)."""

    app: FastAPI
    settings: Settings
    # Saved profiles and the active choice; shared by two apps to stand for two instances.
    store: InMemoryLlmProfileStore
    # What turns a profile into a chat model here, and the record of what it built.
    models: ProfileModels
    open_runtime: Callable[[Settings], Any] = lambda _settings: nullcontext()
    runtimes: list[Runtime] = field(default_factory=list)

    @property
    def runtime(self) -> Runtime:
        return self.runtimes[-1]


@pytest.fixture
def make_llm_app(workspace: Path, clock: FakeClock) -> Callable[..., LlmApp]:
    """Build an app whose runtime is assembled by ``Runtime.assemble``, with LLM profiles.

    Persistence is in memory. ``models`` is the model factory: it returns a labelled scripted
    model per model id, so the store, the manager, the run loop, and the routes are the
    production code and only the chat model is a stand-in (004 research R18). The environment's
    model is ``FREYJA_MODEL`` as usual; pass ``model="openai:env-model"`` to name it. Pass
    ``store``, ``saver``, ``locks``, or ``models`` from an earlier app to simulate a restart or
    a second instance on the same database. ``secret=None`` starts without a secret.
    """

    def _make(
        *,
        models: ProfileModels | None = None,
        store: InMemoryLlmProfileStore | None = None,
        saver: InMemorySaver | None = None,
        locks: InMemoryRunLocks | None = None,
        instance_id: str = "instance-a",
        secret: str | None = TEST_SECRET,
        telemetry: Telemetry | None = None,
        **overrides: Any,
    ) -> LlmApp:
        settings = Settings(workspace_dir=str(workspace), secret_key=secret, **overrides)
        factory = models or ProfileModels()
        profile_store = store or InMemoryLlmProfileStore(clock=clock.now)
        checkpointer = saver or InMemorySaver()
        lock_store = locks or InMemoryRunLocks(settings.run_lock_ttl_seconds, clock=clock.now)
        made = LlmApp(app=FastAPI(), settings=settings, store=profile_store, models=factory)

        @asynccontextmanager
        async def open_runtime(_settings: Settings) -> AsyncIterator[Runtime]:
            async with Runtime.assemble(
                settings,
                checkpointer=checkpointer,
                store=InMemoryStore(),
                locks=lock_store,
                instance_id=instance_id,
                run_worker=False,
                llm_profiles=profile_store,
                model_factory=factory,
                telemetry=telemetry,
            ) as runtime:
                made.runtimes.append(runtime)
                yield runtime

        made.open_runtime = open_runtime
        made.app = create_app(open_runtime=open_runtime)
        return made

    return _make


@pytest.fixture
def make_app() -> Callable[[Runtime], FastAPI]:
    """The production app factory, with the lifespan yielding the given runtime."""

    def _make(runtime: Runtime) -> FastAPI:
        return create_app(open_runtime=lambda _settings: nullcontext(runtime))

    return _make


def indexed_locks(runtime: Runtime) -> IndexedRunLocks:
    """The lease of an assembled runtime, as what ``Runtime.assemble`` wrapped it in.

    ``Runtime.locks`` is typed as any lease, which is what a runtime built by hand holds."""
    assert isinstance(runtime.locks, IndexedRunLocks)
    return runtime.locks


def detail_of(refused: HTTPException) -> Any:
    """What a refusal carries. FastAPI takes any value as a refusal's detail, and the tests
    read the service's objects and lists from it; the attribute is Starlette's, typed as
    text."""
    return refused.detail


def holds_port(text: str, port: str | int) -> bool:
    """Whether ``text`` holds ``port`` as a number of its own.

    A search for an agent's port must not find the same digits inside something else: a
    hexadecimal id, written plainly or in groups with hyphens between them, a longer number,
    or the fraction of a timestamp. In every form an address takes, the port follows a
    colon, a space, an equals sign, or a bracket, and neither a digit nor a hyphen follows it.
    """
    return re.search(rf"(?<![0-9A-Fa-f.\-]){port}(?![0-9A-Fa-f\-])", text) is not None


def parse_sse(lines: Iterable[str]) -> Iterator[tuple[str, Any]]:
    """Yield ``(event, data)`` pairs from raw SSE lines; comment lines are ignored."""
    event: str | None = None
    data: list[str] = []
    for line in lines:
        if line.startswith(":"):
            continue
        if line == "":
            if event is not None:
                yield event, json.loads("\n".join(data)) if data else None
            event, data = None, []
        elif line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data.append(line[len("data:") :].strip())
    if event is not None:
        yield event, json.loads("\n".join(data)) if data else None


@pytest.fixture
def sse_events() -> Callable[[Iterable[str]], list[tuple[str, Any]]]:
    return lambda lines: list(parse_sse(lines))
