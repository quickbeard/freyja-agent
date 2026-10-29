import json
import os
import shutil
from collections.abc import Callable, Iterable, Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import Any

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
from freyja_agent.runtime.locks import InMemoryRunLocks
from freyja_agent.runtime.runtime import Runtime
from tests.fakes import FakeClock

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
        agent = build_agent(
            model, checkpointer=saver, store=store, workspace_dir=settings.workspace_dir
        )
        return Runtime(agent=agent, locks=locks, instance_id=instance_id, settings=settings)

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
