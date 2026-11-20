# Run stream: what feature 007 adds

The run stream is defined in
[001's contract](../../001-thread-runs-api/contracts/sse-events.md), with the progress events
of [003](../../003-a2a-agent-delegation/contracts/sse-events.md), the `start.profile` field
and `no_usable_model` code of [004](../../004-web-llm-profiles/contracts/sse-events.md), and
the `approval_now_required` code of
[005](../../005-web-delegate-agents/contracts/sse-events.md).

This feature changes nothing in the streams of `POST /threads/{thread_id}/runs` and
`POST /threads/{thread_id}/resume`. It adds two routes that answer with the same stream,
`POST /threads/{thread_id}/runs/unattended` and `POST /threads/{thread_id}/resume/unattended`
([openapi.yaml](openapi.yaml)). Their events are the existing ones, made by the same code.
Four things differ, and all four are about these two routes only.

## The run does not depend on the stream

A client that closes the connection, or never reads it, ends nothing. The run goes on to its
terminal event, which is then sent to nobody, and the thread's state and its row
(`GET /threads/{thread_id}`) say how it ended. This replaces "Client disconnect" of 001's
contract for these two routes.

A stream can therefore end without a terminal event for a reason that is not the run's: the
connection dropped, something between the client and the service closed it, or the client
did not keep up and the service closed it with 1,000 events waiting. A client
treats that as "not known" and reads the thread again; it never treats it as a failed run.

## `end` can say `stopped`

```json
{"status": "stopped"}
```

Sent in place of `{"status": "completed"}` when the run ended because a stop was asked for
(`POST /threads/{thread_id}/stop`) and applied. The step that was under way is in the
thread's state; no later step started. A run that finished, paused, or failed before the stop
was applied ends with the event it would have had.

`end` is still the terminal event, and the stream still ends in exactly one of `end`,
`interrupt`, or `error`.

## Headers

```text
Cache-Control: no-store, no-transform
```

`no-transform` is there for the web console: Next.js compresses what it forwards and holds a
compressed stream back until it ends, and leaves a response alone that says this (research
R1). The existing routes keep `Cache-Control: no-store`.

## Ping

A `: ping` comment line is sent every 10 seconds, where the existing routes send one every 15
seconds. The console's forwarding ends a stream that is silent for 30 seconds (research R1);
two pings fall inside every such window.

## The step limit

A run through these routes reports `step_limit` at the last pause between steps inside
`FREYJA_MAX_STEPS`, which can be up to two steps earlier than the same run through the
existing routes. The limit is never exceeded (research R4).
