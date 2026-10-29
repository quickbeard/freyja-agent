# Data Model: Thread Runs API

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Two kinds of data exist. The first is owned by LangGraph and lives in the checkpointer and store
tables that `AsyncPostgresSaver.setup()` and `AsyncPostgresStore.setup()` create; Freyja reads it
through the compiled graph's `aget_state` and never queries those tables directly. The second is
owned by Freyja: one small table for the run lock, plus the API-facing shapes below.

## Entities

### Thread

A conversation keyed by a client-chosen id. It exists once the first checkpoint for its id has
been written; before that, state reads return not found.

| Field | Type | Rules |
|---|---|---|
| `thread_id` | string | `^[A-Za-z0-9._-]{1,128}$` (FR-001a). Used verbatim as the LangGraph `configurable.thread_id`. |
| `messages` | list of Message | Full transcript from the latest checkpoint's `messages` channel, in order (FR-007). |
| `pending` | PendingDecision or null | Derived from the latest checkpoint's interrupts. |
| `run_active` | boolean | Derived from an unexpired RunLock row for this thread. |
| `updated_at` | timestamp or null | `created_at` of the latest checkpoint. |

Owned by LangGraph; not a Freyja table.

### Run

One turn of work on a thread: a message run or a resume run. Not stored as a row; its identity
is the `run_id` echoed in the stream's `start` event and used as part of the lock owner.

| Field | Type | Rules |
|---|---|---|
| `run_id` | UUID4 | Generated per request. |
| `thread_id` | string | As above. |
| `mode` | `run` or `resume` | Which endpoint started it. |
| `debug` | boolean | Adds `debug` events; never changes behavior (FR-004). |
| `outcome` | `completed`, `interrupted`, `error` | Exactly one terminal event per stream (FR-003). |

State transitions of a run:

```text
accepted ──lock acquired──▶ streaming ──reply finished──▶ completed  (terminal event: end)
   │                            │
   │                            ├──gated action proposed──▶ interrupted (terminal event: interrupt)
   │                            │
   │                            ├──exception / step limit──▶ error     (terminal event: error)
   │                            │
   │                            └──client disconnected────▶ stopped   (no event; lock released)
   │
   ├──lock held by another run──▶ refused (HTTP 409 run_active, no stream)
   │
   └──message run while a decision is pending──▶ refused (HTTP 409 decision_pending, no stream)
```

The lock is acquired before the state checks and released in every exit path, including a
refusal. The thread's checkpoint reflects every step that completed before the exit.

### RunLock (Freyja table `freyja_run_locks`)

Enforces one active run per thread across every service instance that shares the database
(FR-006). Created at startup next to the LangGraph tables.

| Column | Type | Rules |
|---|---|---|
| `thread_id` | text, primary key | Same format as Thread. |
| `owner` | text, not null | `"<instance_id>:<run_id>"`. `instance_id` is a UUID4 generated when the service starts. |
| `acquired_at` | timestamptz, not null | Set on acquire. |
| `expires_at` | timestamptz, not null | `acquired_at + run_lock_ttl`; moved forward by the heartbeat while the run is streaming. |

Operations, all single statements so they are atomic under concurrent instances:

- **acquire**: insert; on conflict update owner, `acquired_at`, `expires_at` only where the
  existing row has `expires_at < now()`; `RETURNING owner`. A returned row means acquired; no
  row means another live run holds the thread (HTTP 409).
- **renew**: set `expires_at = now() + ttl` where `thread_id` and `owner` match. Runs every
  `ttl / 3` seconds from a background task for the life of the stream.
- **release**: delete where `thread_id` and `owner` match. Runs in `finally`, so it covers
  completion, interrupt, error, and client disconnect. A lock whose owner died is never
  released explicitly; it expires.
- **is_active**: exists where `thread_id` matches and `expires_at > now()`.

Lifecycle:

```text
(no row) ──acquire──▶ held(owner A) ──renew──▶ held(owner A) ──release──▶ (no row)
                           │
                           └──owner dies, ttl passes──▶ expired ──acquire by B──▶ held(owner B)
```

An in-memory implementation with an injectable clock backs the unit tests; the Postgres
implementation is exercised by the opt-in integration tests. Both implement the same four
operations.

### Message (API shape)

Serialized from LangChain message objects for the `update` event and the state response.

| Field | Type | Present for |
|---|---|---|
| `id` | string or null | all |
| `type` | `human`, `ai`, `tool`, `system` | all |
| `content` | string or list of content blocks | all |
| `name` | string or null | all; the tool name on `tool` messages |
| `tool_calls` | list of `{id, name, args}` | `ai` |
| `tool_call_id` | string | `tool` |

### PendingDecision (API shape)

The proposal waiting on an analyst. Appears in the `interrupt` event and in `ThreadState.pending`.

| Field | Type | Rules |
|---|---|---|
| `interrupt_id` | string | LangGraph's interrupt id; opaque to clients. |
| `action_requests` | list of ActionRequest | One per gated tool call in the step; order is significant. |
| `allowed_decisions` | list of `approve`, `edit`, `reject` | From the tool's interrupt config. |

ActionRequest: `{ "name": string, "args": object, "description": string or null }`.

### Decision (API shape)

One per action request, in the same order, submitted to resume.

| `type` | Extra fields | Effect |
|---|---|---|
| `approve` | none | Tool runs with the proposed args. |
| `edit` | `args`: object, validated against the tool's argument schema | Tool runs with `args`. The tool name cannot be changed. |
| `reject` | `message`: string, optional | Tool does not run; the orchestrator receives the message (or a default) as the tool result and continues. |

Validation (FR-013): `decisions` length must equal `action_requests` length; `type` must be in
`allowed_decisions`; `edit.args` must validate against the tool's schema. Any failure is HTTP 422
and leaves the pending decision untouched.

### Skill

A directory under `workspace/skills/<name>/` containing `SKILL.md` with front matter (`name`,
`description`) and a body. Read by the agent at build time; committed source. Not an API entity.

### Workspace

The directory the agent's file tools can reach, configured by `FREYJA_WORKSPACE_DIR` (default
`workspace`). Contains `skills/` (committed) and `cases/` (runtime, git-ignored). The backend
confines all paths to this root.

## Settings added by this feature

| Setting | Env var | Default | Purpose |
|---|---|---|---|
| `workspace_dir` | `FREYJA_WORKSPACE_DIR` | `workspace` | Root for the file backend and `skills/`. |
| `max_steps` | `FREYJA_MAX_STEPS` | `50` | LangGraph recursion limit per run (FR-004a). |
| `run_lock_ttl_seconds` | `FREYJA_RUN_LOCK_TTL_SECONDS` | `60` | Expiry of the run lock; heartbeat renews every third of it (FR-006). |

`FREYJA_TEST_DATABASE_URI` is documented in `.env.example` as a commented, test-only variable; it
is read by the integration tests, not by `Settings`.
