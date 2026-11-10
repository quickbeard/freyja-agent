"""The tool catalog endpoint (feature 002 contracts/openapi.yaml, research R10; feature 003
research R13)."""

from fastapi import APIRouter, Request

from freyja_agent.api.schemas import (
    AgentCardClaims,
    AgentSkillClaim,
    AgentStatus,
    CatalogTool,
    ServerHints,
    ToolCatalog,
    ToolServerStatus,
)
from freyja_agent.delegates.a2a_agents import AgentCardSummary, AgentLink
from freyja_agent.runtime.runtime import Runtime

router = APIRouter(tags=["tools"])


# exclude_unset: a hint the server did not set is left out, while a field set to null, such
# as `server` for one of Freyja's own tools, is kept.
@router.get("/tools", response_model=ToolCatalog, response_model_exclude_unset=True)
async def read_tools(request: Request) -> ToolCatalog:
    """List every tool the orchestrator can call, and the status of each tool server and agent.

    The tools are the ones the factory compiled in at startup, so the list cannot differ from
    what the orchestrator was given. A server's status is the outcome of the most recent
    startup connection or call. Everything is read from memory: no server is contacted, and
    nothing about how a server is configured is shown beyond which form of connection it uses
    (FR-014).

    An agent's status is the outcome of the most recent attempt to use it, at startup, in a
    hand-off or cancel, or while watching a task; its card's claims are shown apart from its
    classification, and never its address. No agent is contacted (feature 003, FR-024).
    """
    runtime: Runtime = request.app.state.runtime
    return ToolCatalog(
        servers=[
            ToolServerStatus(
                name=server.name,
                connection=server.connection,
                reachable=server.reachable,
                checked_at=server.checked_at,
                error=server.error,
                tool_count=len(server.tools),
            )
            for server in runtime.servers
        ],
        agents=[_agent_status(link) for link in runtime.agents],
        tools=[
            CatalogTool(
                name=tool.name,
                server=tool.server,
                agent=tool.agent,
                description=tool.description,
                input_schema=tool.input_schema,
                classification=tool.classification.value,
                allowed_decisions=list(tool.allowed_decisions),
                server_hints=(
                    ServerHints(**tool.server_hints) if tool.server_hints is not None else None
                ),
            )
            for tool in runtime.catalog
        ],
    )


def _agent_status(link: AgentLink) -> AgentStatus:
    return AgentStatus(
        name=link.name,
        reachable=link.reachable,
        checked_at=link.checked_at,
        # Already stripped of the address and cut to 500 characters by ``AgentLink.mark``.
        error=None if link.reachable else link.error,
        card=_claims(link.card),
    )


def _claims(card: AgentCardSummary | None) -> AgentCardClaims:
    # Every link in the runtime started with its card read; the empty claim is never served.
    if card is None:
        return AgentCardClaims(name="", description="", version="", skills=[])
    return AgentCardClaims(
        name=card.name,
        description=card.description,
        version=card.version,
        skills=[
            AgentSkillClaim(
                id=skill.id, name=skill.name, description=skill.description, tags=list(skill.tags)
            )
            for skill in card.skills
        ],
    )
