"""The routes of the Agents section (feature 005, contracts/openapi.yaml, research R16).

The agents an operator saved are operator-managed configuration: kept in the service's
database and changed only through these routes (constitution principle IV). An agent's
address is in the body of an answer of these routes and in no other answer of the service;
an environment agent's address is in none. The router refuses a request that changes
something when a page of another origin made it, by the rule the LLM routes apply.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from freyja_agent.api.guards import NoInputRoute, refuse_other_origins
from freyja_agent.api.schemas import Agent, AgentCard, AgentList, AgentSkillClaim
from freyja_agent.delegates.a2a_agents import AgentCardSummary, AgentLink
from freyja_agent.runtime.runtime import RunContext, Runtime
from freyja_agent.runtime.saved_agents import AGENT_LIMIT, SavedAgentRecord

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/agents",
    tags=["agents"],
    route_class=NoInputRoute,
    dependencies=[Depends(refuse_other_origins("Agents"))],
)

# The states a warning is about: the agent cannot be handed work now (FR-001a).
UNUSABLE = ("unreachable", "not_in_use")


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def _claims(card: AgentCardSummary | None) -> AgentCard | None:
    """Card details as the list returns them; None for a card this instance has not read."""
    if card is None:
        return None
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


def _saved(record: SavedAgentRecord, context: RunContext, pending: int) -> Agent:
    """A saved agent as the store has it, with this instance's view of whether it answers."""
    agents = context.agents
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
        raise _error(500, "internal", "The saved agents could not be read.") from None
    try:
        try:
            records = await runtime.saved_agents.list_agents()
            counts = await runtime.agent_tasks.pending_counts()
        except Exception as exc:
            logger.warning("The agents could not be listed: %s", type(exc).__name__)
            raise _error(500, "internal", "The saved agents could not be read.") from None
        listed = [
            _from_the_environment(link, counts.get((link.name, None), 0))
            for link in context.links
            if link.source == "environment"
        ]
        listed += [
            _saved(record, context, counts.get((record.name, record.ref), 0))
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
