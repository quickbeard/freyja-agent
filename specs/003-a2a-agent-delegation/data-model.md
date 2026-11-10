# Data Model: A2A Agent Delegation

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Research**: [research.md](research.md)

Entities from the spec's Key Entities, with the fields, rules, and state transitions the plan
gives them. Python names are the planned ones; API shapes are in
[contracts/openapi.yaml](contracts/openapi.yaml).

## Entities

### Agent entry (`A2aAgentConfig`, in `config.py`)

One value of `FREYJA_A2A_AGENTS`, keyed by agent name. Shape and refusals:
[contracts/agent-config.md](contracts/agent-config.md).

| Field | Type | Rule |
|---|---|---|
| (key) name | string | `^[a-z][a-z0-9_-]{0,31}$`; unique across agents and tool servers |
| `url` | string | `http` or `https`; host `localhost` or a loopback IP literal (002 research R2) |
| `read_only` | bool, default `false` | `true` exempts every message to this agent from approval |

Unknown fields are refused; errors never repeat the entry.

### Delegate agent (`AgentLink`, in `delegates/a2a_agents.py`)

The running link to one configured agent, one per agent for the life of the process.

| Field | Meaning |
|---|---|
| `name` | the configured name |
| `card` | `AgentCardSummary` read at startup |
| `streaming` | from the card's capabilities; chooses the send mode (research R5) |
| `reachable`, `checked_at`, `error` | outcome of the most recent startup read, hand-off, answer, cancel, or status check; `error` has the address removed |

Never exposed: the configured URL, the card's advertised URLs.

### Agent card summary (`AgentCardSummary`)

What the catalog and the tool description show from the card. Every field is the agent's own
claim and is never an input to classification.

| Field | From the card |
|---|---|
| `name`, `description`, `version` | the same fields |
| `skills` | `id`, `name`, `description`, `tags` of each skill, in card order |

Startup rules on the full card: no security requirements or schemes, a `JSONRPC` interface, and
text among the default input modes when any are listed (research R3).

### Agent tools (`DelegateTool` with `agent` set)

Two per agent, both with the agent's classification (research R4).

| Tool | Arguments (pydantic) | Pinned on edit | Allowed decisions when gated |
|---|---|---|---|
| `handoff_to_<agent>` | `message: str` (1 to 32,000), `context: str \| None` (up to 32,000), `task_id: str \| None` | `task_id` | approve, edit, reject |
| `cancel_<agent>_task` | `task_id: str` | `task_id` | approve, reject |

`DelegateTool` and `CatalogTool` gain `agent: str | None` (null for tool server tools and
Freyja's own) and `pinned_args: tuple[str, ...]`. Classification: `read_only` if the agent entry
says so, `gated` otherwise.

### Freyja's own tool `check_agent_tasks`

Added to the fixed table `OWN_TOOLS` as `read_only`. Arguments: `task_ids: list[str] | None`.
Returns the thread's agent tasks, or the named ones, with status. It is also the tool the
outcome messages call (below).

### Agent conversation (`freyja_agent_conversations`)

| Column | Type | Rule |
|---|---|---|
| `thread_id` | text | part of the primary key |
| `agent` | text | part of the primary key |
| `context_id` | text | the A2A context id from the agent's first reply |
| `created_at` | timestamptz | |

One row per thread and agent. Written on the first acknowledged reply; read by every later
hand-off from that thread to that agent, whichever run or subagent makes it (FR-008).

### Agent send (`freyja_agent_sends`)

The ledger that keeps a step that runs again from sending twice (FR-010, FR-027).

| Column | Type | Rule |
|---|---|---|
| `thread_id`, `step`, `tool_call_id` | text | primary key; `step` is the graph namespace the tool call ran in |
| `agent` | text | |
| `kind` | text | `handoff`, `answer`, or `cancel` |
| `message_id` | text | the A2A message id sent: `freyja-<thread>-<tool call id>-<step digest>` |
| `request` | text | SHA-256 of the agent, the kind, and the arguments; a row found with another digest is refused |
| `task_id` | text, null | known once acknowledged |
| `sent_at` | timestamptz | written before the request leaves |
| `result` | text, null | the result text the tool returned; null while in flight |

A row with `result` null and `sent_at` set means the request may have left: the tool returns an
error result saying the effect may or may not have happened, and sends nothing.

### Agent task (`freyja_agent_tasks`)

One row per task an agent created for a thread, kept for good (clarification of 2026-11-09).

| Column | Type | Meaning |
|---|---|---|
| `agent`, `task_id` | text | primary key |
| `thread_id` | text | indexed |
| `context_id` | text | at most 256 characters, as `task_id`; a longer or empty one makes the reply malformed (spec FR-009) |
| `message` | text | the task message it was started with |
| `state` | text | see transitions |
| `started_at`, `last_checked_at`, `ended_at` | timestamptz | `ended_at` null until a final state |
| `outcome` | text, null | the text to deliver, set when the task leaves `working` |
| `outcome_ready` | bool | an outcome is waiting to be delivered |
| `delivered_at` | timestamptz, null | when a run recorded the outcome in the thread |
| `watcher`, `watch_until` | text, timestamptz | the instance watching it, and until when |
| `followup_attempts`, `followup_next_at`, `followup_error` | int, timestamptz, text | background run retries (FR-027) |

**States and transitions**:

```text
                hand-off acknowledged
                        │
                        ▼
   ┌──────────────── working ────────────────┐
   │        │            │          │        │
 answered  completed   failed   rejected   canceled ─── (by the agent, or Freyja's cancel)
 (answer    │            │          │        │
 sent) ▲    ▼            ▼          ▼        ▼
   │  input_required    (final)    (final)  (final)
   │        │
   └────────┘
 working or input_required ──(maximum age, or agent removed)──▶ given_up ──(cancel)──▶ canceled
```

- `working` covers the protocol's `submitted` and `working`.
- `completed`, `failed`, `rejected`, `canceled` are final and set `ended_at`. `auth_required`
  from the agent, and a task the agent answers as not found, are stored as `failed`, with the
  reason in the outcome (spec FR-019). A cancel the agent made itself is `canceled` with an
  outcome to deliver (spec FR-020).
- `input_required` is not final: the task is waiting for the user's answer and is not polled,
  but the maximum age still applies.
- `given_up` is Freyja's own state: it stopped watching. The agent may still be working; a
  cancel can still be sent (FR-022).
- Every move away from `working`, including into `given_up`, sets `outcome` and
  `outcome_ready`, except a cancel Freyja made within a run, whose result the orchestrator
  already has.
- An outcome that arrives for a task already `canceled` by Freyja is ignored (FR-022).

### Outcome messages

Recorded in the thread's checkpointed messages to deliver one or more outcomes (research R10).

| Message | Id | Content |
|---|---|---|
| `AIMessage` with one `check_agent_tasks` call per task, named `freyja` | `a2a-outcome-call-<first agent>-<first task id>-<arrived>` | each call's args `{"task_ids": [<task id>]}`; each call's id `a2a_` and 24 hex characters of a digest of its result's id |
| one `ToolMessage` per task, answering its call | `a2a-outcome-<agent>-<task id>-<arrived>` | the outcome text, headed with the agent, task id, and state, as the hand-off's result would have been; an error result for `failed`, `rejected`, `canceled`, and `given_up` |

`<arrived>` is when the outcome arrived (the task's `last_checked_at`), in microseconds since
the epoch. A task can have a second outcome: one that asked a question through the worker, was
answered, and went pending again. That outcome must not share the first one's ids.

The ids make delivery idempotent: a run finds an outcome already recorded and skips it. There is
one call per task because a provider takes exactly one result for each tool call. The tool call
ids are short and plain because providers limit them (OpenAI to 40 characters), and a task id
can be up to 256 characters long.

### Background run

A run started by the worker on a thread with undelivered outcomes. Not stored on its own: it is
a run like any other in the checkpoints, its lease is the thread's run lease, and its retry
state is on the task rows it delivered. Mode `background` in the `start` event, which no client
sees.

## API shapes added or changed

| Shape | Change | Where |
|---|---|---|
| `ThreadState.agent_tasks` | new: list of `AgentTask` | `GET /threads/{id}/state` |
| `AgentTask` | new: agent, task_id, message, status, started_at, last_checked_at, ended_at, outcome_delivered | thread state |
| `ActionRequest.agent` | new: the agent a hand-off or cancel goes to; null otherwise | thread state, `interrupt` event |
| `ToolCatalog.agents` | new: list of `AgentStatus` | `GET /tools` |
| `AgentStatus` | new: name, reachable, checked_at, error, card | catalog |
| `CatalogTool.agent` | new: the agent a tool belongs to; null otherwise | catalog |
| `update` event, `node: "agent"` | new payload: agent, task_id, state, text | run stream |

`AgentTask.status` values, with the spec's words for them (Key Entities "Agent Task"):

| Spec | API and table |
|---|---|
| working | `working` |
| waiting for input | `input_required` |
| finished | `completed` |
| failed | `failed` |
| rejected | `rejected` |
| canceled | `canceled` |
| given up | `given_up` |

## Settings added

`FREYJA_A2A_AGENTS`, `FREYJA_A2A_CALL_TIMEOUT_SECONDS`, `FREYJA_A2A_STARTUP_TIMEOUT_SECONDS`,
`FREYJA_A2A_POLL_INTERVAL_SECONDS`, `FREYJA_A2A_TASK_MAX_AGE_SECONDS`,
`FREYJA_BACKGROUND_RUN_ATTEMPTS`, `FREYJA_BACKGROUND_RETRY_SECONDS`. Defaults and meaning:
research R2.

## Relationships

```text
Agent entry 1 ── 1 AgentLink ── 2 agent tools (hand-off, cancel) ── in the Catalog
Thread 1 ── * Agent conversation (one per agent)
Thread 1 ── * Agent send ── 0..1 Agent task
Thread 1 ── * Agent task ── 0..1 delivered outcome (a ToolMessage in the thread)
Agent task * ── 0..1 watcher (one service instance at a time)
```
