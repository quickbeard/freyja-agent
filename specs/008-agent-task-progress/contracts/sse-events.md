# Contract: Run Event Stream, changes in feature 008

Changes to [003's run event stream](../../003-a2a-agent-delegation/contracts/sse-events.md).
Event kinds, their shapes, their order, and the terminal events are unchanged. One rule
changes: when an `update` with `node: "agent"` is sent for an agent that does not stream.

## `update` with `node: "agent"`, for an agent whose card says it does not stream

A hand-off to such an agent follows the task by reading its status, about once a second,
until the task stops or the call time limit passes. Feature 003 sent one event per change of
state the hand-off observed. From this feature on it sends one event when a read finds either
of:

- another state than the read before, as before, whatever the text; or
- the same state with a text that is not blank and is not the text the hand-off last saw
  (FR-019).

A read that finds the same state and the same text, or no text, sends nothing. A read with
no text does not change what the hand-off last saw, so a text that comes back after a status
without one is not sent again.

```json
{"node": "agent", "data": {"agent": "hello", "task_id": "3f1c...", "state": "working", "text": "One quarter done"}}
{"node": "agent", "data": {"agent": "hello", "task_id": "3f1c...", "state": "working", "text": "Half done"}}
```

The keys and their meaning are 003's. `text` is the update's text as 003 produces it and is
not cut to the 2,000 characters of a kept progress text; it is the agent's own and is
untrusted.

Text that an agent sets and replaces between two reads is not seen. No interval changes.

## Unchanged

- **An agent that streams**: one event per status update the agent sends, in the order it
  sent them (FR-020).
- **A pending task**: no progress event is sent for it. Its progress is read from the
  thread's state ([openapi.yaml](openapi.yaml)), and its outcome reaches the thread as
  before.
- **The orchestrator's check**: its result is an ordinary tool result in an `update` of the
  `tools` node, as any tool's. What it holds is in
  [check-agent-tasks.md](check-agent-tasks.md).
