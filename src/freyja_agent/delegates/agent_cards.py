"""Reading an agent's card, and what is kept of it (feature 005, research R5).

One function, ``read_card``, makes the one request that reads a card: at startup, for an add,
a move, and a check, and when a link connects later. It reads at most one megabyte, follows
no redirect, and either returns the card or raises a ``CardError`` whose ``CardProblem`` is
one of nine codes, each with a sentence the service words itself (FR-007, FR-016). Nothing an
agent or a library said is in such a sentence, and neither is the address.

``card_details`` gives what is kept, shown, and used to describe an agent's tools: the card's
texts, cut and with every known address removed (FR-002, FR-028).
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from a2a.client.card_resolver import parse_agent_card
from a2a.types import AgentCard
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH

JSONRPC = "JSONRPC"

# A card document larger than this cannot be used (FR-007).
CARD_BYTES_LIMIT = 1024 * 1024
# What is kept of a card: each text to this many characters, and this many skills and tags of
# a skill (FR-002).
TEXT_LIMIT = 2000
SKILL_LIMIT = 50
TAG_LIMIT = 50
# How much of a text is cleaned of addresses before it is cut: the kept length, and room for
# the longest address that could start inside it.
_CLEANED = TEXT_LIMIT + 2100

CardCode = Literal[
    "no_answer",
    "timeout",
    "no_card",
    "unreadable",
    "redirect",
    "too_large",
    "auth_required",
    "no_text",
    "no_interface",
]

# One fixed sentence per way of failing (data-model.md "Card problem"). ``timeout`` takes the
# number of seconds.
SENTENCES: dict[CardCode, str] = {
    "no_answer": "Nothing answered at the address.",
    "timeout": "The agent did not answer within {seconds} seconds.",
    "no_card": "Something answered at the address, but it served no agent card.",
    "unreadable": "The agent's card could not be read.",
    "redirect": "The address answered with a redirect, which is not followed.",
    "too_large": "The agent's card is larger than one megabyte.",
    "auth_required": "The agent's card requires authentication, which is out of scope.",
    "no_text": "The agent's card does not accept text messages.",
    "no_interface": "The agent's card offers no JSON-RPC interface.",
}


@dataclass(frozen=True)
class CardProblem:
    """Why an agent cannot be used: a code, and the service's own sentence for it."""

    code: CardCode
    message: str

    @classmethod
    def of(cls, code: CardCode, *, seconds: float | None = None) -> "CardProblem":
        shown = f"{seconds:g}" if seconds is not None else ""
        return cls(code, SENTENCES[code].format(seconds=shown))


class CardError(Exception):
    """Raised by ``read_card``. ``problem`` says which of the nine it was."""

    def __init__(self, code: CardCode, *, seconds: float | None = None) -> None:
        self.problem = CardProblem.of(code, seconds=seconds)
        super().__init__(self.problem.message)

    @property
    def code(self) -> CardCode:
        return self.problem.code

    @property
    def message(self) -> str:
        return self.problem.message


@dataclass(frozen=True)
class SkillSummary:
    id: str
    name: str
    description: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class AgentCardSummary:
    """What the catalog and the tool description show from a card: the agent's own claims."""

    name: str
    description: str
    version: str
    skills: tuple[SkillSummary, ...]
    # True when a text, the skills, or a skill's tags were cut to the limits of FR-002.
    cut: bool = False

    @classmethod
    def from_card(cls, card: AgentCard) -> "AgentCardSummary":
        return cls(
            name=card.name,
            description=card.description,
            version=card.version,
            skills=tuple(
                SkillSummary(skill.id, skill.name, skill.description, tuple(skill.tags))
                for skill in card.skills
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """The form the card details are stored and returned in."""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "skills": [
                {
                    "id": skill.id,
                    "name": skill.name,
                    "description": skill.description,
                    "tags": list(skill.tags),
                }
                for skill in self.skills
            ],
            "cut": self.cut,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentCardSummary":
        return cls(
            name=data["name"],
            description=data["description"],
            version=data["version"],
            skills=tuple(
                SkillSummary(skill["id"], skill["name"], skill["description"], tuple(skill["tags"]))
                for skill in data["skills"]
            ),
            cut=bool(data.get("cut", False)),
        )


def card_code(card: AgentCard) -> CardCode | None:
    """Which of the three checks a card fails, or None (FR-007, 003 research R3)."""
    if card.security_requirements or card.security_schemes:
        return "auth_required"
    modes = list(card.default_input_modes)
    if modes and not any(mode.startswith("text/") for mode in modes):
        return "no_text"
    if not any(interface.protocol_binding == JSONRPC for interface in card.supported_interfaces):
        return "no_interface"
    return None


# The same three checks as feature 003 words them in a startup error.
_STARTUP_WORDS: dict[CardCode, str] = {
    "auth_required": "its card requires authentication, which is out of scope",
    "no_text": "its card does not accept text messages",
    "no_interface": "its card offers no JSON-RPC interface",
}


def card_problem(card: AgentCard) -> str | None:
    """Why a card cannot be used, or None (003 FR-003, research R3)."""
    code = card_code(card)
    return _STARTUP_WORDS[code] if code is not None else None


async def read_card(http: httpx.AsyncClient, address: str, *, limit: float) -> AgentCard:
    """Read and check the card at ``address`` within ``limit`` seconds.

    One ``GET`` of the well-known path under the address, with ``http``, the client of the
    link the card is read for. The body is asked for unencoded and read as it arrives, so the
    size limit counts what the agent sent and nothing is decoded on the way: a body that is
    compressed all the same is not read. No redirect is followed.

    Raises ``CardError``. Whatever else the read meets is ``unreadable``, and no error of a
    library is chained to it: such an error repeats the address.
    """
    url = address.rstrip("/") + AGENT_CARD_WELL_KNOWN_PATH
    # Each failure is turned into its code here and raised after the ``except`` block, so
    # that the library's error, which holds the address, is not kept as its context either.
    code: CardCode | None = None
    body = b""
    try:
        async with asyncio.timeout(limit):
            found = await _card_body(http, url)
        if isinstance(found, bytes):
            body = found
        else:
            code = found.code
    except (TimeoutError, httpx.TimeoutException):
        code = "timeout"
    except httpx.ConnectError:
        code = "no_answer"
    except Exception:
        code = "unreadable"
    if code is not None:
        raise CardError(code, seconds=limit if code == "timeout" else None)
    card = _parsed(body)
    if card is None:
        raise CardError("unreadable")
    code = card_code(card)
    if code is not None:
        raise CardError(code)
    return card


def _parsed(body: bytes) -> AgentCard | None:
    try:
        return parse_agent_card(json.loads(body))
    except Exception:
        return None


# The answers that send a client elsewhere. Any other answer that is not 200 is no card.
_REDIRECTS = frozenset({301, 302, 303, 307, 308})


async def _card_body(http: httpx.AsyncClient, url: str) -> bytes | CardProblem:
    """The body of the card document, or which problem the answer already shows."""
    request = http.build_request("GET", url, headers={"Accept-Encoding": "identity"})
    response = await http.send(request, stream=True, follow_redirects=False)
    try:
        if response.status_code in _REDIRECTS:
            return CardProblem.of("redirect")
        if response.status_code != 200:
            return CardProblem.of("no_card")
        if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
            return CardProblem.of("unreadable")
        announced = response.headers.get("content-length", "")
        if announced.isdigit() and int(announced) > CARD_BYTES_LIMIT:
            return CardProblem.of("too_large")
        body = bytearray()
        async for chunk in response.aiter_raw():
            body += chunk
            if len(body) > CARD_BYTES_LIMIT:
                return CardProblem.of("too_large")
        return bytes(body)
    finally:
        await response.aclose()


def card_details(card: AgentCard, scrub: Callable[[str], str]) -> AgentCardSummary:
    """The details of ``card`` as they are kept and shown: cleaned of addresses, then cut.

    ``scrub`` removes every agent address the process knows, so a card that names another
    agent's address is cleaned too (FR-028). Cleaning comes first: an address the cut would
    split in two would no longer be found.
    """
    cut = False

    def text(value: str) -> str:
        nonlocal cut
        # A text is the agent's, of any length up to the card's megabyte: only the part that
        # can reach the kept text is cleaned. A NUL cannot be stored, and is dropped.
        if len(value) > _CLEANED:
            cut = True
            value = value[:_CLEANED]
        value = scrub(value.replace("\x00", ""))
        if len(value) > TEXT_LIMIT:
            cut = True
            return value[:TEXT_LIMIT]
        return value

    def tags(values: list[str]) -> tuple[str, ...]:
        nonlocal cut
        if len(values) > TAG_LIMIT:
            cut = True
        return tuple(text(value) for value in values[:TAG_LIMIT])

    skills = list(card.skills)
    if len(skills) > SKILL_LIMIT:
        cut = True
    summary = AgentCardSummary(
        name=text(card.name),
        description=text(card.description),
        version=text(card.version),
        skills=tuple(
            SkillSummary(
                text(skill.id), text(skill.name), text(skill.description), tags(list(skill.tags))
            )
            for skill in skills[:SKILL_LIMIT]
        ),
    )
    return AgentCardSummary(
        summary.name, summary.description, summary.version, summary.skills, cut=cut
    )
