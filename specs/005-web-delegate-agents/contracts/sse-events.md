# Run stream: what feature 005 adds

The run stream is defined in
[001's contract](../../001-thread-runs-api/contracts/sse-events.md), with the progress events
of [003](../../003-a2a-agent-delegation/contracts/sse-events.md) and the `start.profile` field
and `no_usable_model` code of [004](../../004-web-llm-profiles/contracts/sse-events.md). This
feature adds one `error` code and changes no event's shape.

## `error` with `approval_now_required`

A resume is refused this way when the paused step holds a call that needed no decision when it
was proposed and needs one now, because an agent was switched to require approval in between
(spec FR-023). Until this feature the same situation, reachable only by a restart with another
configuration, ended with the code `internal` and no reason.

```text
event: start
data: {"thread_id":"t1","run_id":"...","mode":"resume","debug":false,"profile":{...}}

event: error
data: {"code":"approval_now_required","message":"This step cannot continue: it holds a message to agent 'stub' that needed no approval when it was proposed, and the agent now requires approval. Switch the agent back, or remove it, then decide the proposal again."}
```

- Nothing from the paused step ran, and the proposal is still pending: the thread state shows
  it as before.
- The message names each agent it found, by name, and never an address.
- When the service cannot tell which agent caused it, which is the case for a tool server's
  tool after a restart, the code is `internal` as before.
- The code is not one a background run is tried again for.

## Results of an agent's tools

Not events of their own: they arrive as the tool's result in `updates`, like any tool result.
Six results are new, each an error result after which the run continues and nothing was sent
to an agent. Their wording is in [data-model.md](../data-model.md#what-a-call-gives-without-sending).
None holds an address.
