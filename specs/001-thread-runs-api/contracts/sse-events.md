# Contract: Run Event Stream

Both `POST /threads/{thread_id}/runs` and `POST /threads/{thread_id}/resume` answer with
`Content-Type: text/event-stream`. Each event has an `event:` line naming its kind and a `data:`
line holding one JSON object. Clients filter on `event`.

Spec vocabulary mapping: progress event = `update`, message event = `message`, pause event =
`interrupt`, completion = `end`.

## Order guarantees

1. The first event is always `start`, sent before the orchestrator is invoked (SC-001).
2. Zero or more `update`, `message`, and (with `?debug=true`) `debug` events follow, in the
   order the orchestrator produced them.
3. Exactly one terminal event ends the stream: `end`, `interrupt`, or `error` (FR-003). The
   connection closes after it.
4. A `: ping` comment line may appear at any time; it carries no data.

## Events

### `start`

```json
{"thread_id": "inc-2041", "run_id": "0f3c...", "mode": "run", "debug": false}
```

`mode` is `run` or `resume`.

### `update`

One per orchestrator step. `node` is the graph node that ran; `data` is that node's state
update with messages serialized as [Message](../data-model.md#message-api-shape).

```json
{"node": "model", "data": {"messages": [{"type": "ai", "content": "", "tool_calls": [{"id": "call_1", "name": "isolate_host", "args": {"host": "ws-042", "reason": "C2 beaconing"}}]}]}}
```

```json
{"node": "tools", "data": {"messages": [{"type": "tool", "name": "isolate_host", "tool_call_id": "call_1", "content": "Isolation of ws-042 recorded (placeholder: logged only)."}]}}
```

Steps taken inside a subagent launched through the `task` tool are not separate `update`
events; the `task` call and its result are. The subagent's model output does appear in
`message` events.

### `message`

A token-level chunk of the orchestrator's reply as it is produced.

```json
{"message": {"type": "AIMessageChunk", "content": "Looking at", "id": "run-..."}, "metadata": {"langgraph_node": "model", "langgraph_step": 1}}
```

### `debug`

Only with `?debug=true`. The raw LangGraph debug chunk, JSON-serialized. Shape is not part of
the contract and may change with the underlying library.

### `interrupt` (terminal)

The run paused before executing a gated action. Same shape as `ThreadState.pending`.

```json
{
  "interrupt_id": "6b1e...",
  "action_requests": [
    {"name": "isolate_host", "args": {"host": "ws-042", "reason": "C2 beaconing"}, "description": "Isolate a host from the network (placeholder: logs only)."}
  ],
  "allowed_decisions": ["approve", "edit", "reject"]
}
```

### `error` (terminal)

```json
{"code": "step_limit", "message": "Run stopped after reaching the step limit of 50 (FREYJA_MAX_STEPS)."}
```

`code` is one of `step_limit`, `model_error`, `internal`. `message` is human-readable and never
contains secrets.

### `end` (terminal)

```json
{"status": "completed"}
```

## Client disconnect

If the client closes the connection, the server stops the run at the next step boundary,
releases the run lock, and writes no further events. Completed steps are already checkpointed.
