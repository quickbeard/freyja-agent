"""What the three proofs that nothing secret gets out share (features 004, 005, and 007).

`tests/test_llm_secrets_nowhere.py` searches for a recognizable API key (004 FR-020, FR-021),
`tests/test_agents_address_nowhere.py` for a recognizable agent address (005 FR-028), and
`tests/test_thread_index_nowhere.py` for both in what the index of threads and the console's
runs return (007 FR-025). Each drives the service, keeps everything it gave back with `Seen`,
reads what the checkpointer holds with `kept`, and searches all of it, and what was logged,
with `no_key` and `no_address`.
"""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import holds_port
from tests.fakes import ScriptedChatModel

# Nothing else in the suite holds this text, so finding any of it means a key got out.
KEY = "sk-recognizable-key-0001"
NEW_KEY = "sk-recognizable-other-0002"
MARK = "recognizable"
ENV_KEY = "sk-environment-secret-9f2c"
ENV_MARK = "environment-secret"
BASE_URL = "http://127.0.0.1:9997/v1"


class LeakyModel(ScriptedChatModel):
    """A model whose provider repeats the key in every error, until it is ``healed``."""

    healed: bool = False

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> Any:
        if not self.healed:
            self.calls += 1
            raise RuntimeError(f"401: Incorrect API key provided: {KEY}. Check {KEY}!")
        return super()._generate(messages, stop, run_manager, **kwargs)


class Seen:
    """Everything the service gave back, kept as text to be searched."""

    def __init__(self, client: TestClient, sse_events: Any) -> None:
        self.client = client
        self.sse_events = sse_events
        self.texts: list[str] = []

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, path, **kwargs)
        self.texts.append(f"{method} {path} -> {response.status_code} {response.text}")
        return response

    def stream(self, path: str, body: dict[str, Any], **params: Any) -> list[tuple[str, Any]]:
        with self.client.stream("POST", path, json=body, params=params) as response:
            assert response.status_code == 200, response.read()
            lines = list(response.iter_lines())
        # The stream as it went over the wire, every event of it.
        self.texts.append(f"POST {path} stream\\n" + "\\n".join(lines))
        return self.sse_events(iter(lines))

    def state(self, thread: str) -> dict[str, Any]:
        return self.call("GET", f"/threads/{thread}/state").json()


def kept(saver: Any) -> list[str]:
    """Everything the in-memory checkpointer holds, as text: checkpoints, blobs, and the writes
    of each step, a failed step's error among them."""
    texts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, bytes | bytearray):
            texts.append(bytes(value).decode("latin-1"))
        elif isinstance(value, str):
            texts.append(value)
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(key)
                walk(item)
        elif isinstance(value, list | tuple):
            for item in value:
                walk(item)

    for part in (saver.storage, saver.blobs, saver.writes):
        walk(part)
    return texts


def logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Every line the service logged during the test, formatted as a handler writes it."""
    return [caplog.handler.format(record) for record in caplog.records]


def profile(**values: Any) -> dict[str, Any]:
    """The body of an LLM profile that holds ``KEY``."""
    return {
        "name": "Stub",
        "provider": "openai",
        "model": "saved-model",
        "base_url": BASE_URL,
        "api_key": KEY,
        "responses_api": False,
        **values,
    }


def no_key(texts: list[str], *marks: str) -> None:
    """Fail when any of ``marks``, the recognizable part of a key, is in any of ``texts``."""
    for text in texts:
        for mark in marks:
            assert mark not in text, f"{mark!r} found in: {text[:300]}"


def found(text: str, port: int) -> bool:
    """Whether ``text`` holds an address at ``port``: the port as a number of its own, or
    after a colon, which is how every form of an address holds it (005 FR-028)."""
    return holds_port(text, port) or f":{port}" in text


def no_address(texts: list[str], *ports: int) -> None:
    """Fail when an address at any of ``ports`` is in any of ``texts``."""
    for text in texts:
        for port in ports:
            assert not found(text, port), f"{port} found in: {text[:400]}"


def strings(value: Any, *, but: str | None = None) -> list[str]:
    """Every text in a JSON value, keys included, leaving out what is under the key ``but``."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            text
            for key, item in value.items()
            if key != but
            for text in (key, *strings(item, but=but))
        ]
    if isinstance(value, list):
        return [text for item in value for text in strings(item, but=but)]
    return [json.dumps(value)]


def addresses(value: Any) -> list[str]:
    """Every ``url`` of an Agents answer that holds one."""
    if isinstance(value, dict):
        own = [value["url"]] if isinstance(value.get("url"), str) else []
        return own + [text for item in value.values() for text in addresses(item)]
    if isinstance(value, list):
        return [text for item in value for text in addresses(item)]
    return []
