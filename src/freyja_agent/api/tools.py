"""The tool catalog endpoint (feature 002 contracts/openapi.yaml, research R10)."""

from fastapi import APIRouter, Request

from freyja_agent.api.schemas import CatalogTool, ServerHints, ToolCatalog, ToolServerStatus
from freyja_agent.runtime.runtime import Runtime

router = APIRouter(tags=["tools"])


# exclude_unset: a hint the server did not set is left out, while a field set to null, such
# as `server` for one of Freyja's own tools, is kept.
@router.get("/tools", response_model=ToolCatalog, response_model_exclude_unset=True)
async def read_tools(request: Request) -> ToolCatalog:
    """List every tool the orchestrator can call, and the status of each tool server.

    The tools are the ones the factory compiled in at startup, so the list cannot differ from
    what the orchestrator was given. A server's status is the outcome of the most recent
    startup connection or call. Everything is read from memory: no server is contacted, and
    nothing about how a server is configured is shown beyond which form of connection it uses
    (FR-014).
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
        tools=[
            CatalogTool(
                name=tool.name,
                server=tool.server,
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
