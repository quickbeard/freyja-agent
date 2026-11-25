# Contract: Run Event Stream, changes in feature 009

Changes to [001's run event stream](../../001-thread-runs-api/contracts/sse-events.md), as
features 003, 005, 007, and 008 left it. Event kinds, their order, and the terminal events
are unchanged. One key is added, on the runs of a caller's thread.

## `message`: `metadata.reply`

A `message` event carries a piece of a message as it is produced. Until now nothing in the
event said what the piece belongs to: the pieces of a subagent's reply, the messages a tools
step returns, the arguments of a call as they are written, and the model call that
summarizes a long conversation are sent beside the text of Freyja's own reply. From this
feature on, for a run on a thread a caller started:

```json
{"message": {"type": "ai", "content": "Half", "id": "..."}, "metadata": {"langgraph_node": "model", "langgraph_step": 3, "reply": true}}
```

`reply` is `true` for a piece that is text of Freyja's own reply: an AI chunk, from the
`model` node of Freyja's own graph and not of a subagent's at any depth, and not from a call
the service's libraries make for themselves. It is `false` for every other piece.

The key is on the `message` events of a run on a caller's thread, whichever route streams
it: the continuation a decision starts through `POST /threads/{thread_id}/resume` or
`/resume/unattended` has it too. The events of every other thread's runs are unchanged, so
with the feature off no event has the key. By the compatibility rule of feature 001, a
client ignores a key it does not recognize.

The A2A service sends a caller the text of the pieces whose `reply` is `true`, and of no
other ([a2a-server.md](a2a-server.md)).
