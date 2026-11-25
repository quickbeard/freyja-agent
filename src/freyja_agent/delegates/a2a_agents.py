"""Links to the A2A agents Freyja may hand work to (feature 003, research R3, R14; feature 005,
research R6, R12, R13).

A link is the running connection to one agent in use on one instance: an agent named in
``FREYJA_A2A_AGENTS``, or one an operator saved. Making a link contacts nothing. ``connect``
reads the agent's card and builds the client from it; until that has succeeded the link is
not connected and nothing is sent over it (005 FR-021). The registry connects every link at
startup, and a hand-off or a round of watching connects one that is not connected yet, so an
agent that was down is used again without a restart.

Each link owns one ``httpx.AsyncClient`` for its whole life. It has an explicit transport, so
no proxy setting of the environment applies to it, it follows no redirect, and every request
goes to the configured scheme, host, and port: the card says where the agent can be reached,
and Freyja keeps only the path of its JSON-RPC interface (003 FR-002, 005 FR-027). A link
belongs to one address; an agent that is moved gets a new link with a new client. Text about
an agent that is logged, raised, or stored as its status passes through ``describe``, which
removes every agent address the process knows (005 FR-028).
"""

import asyncio
import contextlib
import logging
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from a2a.client import Client, ClientConfig, ClientFactory
from a2a.client.errors import A2AClientTimeoutError
from a2a.types import (
    AgentCard,
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    SendMessageConfiguration,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.constants import PROTOCOL_VERSION_CURRENT, VERSION_HEADER

from freyja_agent.config import A2aAgentConfig
from freyja_agent.delegates.addresses import ADDRESS as ADDRESS
from freyja_agent.delegates.addresses import SCRUBBER

# The card classes and checks live in ``agent_cards`` since feature 005 and are imported back
# so that everything that took them from here still finds them.
from freyja_agent.delegates.agent_cards import JSONRPC as JSONRPC
from freyja_agent.delegates.agent_cards import AgentCardSummary as AgentCardSummary
from freyja_agent.delegates.agent_cards import (
    CardError,
    CardProblem,
    card_details,
    read_card,
)
from freyja_agent.delegates.agent_cards import SkillSummary as SkillSummary
from freyja_agent.delegates.agent_cards import card_problem as card_problem

logger = logging.getLogger(__name__)

ERROR_TEXT_LIMIT = 500
# Where an agent comes from: named in the environment, or saved by an operator (005 FR-017).
Source = Literal["environment", "saved"]

# Returns the httpx transport a link uses for one agent: the network in production, the
# streaming in-process transport in the suite (research R15, R16).
AgentTransport = Callable[[str, str], httpx.AsyncBaseTransport]


def default_transport(_name: str, _url: str) -> httpx.AsyncBaseTransport:
    return httpx.AsyncHTTPTransport()


class AgentStartupError(RuntimeError):
    """An agent's entry that is invalid in itself. The service does not start (005 FR-018)."""


class NotConnectedError(RuntimeError):
    """A request for a link that holds no usable card: nothing was sent (005 FR-021)."""


def handoff_tool_name(agent: str) -> str:
    return f"handoff_to_{agent}"


def cancel_tool_name(agent: str) -> str:
    return f"cancel_{agent}_task"


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


def agent_http_client(
    name: str, url: str, *, transport: AgentTransport, timeout: float
) -> httpx.AsyncClient:
    """The client every request to one agent's address is made with (005 FR-027).

    It has an explicit transport, so no proxy of the environment applies to it, and it
    follows no redirect (research R13). A link has one for its whole life; a card read for
    an address that has no link yet gets one of its own.
    """
    return httpx.AsyncClient(
        transport=transport(name, url),
        follow_redirects=False,
        timeout=timeout,
        headers={VERSION_HEADER: PROTOCOL_VERSION_CURRENT},
    )


async def read_card_at(
    name: str, url: str, *, transport: AgentTransport, limit: float
) -> AgentCard:
    """Read the card at an address no link exists for, with a client made for the read.

    One ``GET``, within ``limit`` seconds, and nothing else: no message and no task (005
    FR-027). Raises ``CardError``. The caller has told the scrubber the address already.
    """
    http = agent_http_client(name, url, transport=transport, timeout=limit)
    try:
        return await read_card(http, url, limit=limit)
    finally:
        await http.aclose()


class AgentLink:
    """The running link to one agent in use on one instance (005 data-model.md "Agent link").

    ``source`` says where the agent comes from. A saved agent's link carries its ``agent_id``
    and ``address_version``, which make its ``ref``, and the card details kept for it; an
    environment agent's has no ref, and its ``card`` is what this instance last read.

    A link does not carry the approval choice of a saved agent: the agent set does, and a
    saved agent's link is made with ``read_only`` false, so a caller that forgets the set
    gates (005 research R7).
    """

    def __init__(
        self,
        name: str,
        config: A2aAgentConfig,
        *,
        transport: AgentTransport,
        call_timeout: float,
        source: Source = "environment",
        agent_id: str | None = None,
        address_version: int | None = None,
        card: AgentCardSummary | None = None,
    ) -> None:
        self.name = name
        self.config = config
        self.source: Source = source
        self.agent_id = agent_id
        self.address_version = address_version
        # The card details the tools are described from.
        self.card = card
        # The details of the card this link last read, whoever asked for the reading. For a
        # saved agent they are not the ones shown: those are the kept ones, which only an
        # operator's move or check replaces (005 FR-015). A check takes them from here.
        self.last_read: AgentCardSummary | None = None
        self.streaming = False
        # True while this instance holds a usable card from the address and a client built
        # from it. Nothing is sent over a link that is not connected.
        self.connected = False
        # False until this instance has tried the agent.
        self.tried = False
        # Outcome of the most recent attempt to use the agent (003 FR-024).
        self.reachable = False
        self.checked_at = datetime.now(UTC)
        self.error: str | None = None
        self.call_timeout = call_timeout
        self.client: Client | None = None
        # Told when the card details of an environment agent change, so that the tools built
        # without them, or from earlier ones, are built again (005 FR-021). Set by the
        # registry.
        self.on_card_changed: Callable[[AgentLink], None] | None = None
        # The address is known to the scrubber before anything requests it (005 research R12).
        SCRUBBER.mark_saved(config.url)
        # An explicit transport: no proxy of the environment applies. No redirect is followed
        # (005 FR-027, research R13).
        self._http = agent_http_client(name, config.url, transport=transport, timeout=call_timeout)
        # One caller reads the card while the others wait for its result.
        self._connecting = asyncio.Lock()
        self._readings = 0
        self._problem: CardProblem | None = None

    @property
    def read_only(self) -> bool:
        return self.config.read_only

    @property
    def ref(self) -> str | None:
        """Which agent, at which address: ``<id>:<address_version>``; None from the environment."""
        if self.source != "saved":
            return None
        return f"{self.agent_id}:{self.address_version}"

    def describe(self, text: str) -> str:
        """Text about this agent that is safe to log, to raise, and to store (005 FR-028)."""
        return SCRUBBER.scrub(text)[:ERROR_TEXT_LIMIT]

    def mark(self, reachable: bool, error: str | None = None) -> None:
        """Record the outcome of the most recent attempt to use the agent.

        While the link holds no usable card, what is known of the agent is what its last
        card read found, and nothing is sent over the link. A call that was under way when
        the card stopped being usable can still end, well or badly: its ending says nothing
        of whether the agent can be used now, and is not recorded (005 FR-021).
        """
        if not self.connected:
            return
        self._note(reachable, error)

    def _note(self, reachable: bool, error: str | None = None) -> None:
        self.tried = True
        self.reachable = reachable
        self.checked_at = datetime.now(UTC)
        self.error = None if reachable else self.describe(error or "")

    async def connect(self, card: AgentCard | None = None, *, limit: float) -> None:
        """Hold a usable card from the agent's address and a client built from it.

        The card is read unless one is handed over, which a caller does that has just read
        it. One caller reads at a time; one that waited takes the result of the reading it
        waited for and requests nothing itself. A reading that fails leaves the link not
        connected, with the card problem's sentence as its error, and raises ``CardError``.
        """
        seen = self._readings
        # One limit for the wait and the read together: a caller that waited for another's
        # reading, which then ended with no result, reads in what is left of its own time.
        clock = asyncio.get_running_loop().time
        deadline = clock() + limit
        waited_too_long = False
        try:
            async with asyncio.timeout(limit):
                await self._connecting.acquire()
        except TimeoutError:
            waited_too_long = True
        if waited_too_long:
            # Another caller's reading has gone on for longer than this one may wait. Only a
            # link that is not connected is read by two callers at once, and what this one
            # found is the most recent thing known of it: no answer in its time.
            problem = CardProblem.of("timeout", seconds=limit)
            if not self.connected:
                self._note(False, problem.message)
            raise CardError.of(problem)
        try:
            if card is None and self._readings != seen:
                # Another caller read while this one waited.
                if self._problem is not None:
                    raise CardError.of(self._problem)
                return
            failed: CardProblem | None = None
            try:
                if card is None:
                    left = max(deadline - clock(), 0.0)
                    card = await read_card(self._http, self.config.url, limit=left)
                config = ClientConfig(
                    httpx_client=self._http, streaming=card.capabilities.streaming
                )
                client = ClientFactory(config).create(pinned_card(card, self.config.url))
            except CardError as error:
                failed = error.problem
            except Exception:
                # A card the SDK cannot build a client from; its error may name the address.
                failed = CardProblem.of("unreadable")
            if failed is not None:
                if failed.code == "timeout":
                    # In the caller's limit, whatever part of it the read itself was given.
                    failed = CardProblem.of("timeout", seconds=limit)
                self._failed(failed)
                raise CardError.of(failed)
            self._readings += 1
            self._problem = None
            self.client = client
            self.streaming = card.capabilities.streaming
            self.connected = True
            self._note(True)
            details = card_details(card, SCRUBBER.scrub)
            self.last_read = details
            if self.source == "environment":
                # A saved agent's details are the kept ones, and change only by an operator's
                # move or check (005 FR-015).
                changed, self.card = details != self.card, details
                if changed and self.on_card_changed is not None:
                    self.on_card_changed(self)
        finally:
            self._connecting.release()

    def _failed(self, problem: CardProblem) -> None:
        self._readings += 1
        self._problem = problem
        self.client = None
        self.connected = False
        self._note(False, problem.message)

    async def get_task(self, task_id: str) -> Task:
        """Read a task's status within the call time limit; raises what the agent raised.

        Reading sends the agent no new work (003 FR-019).
        """
        if self.client is None:
            raise NotConnectedError(f"Agent '{self.name}' is not connected.")
        async with asyncio.timeout(self.call_timeout):
            return await self.client.get_task(GetTaskRequest(id=task_id))

    async def cancel(self, task_id: str, *, limit: float | None = None) -> Task:
        """Ask the agent to cancel a task within the time limit; raises what it raised."""
        if self.client is None:
            raise NotConnectedError(f"Agent '{self.name}' is not connected.")
        async with asyncio.timeout(self.call_timeout if limit is None else limit):
            return await self.client.cancel_task(CancelTaskRequest(id=task_id))

    async def close(self) -> None:
        """Close the link's client for good. Nothing is sent over it afterwards."""
        client, self.client = self.client, None
        self.connected = False
        if client is not None:
            await client.close()
        if not self._http.is_closed:
            await self._http.aclose()


def log_connected(link: AgentLink, *, read_only: bool) -> None:
    """The line that says an agent is in use, with its classification and its skills."""
    skills = len(link.card.skills) if link.card is not None else 0
    logger.info(
        "Agent %s: %s, %d skill%s",
        link.name,
        "read-only" if read_only else "gated",
        skills,
        "" if skills == 1 else "s",
    )


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
# The clock an acknowledgment is timed by: the shared store's, so every instance agrees.
Clock = Callable[[], Awaitable[datetime]]


def state_name(state: int) -> str:
    return _STATES.get(state, "working")


@dataclass
class SendOutcome:
    """What one send produced, within the call time limit (research R5).

    ``task`` is the latest view of the task the agent created, if any; ``reply`` is a direct
    message reply. ``acknowledged`` is true once either arrived. ``timed_out`` means the limit
    passed first; ``error`` says why the send failed, in text safe to show, and ``unreachable``
    whether the request got no answer at all, rather than an answer Freyja could not use.
    ``malformed`` says what was wrong with a reply that carried an unusable identifier; nothing
    from such a reply is kept (FR-009). ``acknowledged_at`` is when the acknowledgment arrived,
    read from the clock ``send_message`` was given: a task starts then, not when the call ends
    (FR-017, FR-021).

    ``progress`` is the last progress text the send received (``progress_text``), and
    ``progress_seen`` the event loop's clock when it first received that text. They live for
    one send, and are how the hand-off's last progress reaches the task's row
    (008 data-model.md "In memory, during a hand-off").
    """

    task: Task | None = None
    reply: Message | None = None
    timed_out: bool = False
    error: str | None = None
    unreachable: bool = False
    malformed: str | None = None
    acknowledged_at: datetime | None = None
    progress: str | None = None
    progress_seen: float | None = None

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


# The most of an agent's progress text that is kept (feature 008, FR-004).
PROGRESS_LIMIT = 2000


def progress_text(status: TaskStatus) -> str | None:
    """The progress text a status carries, as it is kept, or None (008 research R1).

    Only a status that says the task is working carries progress: an ending, a question, and
    a request to sign in are outcomes (008 FR-002). The text is the message's text parts
    joined by line breaks, as a progress update in a run's stream has it; a NUL character and
    a lone surrogate are dropped, since a text column takes neither, and the text is cut to
    ``PROGRESS_LIMIT`` characters. A text that is then empty or blank is none (FR-003).
    Nothing else is done to it: it is the agent's own words, and is not searched for an
    address (FR-004).
    """
    if state_name(status.state) != "working" or not status.HasField("message"):
        return None
    return kept_progress(_text(status.message))


def kept_progress(text: str) -> str | None:
    """``text`` as a progress text is kept: what a text column takes, cut, and not blank.

    A protocol message cannot hold a lone surrogate (its strings are UTF-8), so one reaches
    nothing here today; it is dropped all the same, as the spec says, should a message ever
    come from elsewhere.
    """
    text = text.replace("\x00", "")
    text = text.encode("utf-8", "surrogatepass").decode("utf-8", "ignore")[:PROGRESS_LIMIT]
    return text if text.strip() else None


def _note_progress(outcome: SendOutcome, status: TaskStatus) -> None:
    """Note the progress ``status`` carries, when it is a text the send has not yet received.

    A text equal to the one noted is not noted again, so ``progress_seen`` is when the send
    first received it (008 research R4).
    """
    text = progress_text(status)
    if text is not None and text != outcome.progress:
        outcome.progress = text
        outcome.progress_seen = asyncio.get_running_loop().time()


async def _note_acknowledgment(outcome: SendOutcome, clock: Clock | None) -> None:
    """Time the acknowledgment once, when it arrives.

    A clock that fails leaves it untimed, and the task then starts when it is recorded: the
    failure is the store's, and must not be reported as the agent's.
    """
    if clock is None or outcome.acknowledged_at is not None or not outcome.acknowledged:
        return
    with contextlib.suppress(Exception):  # the store's failure, not the agent's (see above)
        outcome.acknowledged_at = await clock()


async def _send(
    link: "AgentLink",
    message: Message,
    on_progress: OnProgress,
    outcome: SendOutcome,
    clock: Clock | None,
) -> None:
    """Send one message and follow it until it stops, updating ``outcome`` as events arrive.

    The call keeps the client, and the way of following a task, that the link had when it
    began. A card read meanwhile may leave the link with another client or with none; a
    call that is under way ends on the connection it was sent over (005 research R6).
    """
    client, streaming = link.client, link.streaming
    if client is None:
        raise NotConnectedError(f"Agent '{link.name}' is not connected.")
    configuration = None if streaming else SendMessageConfiguration(return_immediately=True)
    request = SendMessageRequest(message=message, configuration=configuration)
    async for event in client.send_message(request):
        kind = event.WhichOneof("payload")
        if kind == "message":
            outcome.reply = event.message
            await _note_acknowledgment(outcome, clock)
            return
        if kind == "task":
            outcome.task = Task()
            outcome.task.CopyFrom(event.task)
            _note_progress(outcome, event.task.status)
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
            _note_progress(outcome, update)
            on_progress(outcome.task.id, state_name(update.state), _text(update.message))
        elif kind == "artifact_update":
            _acknowledged(outcome, event.artifact_update)
            _merge_artifact(outcome.task, event.artifact_update)
        await _note_acknowledgment(outcome, clock)
        if outcome.state in _STOP:
            return
    if not streaming and outcome.task is not None:
        await _poll(client, outcome, on_progress)


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


def _merge_artifact(task: Task, update: TaskArtifactUpdateEvent) -> None:
    """Put a streamed artifact update into ``task`` the way the agent's own record holds it.

    An agent may send one artifact in several updates, all naming the same artifact id: the
    first replaces whatever the task held under that id, and each one marked ``append`` adds its
    parts to it. The task then holds what ``get_task`` would return for it, so a result reads
    the same whether the hand-off followed the stream or the worker found the task later
    (FR-006, FR-020). An update marked ``append`` for an artifact never sent before, and one
    that names no artifact id, is kept as a new artifact: nothing an agent sends ends a run
    (FR-009).
    """
    artifact = update.artifact
    held = next(
        (
            a
            for a in task.artifacts
            if artifact.artifact_id and a.artifact_id == artifact.artifact_id
        ),
        None,
    )
    if held is None:
        task.artifacts.append(artifact)
    elif update.append:
        held.parts.extend(artifact.parts)
    else:
        held.CopyFrom(artifact)


async def _poll(client: Client, outcome: SendOutcome, on_progress: OnProgress) -> None:
    """Check a task of an agent that does not stream until it stops; report each change.

    A change is another state, whatever its text, or, since feature 008, the same state with
    a text that is not the last one seen (FR-019). Texts are compared as kept texts are made
    (``progress_text``): blank is none, and two texts alike in their first 2,000 characters
    are one. The update carries the status's whole text, as before. A read with no text
    leaves the last text seen as it was, so a text that comes back is not sent again
    (008 research R5). ``client`` is the one the message was sent with.
    """
    if outcome.task is None:
        raise RuntimeError("There is no task to check.")
    # The protocol's own states, so submitted to working counts as a change.
    seen = outcome.task.status.state
    said = progress_text(outcome.task.status)
    while outcome.state not in _STOP:
        await asyncio.sleep(POLL_SECONDS)
        task = await client.get_task(GetTaskRequest(id=outcome.task.id))
        outcome.task = task
        _note_progress(outcome, task.status)
        text = progress_text(task.status)
        if task.status.state != seen:
            seen = task.status.state
            on_progress(task.id, state_name(seen), _text(task.status.message))
        elif text is not None and text != said:
            on_progress(task.id, state_name(seen), _text(task.status.message))
        if text is not None:
            said = text


async def send_message(
    link: "AgentLink",
    message: Message,
    on_progress: OnProgress,
    clock: Clock | None = None,
    *,
    limit: float | None = None,
) -> SendOutcome:
    """Send ``message`` to the agent and wait for it within the call time limit (FR-009).

    ``clock`` times the acknowledgment (``SendOutcome.acknowledged_at``). ``limit`` is what is
    left of the call time limit when the call first had to read the agent's card.
    """
    outcome = SendOutcome()
    broke = False
    try:
        async with asyncio.timeout(link.call_timeout if limit is None else limit):
            await _send(link, message, on_progress, outcome, clock)
    except (TimeoutError, A2AClientTimeoutError):
        outcome.timed_out = True
        if not outcome.acknowledged:
            link.mark(False, f"it did not answer within {link.call_timeout:g} seconds")
            return outcome
    except Exception as exc:  # every failure becomes an outcome; none ends a run
        error = link.describe(str(exc) or type(exc).__name__)
        link.mark(False, error)
        if not outcome.acknowledged:
            outcome.error = error
            outcome.unreachable = unreachable(exc)
            return outcome
        # Acknowledged, then the stream broke: the agent has the task, so it is kept as last
        # seen and watched like a task still working at the limit (FR-017).
        broke = True
    outcome.malformed = malformed(outcome)
    if outcome.malformed is not None:
        link.mark(False, f"it replied with something malformed: {outcome.malformed}")
        return outcome
    if not broke:
        link.mark(True)
    return outcome


# A task or conversation id longer than this makes a reply malformed (FR-009).
ID_LIMIT = 256
# What an id must not hold: a control character, which every line break but two is, or a
# separator of lines or paragraphs. The agent chooses its task's id, and the id is written
# into what the model reads line by line, its system message included: a line break in one
# would start a line of the agent's choosing, which could pass for another task or for the
# service's own words.
_UNSAFE_CATEGORIES = frozenset({"Cc", "Zl", "Zp"})


def _unsafe(value: str) -> bool:
    return any(unicodedata.category(character) in _UNSAFE_CATEGORIES for character in value)


def malformed(outcome: SendOutcome) -> str | None:
    """What makes a reply unusable, or None: a task or conversation id that is empty, longer
    than ``ID_LIMIT`` characters, or holds a control character or a line break."""
    checks: list[tuple[str, str]] = []
    if outcome.reply is not None:
        # A direct message may leave its conversation out; it then starts none.
        if outcome.reply.context_id:
            checks.append(("conversation id", outcome.reply.context_id))
    elif outcome.task is not None:
        checks += [("task id", outcome.task.id), ("conversation id", outcome.task.context_id)]
    for name, value in checks:
        if not value:
            return f"its {name} is empty"
        if len(value) > ID_LIMIT:
            return f"its {name} is longer than {ID_LIMIT} characters"
        if _unsafe(value):
            return f"its {name} holds a control character or a line break"
    return None


def unreachable(exc: BaseException) -> bool:
    """True when the request got no answer from the agent: no connection, or it broke off.

    Any other failure is an answer Freyja could not use: an error status, a protocol error the
    agent sent, or a reply that does not parse.
    """
    cause = exc if isinstance(exc, httpx.TransportError) else exc.__cause__
    return isinstance(cause, httpx.TransportError)
