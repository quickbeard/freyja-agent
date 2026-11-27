"""The stand-in agent's card options that feature 005's card tests rely on (005 T003).

These check the test tooling itself: what ``tests/a2a_stub.py`` serves where a card would be.
"""

import gzip
import json
from typing import Any

import httpx
import pytest
from a2a.server.request_handlers.response_helpers import agent_card_to_dict

from tests.a2a_stub import CARD_PATH, ELSEWHERE, StubAgent, make_stub, stub_card
from tests.asgi_stream import StreamingASGITransport


async def served(stub: StubAgent, **headers: str) -> tuple[int, httpx.Headers, bytes]:
    """The status, the headers, and the body as sent, without decoding it."""
    transport = StreamingASGITransport(stub.app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9999") as client,
        client.stream("GET", CARD_PATH, headers=headers) as response,
    ):
        body = b"".join([chunk async for chunk in response.aiter_raw()])
        return response.status_code, response.headers, body


def card_of(body: bytes) -> dict[str, Any]:
    return json.loads(body)


async def test_the_usual_card_is_unchanged() -> None:
    status, headers, body = await served(make_stub("usual"))
    card = card_of(body)
    assert status == 200
    # Served by the SDK's own route, as before: with its ETag, and not compressed.
    assert headers["etag"]
    assert "content-encoding" not in headers
    assert card == agent_card_to_dict(stub_card("usual"))
    assert (card["name"], card["version"]) == ("Stub Agent usual", "1.0.0")
    assert card["description"] == "A stand-in agent for Freyja's tests."
    assert [(skill["id"], skill["name"], skill["tags"]) for skill in card["skills"]] == [
        ("echo", "Echo", ["test"]),
        ("report", "Report", ["test"]),
    ]


async def test_a_card_can_have_many_skills_many_tags_and_long_texts() -> None:
    large = stub_card("large", skill_count=60, tag_count=70, text_length=3000)
    _, _, body = await served(make_stub("large", card=large))
    card = card_of(body)
    assert len(card["skills"]) == 60
    assert {len(skill["tags"]) for skill in card["skills"]} == {70}
    texts = [card["name"], card["description"], card["version"]]
    texts += [skill[key] for skill in card["skills"] for key in ("name", "description")]
    assert {len(text) for text in texts} == {3000}
    assert card["name"].startswith("Stub Agent large")


async def test_a_card_can_have_fewer_skills_than_usual() -> None:
    _, _, one = await served(make_stub("one", card=stub_card("one", skill_count=1)))
    assert [skill["id"] for skill in card_of(one)["skills"]] == ["echo"]
    assert list(stub_card("none", skill_count=0).skills) == []
    assert list(stub_card("none", skills=False, skill_count=5).skills) == []
    assert [list(skill.tags) for skill in stub_card("bare", tag_count=0).skills] == [[], []]


def test_a_text_length_pads_or_cuts_to_exactly_that_many_characters() -> None:
    assert stub_card("x", text_length=5).name == "Stub "
    assert stub_card("x", text_length=20).name == "Stub Agent x".ljust(20, "x")


@pytest.mark.parametrize("option", ["skill_count", "tag_count", "text_length"])
def test_a_negative_size_is_refused(option: str) -> None:
    with pytest.raises(ValueError, match=option):
        sizes: dict[str, Any] = {option: -1}
        stub_card("x", **sizes)


async def test_any_bytes_can_be_served_where_the_card_would_be() -> None:
    status, _, body = await served(make_stub("big", card_body=b"x" * 1_100_000))
    assert (status, len(body)) == (200, 1_100_000)
    status, _, body = await served(make_stub("other", card_body=b"not a card"))
    assert (status, body) == (200, b"not a card")
    status, _, body = await served(make_stub("none", card_body=b"", card_status=404))
    assert (status, body) == (404, b"")
    # A status alone keeps the card as the body.
    status, _, body = await served(make_stub("down", card_status=503))
    assert status == 503
    assert card_of(body) == agent_card_to_dict(stub_card("down"))


async def test_a_body_can_be_sent_with_no_length_announced() -> None:
    status, headers, body = await served(make_stub("big", card_body=b"x" * 1_100_000))
    assert headers["content-length"] == "1100000"
    stub = make_stub("big", card_body=b"x" * 1_100_000, card_chunked=True)
    status, headers, body = await served(stub)
    assert status == 200
    assert "content-length" not in headers
    assert body == b"x" * 1_100_000


def test_options_that_cannot_be_served_together_are_refused() -> None:
    with pytest.raises(ValueError, match="card_encoding"):
        make_stub("x", card_encoding="br")
    options: list[dict[str, Any]] = [
        {"card_body": b"x"},
        {"card_status": 404},
        {"card_encoding": "gzip"},
    ]
    for option in options:
        with pytest.raises(ValueError, match="redirect_card_to"):
            make_stub("x", redirect_card_to=ELSEWHERE, **option)
    with pytest.raises(ValueError, match="redirect_card_to"):
        make_stub("x", redirect_card_to=ELSEWHERE, card_chunked=True)


async def test_a_card_can_be_served_compressed_whatever_was_asked() -> None:
    status, headers, body = await served(
        make_stub("zip", card_encoding="gzip"), **{"accept-encoding": "identity"}
    )
    assert status == 200
    assert headers["content-encoding"] == "gzip"
    assert card_of(gzip.decompress(body))["name"] == "Stub Agent zip"

    # A small body on the wire that is large once decoded, for the size limit's test.
    stub = make_stub("bomb", card_body=b" " * 5_000_000, card_encoding="gzip")
    _, _, body = await served(stub)
    assert len(body) < 10_000
    assert len(gzip.decompress(body)) == 5_000_000


async def test_a_stand_in_with_another_card_body_still_answers_messages() -> None:
    """Only the card route changes: the JSON-RPC endpoint is where the card says it is."""
    stub = make_stub("still", card_body=b"not a card")
    transport = StreamingASGITransport(stub.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9999") as client:
        response = await client.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "nope"})
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32601
