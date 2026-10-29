# Quickstart: Thread Runs API

Validation guide for the feature. Each scenario maps to a user story in [spec.md](spec.md); the
request and event shapes are in [contracts/openapi.yaml](contracts/openapi.yaml) and
[contracts/sse-events.md](contracts/sse-events.md).

## Prerequisites

- Docker with Compose v2, `uv`, and a model provider key for one of the providers in `.env.example`.
- A clean clone. Nothing below edits a credential in the example configuration (SC-006).

## Setup

```bash
uv sync
cp .env.example .env            # pick one FREYJA_MODEL block and set its key; leave the database line as is
docker compose up -d postgres   # credentials match FREYJA_DATABASE_URI in .env.example
uv run --env-file .env freyja-api
```

Expected: the service logs that the checkpointer, store, and run-lock table are ready, then
listens on 127.0.0.1:8000. If Postgres is down, startup fails and the error names the database
connection (User Story 5, scenario 4).

## Scenario 1: stream a run (User Story 1)

```bash
curl -N -X POST localhost:8000/threads/demo-1/runs \
  -H 'content-type: application/json' \
  -d '{"message": "Summarize what you can do and which skills you have loaded."}'
```

Expected: the first line pair is `event: start` within a second, then `event: update` and
`event: message` lines, then `event: end` with `{"status": "completed"}` and the connection
closes. The reply mentions the committed placeholder skill, which proves skills loaded from
`workspace/skills/`.

Follow-up on the same thread:

```bash
curl -N -X POST localhost:8000/threads/demo-1/runs \
  -H 'content-type: application/json' \
  -d '{"message": "Repeat the first thing I asked you, word for word."}'
```

Expected: the reply quotes the first message. Restart the service and repeat; the reply still
quotes it (scenario 3).

Validation errors:

```bash
curl -s -X POST 'localhost:8000/threads/bad%20id/runs' -H 'content-type: application/json' -d '{"message": "x"}'   # 422, thread id
curl -s -X POST localhost:8000/threads/demo-1/runs -H 'content-type: application/json' -d '{"message": ""}'         # 422, empty message
```

## Scenario 2: the gated action (User Story 2)

```bash
curl -N -X POST localhost:8000/threads/task-42/runs \
  -H 'content-type: application/json' \
  -d '{"message": "Tell the #ops channel that the nightly report is ready."}'
```

Expected: the stream ends with `event: interrupt` whose data lists one action request named
`send_notification` with `channel` set to `#ops`, and `allowed_decisions` of approve, edit, reject. No
`send_notification` log line has been written yet.

Approve:

```bash
curl -N -X POST localhost:8000/threads/task-42/resume \
  -H 'content-type: application/json' \
  -d '{"decisions": [{"type": "approve"}]}'
```

Expected: the service log shows exactly one `send_notification` line for `#ops`; the stream shows a
`tool` message with the placeholder confirmation, then the orchestrator's acknowledgement, then
`event: end`.

Edit (new thread, same opening message, then):

```bash
curl -N -X POST localhost:8000/threads/task-43/resume \
  -H 'content-type: application/json' \
  -d '{"decisions": [{"type": "edit", "args": {"channel": "#ops-staging", "message": "Nightly report is ready (staging)."}}]}'
```

Expected: the log line names `#ops-staging`, not `#ops`.

Reject (new thread, same opening message, then):

```bash
curl -N -X POST localhost:8000/threads/task-44/resume \
  -H 'content-type: application/json' \
  -d '{"decisions": [{"type": "reject", "message": "Not needed, I will tell the team myself."}]}'
```

Expected: no `send_notification` log line; the orchestrator says the action was not taken and
repeats the reason.

Refusals:

```bash
curl -s -X POST localhost:8000/threads/demo-1/resume -H 'content-type: application/json' -d '{"decisions": [{"type": "approve"}]}'
# 409 {"detail": {"code": "nothing_pending", ...}}
curl -s -X POST localhost:8000/threads/task-45/resume -H 'content-type: application/json' -d '{"decisions": [{"type": "launch"}]}'
# 422 on a paused thread (unknown type); GET state still shows pending
curl -s -X POST localhost:8000/threads/task-45/runs -H 'content-type: application/json' -d '{"message": "never mind"}'
# 409 {"detail": {"code": "decision_pending", ...}}: decide through resume first; the proposal is not dropped
```

## Scenario 3: inspect state (User Story 3)

```bash
curl -s localhost:8000/threads/task-42/state | jq
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/threads/never-used/state   # 404
```

Expected: `messages` holds the full transcript including the `ai` message with `tool_calls` and
the `tool` result; `pending` is null after the resume and non-null on a thread that is paused;
`run_active` is false once the stream has closed.

## Scenario 4: decide after a restart (User Story 4)

1. Start a run on `task-46` that pauses on `send_notification`.
2. Stop the service (Ctrl-C) and start it again.
3. `GET /threads/task-46/state` shows the same `pending` proposal.
4. Resume with approve; the continuation completes and the log line appears.

## Scenario 5: debug stream and conflicts (User Story 5, FR-006)

```bash
curl -N -X POST 'localhost:8000/threads/demo-2/runs?debug=true' -H 'content-type: application/json' -d '{"message": "hello"}'
```

Expected: `event: debug` lines appear between the others; the reply is unchanged.

Conflict: start a long run on `demo-3` in one terminal and, while it streams, start another in a
second terminal.

Expected: the second returns 409 with code `run_active`; the first is unaffected. Kill the
service with SIGKILL while a run streams, restart it, and a new run on that thread is refused
until `FREYJA_RUN_LOCK_TTL_SECONDS` (default 60) has passed, then accepted.

## Step limit (FR-004a)

Run the service with `FREYJA_MAX_STEPS=2` and send any message that needs a tool call.

Expected: the stream ends with `event: error`, code `step_limit`, message naming the limit of 2.
The next run on the thread works.

## Automated checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest                                   # no model provider, no database needed
FREYJA_TEST_DATABASE_URI=postgresql://freyja:freyja@localhost:5432/freyja_test uv run pytest tests/integration
```

The integration tests create their own tables in the named database and are skipped when the
variable is unset.
