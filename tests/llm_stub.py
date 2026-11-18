"""A stand-in model endpoint for the suite: OpenAI-compatible, in the test process (004 R18).

It serves chat completions and the Responses API under ``/v1`` and records every request it
receives, with the key it was sent. The suite reaches it through the real ``ChatOpenAI`` client
(``stub_factory``); run as a module it serves on the loopback interface for the quickstart:

    uv run python -m tests.llm_stub --http 9997     # base URL http://127.0.0.1:9997/v1

It behaves by the model id it is asked for:

- ``refuse-conversation``: answers a first message and refuses, with a 422, a request that
  carries earlier turns (an assistant message, a tool call, or a tool result). This is the
  failure a profile's test exists to catch (research R7);
- ``repeat-key``: fails with an error that repeats the key it was sent;
- ``reject-key``: answers 401 to every request;
- ``never-answer``: does not answer;
- ``slow``: answers after 20 seconds;
- any other id: answers everything.
"""

import argparse
import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from freyja_agent.llm.providers import ModelSpec, model_arguments

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


@dataclass
class LlmStub:
    """The stand-in and what it received."""

    app: FastAPI = field(default_factory=FastAPI)
    requests: list[StubRequest] = field(default_factory=list)

    def __post_init__(self) -> None:
        @self.app.post("/v1/chat/completions")
        async def chat(request: Request) -> JSONResponse:
            return await self._answer("chat", request)

        @self.app.post("/v1/responses")
        async def responses(request: Request) -> JSONResponse:
            return await self._answer("responses", request)

    async def _answer(self, api: str, request: Request) -> JSONResponse:
        body = await request.json()
        key = request.headers.get("authorization", "").removeprefix("Bearer ")
        received = StubRequest(api=api, key=key, model=str(body.get("model")), body=body)
        self.requests.append(received)
        model = received.model

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
