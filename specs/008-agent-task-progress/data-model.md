# Data Model: Progress of Pending Agent Tasks

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Research**:
[research.md](research.md)

One entity changes: the agent task of feature 003 gains its kept progress. No table is
added, and nothing is kept outside the database.

## Agent task (changed)

Table `freyja_agent_tasks`, record `AgentTaskRecord` in `runtime/agent_tasks.py`. Everything
feature 003 and feature 005 define of it stays
([003 data model](../003-a2a-agent-delegation/data-model.md)). Two columns are added:

| Column | Type | Null | Meaning |
|---|---|---|---|
| `progress` | `text` | yes | The progress text the service last learned for the task: at most 2,000 characters, in the agent's own words. Null for a task that has none. |
| `progress_at` | `timestamptz` | yes | When the service first learned that text, on the database's clock. Null exactly when `progress` is null. |

Both are added to the table of a deployment at startup, each only when it is missing, under
the lock timeout feature 005 set for `agent_ref` (`_ADDED_COLUMNS`), and are part of the
`CREATE TABLE` statement for a new database. No index is added: progress is read with its
row and never searched.

### Rules

- **What a progress text is** (FR-002, FR-004): the text parts of the status message of a
  status that says the task is working, joined by line breaks, with NUL characters and lone
  surrogates dropped, cut to 2,000 characters. A text that is then empty or blank is no
  text. One function decides it, `progress_text` ([research.md](research.md) R1).
- **At most one per task** (FR-001): a new text replaces the one kept, and its time. No
  earlier text is kept anywhere.
- **The time stays while the text repeats** (FR-003): the two columns are written only when
  the text learned is not the text kept. A status with no text writes neither.
- **The time is the store's** (FR-005): the database's `now()` for a check; for a hand-off,
  the database's `now()` when the task is recorded, less the seconds since the hand-off
  first saw the text (R4). Never a time from the agent.
- **Both or neither**: `progress` and `progress_at` are always written together.
- **Never cleared** (FR-007): nothing sets them back to null, and nothing that ends a task
  or gives it up names them.
- **Not an updatable key**: the task's agent, id, thread, and `agent_ref` still never change.
  The two new fields are updatable like the task's other state.

### Who writes it

| Writer | When | What |
|---|---|---|
| The hand-off (`a2a_tools._record`) | Once, when it records the task it followed: as pending, as ended, or after an answer | The last progress text it received, if any, and if the row does not hold the same text already (FR-006) |
| The worker (`DelegationWorker._check`) | At each check that finds the task working | The text the status carries, if any and if it is not the text kept, in the statement that sets `last_checked_at` (R3) |

Nothing else writes it. Neither writer makes a request for it or adds a statement.

### Lifecycle

```text
task recorded by its hand-off
  progress = last text the hand-off received, or null

while the row says working, at each check:
  status working, text T, T != kept   -> progress = T, progress_at = now
  status working, text T, T == kept   -> unchanged
  status working, no text             -> unchanged
  agent not reached                   -> unchanged (nothing learned)

row comes to wait for input           -> unchanged; the worker keeps nothing while it waits
answered, hand-off records it working -> progress = last text that hand-off received, if new
task ends, or is given up             -> unchanged, for as long as the row exists
```

A row is never deleted (feature 003), so the last progress of an ended task stays readable in
the thread's state.

### Several instances

One instance watches a task at a time (003 FR-019), and both writers write one conditional
statement on the row. Every instance reads the row from the database, so all show the same
progress (FR-008). An instance on an earlier version reads and writes its own columns and
leaves these two alone.

## Kept progress, as the API returns it

`GET /threads/{thread_id}/state` returns each agent task with one more key
([contracts/openapi.yaml](contracts/openapi.yaml)):

| Key | Type | Meaning |
|---|---|---|
| `progress` | object or null | Null when the task has no kept progress. |
| `progress.text` | string, 1 to 2,000 characters | The progress text, as kept. |
| `progress.learned_at` | date-time | `progress_at`. |

It is returned for every agent task that has it, whatever the task's status (FR-009). What
is shown of it is each client's rule; the console's is below.

## What the orchestrator is given

- On every model call: the pending tasks, one line each, as today. No progress (FR-014).
- In the result of `check_agent_tasks`: one more line under each task that is `working` and
  has kept progress ([contracts/check-agent-tasks.md](contracts/check-agent-tasks.md)).

## What the web app holds

Nothing is stored. `AgentTask` in `web/lib/tasks.ts` gains `progress: { text: string;
learned_at: string } | null`, as the service returns it. The task's view holds the browser's
current time in its state, to word how long ago a progress text was learned; it is set by
the timer the view already has and when a reading arrives (R8).

The page shows progress for an agent task whose status is `working` and whose `progress` is
not null, and for no other (FR-010).

## In memory, during a hand-off

`SendOutcome` in `delegates/a2a_agents.py` gains two fields, which live for one send:

| Field | Meaning |
|---|---|
| `progress` | The last progress text the send received, or `None`. |
| `progress_seen` | The event loop's clock when that text was first received. |

They are how the hand-off's last progress reaches the row (R4).

## Existing entities this feature reads and does not change

- **Agent conversation, agent send, proposed call** (features 003 and 005).
- **Thread record** (feature 007): the index keeps whether a thread has a pending agent
  task, and nothing of its progress.
- **The thread's checkpoint**: progress is never written into a thread (FR-016).
