# Data Model: Receive Tasks from Other Agents over A2A

Two tables are new. No existing table changes. Both have a Postgres store and an in-memory
store of one protocol, `ReceivedTaskStore` (`runtime/received_tasks.py`), as the other stores
of the service do. The tables are made at startup when missing; with the feature off they are
made and stay empty, so that turning it on needs no step of its own.

Every statement is a fixed string with bound parameters. Text that comes from a caller is cut
and cleaned of what a text column does not take before it is kept, as the index of threads
does.

## Caller (configuration, not stored)

From `FREYJA_A2A_SERVER_CALLERS` ([contracts/settings.md](contracts/settings.md)).

| Field | Rule |
|---|---|
| `name` | The form of an agent's name: lowercase letters, digits, hyphens, underscores, starting with a letter, at most 32 characters. Unique. Never `local`. |
| `token` | 16 to 4,096 visible ASCII characters. Unique among callers. A secret value: not printed, not serialized, compared in constant time. |

The local caller has the name `local` and no token. A caller's name is what the two tables,
the log, and the console know it by; the token is in none of them.

## Conversation: `freyja_a2a_conversations`

One row per conversation id of one caller.

| Column | Type | Rule |
|---|---|---|
| `caller` | text, not null | The caller's name. |
| `context_id` | text, not null | The caller's conversation id, at most 128 characters, or one the service made. It is returned to its caller and appears nowhere else: not in a log line, not in the console, not in a thread, and not in what a model is told. |
| `thread_id` | text, not null, unique | The thread, made by the service: 32 hexadecimal characters. Never chosen by a caller and never returned to one. |
| `created_at` | timestamptz, not null | |

Primary key: (`caller`, `context_id`). Two callers that choose one id have two rows and two
threads. A row is never changed and never removed.

**This row is what makes a thread a caller's** (FR-012, FR-020, FR-024). It is written in the
transaction that accepts the conversation's first message, before any run starts. Every
question "did a caller start this thread, and which" is one read of this table by
`thread_id`.

## Received task: `freyja_a2a_tasks`

One row per message that was accepted.

| Column | Type | Rule |
|---|---|---|
| `task_id` | text, primary key | Made by the service: 32 hexadecimal characters. Not a thread's id. |
| `caller` | text, not null | With `context_id`, the conversation. |
| `context_id` | text, not null | |
| `thread_id` | text, not null | Copied from the conversation, so that a task is read without a join. |
| `hops` | integer, not null | The hand-over count the message carried; 0 when it carried none. |
| `state` | text, not null | `working`, `completed`, `failed`, or `canceled`. |
| `reason` | text, null | A code of the fixed set below, for `failed` and `canceled`. |
| `result` | text, null | Freyja's last reply, cleaned, for `completed`. |
| `created_at` | timestamptz, not null | When the message was accepted. |
| `started_at` | timestamptz, null | When the task's run had taken the thread's lease, by the clock of the instance that started it, which is the clock that run's note in the index is written by (research R7). Null in the moment between acceptance and that, during which the task is working whatever the thread says. |
| `base_checkpoint` | text, null | The id of the thread's last checkpoint when the run took the lease; null for a thread that had none. A thread still at this checkpoint has recorded nothing of the task. |
| `ended_at` | timestamptz, null | Set with every state other than `working`. |
| `released_at` | timestamptz, null | When the task stopped counting against the limits. |
| `reject_attempts` | integer, not null, default 0 | How often a proposal of the thread was rejected by rule for this task. |
| `reject_after` | timestamptz, null | Not before when the next such attempt may be made. |

Indexes: (`caller`, `context_id`, `created_at`), for a conversation's last task; (`thread_id`);
and one on `released_at` where it is null, for the counts.

A task's message is not kept here: it is in the thread.

### States

```text
            accepted
               │
               ▼
           working ──────────────┬───────────────┬──────────────┐
   (a run in progress,           │               │              │
    a proposal pending,          ▼               ▼              ▼
    or an agent task awaited) completed        failed        canceled
```

- **working → completed, failed, canceled** by the thread's standing (research R7), written
  once by whichever instance looks first. The write names the state it expects to replace,
  so that two instances that look together write one end.
- **working → canceled** by its caller's cancel, at once (research R11).
- A row whose `state` is not `working` never changes again, apart from `released_at` and the
  two columns that count rejections by rule.
- A row that is `working` with `started_at` null for more than a minute is failed as
  interrupted: the process died between accepting the message and starting its run.

### `released_at`

A task counts against the two limits of FR-025 while `released_at` is null.

- A task that completes or fails is released in the same write: its thread has nothing in
  progress, pending, or awaited, which is what ended it.
- A task that ended as canceled, by its caller or by a stop, is released when its thread has
  no run in progress and no agent task awaited. A proposal that is still pending does not
  keep it counting (FR-025).

Whether a conversation takes another message (FR-010) is not read from this column: it is
decided when the message's run takes the thread's lease, from the thread itself (below).

### Reasons

| Code | State | Sentence sent to the caller |
|---|---|---|
| `no_usable_model` | failed | No usable model is active. |
| `model_error` | failed | The model could not be used. |
| `step_limit` | failed | The run reached its step limit. |
| `interrupted` | failed | The run was interrupted. |
| `internal` | failed | An internal error. |
| `stopped` | canceled | The run was stopped before it finished. |
| `canceled` | canceled | The caller canceled the task. |

A task the service cancels because the settings no longer name its caller has the code
`canceled` as well: nobody is left to read it, and the log says why.

A code the index notes for a failed run and that is not in this table is `internal`. The
note's own message is never sent to a caller (FR-019).

## Accepting a message

One transaction, under one advisory lock for the whole table pair:

1. Find the conversation (`caller`, `context_id`), or make it with a new `thread_id`.
2. When the conversation existed: its last task must not be `working`, or the message is
   refused as "not now".
3. Count the rows with `released_at` null, for the caller and in all, after the rows that
   still count were looked at once (research R7): at the limit, refused.
4. Write the task's row, `working`, with `started_at` null.

Then the run is started on the thread, by the function the thread route uses. It takes the
lease, and under it refuses a thread that is not at rest: a run in progress, a proposal
pending, or an agent task awaited. When it refuses, the task's row is removed and the
message is refused as "not now"; a conversation row made in step 1 stays, with a thread that
has no state. When it has the lease, `started_at` and `base_checkpoint` are written.

## What is derived and not stored

- **The status message** of a working task: "waiting for a person's approval of ...",
  "waiting for agent ...", or the last step's names. It is worded when the task is read,
  from the thread's standing, and for a follower from the run's events.
- **The artifact** of a completed task: one, `reply`, whose text is `result`.
- **A task's history**: none is returned.

## Read by other parts

| Reader | What it asks |
|---|---|
| `Runtime.begin_run`, `begin_read`, `SegmentedGraph` | Which caller started this thread, if any; the hand-over count of the conversation's last task. |
| The thread routes | Whether a thread is a caller's, to refuse a message; whether its conversation has a working task, to refuse an approval when it has none. |
| `UnattendedRuns.should_stop` | Whether the last task of a caller's thread was canceled by its caller, at each boundary of a console run on it. |
| The sweep, every 30 seconds | Every row with `released_at` null; in its first round, the working rows of callers the settings no longer name. |
| Startup, with the feature off | Whether any conversation exists; while one does, which caller started a thread, to refuse to run it. |
| `GET /threads`, `GET /threads/{id}` | For the listed threads: the caller's name, and whether its last task was canceled by the caller. |
| The lease's release | The conversation of a thread, to look at its task. |

## Files

`<FREYJA_A2A_SERVER_FILES_DIR>/<caller name>/` is a named caller's file area: made when a run
of the caller's first writes there, never removed by the service, and shared by the caller's
conversations. Nothing of it is in a table.
