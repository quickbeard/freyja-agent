# Contract: Run Event Stream, changes in feature 004

Changes to [001's run event stream](../../001-thread-runs-api/contracts/sse-events.md), as
extended by [003](../../003-a2a-agent-delegation/contracts/sse-events.md). Event kinds, order
guarantees, and terminal events are unchanged. Both changes add; nothing is removed or renamed.

## `start` gains `profile`

The first event of every run names the LLM profile the run uses (FR-024a). That covers a run, a
continuation after a pause, and a background run.

```json
{"thread_id": "inc-2041", "run_id": "0f3c...", "mode": "run", "debug": false,
 "profile": {"id": "5b1f0c2e9a7d4e3fb6a81c0d2e4f6a8b", "name": "Local vLLM", "source": "saved"}}
```

| Key | Meaning |
|---|---|
| `id` | the profile's id; `environment` for the environment's profile |
| `name` | the profile's name when the run started |
| `source` | `saved` or `environment` |

The profile is the one active when the run starts and does not change during the run: a run in
progress finishes on the model it started with (FR-013). The service log has one line per run
with the same three values. Neither contains a key.

When no usable model is active, `profile` still names the active profile, and the run ends with
the error below.

## `error` gains `no_usable_model`

```json
{"code": "no_usable_model", "message": "No usable model is active. Activate a usable LLM profile, then send the message again."}
```

Sent as the terminal event of a run that starts while the active profile is not usable (FR-016):
its key cannot be read, no secret is set, or the environment names no usable model. The
orchestrator is not invoked, nothing is added to the thread, and the service does not switch to
another profile by itself. After it, the thread is as it was before the request, so the same
message can be sent again once a usable profile is active. A resume that ends this way leaves
the proposal pending.

`model_error` is unchanged in meaning: an exception raised by the chat model. From this feature
on its `message` has the active profile's key removed, should a provider repeat it (FR-021).
