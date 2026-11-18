"""A stand-in model endpoint for the suite: OpenAI-compatible, in the test process (004 R18).

It serves chat completions and the Responses API under ``/v1`` and records every request it
receives, with the key it was sent. The suite reaches it through the real ``ChatOpenAI`` client
(``stub_factory``); run as a module it serves on the loopback interface for the quickstart:

    uv run --env-file .env python -m tests.llm_stub --http 9997

Its base URL is then ``http://127.0.0.1:9997/v1``. The env file is needed because the module
imports the service's settings with the provider table.

It behaves by the model id it is asked for:

- ``refuse-conversation``: answers a first message and refuses, with a 422, a request that
  carries earlier turns (an assistant message, a tool call, or a tool result). This is the
  failure a profile's test exists to catch (research R7);
- ``repeat-key``: fails with an error that repeats the key it was sent;
- ``reject-key``: answers 401 to every request;
- ``never-answer``: does not answer;
- ``slow``: answers after 20 seconds;
- any other id: answers everything.

A request that asks for a stream is answered as one, in the API's own events, because that is
how a run asks: a profile that points here can be run on, not only tested.
"""

import argparse
import asyncio
import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from freyja_agent.llm.providers import ModelSpec, model_arguments

# Shown in the terminal when the stand-in is served for the quickstart; silent in the suite.
logger = logging.getLogger("uvicorn.error")

REFUSAL = "Input should be a valid string: this endpoint takes no earlier turns as input"
SLOW_SECONDS = 20.0


@dataclass
class StubRequest:
    """One request the stand-in received."""

    # "chat" for chat completions, "responses" for the Responses API.
    api: str
    key: str
    model: str
    body: dict[str, Any]

    @property
    def turns(self) -> list[dict[str, Any]]:
        """What the request carries as its conversation, in either API's shape."""
        if self.api == "chat":
            return list(self.body.get("messages", []))
        given = self.body.get("input", [])
        return [{"role": "user", "content": given}] if isinstance(given, str) else list(given)

    @property
    def earlier_turns(self) -> bool:
        """Whether it carries more than the user's own messages."""
        return any(
            turn.get("role") in ("assistant", "tool")
            or turn.get("type") in ("function_call", "function_call_output")
            for turn in self.turns
        )

    @property
    def tool_names(self) -> list[str]:
        """The tools defined in the request, in either API's shape."""
        return [
            tool.get("name") or tool.get("function", {}).get("name")
            for tool in self.body.get("tools", [])
        ]


def error(status: int, message: str, code: str) -> JSONResponse:
    body = {"error": {"message": message, "type": "invalid_request_error", "code": code}}
    return JSONResponse(status_code=status, content=body)


def _chat_answer(model: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-stub",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ready"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _responses_answer(model: str) -> dict[str, Any]:
    return {
        "id": "resp_stub",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": model,
        "output": [
            {
                "type": "message",
                "id": "msg_stub",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "ready", "annotations": []}],
            }
        ],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 1,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 1,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 2,
        },
    }


def _chat_stream(model: str) -> Iterator[str]:
    """Chat completions, streamed: one chunk with the text, one that ends it, the usage."""
    chunk = {"id": "chatcmpl-stub", "object": "chat.completion.chunk", "created": 0, "model": model}
    delta = {"role": "assistant", "content": "ready"}
    usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    for part in (
        {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": usage},
    ):
        yield f"data: {json.dumps({**chunk, **part})}\n\n"
    yield "data: [DONE]\n\n"


def _responses_stream(model: str) -> Iterator[str]:
    """The Responses API, streamed: the events of one message with one piece of text."""
    done = _responses_answer(model)
    started = {**done, "status": "in_progress", "output": []}
    item = done["output"][0]
    text = {"type": "output_text", "text": "ready", "annotations": []}
    where = {"item_id": item["id"], "output_index": 0, "content_index": 0}
    events: list[dict[str, Any]] = [
        {"type": "response.created", "response": started},
        {"type": "response.in_progress", "response": started},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {**item, "status": "in_progress", "content": []},
        },
        {"type": "response.content_part.added", **where, "part": {**text, "text": ""}},
        {"type": "response.output_text.delta", **where, "delta": "ready", "logprobs": []},
        {"type": "response.output_text.done", **where, "text": "ready", "logprobs": []},
        {"type": "response.content_part.done", **where, "part": text},
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": done},
    ]
    for number, event in enumerate(events):
        body = json.dumps({**event, "sequence_number": number})
        yield f"event: {event['type']}\ndata: {body}\n\n"


@dataclass
class LlmStub:
    """The stand-in and what it received."""

    app: FastAPI = field(default_factory=FastAPI)
    requests: list[StubRequest] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Also under the path the LangSmith gateway gives OpenAI, so that an environment
        # which reaches its model through the gateway can be pointed here.
        @self.app.post("/v1/chat/completions")
        @self.app.post("/openai/v1/chat/completions")
        async def chat(request: Request) -> Response:
            return await self._answer("chat", request)

        @self.app.post("/v1/responses")
        @self.app.post("/openai/v1/responses")
        async def responses(request: Request) -> Response:
            return await self._answer("responses", request)

    async def _answer(self, api: str, request: Request) -> Response:
        body = await request.json()
        key = request.headers.get("authorization", "").removeprefix("Bearer ")
        received = StubRequest(api=api, key=key, model=str(body.get("model")), body=body)
        self.requests.append(received)
        model = received.model
        logger.info("%s request for model %s, %d turns", api, model, len(received.turns))

        if model == "reject-key":
            return error(401, "Incorrect API key provided.", "invalid_api_key")
        if model == "repeat-key":
            return error(400, f"The key {key} is not allowed to use this model.", "not_allowed")
        if model == "never-answer":
            await asyncio.Event().wait()
        if model == "slow":
            await asyncio.sleep(SLOW_SECONDS)
        if model == "refuse-conversation" and received.earlier_turns:
            return error(422, REFUSAL, "invalid_type")
        if body.get("stream"):
            events = _chat_stream(model) if api == "chat" else _responses_stream(model)
            return StreamingResponse(events, media_type="text/event-stream")
        answer = _chat_answer(model) if api == "chat" else _responses_answer(model)
        return JSONResponse(content=answer)

    def factory(self, spec: ModelSpec) -> BaseChatModel:
        """A model factory that builds the real client and points it at this stand-in.

        Only the transport is replaced: the client, its request shapes, and its errors are the
        ones a deployment has. The spec's base URL is kept, so the request paths are real too.
        """
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app))
        return init_chat_model(spec.name, **model_arguments(spec), http_async_client=client)


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Serve the stand-in model endpoint on loopback.")
    parser.add_argument("--http", type=int, required=True, metavar="PORT")
    args = parser.parse_args()
    uvicorn.run(LlmStub().app, host="127.0.0.1", port=args.http)


if __name__ == "__main__":
    main()
