import json
import os
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

from fastapi import FastAPI
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from freyja_agent.agent.factory import build_agent
from freyja_agent.api.app import create_app
from freyja_agent.config import Settings
from freyja_agent.delegates.a2a_agents import default_transport
from freyja_agent.runtime.agent_tasks import InMemoryAgentTaskStore
from freyja_agent.runtime.locks import InMemoryRunLocks
from freyja_agent.runtime.runtime import Runtime
from tests.a2a_stub import StubAgent
from tests.a2a_stub import make_stub as make_a2a_stub
from tests.asgi_stream import StreamingASGITransport
from tests.fakes import FakeClock
from tests.mcp_stub import StubServer, make_stub

REPO_SKILLS = Path(__file__).resolve().parent.parent / "workspace" / "skills"


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
        **overrides: Any,
    ) -> Runtime:
        settings = Settings(workspace_dir=str(workspace), **overrides)
        saver = saver or InMemorySaver()
        store = store or InMemoryStore()
        locks = locks or InMemoryRunLocks(settings.run_lock_ttl_seconds, clock=clock.now)
        orchestrator = build_agent(
            model,
            checkpointer=saver,
            store=store,
            workspace_dir=settings.workspace_dir,
            max_steps=settings.max_steps,
        )
        return Runtime(
            agent=orchestrator.agent,
            catalog=orchestrator.catalog,
            locks=locks,
            instance_id=instance_id,
            settings=settings,
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

    @property
    def runtime(self) -> Runtime:
        return self.runtimes[-1]


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
        )

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
                # ``network``: the production transport, to agents served on loopback
                # (tests/test_a2a_transport.py only).
                agent_transport=(
                    default_transport if network else lambda name, _url: transports[name]
                ),
                run_worker=run_worker,
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
