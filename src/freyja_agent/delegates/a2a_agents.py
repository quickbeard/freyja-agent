"""Links to the A2A agents named in ``FREYJA_A2A_AGENTS`` (feature 003, research R3, R14).

``open_agents`` is entered once, by the runtime, after the tool servers and before the
orchestrator is built. It reads every agent's card, checks it, and fails naming the agent when
anything is wrong, so the service never serves requests with an agent it cannot use (FR-003).

Each link owns one ``httpx.AsyncClient`` for the life of the process. It follows no redirect,
and every request goes to the configured scheme, host, and port: the card says where the agent
can be reached, and Freyja keeps only the path of its JSON-RPC interface (FR-002). Text about an
agent that is logged, raised, or stored as its status passes through ``describe``, which removes
the configured address (FR-009).
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Collection, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import httpx
from a2a.client import A2ACardResolver, Client, ClientConfig, ClientFactory
from a2a.types import (
    AgentCard,
    GetTaskRequest,
    Message,
    SendMessageConfiguration,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
)
from a2a.utils.constants import PROTOCOL_VERSION_CURRENT, VERSION_HEADER

from freyja_agent.config import A2aAgentConfig

logger = logging.getLogger(__name__)

ERROR_TEXT_LIMIT = 500
# Stands in for an agent's address in any text that may be shown or logged (FR-009).
ADDRESS = "<address>"
JSONRPC = "JSONRPC"

# Returns the httpx transport a link uses for one agent: the network in production, the
# streaming in-process transport in the suite (research R15, R16).
AgentTransport = Callable[[str, str], httpx.AsyncBaseTransport]


def default_transport(_name: str, _url: str) -> httpx.AsyncBaseTransport:
    return httpx.AsyncHTTPTransport()


class AgentStartupError(RuntimeError):
    """An agent that cannot be used at startup. The service does not start (FR-003)."""


def handoff_tool_name(agent: str) -> str:
    return f"handoff_to_{agent}"


def cancel_tool_name(agent: str) -> str:
    return f"cancel_{agent}_task"


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


def card_problem(card: AgentCard) -> str | None:
    """Why a card cannot be used, or None (FR-003, research R3)."""
    if card.security_requirements or card.security_schemes:
        return "its card requires authentication, which is out of scope"
    modes = list(card.default_input_modes)
    if modes and not any(mode.startswith("text/") for mode in modes):
        return "its card does not accept text messages"
    if not any(interface.protocol_binding == JSONRPC for interface in card.supported_interfaces):
        return "its card offers no JSON-RPC interface"
    return None


def pinned_card(card: AgentCard, configured_url: str) -> AgentCard:
    """A copy of the card whose interface URLs point at the configured scheme, host, and port.

    Only the path, and query, of each advertised URL is kept, so no request can follow the
    card to another host (FR-002). The SDK still chooses the interface and protocol version.
    """
    configured = urlsplit(configured_url)
    pinned = AgentCard()
    pinned.CopyFrom(card)
    for interface in pinned.supported_interfaces:
        advertised = urlsplit(interface.url)
        interface.url = urlunsplit(
            (configured.scheme, configured.netloc, advertised.path or "/", advertised.query, "")
        )
    return pinned


class AgentLink:
    """The running link to one configured agent (data-model.md "Delegate agent")."""

    def __init__(
        self,
        name: str,
        config: A2aAgentConfig,
        *,
        transport: AgentTransport,
        call_timeout: float,
    ) -> None:
        self.name = name
        self.config = config
        self.card: AgentCardSummary | None = None
        self.streaming = False
        # Outcome of the most recent attempt to use the agent (FR-024).
        self.reachable = False
        self.checked_at = datetime.now(UTC)
        self.error: str | None = None
        self._transport = transport
        self.call_timeout = call_timeout
        self._http: httpx.AsyncClient | None = None
        self.client: Client | None = None

    @property
    def read_only(self) -> bool:
        return self.config.read_only

    def describe(self, text: str) -> str:
        """Text about this agent that is safe to log, to raise, and to store (FR-009)."""
        for address in self._addresses:
            text = text.replace(address, ADDRESS)
        return text[:ERROR_TEXT_LIMIT]

    @property
    def _addresses(self) -> tuple[str, ...]:
        """The forms in which the configured address can appear, longest first."""
        parts = urlsplit(self.config.url)
        host_and_port = parts.netloc.rpartition("@")[2]
        forms = {
            self.config.url,
            self.config.url.rstrip("/"),
            f"{parts.scheme}://{parts.netloc}",
            parts.netloc,
            host_and_port,
        }
        return tuple(sorted((form for form in forms if form), key=len, reverse=True))

    def mark(self, reachable: bool, error: str | None = None) -> None:
        """Record the outcome of the most recent attempt to use the agent."""
        self.reachable = reachable
        self.checked_at = datetime.now(UTC)
        self.error = None if reachable else self.describe(error or "")

    async def start(self, startup_timeout: float) -> None:
        """Read and check the card and build the client. Closes the link again on failure."""
        self._http = httpx.AsyncClient(
            transport=self._transport(self.name, self.config.url),
            follow_redirects=False,
            timeout=self.call_timeout,
            headers={VERSION_HEADER: PROTOCOL_VERSION_CURRENT},
        )
        try:
            async with asyncio.timeout(startup_timeout):
                card = await A2ACardResolver(
                    self._http, self.config.url.rstrip("/")
                ).get_agent_card()
            problem = card_problem(card)
            if problem is not None:
                raise AgentStartupError(f"Agent '{self.name}': {problem}.")
            config = ClientConfig(httpx_client=self._http, streaming=card.capabilities.streaming)
            self.client = ClientFactory(config).create(pinned_card(card, self.config.url))
        except BaseException:
            await self.close()
            raise
        self.card = AgentCardSummary.from_card(card)
        self.streaming = card.capabilities.streaming
        self.mark(True)

    async def close(self) -> None:
        client, self.client = self.client, None
        http, self._http = self._http, None
        if client is not None:
            await client.close()
        if http is not None:
            await http.aclose()


def _start_failure(link: AgentLink, exc: BaseException, startup_timeout: float) -> str:
    if isinstance(exc, AgentStartupError):
        return str(exc)
    if isinstance(exc, TimeoutError):
        why = f"its card was not read within {startup_timeout:g} seconds"
    else:
        why = f"its card could not be read: {link.describe(str(exc) or type(exc).__name__)}"
    return f"Agent '{link.name}': {why}."


async def _close_all(links: Sequence[AgentLink]) -> None:
    await asyncio.gather(*(link.close() for link in links), return_exceptions=True)


@asynccontextmanager
async def open_agents(
    configs: Mapping[str, A2aAgentConfig],
    *,
    transport: AgentTransport = default_transport,
    startup_timeout: float,
    call_timeout: float,
    server_names: Collection[str] = (),
    taken_tool_names: Collection[str] = (),
) -> AsyncIterator[tuple[AgentLink, ...]]:
    """Read every configured agent's card and yield the links; close them on exit.

    Raises ``AgentStartupError`` naming every agent that cannot be used. An agent named like a
    tool server is refused before any card is read, because a proposal must name one origin,
    and so is an agent whose tools would share a name with ``taken_tool_names``: Freyja's own
    tools and the tool servers'. Nothing is left open when it raises.
    """
    problems = [
        f"Agent '{name}': the name is also a tool server's name."
        for name in configs
        if name in server_names
    ]
    problems += [
        f"Agent '{name}': the tool name '{tool}' is already used by another tool."
        for name in configs
        for tool in (handoff_tool_name(name), cancel_tool_name(name))
        if tool in taken_tool_names
    ]
    if problems:
        raise AgentStartupError("; ".join(problems))
    links = [
        AgentLink(name, config, transport=transport, call_timeout=call_timeout)
        for name, config in configs.items()
    ]
    outcomes = await asyncio.gather(
        *(link.start(startup_timeout) for link in links), return_exceptions=True
    )
    failures = [
        (link, outcome)
        for link, outcome in zip(links, outcomes, strict=True)
        if isinstance(outcome, BaseException)
    ]
    if failures:
        await _close_all(links)
        raise AgentStartupError(
            "; ".join(_start_failure(link, exc, startup_timeout) for link, exc in failures)
        )
    try:
        for link in links:
            skills = len(link.card.skills) if link.card is not None else 0
            logger.info(
                "Agent %s: %s, %d skill%s",
                link.name,
                "read-only" if link.read_only else "gated",
                skills,
                "" if skills == 1 else "s",
            )
        yield tuple(links)
    finally:
        await _close_all(links)


# How often a task of an agent that does not stream is checked while a call waits (research R5).
POLL_SECONDS = 1.0

# The protocol's task states as the store and the API name them (data-model.md).
_STATES = {
    TaskState.TASK_STATE_SUBMITTED: "working",
    TaskState.TASK_STATE_WORKING: "working",
    TaskState.TASK_STATE_COMPLETED: "completed",
    TaskState.TASK_STATE_FAILED: "failed",
    TaskState.TASK_STATE_CANCELED: "canceled",
    TaskState.TASK_STATE_REJECTED: "rejected",
    TaskState.TASK_STATE_INPUT_REQUIRED: "input_required",
    TaskState.TASK_STATE_AUTH_REQUIRED: "auth_required",
}
# A call stops waiting at any of these: the task is final, or waits for someone else.
_STOP = {"completed", "failed", "canceled", "rejected", "input_required", "auth_required"}

# Receives (task id, state, text) for each progress update the agent reports.
OnProgress = Callable[[str, str, str], None]


def state_name(state: int) -> str:
    return _STATES.get(state, "working")


@dataclass
class SendOutcome:
    """What one send produced, within the call time limit (research R5).

    ``task`` is the latest view of the task the agent created, if any; ``reply`` is a direct
    message reply. ``acknowledged`` is true once either arrived. ``timed_out`` means the limit
    passed first; ``error`` says why the send failed, in text safe to show.
    """

    task: Task | None = None
    reply: Message | None = None
    timed_out: bool = False
    error: str | None = None

    @property
    def acknowledged(self) -> bool:
        return self.task is not None or self.reply is not None

    @property
    def state(self) -> str | None:
        if self.reply is not None:
            return "completed"
        return state_name(self.task.status.state) if self.task is not None else None

    @property
    def context_id(self) -> str | None:
        source = self.task if self.task is not None else self.reply
        return (source.context_id or None) if source is not None else None


def _text(message: Message | None) -> str:
    if message is None:
        return ""
    return "\n".join(part.text for part in message.parts if part.WhichOneof("content") == "text")


async def _send(
    link: "AgentLink", message: Message, on_progress: OnProgress, outcome: SendOutcome
) -> None:
    """Send one message and follow it until it stops, updating ``outcome`` as events arrive."""
    if link.client is None:
        raise RuntimeError(f"Agent '{link.name}' is not started.")
    configuration = None if link.streaming else SendMessageConfiguration(return_immediately=True)
    request = SendMessageRequest(message=message, configuration=configuration)
    async for event in link.client.send_message(request):
        kind = event.WhichOneof("payload")
        if kind == "message":
            outcome.reply = event.message
            return
        if kind == "task":
            outcome.task = Task()
            outcome.task.CopyFrom(event.task)
            if event.task.status.HasField("message"):
                on_progress(
                    event.task.id,
                    state_name(event.task.status.state),
                    _text(event.task.status.message),
                )
        elif kind == "status_update":
            _acknowledged(outcome, event.status_update)
            outcome.task.status.CopyFrom(event.status_update.status)
            update = event.status_update.status
            on_progress(outcome.task.id, state_name(update.state), _text(update.message))
        elif kind == "artifact_update":
            _acknowledged(outcome, event.artifact_update)
            outcome.task.artifacts.append(event.artifact_update.artifact)
        if outcome.state in _STOP:
            return
    if not link.streaming and outcome.task is not None:
        await _poll(link, outcome, on_progress)


def _acknowledged(
    outcome: SendOutcome, update: TaskStatusUpdateEvent | TaskArtifactUpdateEvent
) -> None:
    """Start the task from an update when the agent sent no task first.

    A message that continues a task, such as an answer, gets no new task: the agent streams
    updates for the task it already has, each naming that task and its conversation. The first
    of them is the acknowledgment (feature 003, FR-016).
    """
    if outcome.task is None:
        outcome.task = Task(id=update.task_id, context_id=update.context_id)


async def _poll(link: "AgentLink", outcome: SendOutcome, on_progress: OnProgress) -> None:
    """Check a task of an agent that does not stream until it stops; report each change."""
    if link.client is None or outcome.task is None:
        raise RuntimeError(f"Agent '{link.name}' has no task to check.")
    # The protocol's own states, so submitted to working counts as a change.
    seen = outcome.task.status.state
    while outcome.state not in _STOP:
        await asyncio.sleep(POLL_SECONDS)
        task = await link.client.get_task(GetTaskRequest(id=outcome.task.id))
        outcome.task = task
        if task.status.state != seen:
            seen = task.status.state
            on_progress(task.id, state_name(seen), _text(task.status.message))


async def send_message(link: "AgentLink", message: Message, on_progress: OnProgress) -> SendOutcome:
    """Send ``message`` to the agent and wait for it within the call time limit (FR-009)."""
    outcome = SendOutcome()
    try:
        async with asyncio.timeout(link.call_timeout):
            await _send(link, message, on_progress, outcome)
    except TimeoutError:
        outcome.timed_out = True
        if not outcome.acknowledged:
            link.mark(False, f"it did not answer within {link.call_timeout:g} seconds")
            return outcome
    except Exception as exc:  # every failure becomes an outcome; none ends a run
        outcome.error = link.describe(str(exc) or type(exc).__name__)
        link.mark(False, outcome.error)
        return outcome
    link.mark(True)
    return outcome
