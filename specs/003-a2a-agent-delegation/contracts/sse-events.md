# Contract: Run Event Stream, changes in feature 003

Changes to [001's run event stream](../../001-thread-runs-api/contracts/sse-events.md). Event
kinds, order guarantees, and terminal events are unchanged.

## `update` with `node: "agent"`

A progress update from an agent while a hand-off or answer is in progress within the run
(FR-007). It appears between the `update` that proposes or starts the hand-off and the `update`
of the `tools` node that carries the hand-off's result, in the order the agent sent the
updates. A hand-off made by a subagent reports progress the same way. For an agent whose card
says it does not stream, there is one event per status change Freyja observes when it checks
the task, about once a second.

```json
{"node": "agent", "data": {"agent": "hello", "task_id": "3f1c...", "state": "working", "text": "Processing request..."}}
```

| Key | Meaning |
|---|---|
| `agent` | the configured agent name |
| `task_id` | the agent's task id |
| `state` | `working`, `input_required`, `completed`, `failed`, `rejected`, or `canceled`, as the update reports it |
| `text` | the update's text, with non-text parts replaced by notes ([research R6](../research.md#r6-turning-a-reply-into-text-fr-006)); may be empty |

Part of the contract: `node: "agent"` and these four keys. The text is the agent's own and is
untrusted.

No progress event is sent while a task is pending: a pending task's later states reach the
thread through a background run or the next user run, and the thread state lists them.

## `start` mode `background`

A background run's `start` event has `mode: "background"`. No client receives it, because a
background run has no stream; it is listed here so the mode is reserved.

## Steps inside a subagent

Unchanged: a subagent's steps are not separate `update` events. The stream now reads the
subagent's progress events, which needs LangGraph's subgraph streaming; only `node: "agent"`
updates and `message` events come from inside a subagent.
