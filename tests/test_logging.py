"""FR-008, quickstart Setup: the service's own INFO lines are visible once the app starts."""

import logging
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from freyja_agent.runtime.runtime import Runtime
from tests.fakes import ScriptedChatModel


def test_startup_raises_the_package_logger_to_info(
    make_runtime: Callable[..., Runtime],
    make_app: Callable[[Runtime], FastAPI],
    caplog: pytest.LogCaptureFixture,
) -> None:
    package = logging.getLogger("freyja_agent")
    package.setLevel(logging.NOTSET)  # a fresh process: nothing configured yet
    assert package.getEffectiveLevel() != logging.INFO or logging.getLogger().level == logging.INFO

    with TestClient(make_app(make_runtime(ScriptedChatModel()))):
        assert package.getEffectiveLevel() == logging.INFO
        assert logging.getLogger("freyja_agent.runtime.runtime").isEnabledFor(logging.INFO)
        logging.getLogger("freyja_agent.runtime.runtime").info("Persistence ready: probe")

    assert any(r.getMessage() == "Persistence ready: probe" for r in caplog.records)
