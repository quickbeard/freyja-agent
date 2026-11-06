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
   connection closes after it. The one exception is a client that disconnects first: its
   stream ends with no terminal event (see Client disconnect).
4. A `: ping` comment line may appear at any time; it carries no data.

A failure found before the stream starts (an invalid request, a conflict, a thread that was
not found) is answered with an HTTP status and never with an event. Once the response is 200,
every failure is an `error` event.

## Stability

Part of the contract: the event kinds, the order guarantees above, the shapes of `start`,
`interrupt`, `error`, and `end`, and the [Message](../data-model.md#message-api-shape) shape.

Follows the underlying library and may change when it is upgraded: the node names in
`update` (`model`, `tools`, and the names of middleware nodes), the keys of an `update`'s
`data` other than `messages`, the values in a `message` event's `metadata`, and everything in
`debug`. New event kinds and new fields may be added; a client ignores what it does not
recognize.

## Events

### `start`

```json
{"thread_id": "inc-2041", "run_id": "0f3c...", "mode": "run", "debug": false}
```

`mode` is `run` or `resume`.

### `update`

One per orchestrator step that changed state. `node` is the graph node that ran (`model`,
`tools`, or a middleware node); `data` is that node's state update with messages serialized
as [Message](../data-model.md#message-api-shape). Steps that produce no state update are
not emitted.

```json
{"node": "model", "data": {"messages": [{"type": "ai", "content": "", "tool_calls": [{"id": "call_1", "name": "send_notification", "args": {"channel": "#ops", "message": "Nightly report is ready."}}]}]}}
```

```json
{"node": "tools", "data": {"messages": [{"type": "tool", "name": "send_notification", "tool_call_id": "call_1", "content": "Notification to #ops recorded (placeholder: logged only)."}]}}
```

Steps taken inside a subagent launched through the `task` tool are not separate `update`
events; the `task` call and its result are. The subagent's model output does appear in
`message` events.

### `message`

A chunk of the orchestrator's reply as it is produced: token-level for providers that
stream, one whole message per model call otherwise. The message uses the same
[Message](../data-model.md#message-api-shape) shape as state and `update` events, so a
chunk reports `type: "ai"`, not the chunk class name. `metadata` has two keys,
`langgraph_node` and `langgraph_step`.

Model output from a subagent arrives as `message` events too. Nothing in the event tells a
subagent's text from the orchestrator's in this increment; a client that needs only the final
reply reads it from the thread state after `end`.

```json
{"message": {"id": "run-...", "type": "ai", "content": "Looking at", "name": null, "tool_calls": []}, "metadata": {"langgraph_node": "model", "langgraph_step": 1}}
```

### `debug`

Only with `?debug=true`. The raw LangGraph debug chunk, JSON-serialized. Shape is not part of
the contract and may change with the underlying library.

Debug events are unredacted. They may carry anything held in the thread's state and the run's
internal records, including full messages and tool arguments. That is accepted in this
increment because the same reader can read the thread's state and there is no authentication.

### `interrupt` (terminal)

The run paused before executing a gated action. Same shape as `ThreadState.pending`.

```json
{
  "interrupt_id": "6b1e...",
  "action_requests": [
    {"name": "send_notification", "server": null, "args": {"channel": "#ops", "message": "Nightly report is ready."}, "description": "Send a notification to a channel (placeholder: logs only).", "allowed_decisions": ["approve", "edit", "reject"]}
  ],
  "allowed_decisions": ["approve", "edit", "reject"]
}
```

`interrupt_id` identifies the proposal; the resume request sends it back. `server` names the
tool server a proposed tool belongs to and is `null` for one of Freyja's own tools (added by
feature 002). Each action request
carries its own `allowed_decisions`, which is the list a decision for that action is checked
against. The top-level list is the union of them, for display.

### `error` (terminal)

```json
{"code": "step_limit", "message": "Run stopped after reaching the step limit of 50 (FREYJA_MAX_STEPS)."}
```

`code` is one of `step_limit`, `model_error` (an exception raised by the chat model: provider
API errors and timeouts), `internal` (anything else, including a database failure during the
run).

`message` is human-readable. The service puts no value of its own credential settings in it
(FR-004b). For `model_error` it includes the provider's error text unfiltered; what that text
contains is the provider's choice. For `internal` it is a fixed sentence, and the detail goes
to the service log.

When the step limit is reached at the step that proposes a gated action, the stream ends with
`step_limit` and not with `interrupt`: nothing is pending and the action does not run.

After an `error`, the thread state shows the steps completed before the failure. The state
has no field that marks the run as failed.

### `end` (terminal)

```json
{"status": "completed"}
```

## Client disconnect

If the client closes the connection, the server stops the run at the next step boundary,
releases the run lock, and writes no further events. Completed steps are already checkpointed.

The same holds for a continuation started by resume. Afterwards the thread state says where
things stand: if `pending` still shows the proposal, the action's step did not complete and
the proposal can be decided again; if `pending` is null, the decision is spent and the
action's result is in `messages`.
