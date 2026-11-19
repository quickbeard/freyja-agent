"""The agents one service instance has tools for (feature 005, research R3, R4).

The registry owns the links of one instance and holds its current ``AgentSet``: the links in
use, each agent's approval choice, and the saved agents that are not in use with the reason
for each. Agents come from two places. Those named in ``FREYJA_A2A_AGENTS`` are fixed for the
life of the process. Those an operator saved are read from the store, whose one-row revision
says whether the set this instance built for is still current.

``start`` refuses an environment entry that is invalid in itself, as feature 003 did, and
nothing else: an agent whose card cannot be read or used gets its link and its tools all the
same, a warning, and another try whenever it is needed (FR-018, FR-020, FR-021).

``refresh`` reads the revision, one single-row read, and makes a new set when it moved. A
link whose agent ref is unchanged is kept, so a run on an earlier set and a run on the new one
share it; a link is made for each agent that is new or was moved. Sets are held while
something uses them. A set that is no longer current and that nothing holds closes the links
only it had, so a run in progress keeps the links it started with for as long as it runs.
"""

import asyncio
import logging
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

from a2a.types import AgentCard

from freyja_agent.config import A2aAgentConfig, AddressError, canonical_address, destination
from freyja_agent.delegates.a2a_agents import (
    AgentLink,
    AgentStartupError,
    AgentTransport,
    cancel_tool_name,
    default_transport,
    handoff_tool_name,
    log_connected,
    read_card_at,
)
from freyja_agent.delegates.addresses import SCRUBBER
from freyja_agent.delegates.agent_cards import AgentCardSummary, CardError, cleaned
from freyja_agent.runtime.saved_agents import SavedAgentRecord, SavedAgentStore

logger = logging.getLogger(__name__)

# Why a saved agent is not in use: the environment's is the one in use (FR-017).
NAME_IS_AN_ENVIRONMENT_AGENT = "An agent named in the environment has this name."
NAME_IS_A_TOOL_SERVER = "A tool server has this name."
TOOL_NAME_TAKEN = "Another tool has the name of one of this agent's tools."
ADDRESS_IS_AN_ENVIRONMENT_AGENT = "An agent named in the environment is at this address."


@dataclass(frozen=True)
class NotInUse:
    """A saved agent the environment overrides, and which rule it is."""

    record: SavedAgentRecord
    reason: str


@dataclass(frozen=True, eq=False)
class AgentSet:
    """What one instance builds its orchestrator's agent tools from (data-model.md "Agent set").

    Two sets are the same set only when they are the same object: the orchestrator is rebuilt
    when the set is not the one it was built for.
    """

    # The revision of the saved agents this set was read at.
    revision: int = 0
    # The links in use: environment agents first, then saved agents by name.
    links: tuple[AgentLink, ...] = ()
    # Each agent's approval choice by name, as the environment's entry names it: True runs
    # without approval.
    read_only: Mapping[str, bool] = field(default_factory=dict)
    # The saved agents that have no link and no tools, with the reason for each.
    not_in_use: tuple[NotInUse, ...] = ()
    # The saved agents in use, by the name of their link.
    records: Mapping[str, SavedAgentRecord] = field(default_factory=dict)

    def link(self, name: str) -> AgentLink | None:
        """The link in use under ``name``, if any."""
        return next((link for link in self.links if link.name == name), None)

    def gated(self, link: AgentLink) -> bool:
        """Whether a message to the link's agent requires a person's approval."""
        return not self.read_only.get(link.name, False)


def _destination(url: str) -> str | None:
    """Where an environment agent's address leads, or None for a spelling the rule refuses.

    An environment entry keeps its own rule and its own spelling (research R11), so one that
    the saved form does not allow cannot be compared and matches no saved agent.
    """
    try:
        return destination(canonical_address(url))
    except AddressError:
        return None


class AgentRegistry:
    """The links of one instance, and the set a run is built with."""

    def __init__(
        self,
        configs: Mapping[str, A2aAgentConfig],
        *,
        saved: SavedAgentStore,
        transport: AgentTransport = default_transport,
        startup_timeout: float,
        call_timeout: float,
        server_names: Collection[str] = (),
        taken_tool_names: Collection[str] = (),
    ) -> None:
        self._configs = dict(configs)
        self._saved = saved
        self._transport = transport
        self._startup_timeout = startup_timeout
        self._call_timeout = call_timeout
        self._server_names = frozenset(server_names)
        self._taken_tool_names = frozenset(taken_tool_names)
        self._environment: tuple[AgentLink, ...] = ()
        self._destinations = frozenset(
            found
            for found in (_destination(config.url) for config in self._configs.values())
            if found is not None
        )
        self.current = AgentSet()
        # How many contexts hold each set that is in use.
        self._holders: dict[AgentSet, int] = {}
        # Two runs that start together after a change make one new set, not two.
        self._lock = asyncio.Lock()
        self._started = False

    async def start(self) -> AgentSet:
        """Make a link for every agent in use and try them all at once (research R4).

        Raises ``AgentStartupError`` for an environment entry whose name is a tool server's or
        whose tool name is taken, before anything is contacted. A card that cannot be read or
        used stops nothing: the agent is warned about and tried again when it is needed.
        """
        problems = [
            f"Agent '{name}': the name is also a tool server's name."
            for name in self._configs
            if name in self._server_names
        ]
        problems += [
            f"Agent '{name}': the tool name '{tool}' is already used by another tool."
            for name in self._configs
            for tool in (handoff_tool_name(name), cancel_tool_name(name))
            if tool in self._taken_tool_names
        ]
        if problems:
            raise AgentStartupError("; ".join(problems))
        self._environment = tuple(
            self._watched(
                AgentLink(name, config, transport=self._transport, call_timeout=self._call_timeout)
            )
            for name, config in self._configs.items()
        )
        revision = await self._saved.revision()
        made = self._make(revision, await self._saved.list_agents(), AgentSet())
        # All at once, each within the startup limit, so any number of agents that are down
        # delay startup by one limit (SC-006).
        outcomes = await asyncio.gather(
            *(link.connect(limit=self._startup_timeout) for link in made.links),
            return_exceptions=True,
        )
        for link, outcome in zip(made.links, outcomes, strict=True):
            if isinstance(outcome, CardError):
                _warn_unusable(link, outcome)
            elif isinstance(outcome, BaseException):
                await self._close(made.links)
                raise outcome
            else:
                log_connected(link, read_only=not made.gated(link))
        for unused in made.not_in_use:
            _warn_not_in_use(unused)
        self.current = made
        self._started = True
        return made

    def _watched(self, link: AgentLink) -> AgentLink:
        link.on_card_changed = self._card_changed
        return link

    def _card_changed(self, _link: AgentLink) -> None:
        """An environment agent's card was read after its tools were built: build them again."""
        if self._started:
            self.mark_changed()

    def overridden(self, name: str, leads_to: str) -> str | None:
        """Which rule keeps a saved agent of this name and destination out of use, or None.

        What the environment configures comes first (FR-017): an agent named there, a tool
        server, or a tool that has one of the agent's two tool names, and then an
        environment agent at the place the address leads to. The same rule decides that an
        agent is not saved under such a name or address in the first place (FR-006).
        """
        if name in self._configs:
            return NAME_IS_AN_ENVIRONMENT_AGENT
        if name in self._server_names:
            return NAME_IS_A_TOOL_SERVER
        tools = (handoff_tool_name(name), cancel_tool_name(name))
        if any(tool in self._taken_tool_names for tool in tools):
            return TOOL_NAME_TAKEN
        if leads_to in self._destinations:
            return ADDRESS_IS_AN_ENVIRONMENT_AGENT
        return None

    def _why_not_in_use(self, record: SavedAgentRecord) -> str | None:
        """Which rule keeps a saved agent out of use, or None (FR-017)."""
        return self.overridden(record.name, record.destination)

    async def read_card(self, name: str, url: str, *, limit: float) -> AgentCard:
        """Read the card of an agent that is not saved yet, as a link of this instance would.

        The same transport and the same kind of client as a link's, made for this one read
        and closed after it. Raises ``CardError``.
        """
        return await read_card_at(name, url, transport=self._transport, limit=limit)

    def _make(
        self, revision: int, records: Collection[SavedAgentRecord], earlier: AgentSet
    ) -> AgentSet:
        """The set for ``records``, keeping every link of ``earlier`` whose ref is unchanged."""
        kept = {link.ref: link for link in earlier.links if link.ref is not None}
        links = list(self._environment)
        read_only = {name: config.read_only for name, config in self._configs.items()}
        not_in_use: list[NotInUse] = []
        in_use: dict[str, SavedAgentRecord] = {}
        for record in sorted(records, key=lambda found: found.name):
            reason = self._why_not_in_use(record)
            if reason is not None:
                not_in_use.append(NotInUse(record, reason))
                continue
            details = AgentCardSummary.from_dict(record.card)
            link = kept.get(record.ref)
            if link is None:
                link = AgentLink(
                    record.name,
                    # Never read-only on the link: the approval choice is the set's, so a
                    # caller that forgets the set gates (research R7).
                    A2aAgentConfig(url=record.url, read_only=False),
                    transport=self._transport,
                    call_timeout=self._call_timeout,
                    source="saved",
                    agent_id=record.id,
                    address_version=record.address_version,
                    card=details,
                )
            else:
                # The kept details may have been replaced by a check (FR-015).
                link.card = details
            links.append(link)
            read_only[record.name] = record.read_only
            in_use[record.name] = record
        # Every address of this set is known to the scrubber by now: each link told it when
        # it was made. Details that were read or kept before one of them became an agent's
        # are cleaned of it here, before the tools are described from them (FR-028).
        for link in links:
            if link.card is not None:
                link.card = cleaned(link.card, SCRUBBER.scrub)
        return AgentSet(
            revision=revision,
            links=tuple(links),
            read_only=read_only,
            not_in_use=tuple(not_in_use),
            records=in_use,
        )

    async def refresh(self) -> AgentSet:
        """The set as the store has it now: a new one when the revision moved (research R3)."""
        if await self._saved.revision() == self.current.revision:
            return self.current
        async with self._lock:
            # Another run may have made the new set while this one waited for the lock.
            revision = await self._saved.revision()
            if revision != self.current.revision:
                earlier = self.current
                made = self._make(revision, await self._saved.list_agents(), earlier)
                known = {(unused.record.id, unused.reason) for unused in earlier.not_in_use}
                for unused in made.not_in_use:
                    if (unused.record.id, unused.reason) not in known:
                        _warn_not_in_use(unused)
                await self._replace(made)
        return self.current

    async def adopt(self, record: SavedAgentRecord, card: AgentCard) -> AgentSet:
        """Take up a change this instance just made, with the card it just read.

        The agent's link is connected with ``card``, so the card is not read a second time.
        """
        made = await self.refresh()
        link = made.link(record.name)
        if link is not None and link.ref == record.ref:
            await link.connect(card, limit=self._startup_timeout)
        return made

    def mark_changed(self) -> None:
        """Have the next context rebuild on this instance, the revision not having moved.

        The current set gives way to another with the same links, which the orchestrator was
        not built for.
        """
        earlier = self.current
        self.current = AgentSet(
            revision=earlier.revision,
            links=earlier.links,
            read_only=earlier.read_only,
            not_in_use=earlier.not_in_use,
            records=earlier.records,
        )

    def hold(self, held: AgentSet) -> None:
        """Count one more user of ``held``; its links stay open at least until it is released."""
        self._holders[held] = self._holders.get(held, 0) + 1

    async def release(self, held: AgentSet) -> None:
        """Count one user less; a set nothing uses any more closes the links only it had."""
        left = self._holders.get(held, 0) - 1
        if left > 0:
            self._holders[held] = left
            return
        self._holders.pop(held, None)
        if held is not self.current:
            await self._close_unused(held)

    async def _replace(self, made: AgentSet) -> None:
        earlier, self.current = self.current, made
        if earlier not in self._holders:
            await self._close_unused(earlier)

    async def _close_unused(self, left: AgentSet) -> None:
        """Close the links of ``left`` that neither the current set nor a held one has."""
        in_use = set(self.current.links)
        for held in self._holders:
            in_use.update(held.links)
        await self._close([link for link in left.links if link not in in_use])

    @staticmethod
    async def _close(links: Collection[AgentLink]) -> None:
        await asyncio.gather(*(link.close() for link in links), return_exceptions=True)

    async def close(self) -> None:
        """Close every link, of the current set and of every set still held."""
        links = set(self.current.links) | set(self._environment)
        for held in self._holders:
            links.update(held.links)
        self._holders.clear()
        await self._close(links)


def _warn_unusable(link: AgentLink, error: CardError) -> None:
    """The warning for an agent whose card could not be read or used (FR-020, FR-030)."""
    logger.warning("Agent %s cannot be used: %s", link.name, error.message)


def _warn_not_in_use(unused: NotInUse) -> None:
    """The warning for a saved agent the environment overrides (FR-017, FR-030)."""
    logger.warning("Saved agent %s is not in use: %s", unused.record.name, unused.reason)
