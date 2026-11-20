# Data Model: Tasks Section of the Web Console

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Research**: [research.md](research.md)

One table is added. Everything else a task shows, its conversation, its proposal, and its
agent tasks, is the thread's state as features 001 and 003 keep it, and is not changed.

## Thread record

One row per thread that has had a run since this feature was installed, or was opened since
(FR-004). Kept by
`ThreadIndex` (`runtime/thread_index.py`): `PostgresThreadIndex` in the table
`freyja_threads`, and `InMemoryThreadIndex` for the suite.

| Field | Type | Meaning |
|---|---|---|
| `thread_id` | text, primary key | The thread's id, as its client chose it. |
| `first_message` | text or null | The first 200 characters of the thread's first message from a person. Null until it is known. |
| `created_at` | timestamp | When the row was made: the thread's first run that the index saw. |
| `changed_at` | timestamp | When the thread last changed: the start of the run in progress, or the thread's last checkpoint as read when its last run was settled. |
| `run_owner` | text or null | The lease owner of the run that has not ended, `<instance>:<run>`. Null when no run is known to be in progress. |
| `run_unattended` | boolean | Whether that run, or the last one, was started through the unattended routes. Only such a run can be stopped. |
| `stop_owner` | text or null | The `run_owner` a stop was asked for. A stop counts only while it equals `run_owner`. |
| `noted` | text or null | How the last run ended as its reader saw it: `failed` or `stopped`. Written only for unattended runs. Cleared when a run starts. |
| `error_code` | text or null | With `noted = failed`: the code of the run's `error` event. |
| `error_message` | text or null | With `noted = failed`: the message of the run's `error` event, at most 2,000 characters, as it was sent. |
| `settled` | text or null | What the thread's state said when its last run was looked at: `paused`, `finished`, or `unfinished`. Null while a run is in progress and until its end has been looked at. |
| `agents_pending` | boolean | Whether an agent task of the thread was pending when the row was settled. |

Index: `(settled, changed_at DESC)`, for the list's order and the count of paused threads.

### Rules

- `thread_id` follows the service's rule for a thread id (001 FR-001a). The index never makes
  an id.
- `first_message` is plain text, cut by characters. A first message that is not text, which
  no client of this feature sends, is kept as an empty string.
- `error_message` is the stream's own text, from which `run_stream` has already taken the
  LLM profile's key (004 FR-021). The index adds nothing to it and cuts it at 2,000
  characters.
- A row is removed when it is settled and the thread has no state: its only run recorded
  nothing, so there is no task to list (FR-008).

### Lifecycle

```text
                    lease taken                      lease given back
   (no row) ────────────────────────▶ in progress ────────────────────────▶ ended
                run_owner set            │   ▲          run_owner cleared      │
                settled, noted cleared   │   │          settled still null     │
                changed_at = now         │   │                                 │ a list or a row is read:
                                         │   │ lease taken again               │ one read of the thread's
               noted, error set ─────────┘   │                                 ▼ state and agent tasks
               by the run's reader           └─────────────────────────────  settled
               (unattended runs)                                             paused | finished | unfinished
```

- **Lease taken** (`IndexedRunLocks.acquire`, when the inner lease was taken): the row is made
  if it is missing; `run_owner` is set, `stop_owner`, `noted`, the error, and `settled` are
  cleared, `run_unattended` is false, and `changed_at` is now.
- **Marked unattended** (the unattended routes, once the existing handler has accepted the
  run): `run_unattended` is true, and `first_message` is filled from the request when the row
  has none.
- **Noted** (`UnattendedRuns`, at the run's last event): `noted` and the error, for a run
  that ended with `error` or was stopped.
- **Lease given back** (`IndexedRunLocks.release`): `run_owner` is cleared when it is this
  run's. A row with no `run_owner`, whose run the index did not see start, has `settled`
  cleared, so that it is looked at again.
- **Settled** (the list and the row routes): for a row with no `run_owner` and no `settled`,
  and for a row whose `run_owner` holds no lease any more, the thread's state is read once.
  `settled` is `paused` when it has a proposal, `finished` when it has no step left to take,
  and `unfinished` otherwise; `changed_at`, `first_message`, and `agents_pending` are filled.
  The write is refused when a run has started meanwhile.
- **Opened** (the row route): a thread that has no row and has a state gets one, settled at
  once, with `created_at` and `changed_at` from its last checkpoint. When the thread's lease
  is held at that moment, the row is made without `settled`: it is `running` by the lease,
  and is settled once the lease is gone. This is how a thread
  from before this feature, or one whose row could not be written, comes into the list
  (FR-004, FR-023).

Every write that is for one run names the `run_owner` it is for, except the mark that a run
is a console run, which is written while that run holds the thread's lease. So a late write
for an earlier run changes
nothing of a later one.

### Several instances

The table is shared, like the lease. A run's row is written by the instance that runs it; any
instance settles it and any instance sets `stop_owner`. Two instances that settle the same
row read the same state and write the same values.

## Status

Worked out by the service from a row and returned in `ThreadSummary.status` (research R7).
First match:

| Order | Status | When |
|---|---|---|
| 1 | `running` | The thread's lease is held, on any instance. |
| 2 | `needs_decision` | `settled` is `paused`. |
| 3 | `failed` | `noted` is `failed`. |
| 4 | `stopped` | `noted` is `stopped`, or `settled` is `unfinished`. |
| 5 | `waiting_for_agent` | `agents_pending` is true. |
| 6 | `done` | Otherwise. |

`stoppable` is true when the status is `running`, `run_owner` is set, and `run_unattended`
is true. `stopping` is
true when, besides, `stop_owner` equals `run_owner`.

## Unattended run (in memory)

Not stored. `UnattendedRuns` holds, for each run it drives on this instance: the thread's id,
the task that reads the run's events, and the queues of the readers that are connected. A
reader that leaves is taken out, and so is one with 1,000 events waiting for it; the task
ends when the run's events end. The set of threads
with such a run is what `SegmentedGraph` asks to decide whether a run is run in segments.

## What the web app holds

Nothing is stored. In the page's memory, for as long as the tab is open:

- **Live run** (`web/lib/task-runs.ts`): for a run this tab started, the thread's id, the
  events received, and how the stream ended: its last event, or that the connection ended
  without one.
- What SWR keeps of `GET /threads`, `GET /threads/{id}`, and `GET /threads/{id}/state`.

A text typed into the new-task box or the follow-up box is kept only until it is sent or the
box is closed, and is put back when the service refuses it (FR-008).

## Existing entities this feature reads

- **Thread state** (001, 003): `messages`, `pending`, `run_active`, `updated_at`,
  `agent_tasks`. Read through `GET /threads/{thread_id}/state`, unchanged.
- **Run lease** (001): `freyja_run_locks`. Read through `RunLocks.is_active`; written only by
  the code that writes it today, through the wrapper.
- **Agent task** (003): read through `AgentTaskStore.list_tasks`, to learn whether one is
  pending.
