"""The routes of the Agents section (feature 005, contracts/openapi.yaml, research R16).

The agents an operator saved are operator-managed configuration: kept in the service's
database and changed only through these routes (constitution principle IV). An agent's
address is in the body of an answer of these routes and in no other answer of the service;
an environment agent's address is in none. The router refuses a request that changes
something when a page of another origin made it, by the rule the LLM routes apply.

Adding an agent and checking one read its card and nothing more: the agent receives no
message and no task (FR-027). Changing an agent's approval choice contacts no agent at all.
An agent requires approval unless a request says otherwise; the console asks an operator to
confirm before it sends such a request (FR-011).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request

from freyja_agent.api.guards import NoInputRoute, refuse_other_origins
from freyja_agent.api.schemas import (
    AGENT_ID_PATTERN,
    Agent,
    AgentCard,
    AgentChanged,
    AgentCheck,
    AgentCreate,
    AgentList,
    AgentSkillClaim,
    AgentUpdate,
)
from freyja_agent.config import destination
from freyja_agent.delegates.a2a_agents import AgentCardSummary, AgentLink
from freyja_agent.delegates.addresses import SCRUBBER
from freyja_agent.delegates.agent_cards import CardError, CardProblem, card_details, cleaned
from freyja_agent.delegates.agent_registry import (
    ADDRESS_IS_AN_ENVIRONMENT_AGENT,
    AgentRegistry,
    AgentSet,
)
from freyja_agent.runtime.runtime import RunContext, Runtime
from freyja_agent.runtime.saved_agents import (
    AGENT_LIMIT,
    AddressTakenError,
    AgentChangedError,
    AgentLimitError,
    AgentNotFoundError,
    NameTakenError,
    SavedAgentRecord,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/agents",
    tags=["agents"],
    route_class=NoInputRoute,
    dependencies=[Depends(refuse_other_origins("Agents"))],
)

# The states a warning is about: the agent cannot be handed work now (FR-001a).
UNUSABLE = ("unreachable", "not_in_use")

# A card read a person asked for ends within this many seconds, whatever the service's limit
# for reading a card is set to: the console's server stops waiting after thirty (FR-014,
# research R15). A constant of the contract, not a setting.
CARD_READ_SECONDS = 20.0

# The refusals of an add, in the service's words. None holds a name or an address.
SAVED_NAME_TAKEN = "Another saved agent has this name."
SAVED_ADDRESS_TAKEN = "Another saved agent is at this address."
LIMIT_REACHED = f"{AGENT_LIMIT} agents are saved, which is the most there can be."
NOT_FOUND = "There is no agent with this id."
NOT_IN_USE = "This saved agent is not in use, so it cannot be checked."
UNREADABLE = "The saved agents could not be read."
# The refusals of a change.
CHANGED_SINCE = "The agent was changed since this page read it. Nothing was changed."
ENVIRONMENT_READ_ONLY = "An agent named in the environment cannot be changed here."
ONLY_REMOVED = "This saved agent is not in use, so it can only be removed."

AgentIdPath = Annotated[str, Path(min_length=1, max_length=64, pattern=AGENT_ID_PATTERN)]


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def _claims(card: AgentCardSummary | None) -> AgentCard | None:
    """Card details as the list returns them; None for a card this instance has not read.

    Cleaned again of every address the process knows now: what a card said before another
    agent was saved at an address it names is kept as it was read (FR-028).
    """
    if card is None:
        return None
    card = cleaned(card, SCRUBBER.scrub)
    return AgentCard(
        name=card.name,
        description=card.description,
        version=card.version,
        skills=[
            AgentSkillClaim(
                id=skill.id, name=skill.name, description=skill.description, tags=list(skill.tags)
            )
            for skill in card.skills
        ],
        cut=card.cut,
    )


def _attempt(link: AgentLink) -> dict[str, object]:
    """The outcome of this instance's most recent attempt to use the link's agent (FR-003)."""
    if not link.tried:
        return {"state": "not_tried", "checked_at": None, "reason": None}
    if link.reachable:
        return {"state": "reachable", "checked_at": link.checked_at, "reason": None}
    # Already stripped of every address and cut to 500 characters by ``AgentLink.mark``.
    return {"state": "unreachable", "checked_at": link.checked_at, "reason": link.error}


def _from_the_environment(link: AgentLink, pending: int) -> Agent:
    """An environment agent: nothing of it can be changed, and its address is not shown."""
    return Agent(
        id=f"env.{link.name}",
        name=link.name,
        source="environment",
        approval="none" if link.read_only else "required",
        url=None,
        version=None,
        card=_claims(link.card),
        pending_tasks=pending,
        created_at=None,
        updated_at=None,
        **_attempt(link),
    )


def _saved(record: SavedAgentRecord, agents: AgentSet | None, pending: int) -> Agent:
    """A saved agent as the store has it, with this instance's view of whether it answers.

    ``agents`` is the set that view is read from: a context's, or the one this instance
    took up after a change it made; None when it has none.
    """
    link = agents.link(record.name) if agents is not None else None
    unused = (
        next((found for found in agents.not_in_use if found.record.id == record.id), None)
        if agents is not None
        else None
    )
    if unused is not None:
        attempt: dict[str, object] = {
            "state": "not_in_use",
            "checked_at": None,
            "reason": unused.reason,
        }
    elif link is not None and link.ref == record.ref:
        attempt = _attempt(link)
    else:
        # Saved or moved since this context's set was read: nothing has tried it yet.
        attempt = {"state": "not_tried", "checked_at": None, "reason": None}
    return Agent(
        id=record.id,
        name=record.name,
        source="saved",
        approval="none" if record.read_only else "required",
        url=record.url,
        version=record.version,
        # The details an operator saw when the agent was saved, moved, or last checked.
        card=_claims(AgentCardSummary.from_dict(record.card)),
        pending_tasks=pending,
        created_at=record.created_at,
        updated_at=record.updated_at,
        **attempt,
    )


async def agent_list(runtime: Runtime) -> AgentList:
    """Every agent as this instance sees it: the environment's first, then the saved by name.

    Read from the database and from what this instance holds in memory: one context, then
    the saved agents and the pending counts. No agent is contacted (FR-003).

    The list returns addresses and the versions a change is made from, so it never answers
    with an earlier list: when the saved agents cannot be read it answers with an error
    (FR-004), where the catalog and a thread's state answer from what the instance last had.
    """
    try:
        context = await runtime.begin_run()
    except Exception as exc:
        logger.warning("The agents could not be listed: %s", type(exc).__name__)
        raise _error(500, "internal", UNREADABLE) from None
    try:
        try:
            records = await runtime.saved_agents.list_agents()
            counts = await runtime.agent_tasks.pending_counts()
        except Exception as exc:
            logger.warning("The agents could not be listed: %s", type(exc).__name__)
            raise _error(500, "internal", UNREADABLE) from None
        listed = [
            _from_the_environment(link, counts.get((link.name, None), 0))
            for link in context.links
            if link.source == "environment"
        ]
        listed += [
            _saved(record, context.agents, counts.get((record.name, record.ref), 0))
            for record in sorted(records, key=lambda found: found.name)
        ]
    finally:
        await context.release()
    return AgentList(
        agents=listed,
        limit=AGENT_LIMIT,
        unusable=sum(1 for agent in listed if agent.state in UNUSABLE),
    )


@router.get("", response_model=AgentList)
async def list_agents(request: Request) -> AgentList:
    """List every agent Freyja may hand work to, with this instance's view of each."""
    return await agent_list(_runtime(request))


def _taken(reason: str) -> HTTPException:
    """The refusal for a name or an address the environment has, by the rule that found it."""
    code = "address_taken" if reason == ADDRESS_IS_AN_ENVIRONMENT_AGENT else "name_taken"
    return _error(409, code, reason)


def _unusable(problem: CardProblem) -> HTTPException:
    """The refusal for an agent whose card cannot be used: the code and its sentence (FR-016)."""
    return HTTPException(
        status_code=409,
        detail={"code": "agent_unusable", "reason": problem.code, "message": problem.message},
    )


async def _free(runtime: Runtime, registry: AgentRegistry, name: str, leads_to: str) -> None:
    """Refuse a name or an address that is taken, or an add beyond the limit (FR-006).

    What the environment has first, which the store does not know, then what is saved. All
    of it before any card is read; the store decides again when the agent is saved.
    """
    reason = registry.overridden(name, leads_to)
    if reason is not None:
        raise _taken(reason)
    try:
        records = await runtime.saved_agents.list_agents()
    except Exception as exc:
        logger.warning("Agent %s was not added: %s", name, type(exc).__name__)
        raise _error(500, "internal", UNREADABLE) from None
    if len(records) >= AGENT_LIMIT:
        raise _error(409, "agent_limit", LIMIT_REACHED)
    if any(record.name == name for record in records):
        raise _error(409, "name_taken", SAVED_NAME_TAKEN)
    if any(record.destination == leads_to for record in records):
        raise _error(409, "address_taken", SAVED_ADDRESS_TAKEN)


async def _save(runtime: Runtime, body: AgentCreate, card: AgentCardSummary) -> SavedAgentRecord:
    """Save the agent; the store decides on the name, the address, and the limit again."""
    refused: HTTPException | None = None
    try:
        return await runtime.saved_agents.add(
            name=body.name,
            url=body.url,
            card=card.to_dict(),
            read_only=body.approval == "none",
        )
    except NameTakenError:
        refused = _error(409, "name_taken", SAVED_NAME_TAKEN)
    except AddressTakenError:
        refused = _error(409, "address_taken", SAVED_ADDRESS_TAKEN)
    except AgentLimitError:
        refused = _error(409, "agent_limit", LIMIT_REACHED)
    except Exception as exc:
        # What a database says may name its own address, or the value it refused.
        logger.warning("Agent %s was not added: %s", body.name, type(exc).__name__)
        refused = _error(500, "internal", "The agent could not be saved.")
    # Raised here, not in a handler, so that no error of the store is kept behind it.
    raise refused


@router.post("", response_model=Agent, status_code=201)
async def add_agent(body: AgentCreate, request: Request) -> Agent:
    """Add an agent: read its card at the address, and save it when it can be used.

    Nothing is saved for an agent that cannot be used, and the agent is sent nothing but the
    request for its card (FR-008, FR-027). The saved agent requires approval unless the
    request chose ``none``. On success this instance takes the change up at once, and every
    other at its next context.
    """
    runtime = _runtime(request)
    registry = runtime.registry
    if registry is None:
        raise _error(500, "internal", "This instance cannot add agents.")
    name, url = body.name, body.url
    await _free(runtime, registry, name, destination(url))
    slots = runtime.card_reads
    busy = slots.refusal(None)
    if busy is not None:
        raise _error(409, "check_busy", busy)
    slots.take(None)
    try:
        # Known to the scrubber before it is first requested, whatever comes of the request.
        SCRUBBER.register(url)
        limit = min(runtime.settings.a2a_startup_timeout_seconds, CARD_READ_SECONDS)
        problem: CardProblem | None = None
        try:
            card = await registry.read_card(name, url, limit=limit)
        except CardError as error:
            problem = error.problem
        if problem is not None:
            logger.info("Agent %s was not added: %s", name, problem.message)
            raise _unusable(problem)
        record = await _save(runtime, body, card_details(card, SCRUBBER.scrub))
    finally:
        slots.release(None)
    # A saved agent's address is kept by the scrubber for as long as the process runs.
    SCRUBBER.mark_saved(record.url)
    agents: AgentSet | None = None
    try:
        # With the card just read, so that it is not read a second time.
        agents = await registry.adopt(record, card)
    except Exception as exc:
        # The add stands: this instance, like every other, takes it up at its next context.
        logger.warning(
            "Agent %s was added, and this instance could not take it up at once: %s",
            name,
            type(exc).__name__,
        )
    logger.info(
        "Agent added: %s (%s), approval %s",
        record.name,
        record.id,
        "none" if record.read_only else "required",
    )
    return _saved(record, agents, 0)


def _approval(read_only: bool) -> str:
    return "none" if read_only else "required"


async def _changeable(
    runtime: Runtime, registry: AgentRegistry, agent_id: str
) -> tuple[SavedAgentRecord, int]:
    """The saved agent a change is for and its pending tasks, or the refusal (FR-009, FR-017).

    An environment agent is never changed here, and a saved agent the environment overrides
    can only be removed. Read from the store, not from a set this instance holds: what is
    refused or allowed is what is saved now.
    """
    if agent_id.startswith("env."):
        if agent_id.removeprefix("env.") in runtime.settings.a2a_agents:
            raise _error(409, "agent_read_only", ENVIRONMENT_READ_ONLY)
        raise _error(404, "agent_not_found", NOT_FOUND)
    try:
        record = await runtime.saved_agents.get_agent(agent_id)
        counts = await runtime.agent_tasks.pending_counts()
    except Exception as exc:
        logger.warning("Agent %s was not changed: %s", agent_id, type(exc).__name__)
        raise _error(500, "internal", UNREADABLE) from None
    if record is None:
        raise _error(404, "agent_not_found", NOT_FOUND)
    if registry.overridden(record.name, record.destination) is not None:
        raise _error(409, "agent_read_only", ONLY_REMOVED)
    return record, counts.get((record.name, record.ref), 0)


@router.patch("/{agent_id}", response_model=AgentChanged)
async def change_agent(agent_id: AgentIdPath, body: AgentUpdate, request: Request) -> AgentChanged:
    """Change a saved agent's approval choice (FR-009, FR-012).

    No card is read and no agent is contacted, so the change is not refused because of the
    agent's state, and its pending tasks stay as they are (FR-026). Requiring approval holds
    at once, also for runs in progress, because every tool of a saved agent reads the agent
    before it sends (FR-022); running without approval holds from the next run. A request
    that names the choice the agent has changes nothing and answers with the agent. This
    instance takes the change up at once, and every other at its next context.
    """
    runtime = _runtime(request)
    registry = runtime.registry
    if registry is None:
        raise _error(500, "internal", "This instance cannot change agents.")
    record, pending = await _changeable(runtime, registry, agent_id)
    refused: HTTPException | None = None
    changed = record
    try:
        changed, _given_up = await runtime.saved_agents.change(
            agent_id,
            body.version,
            read_only=None if body.approval is None else body.approval == "none",
        )
    except AgentNotFoundError:
        refused = _error(404, "agent_not_found", NOT_FOUND)
    except AgentChangedError:
        refused = _error(409, "agent_changed", CHANGED_SINCE)
    except Exception as exc:
        # What a database says may name its own address.
        logger.warning("Agent %s was not changed: %s", record.name, type(exc).__name__)
        refused = _error(500, "internal", "The agent could not be changed.")
    if refused is not None:
        # Raised here, not in a handler, so that no error of the store is kept behind it.
        raise refused
    agents: AgentSet | None = None
    try:
        agents = await registry.refresh()
    except Exception as exc:
        # The change stands: this instance, like every other, takes it up at its next context.
        logger.warning(
            "Agent %s was changed, and this instance could not take it up at once: %s",
            changed.name,
            type(exc).__name__,
        )
    if changed.version != body.version:
        logger.info(
            "Agent changed: %s (%s): its approval choice is now %s, was %s",
            changed.name,
            changed.id,
            _approval(changed.read_only),
            _approval(record.read_only),
        )
    # A change of the approval choice alone gives up no task (FR-026).
    return AgentChanged(agent=_saved(changed, agents, pending), given_up=0)


@dataclass(frozen=True)
class _InUse:
    """An agent in use as a check needs it: its link, and its record when it is a saved one."""

    link: AgentLink
    record: SavedAgentRecord | None


def _in_use(context: RunContext, agent_id: str) -> _InUse:
    """The agent ``agent_id`` names in this context's set, or the refusal (FR-013).

    ``env.<name>`` names an environment agent, and anything else a saved agent's id. Only an
    agent in use can be checked: a saved agent the environment overrides has no link.
    """
    agents = context.agents
    if agents is None:
        raise _error(404, "agent_not_found", NOT_FOUND)
    if agent_id.startswith("env."):
        link = agents.link(agent_id.removeprefix("env."))
        if link is None or link.source != "environment":
            raise _error(404, "agent_not_found", NOT_FOUND)
        return _InUse(link, None)
    if any(unused.record.id == agent_id for unused in agents.not_in_use):
        raise _error(409, "agent_read_only", NOT_IN_USE)
    record = next((found for found in agents.records.values() if found.id == agent_id), None)
    link = agents.link(record.name) if record is not None else None
    if record is None or link is None:
        raise _error(404, "agent_not_found", NOT_FOUND)
    return _InUse(link, record)


def _differs(before: AgentCardSummary | None, read: AgentCardSummary | None) -> bool:
    """Whether the details a check read are other than those kept or shown before it.

    Both as they read now: details are cleaned of the addresses known when they were read,
    so the ones from before are cleaned again first. An address that became an agent's
    since is then gone from both, and its going is not a change of the card (FR-013).
    Details read where none were kept or shown replace none.
    """
    if before is None or read is None:
        return False
    return cleaned(before, SCRUBBER.scrub) != read


async def _still_there(runtime: Runtime, record: SavedAgentRecord) -> bool:
    """Whether the saved agent is still the one that was checked: not moved, not removed."""
    now = await runtime.saved_agents.get_agent(record.id)
    return now is not None and now.ref == record.ref


@router.post("/{agent_id}/check", response_model=AgentCheck)
async def check_agent(agent_id: AgentIdPath, request: Request) -> AgentCheck:
    """Read an agent's card again and say whether the agent can be used (FR-013).

    For a saved agent and for an environment agent, and for nothing but an agent in use. The
    card is read through the agent's link, so a read that succeeds connects the link and one
    that fails leaves it not connected: hand-offs then fail with the reason until a later
    read succeeds. A saved agent's kept details are replaced when the card can be used and
    differs, which every instance takes up at its next context; an environment agent's are
    this instance's own. Nothing else changes: no address, no approval choice, no thread,
    and no task.
    """
    runtime = _runtime(request)
    try:
        context = await runtime.begin_run()
    except Exception as exc:
        logger.warning("Agent %s was not checked: %s", agent_id, type(exc).__name__)
        raise _error(500, "internal", UNREADABLE) from None
    slots = runtime.card_reads
    taken = False
    try:
        # The context is held for as long as the link is used: its set keeps the link open.
        agent = _in_use(context, agent_id)
        link, record = agent.link, agent.record
        busy = slots.refusal(agent_id)
        if busy is not None:
            raise _error(409, "check_busy", busy)
        slots.take(agent_id)
        taken = True
        limit = min(runtime.settings.a2a_startup_timeout_seconds, CARD_READ_SECONDS)
        shown = link.card
        problem: CardProblem | None = None
        try:
            await link.connect(limit=limit)
        except CardError as error:
            problem = error.problem
        checked_at = link.checked_at if link.tried else datetime.now(UTC)
        if problem is not None:
            # The warning of startup, since a card read for the agent failed (FR-030).
            logger.warning("Agent %s cannot be used: %s", link.name, problem.message)
        recorded, card_changed = True, False
        if record is None:
            # An environment agent's details are the ones this instance last read; the link
            # took them, and had the tools built again when they differ. The first details
            # an agent ever showed replace none.
            card_changed = problem is None and _differs(shown, link.card)
        else:
            failure: HTTPException | None = None
            try:
                kept_details = AgentCardSummary.from_dict(record.card)
                if problem is None and _differs(kept_details, link.last_read):
                    kept = await runtime.saved_agents.replace_card(
                        record.id, record.ref, link.last_read.to_dict()
                    )
                    recorded, card_changed = kept.recorded, kept.changed
                else:
                    recorded = await _still_there(runtime, record)
            except Exception as exc:
                logger.warning("Agent %s was checked: %s", link.name, type(exc).__name__)
                failure = _error(500, "internal", "The check's result could not be recorded.")
            if failure is not None:
                raise failure
    finally:
        if taken:
            slots.release(agent_id)
        await context.release()
    logger.info(
        "Agent checked: %s (%s): %s; %s",
        link.name,
        agent_id,
        # The sentence without its full stop: another clause follows it in this line.
        (
            "it can be used"
            if problem is None
            else f"it cannot be used: {problem.message.rstrip('.')}"
        ),
        (
            f"its card details were {'replaced' if card_changed else 'not replaced'}"
            if recorded
            else "the agent was moved or removed meanwhile, so nothing was recorded"
        ),
    )
    return AgentCheck(
        usable=problem is None,
        reason=problem.code if problem is not None else None,
        message=problem.message if problem is not None else None,
        checked_at=checked_at,
        recorded=recorded,
        card_changed=card_changed,
    )
