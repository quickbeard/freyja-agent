"""Request and response models for the thread endpoints (contracts/openapi.yaml)."""

from datetime import datetime
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    model_validator,
)
from pydantic_core import PydanticCustomError

from freyja_agent.config import SERVER_NAME_PATTERN, AddressError, canonical_address
from freyja_agent.llm.store import BASE_URL_LIMIT, MODEL_LIMIT, NAME_LIMIT, REASON_LIMIT
from freyja_agent.runtime.saved_agents import InvalidAgentError, check_name

# Client-chosen, opaque. Letters, digits, hyphen, underscore, dot; 1 to 128 chars; at least one
# character that is not a dot, so "." and ".." never name a thread (FR-001a). The length is a
# separate bound because the pattern is written without lookahead, which the validation
# engine does not support.
THREAD_ID_PATTERN = r"^[A-Za-z0-9._-]*[A-Za-z0-9_-][A-Za-z0-9._-]*$"
THREAD_ID_MAX_LENGTH = 128


class RunRequest(BaseModel):
    message: Annotated[str, Field(min_length=1, max_length=32000)]


class ApproveDecision(BaseModel):
    type: Literal["approve"]


class EditDecision(BaseModel):
    type: Literal["edit"]
    args: dict[str, Any]


class RejectDecision(BaseModel):
    type: Literal["reject"]
    message: Annotated[str | None, Field(max_length=4000)] = None


Decision = Annotated[
    ApproveDecision | EditDecision | RejectDecision,
    Field(discriminator="type"),
]


class ResumeRequest(BaseModel):
    # The proposal being decided, as the interrupt event and the thread state report it. A
    # decision sent twice therefore cannot be applied to a later proposal (FR-013).
    interrupt_id: str
    decisions: Annotated[list[Decision], Field(min_length=1)]


class ToolCall(BaseModel):
    id: str
    name: str
    args: dict[str, Any]


class Message(BaseModel):
    id: str | None = None
    type: Literal["human", "ai", "tool", "system"]
    content: str | list[Any]
    name: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None


AllowedDecision = Literal["approve", "edit", "reject"]


class ActionRequest(BaseModel):
    name: str
    # The tool server the proposed tool belongs to now; None for Freyja's own tools and for a
    # tool that is no longer configured (feature 002, FR-008, FR-019).
    server: str | None = None
    # The agent a hand-off, answer, or cancel goes to; None for every other tool and for a
    # proposal whose agent is no longer configured (feature 003, FR-013).
    agent: str | None = None
    args: dict[str, Any]
    description: str | None = None
    # The decisions this action accepts; ``PendingDecision.allowed_decisions`` is the union.
    allowed_decisions: list[AllowedDecision] = Field(default_factory=list)


class PendingDecision(BaseModel):
    interrupt_id: str
    action_requests: list[ActionRequest]
    allowed_decisions: list[AllowedDecision]


AgentTaskStatus = Literal[
    "working", "input_required", "completed", "failed", "rejected", "canceled", "given_up"
]


class AgentTaskProgress(BaseModel):
    """An agent's own claim about its work (feature 008). It is shown, and it decides nothing:
    ``status`` alone says whether the task is working, has ended, or waits for input."""

    # The text parts of the status message the agent attached to a working status, joined by
    # line breaks and cut to 2,000 characters. Untrusted text in the agent's own words: it may
    # hold several lines, markup, links, or an address, and passes as the agent wrote it.
    text: str
    # When the service first learned this text, on the database's clock, never a time the
    # agent supplied. It stays while the agent repeats the same text.
    learned_at: datetime


class AgentTask(BaseModel):
    """One task an agent created for the thread (feature 003, FR-017)."""

    agent: str
    # The agent's own id for the task.
    task_id: str
    # The task message the task was started with.
    message: str
    # ``working`` and ``input_required`` are pending.
    status: AgentTaskStatus
    started_at: datetime
    last_checked_at: datetime
    # None while the task is pending.
    ended_at: datetime | None
    # True once a run recorded the task's outcome in the thread's messages, and from the start
    # for a task whose outcome the hand-off itself returned.
    outcome_delivered: bool
    # The progress the agent last reported, or None when the service has learned none. Kept,
    # and returned, for a task of any status: for one that ended, was given up, or waits for
    # input, the last progress learned while it was working (feature 008, FR-009).
    progress: AgentTaskProgress | None


class ThreadState(BaseModel):
    thread_id: str
    run_active: bool
    messages: list[Message]
    pending: PendingDecision | None
    updated_at: str | None = None
    # Every agent task the thread has started, pending or ended, oldest first (feature 003).
    agent_tasks: list[AgentTask] = Field(default_factory=list)


class ServerHints(BaseModel):
    """What a tool server says about its own tool. The server's claim only: it is shown and
    never affects the classification. Only the hints the server set are serialized."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    read_only: bool | None = None
    destructive: bool | None = None
    idempotent: bool | None = None
    open_world: bool | None = None


class CatalogTool(BaseModel):
    """One tool the orchestrator can call (feature 002 contracts/openapi.yaml)."""

    name: str
    # The tool server that provides the tool; None for Freyja's own tools.
    server: str | None
    # The agent a hand-off or cancel tool belongs to; None for every other tool (feature 003).
    agent: str | None
    description: str
    # JSON Schema of the tool's arguments; an edit decision is validated against it.
    input_schema: dict[str, Any]
    classification: Literal["read_only", "workspace", "gated"]
    # Empty unless the tool is gated.
    allowed_decisions: list[AllowedDecision]
    server_hints: ServerHints | None


class ToolServerStatus(BaseModel):
    """A configured tool server and the outcome of the most recent attempt to use it."""

    name: Annotated[str, Field(pattern=SERVER_NAME_PATTERN)]
    # Which form of connection the entry uses; nothing else about the entry is shown.
    connection: Literal["command", "address"]
    reachable: bool
    checked_at: datetime
    error: Annotated[str | None, Field(max_length=500)]
    tool_count: Annotated[int, Field(ge=0)]


class AgentSkillClaim(BaseModel):
    id: str
    name: str
    description: str
    tags: list[str]


class AgentCardClaims(BaseModel):
    """What an agent's card says about itself, read at startup: the agent's own claim, which
    never affects classification (feature 003 contracts/openapi.yaml)."""

    name: str
    description: str
    version: str
    skills: list[AgentSkillClaim]


class AgentStatus(BaseModel):
    """A configured agent and the outcome of the most recent attempt to use it (FR-024)."""

    name: str
    reachable: bool
    checked_at: datetime
    # Why the last attempt failed, with the agent's address removed; None when reachable.
    error: Annotated[str | None, Field(max_length=500)]
    card: AgentCardClaims


class ToolCatalog(BaseModel):
    servers: list[ToolServerStatus]
    # One entry per configured agent, in configuration order; empty when none is (feature 003).
    agents: list[AgentStatus]
    tools: list[CatalogTool]


# Feature 005: the Agents section (specs/005-web-delegate-agents/contracts/openapi.yaml). A
# saved agent's address is in ``Agent.url`` and in no other shape of the service; an
# environment agent's is in none.
AGENT_TEXT_LIMIT = 2000
AGENT_LIST_LIMIT = 50


class AgentCard(BaseModel):
    """What an agent says of itself: its own claim, never an input to approval (005 FR-002).

    Each text is cut to 2,000 characters, the skills to 50, and a skill's tags to 50, with
    every agent address removed, before the service keeps or returns it.
    """

    name: Annotated[str, Field(max_length=AGENT_TEXT_LIMIT)]
    description: Annotated[str, Field(max_length=AGENT_TEXT_LIMIT)]
    version: Annotated[str, Field(max_length=AGENT_TEXT_LIMIT)]
    skills: Annotated[list[AgentSkillClaim], Field(max_length=AGENT_LIST_LIMIT)]
    # True when a text, the list of skills, or a skill's tags were cut.
    cut: bool


class Agent(BaseModel):
    """One agent Freyja may hand work to, as the Agents section shows it (005 FR-001)."""

    # A saved agent's id, 32 hex characters; ``env.<name>`` for an environment agent.
    id: str
    name: str
    source: Literal["saved", "environment"]
    # Whether a message to the agent waits for a person: ``none`` runs without approval.
    approval: Literal["required", "none"]
    # A saved agent's address in the form the service keeps it in; None from the environment.
    url: Annotated[str | None, Field(max_length=AGENT_TEXT_LIMIT)]
    # What a change or a removal sends back; None for an environment agent.
    version: Annotated[int | None, Field(ge=1)]
    # This instance's most recent attempt to use the agent; ``not_tried`` before any, and
    # ``not_in_use`` for a saved agent the environment overrides.
    state: Literal["reachable", "unreachable", "not_tried", "not_in_use"]
    # None while ``not_tried`` or ``not_in_use``.
    checked_at: datetime | None
    # Why the agent is unreachable or not in use. Holds no address.
    reason: Annotated[str | None, Field(max_length=500)]
    # None for an environment agent whose card this instance has not read.
    card: AgentCard | None
    # The agent's pending tasks, on every thread.
    pending_tasks: Annotated[int, Field(ge=0)]
    created_at: datetime | None
    updated_at: datetime | None


def _agent_name(value: str) -> str:
    """A name in the form environment agents use, refused in the store's own sentence."""
    refused = None
    try:
        return check_name(value)
    except InvalidAgentError as exc:
        refused = str(exc)
    # Raised outside the handler: the error carries the sentence and nothing behind it.
    raise PydanticCustomError("agent_name", refused)  # type: ignore[arg-type]


def _agent_address(value: str) -> str:
    """The address in the one form it is kept in, refused in the rule's own sentence.

    The rule words every refusal itself and never repeats what was entered (005 research
    R11), so its sentence is the whole message: nothing of pydantic's is put before it.
    """
    refused = None
    try:
        return canonical_address(value)
    except AddressError as exc:
        refused = str(exc)
    raise PydanticCustomError("agent_address", refused)  # type: ignore[arg-type]


class AgentCreate(BaseModel):
    """What adding an agent takes: a name and an address (005 FR-006, FR-008).

    ``url`` is validated by the address rule and kept in its form, at most 2,000 characters,
    which the rule checks itself. ``approval`` is ``required`` when left out; ``none`` adds
    an agent that runs without approval, which the console asks an operator to confirm
    (005 FR-011). No refused value is repeated.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: Annotated[str, AfterValidator(_agent_name)]
    url: Annotated[str, AfterValidator(_agent_address)]
    approval: Literal["required", "none"] = "required"


def _a_choice(value: str | None) -> str:
    """Refuse an approval choice sent as null: a field that is kept is left out, not emptied."""
    if value is None:
        raise PydanticCustomError("literal_error", "Input should be 'required' or 'none'")
    return value


def _a_new_address(value: str | None) -> str:
    """The address rule for a change. An address sent as null is refused: a value that is
    kept is left out."""
    if value is None:
        raise PydanticCustomError("agent_address", "Enter the address.")
    return _agent_address(value)


class AgentUpdate(BaseModel):
    """What changing a saved agent takes (005 FR-009, FR-012; research R14).

    ``version`` is the one the page that asks last read: the change is refused when the agent
    was changed since. ``url`` and ``approval`` left out are kept. ``url`` is validated by
    the address rule and kept in its form, as for an add. Nothing else is accepted: a saved
    agent's name never changes. No refused value is repeated.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    version: Annotated[int, Field(ge=1, strict=True)]
    url: Annotated[str | None, AfterValidator(_a_new_address)] = None
    approval: Annotated[Literal["required", "none"] | None, AfterValidator(_a_choice)] = None


class AgentChanged(BaseModel):
    """A saved agent after a change, and how many of its tasks the change gave up."""

    agent: Agent
    given_up: Annotated[int, Field(ge=0)]


class AgentRemoved(BaseModel):
    """How many of a removed agent's tasks the removal gave up (005 FR-024)."""

    given_up: Annotated[int, Field(ge=0)]


# A saved agent's id, or ``env.<name>`` for an environment agent (contracts/openapi.yaml).
AGENT_ID_PATTERN = r"^([0-9a-f]{32}|env\.[a-z][a-z0-9_-]{0,31})$"


class AgentCheck(BaseModel):
    """What a check of an agent found (005 FR-013, FR-016)."""

    usable: bool
    # Which check the card failed, and the service's own sentence for it; None when usable.
    reason: (
        Literal[
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
        | None
    )
    message: str | None
    checked_at: datetime
    # False when the agent was moved or removed while the check ran: nothing was kept.
    recorded: bool
    # True when this check replaced the card details kept or shown for the agent by
    # different ones. The agent's tools are described from the new details from the next run.
    card_changed: bool


class AgentList(BaseModel):
    # Environment agents first, in configuration order, then saved agents by name.
    agents: list[Agent]
    # How many agents can be saved.
    limit: int
    # How many agents are ``unreachable`` or ``not_in_use``.
    unusable: Annotated[int, Field(ge=0)]


class ErrorDetail(BaseModel):
    code: Literal[
        "run_active",
        "decision_pending",
        "nothing_pending",
        "proposal_mismatch",
        "thread_not_found",
        "internal",
        # Feature 004, the LLM profile routes.
        "profile_not_found",
        "profile_read_only",
        "profile_active",
        "profile_unusable",
        "profile_limit",
        "name_taken",
        "secret_not_configured",
        "test_busy",
        "other_origin",
    ]
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail


# Feature 004: LLM profiles (specs/004-web-llm-profiles/contracts/openapi.yaml). No response
# model here has a key field, and a request's key is a SecretStr, so it is never shown when a
# model is printed or logged (FR-020).

API_KEY_LIMIT = 4000
LlmProvider = Literal["openai", "anthropic", "openrouter"]
ProfileName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_LIMIT)
]
ModelId = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MODEL_LIMIT)
]
ApiKey = Annotated[SecretStr, Field(min_length=1, max_length=API_KEY_LIMIT)]


def _web_address(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("must be an http or https address with a host")
    return value


BaseUrl = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=BASE_URL_LIMIT),
    AfterValidator(_web_address),
]
# A refused value is never repeated: on these routes it may be a key (research R9).
_REQUEST = ConfigDict(extra="forbid", hide_input_in_errors=True)


class LlmProfileCreate(BaseModel):
    model_config = _REQUEST

    name: ProfileName
    provider: LlmProvider
    model: ModelId
    base_url: BaseUrl | None = None
    api_key: ApiKey
    # Used for an OpenAI-compatible endpoint only: the Responses API, or chat completions.
    responses_api: bool = True


class LlmProfileUpdate(BaseModel):
    """Only the fields sent are changed; a key left out keeps the stored one (FR-009)."""

    model_config = _REQUEST

    name: ProfileName | None = None
    provider: LlmProvider | None = None
    model: ModelId | None = None
    base_url: BaseUrl | None = None
    api_key: ApiKey | None = None
    responses_api: bool | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("send at least one field to change")
        # Left out means unchanged. Only a base URL can be taken away by sending null.
        cleared = sorted(
            name for name in self.model_fields_set - {"base_url"} if getattr(self, name) is None
        )
        if cleared:
            raise ValueError(f"{', '.join(cleared)} cannot be null; leave it out to keep it")
        return self


class LlmProfileDraft(BaseModel):
    """Values to test without saving them (FR-017)."""

    model_config = _REQUEST

    provider: LlmProvider
    model: ModelId
    base_url: BaseUrl | None = None
    api_key: ApiKey | None = None
    responses_api: bool = True
    # A saved profile whose stored key is used when ``api_key`` is left out.
    profile_id: Annotated[str, Field(min_length=1, max_length=64)] | None = None

    @model_validator(mode="after")
    def _a_key_from_somewhere(self) -> Self:
        if self.api_key is None and self.profile_id is None:
            raise ValueError("send 'api_key', or 'profile_id' to use a saved profile's key")
        return self


LlmTestStep = Literal["first_message", "conversation"]


class LlmLastTest(BaseModel):
    ok: bool
    at: datetime
    # The step that failed; None when the test passed.
    step: LlmTestStep | None
    # Why it failed, with any key removed; None when the test passed.
    reason: Annotated[str | None, Field(max_length=REASON_LIMIT)]


class LlmTestResult(LlmLastTest):
    # Whether the result was kept with a listed profile.
    recorded: bool


class LlmProfile(BaseModel):
    # 32 hex characters for a saved profile; "environment" for the environment's.
    id: str
    name: str
    source: Literal["saved", "environment"]
    # One of the three for a saved profile; the environment's may carry another prefix.
    provider: str
    model: str
    base_url: str | None
    # None for a provider with no such choice.
    responses_api: bool | None
    # The key's last four characters; empty for a key under eight characters, or none.
    key_hint: str
    active: bool
    usable: bool
    unusable_reason: (
        Literal["key_unreadable", "secret_not_configured", "not_configured", "cannot_build"] | None
    )
    # None when not tested since the profile's last change.
    last_test: LlmLastTest | None
    created_at: datetime | None
    updated_at: datetime | None


class LlmProfileList(BaseModel):
    profiles: list[LlmProfile]
    # The id of the one active profile; "environment" when no saved one is.
    active_id: str
    # False when the active profile is not usable: runs then end with no_usable_model.
    usable_model: bool
    # False when profiles cannot be saved because no secret is set.
    secret_configured: bool
    limit: int


class RunProfile(BaseModel):
    """The profile a run uses, in its stream's first event (contracts/sse-events.md)."""

    id: str
    name: str
    source: Literal["saved", "environment"]
