"""Reading an agent's card, and what is kept of it (feature 005, FR-002, FR-007, FR-016;
research R5). Stand-in agents are reached in the test process."""

import time
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from a2a.types import AgentCard

from freyja_agent.delegates.addresses import ADDRESS, AddressScrubber
from freyja_agent.delegates.agent_cards import (
    SENTENCES,
    AgentCardSummary,
    CardError,
    CardProblem,
    card_details,
    read_card,
)
from tests.a2a_stub import ELSEWHERE, StubAgent, make_stub, stub_card
from tests.asgi_stream import StreamingASGITransport

URL = "http://127.0.0.1:9998"


def client_of(transport: httpx.AsyncBaseTransport) -> httpx.AsyncClient:
    """A client as a link builds it: no redirect followed, and a timeout of its own."""
    return httpx.AsyncClient(transport=transport, follow_redirects=False, timeout=30)


async def read(stub: StubAgent, *, limit: float = 5) -> tuple[AgentCard, StreamingASGITransport]:
    transport = StreamingASGITransport(stub.app)
    async with client_of(transport) as http:
        return await read_card(http, URL, limit=limit), transport


async def problem_of(
    stub: StubAgent, *, limit: float = 5, prepare: Callable[[StreamingASGITransport], None] = id
) -> tuple[CardProblem, StreamingASGITransport]:
    transport = StreamingASGITransport(stub.app)
    prepare(transport)
    async with client_of(transport) as http:
        with pytest.raises(CardError) as raised:
            await read_card(http, URL, limit=limit)
    error = raised.value
    # Nothing a library said rides along, as a cause or as a context: it repeats the address.
    assert error.__cause__ is None
    assert error.__context__ is None
    assert str(error) == error.problem.message
    return error.problem, transport


async def test_a_usable_card_is_returned_after_one_request() -> None:
    card, transport = await read(make_stub("stub"))
    assert card.name == "Stub Agent stub"
    (request,) = transport.requests
    assert (request.method, request.host, request.port) == ("GET", "127.0.0.1", 9998)
    assert request.path == "/.well-known/agent-card.json"
    assert request.headers["accept-encoding"] == "identity"


async def test_the_card_is_asked_for_under_the_address_whatever_its_path() -> None:
    transport = StreamingASGITransport(make_stub("stub").app)
    async with client_of(transport) as http:
        with pytest.raises(CardError):
            await read_card(http, f"{URL}/agents/one/", limit=5)
    assert [r.path for r in transport.requests] == ["/agents/one/.well-known/agent-card.json"]


async def test_a_card_that_names_no_kind_of_input_is_usable() -> None:
    card, _ = await read(make_stub("stub", card=stub_card("stub", input_modes=())))
    assert list(card.default_input_modes) == []


def down(transport: StreamingASGITransport) -> None:
    transport.down = True


def hang(transport: StreamingASGITransport) -> None:
    transport.hang = True


async def test_nothing_answering_is_no_answer() -> None:
    problem, _ = await problem_of(make_stub("stub"), prepare=down)
    assert problem == CardProblem("no_answer", "Nothing answered at the address.")


async def test_an_agent_that_never_answers_ends_at_the_limit() -> None:
    began = time.monotonic()
    problem, _ = await problem_of(make_stub("stub"), limit=0.3, prepare=hang)
    assert time.monotonic() - began < 3
    assert problem == CardProblem("timeout", "The agent did not answer within 0.3 seconds.")


@pytest.mark.parametrize("status", [404, 401, 403, 500, 204])
async def test_an_answer_that_is_no_card_is_no_card(status: int) -> None:
    problem, _ = await problem_of(make_stub("stub", card_body=b"", card_status=status))
    assert problem == CardProblem(
        "no_card", "Something answered at the address, but it served no agent card."
    )


@pytest.mark.parametrize(
    "body",
    [
        b"not a card",
        b"[1, 2, 3]",
        b'"a string"',
        b'{"name": 7}',
        b'{"skills": "none"}',
        b"\xff\xfe",
    ],
    ids=["text", "list", "string", "wrong-type", "wrong-shape", "not-utf8"],
)
async def test_a_body_that_is_no_card_is_unreadable(body: bytes) -> None:
    problem, _ = await problem_of(make_stub("stub", card_body=body))
    assert problem == CardProblem("unreadable", "The agent's card could not be read.")


async def test_a_redirect_is_not_followed() -> None:
    target = "http://elsewhere.example/.well-known/agent-card.json"
    problem, transport = await problem_of(make_stub("stub", redirect_card_to=target))
    assert problem == CardProblem(
        "redirect", "The address answered with a redirect, which is not followed."
    )
    assert [r.host for r in transport.requests] == ["127.0.0.1"]


async def test_a_redirect_is_not_followed_by_a_client_that_follows_them() -> None:
    target = "http://elsewhere.example/.well-known/agent-card.json"
    transport = StreamingASGITransport(make_stub("stub", redirect_card_to=target).app)
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as http:
        with pytest.raises(CardError) as raised:
            await read_card(http, URL, limit=5)
    assert raised.value.code == "redirect"
    assert [r.host for r in transport.requests] == ["127.0.0.1"]


async def test_an_answer_that_is_not_modified_is_no_card() -> None:
    problem, _ = await problem_of(make_stub("stub", card_body=b"", card_status=304))
    assert problem.code == "no_card"


async def test_a_card_larger_than_one_megabyte_is_too_large() -> None:
    sentence = "The agent's card is larger than one megabyte."
    announced, _ = await problem_of(make_stub("stub", card_body=b" " * 1_100_000))
    assert announced == CardProblem("too_large", sentence)
    unannounced, _ = await problem_of(
        make_stub("stub", card_body=b" " * 1_100_000, card_chunked=True)
    )
    assert unannounced == CardProblem("too_large", sentence)
    # Exactly one megabyte of a body that is no card is read, and found unreadable.
    at_the_limit, _ = await problem_of(make_stub("stub", card_body=b" " * (1024 * 1024)))
    assert at_the_limit.code == "unreadable"


class CountingTransport(httpx.AsyncBaseTransport):
    """Serves an endless body with no length announced, and counts what was taken of it."""

    def __init__(self) -> None:
        self.taken = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        async def body() -> AsyncIterator[bytes]:
            while True:
                self.taken += 64 * 1024
                yield b" " * (64 * 1024)

        return httpx.Response(200, content=body(), request=request)


async def test_the_read_stops_once_one_megabyte_has_arrived() -> None:
    transport = CountingTransport()
    async with client_of(transport) as http:
        with pytest.raises(CardError) as raised:
            await read_card(http, URL, limit=5)
    assert raised.value.code == "too_large"
    assert transport.taken <= 1024 * 1024 + 2 * 64 * 1024


async def test_a_compressed_card_is_not_decoded() -> None:
    problem, _ = await problem_of(make_stub("stub", card_encoding="gzip"))
    assert problem.code == "unreadable"
    # A small body that would be five megabytes once decoded is refused the same way.
    bomb = make_stub("stub", card_body=b" " * 5_000_000, card_encoding="gzip")
    problem, _ = await problem_of(bomb)
    assert problem.code == "unreadable"


@pytest.mark.parametrize(
    ("card", "code", "sentence"),
    [
        (
            stub_card("stub", security=True),
            "auth_required",
            "The agent's card requires authentication, which is out of scope.",
        ),
        (
            stub_card("stub", input_modes=("image/png",)),
            "no_text",
            "The agent's card does not accept text messages.",
        ),
        (
            stub_card("stub", binding="HTTP+JSON"),
            "no_interface",
            "The agent's card offers no JSON-RPC interface.",
        ),
    ],
    ids=["authentication", "no-text", "no-json-rpc"],
)
async def test_a_card_freyja_cannot_use_says_which_check_failed(
    card: AgentCard, code: str, sentence: str
) -> None:
    problem, _ = await problem_of(make_stub("stub", card=card))
    assert problem == CardProblem(code, sentence)  # type: ignore[arg-type]


class Failing(httpx.AsyncBaseTransport):
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise self.error


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (httpx.ConnectError("All connection attempts failed"), "no_answer"),
        (httpx.ConnectTimeout("timed out"), "timeout"),
        (httpx.ReadTimeout("timed out"), "timeout"),
        (
            httpx.RemoteProtocolError("Server disconnected without sending a response."),
            "unreadable",
        ),
        (httpx.ReadError("[Errno 104] Connection reset by peer ('127.0.0.1', 9998)"), "unreadable"),
        (RuntimeError("something else, at http://127.0.0.1:9998"), "unreadable"),
    ],
    ids=["refused", "connect-timeout", "read-timeout", "disconnected", "reset", "anything-else"],
)
async def test_whatever_the_read_meets_is_one_of_the_nine(error: Exception, code: str) -> None:
    async with client_of(Failing(error)) as http:
        with pytest.raises(CardError) as raised:
            await read_card(http, URL, limit=5)
    assert raised.value.code == code
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "9998" not in str(raised.value)


def test_no_sentence_holds_an_address_or_anything_an_agent_said() -> None:
    assert len(SENTENCES) == 9
    for code in SENTENCES:
        message = CardProblem.of(code, seconds=20).message  # type: ignore[arg-type]
        assert message.endswith(".")
        for part in ("http", "127.0.0.1", "localhost", "9998", "{", "}"):
            assert part not in message
    assert (
        CardProblem.of("timeout", seconds=20).message
        == "The agent did not answer within 20 seconds."
    )
    assert (
        CardProblem.of("timeout", seconds=2.5).message
        == "The agent did not answer within 2.5 seconds."
    )


# The card details: what is kept, shown, and used to describe an agent's tools.


def details(card: AgentCard, scrubber: AddressScrubber | None = None) -> AgentCardSummary:
    return card_details(card, (scrubber or AddressScrubber()).scrub)


def test_a_card_within_the_limits_is_kept_whole() -> None:
    kept = details(stub_card("stub"))
    assert kept == AgentCardSummary.from_card(stub_card("stub"))
    assert kept.cut is False
    assert AgentCardSummary.from_dict(kept.to_dict()) == kept


def test_each_text_is_cut_to_two_thousand_characters() -> None:
    kept = details(stub_card("stub", text_length=3000))
    texts = [kept.name, kept.description, kept.version]
    texts += [text for skill in kept.skills for text in (skill.name, skill.description)]
    assert {len(text) for text in texts} == {2000}
    assert kept.name.startswith("Stub Agent stub")
    assert kept.cut is True
    exactly = details(stub_card("stub", text_length=2000))
    assert exactly.cut is False
    assert len(exactly.description) == 2000


def test_skills_and_a_skills_tags_are_cut_to_fifty() -> None:
    kept = details(stub_card("stub", skill_count=60, tag_count=70))
    assert len(kept.skills) == 50
    assert [skill.id for skill in kept.skills[:3]] == ["echo", "report", "skill-2"]
    assert {len(skill.tags) for skill in kept.skills} == {50}
    assert kept.skills[0].tags[:2] == ("tag-0", "tag-1")
    assert kept.cut is True
    assert details(stub_card("stub", skill_count=50, tag_count=50)).cut is False
    assert details(stub_card("stub", skill_count=51)).cut is True
    assert details(stub_card("stub", tag_count=51)).cut is True


def test_every_known_address_is_removed_from_every_text() -> None:
    scrubber = AddressScrubber()
    scrubber.mark_saved(URL)
    scrubber.mark_saved("http://localhost:9996")
    card = stub_card("stub", description=f"Reach me at {URL}/a2a or my friend at localhost:9996.")
    card.name = "Agent on 127.0.0.1:9998"
    card.version = "1.0 (localhost:9998)"
    card.skills[0].id = "at-127.0.0.1:9998"
    card.skills[0].name = "Echo at [::1]:9998"
    card.skills[0].description = f"Posts to {URL}"
    card.skills[0].tags.append("127.0.0.1:9996")
    kept = details(card, scrubber)
    whole = repr(kept.to_dict())
    assert "9998" not in whole
    assert "9996" not in whole
    assert kept.description == f"Reach me at {ADDRESS}/a2a or my friend at {ADDRESS}."
    assert kept.skills[0].tags == ("test", ADDRESS)
    # An address elsewhere is the agent's own claim and stays, as a card's other text does.
    assert (
        ELSEWHERE
        in details(stub_card("stub", description=f"See {ELSEWHERE}"), scrubber).description
    )


def test_an_address_is_removed_before_the_cut_could_split_it() -> None:
    scrubber = AddressScrubber()
    scrubber.mark_saved(URL)
    description = "x" * 1990 + " " + URL + " and more"
    kept = details(stub_card("stub", description=description), scrubber)
    assert len(kept.description) == 2000
    assert "127.0.0.1" not in kept.description
    assert kept.cut is True


def test_a_character_that_cannot_be_stored_is_dropped() -> None:
    card = stub_card("stub", description="before\x00after")
    card.skills[0].tags.append("a\x00tag")
    kept = details(card)
    assert kept.description == "beforeafter"
    assert kept.skills[0].tags == ("test", "atag")
    assert "\x00" not in repr(kept.to_dict())


def test_a_very_long_text_is_cut_without_being_cleaned_whole() -> None:
    scrubber = AddressScrubber()
    for number in range(220):
        scrubber.register(f"http://127.0.0.1:{8000 + number}")
    scrubbed: list[int] = []

    def scrub(text: str) -> str:
        scrubbed.append(len(text))
        return scrubber.scrub(text)

    description = ("see 127.0.0.1:8101 " * 60_000)[:900_000]
    began = time.monotonic()
    kept = card_details(stub_card("stub", description=description), scrub)
    assert time.monotonic() - began < 2
    assert max(scrubbed) <= 2000 + 2100
    assert len(kept.description) == 2000
    assert "8101" not in kept.description
    assert kept.cut is True


def test_the_stored_form_reads_back_the_same() -> None:
    kept = details(stub_card("stub", skill_count=60, text_length=2100))
    stored = kept.to_dict()
    assert stored["cut"] is True
    assert set(stored) == {"name", "description", "version", "skills", "cut"}
    assert set(stored["skills"][0]) == {"id", "name", "description", "tags"}
    assert AgentCardSummary.from_dict(stored) == kept
