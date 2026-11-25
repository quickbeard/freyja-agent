"""The stand-in caller: the A2A protocol's own client, as another agent would call Freyja.

Feature 009 has Freyja receive tasks from other agents over A2A. Neither ARTEX nor heimdall
has a client yet, so the suite and the quickstart play the calling agent with this: the SDK's
``ClientFactory`` behind a small class, over any httpx transport (in-process in the suite,
the network by hand), with an optional bearer token.

As a command, for the quickstart (specs/009-a2a-server/quickstart.md)::

    uv run python -m tests.a2a_caller --url http://localhost:8000 card
    uv run python -m tests.a2a_caller --url http://localhost:8000 send "Say hello."
    uv run python -m tests.a2a_caller --url http://localhost:8000 stream "Say hello."
    uv run python -m tests.a2a_caller --url http://localhost:8000 get <task id>
    uv run python -m tests.a2a_caller --url http://localhost:8000 follow <task id>
    uv run python -m tests.a2a_caller --url http://localhost:8000 cancel <task id>

``--context`` continues a conversation, ``--token`` presents a caller's token, ``--hops``
sets the hand-over count, and ``--return-immediately`` asks a send not to wait. Each task or
event is printed as one JSON object; a refusal of the protocol as the JSON-RPC error it was,
``{"error": {"code", "message", "data": [{"metadata"}]}}``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from a2a.client import Client, ClientConfig, ClientFactory
from a2a.client.card_resolver import parse_agent_card
from a2a.types import (
    AgentCard,
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
)
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    PROTOCOL_VERSION_CURRENT,
    VERSION_HEADER,
)
from a2a.utils.errors import JSON_RPC_ERROR_CODE_MAP, A2AError
from google.protobuf.json_format import MessageToDict

# The name a message carries its hand-over count under (feature 009, FR-027).
HOPS_KEY = "freyja.hops"


def message(text: str, *, context: str | None = None, hops: int | None = None) -> Message:
    """A caller's message: one text part, in ``context`` when given."""
    made = Message(
        message_id=uuid.uuid4().hex,
        role=Role.ROLE_USER,
        parts=[Part(text=text)],
        context_id=context or "",
    )
    if hops is not None:
        made.metadata.update({HOPS_KEY: hops})
    return made


def as_json(value: Any) -> dict[str, Any]:
    """A protocol object as the protocol's JSON names have it."""
    return MessageToDict(value)


class StandInCaller:
    """Another agent calling Freyja at ``url``, over ``transport`` when one is given."""

    def __init__(
        self,
        url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        token: str | None = None,
        timeout: float = 60,
    ) -> None:
        self.url = url.rstrip("/")
        headers = {VERSION_HEADER: PROTOCOL_VERSION_CURRENT}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.AsyncClient(
            transport=transport, headers=headers, timeout=timeout, follow_redirects=False
        )

    async def __aenter__(self) -> StandInCaller:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self.http.aclose()

    async def card(self) -> AgentCard:
        answer = await self.http.get(self.url + AGENT_CARD_WELL_KNOWN_PATH)
        answer.raise_for_status()
        return parse_agent_card(answer.json())

    async def client(self, *, streaming: bool = True) -> Client:
        config = ClientConfig(httpx_client=self.http, streaming=streaming)
        return ClientFactory(config).create(await self.card())

    async def send(
        self,
        text: str,
        *,
        context: str | None = None,
        hops: int | None = None,
        return_immediately: bool = False,
    ) -> Task | Message:
        """Send without a stream: the task, or a message, as the answer has it."""
        client = await self.client(streaming=False)
        request = SendMessageRequest(
            message=message(text, context=context, hops=hops),
            configuration=SendMessageConfiguration(return_immediately=return_immediately),
        )
        async for event in client.send_message(request):
            kind = event.WhichOneof("payload")
            if kind == "task":
                return event.task
            if kind == "message":
                return event.message
        raise RuntimeError("the answer held neither a task nor a message")

    async def stream(
        self, text: str, *, context: str | None = None, hops: int | None = None
    ) -> AsyncIterator[StreamResponse]:
        """Send and follow: every event of the stream as it arrives."""
        client = await self.client(streaming=True)
        request = SendMessageRequest(message=message(text, context=context, hops=hops))
        async for event in client.send_message(request):
            yield event

    async def get(self, task_id: str) -> Task:
        return await (await self.client()).get_task(GetTaskRequest(id=task_id))

    async def follow(self, task_id: str) -> AsyncIterator[StreamResponse]:
        async for event in (await self.client()).subscribe(SubscribeToTaskRequest(id=task_id)):
            yield event

    async def cancel(self, task_id: str) -> Task:
        return await (await self.client()).cancel_task(CancelTaskRequest(id=task_id))


def error_json(error: A2AError) -> dict[str, Any]:
    """A refusal the client raised, as the JSON-RPC error the service answered with."""
    return {
        "error": {
            "code": JSON_RPC_ERROR_CODE_MAP.get(type(error)),
            "message": error.message,
            "data": [{"metadata": error.data or {}}],
        }
    }


def _out(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value) + "\n")
    sys.stdout.flush()


async def _main(arguments: argparse.Namespace) -> None:
    async with StandInCaller(arguments.url, token=arguments.token) as caller:
        what = arguments.operation
        if what == "card":
            found: Any = await caller.card()
        elif what == "send":
            found = await caller.send(
                arguments.value,
                context=arguments.context,
                hops=arguments.hops,
                return_immediately=arguments.return_immediately,
            )
        elif what == "stream":
            async for event in caller.stream(
                arguments.value, context=arguments.context, hops=arguments.hops
            ):
                _out(as_json(event))
            return
        elif what == "follow":
            async for event in caller.follow(arguments.value):
                _out(as_json(event))
            return
        elif what == "get":
            found = await caller.get(arguments.value)
        else:
            found = await caller.cancel(arguments.value)
        _out(as_json(found))


def parser() -> argparse.ArgumentParser:
    made = argparse.ArgumentParser(prog="python -m tests.a2a_caller", description=__doc__)
    made.add_argument("--url", required=True, help="Freyja's address, without /a2a")
    made.add_argument("--token", help="a caller's token, sent as a bearer token")
    made.add_argument("--context", help="the conversation to continue")
    made.add_argument("--hops", type=int, help="the hand-over count the message carries")
    made.add_argument("--return-immediately", action="store_true")
    made.add_argument("operation", choices=["card", "send", "stream", "get", "follow", "cancel"])
    made.add_argument("value", nargs="?", help="the text to send, or a task's id")
    return made


if __name__ == "__main__":  # pragma: no cover - run by hand
    parsed = parser().parse_args()
    if parsed.operation != "card" and parsed.value is None:
        parser().error(f"{parsed.operation} needs a value")
    try:
        asyncio.run(_main(parsed))
    except httpx.HTTPStatusError as error:
        sys.stderr.write(json.dumps({"status": error.response.status_code}) + "\n")
        sys.exit(1)
    except A2AError as error:
        _out(error_json(error))
        sys.exit(1)
