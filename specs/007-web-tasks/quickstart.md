# Quickstart: Tasks Section of the Web Console

Validation guide for the feature. Each section maps to a user story in [spec.md](spec.md). The
routes are in [contracts/openapi.yaml](contracts/openapi.yaml), what their stream adds in
[contracts/sse-events.md](contracts/sse-events.md), and the table in
[data-model.md](data-model.md).

The actions proposed here are `send_notification`, Freyja's placeholder, which only writes a
log line, and hand-offs to `tests/a2a_stub.py`, the suite's stand-in agent, which does nothing
outside its own process. Do not follow these steps with an agent or a tool server you would
not hand work to: an action that is approved here really runs.

## Prerequisites

- Everything the [001 quickstart](../001-thread-runs-api/quickstart.md) needs: Docker with
  Compose v2, `uv`, and a model provider key in `.env` for the environment's model. A model
  that calls tools is needed for sections 2, 4, and 5; the stand-in model endpoint
  (`tests/llm_stub.py`) answers in text only.
- Node 20.9 or newer and `pnpm`, for the web app.
- `curl` and `jq`.
- `.env` with no `FREYJA_A2A_AGENTS` and no `FREYJA_MCP_SERVERS` to begin with.

## Setup

```bash
uv sync
docker compose up -d postgres

# Terminal 1: the service
uv run --env-file .env freyja-api

# Terminal 2: the web app (add -p <port> if 3000 is taken)
cd web && pnpm install && pnpm dev               # http://localhost:3000
```

The commands below call the service on `localhost:8000`.

## 1. Hand Freyja a task and read the result (User Story 1)

Open `http://localhost:3000` and choose Tasks in the sidebar.

- The page says that there is no task yet and offers New task. It also says that everyone
  who can open the console sees every task and can decide every proposal.

Press New task, type `Summarize what you can do and which skills you have loaded.`, and send.

- The address changes to `/tasks/<id>`, and the reply appears as it is written, before the
  run has ended. When it has ended, the page shows the message, the reply, and the status
  done.

```bash
curl -s localhost:8000/threads | jq '{needs_decision, total, threads: [.threads[] | {thread_id, first_message, status}]}'
# one thread, done
```

Reload the page: the same conversation. Text is shown as text: a reply that holds markup or a
link shows its characters and nothing to click.

Without a usable model: in the LLM section, activate a profile that is not usable, then send
a new task. The page says that no usable model is active and points to the LLM section, the
text is still in the box, and `GET /threads` lists no new thread. Activate the usable profile
again.

## 2. Decide an action (User Story 2)

Send the task `Tell #ops that the nightly report is ready.`

- The run pauses. A card names `send_notification`, with `channel` and `message` in full, and
  offers Approve, Edit, and Reject. The service's log has no line from the action.

Approve. The continuation streams on the same page; the log has one line with `#ops`.

Send the same task again. This time choose Edit, change the channel to `#ops-staging`, and
approve: the log line says `#ops-staging`, and the conversation shows that a person changed
the arguments. Send it a third time and reject with a reason: no log line, and Freyja says
the action was not taken and repeats the reason.

Refusals, each of which leaves the proposal in place:

- Edit the arguments to `{"channel": "#ops"}`, which lacks `message`: the service's reason is
  shown beside the box, which keeps the text.
- Edit them to text that is not JSON: the page does not send it.
- Open a paused task in two windows, approve in one, then approve in the other: the second
  says the proposal is no longer the pending one and shows the task as it is.

Pressing Enter in the edit box, or in the reason, sends no decision.

## 3. The list and the number (User Story 3)

With a task paused on a proposal, open the LLM section.

- The sidebar's Tasks entry shows 1.

Open Tasks: the paused task is first, with "Needs your decision"; the others follow, newest
first, each with the beginning of its first message, its status, and when it last changed.

Decide the proposal from the task's page and go back to the LLM section: within fifteen
seconds the number is gone, without a reload.

A thread started by another client is a row like any other:

```bash
curl -sN -X POST localhost:8000/threads/by-curl/runs -H 'content-type: application/json' \
  -d '{"message": "Say hello."}' > /dev/null
curl -s localhost:8000/threads/by-curl | jq '{status, stoppable, first_message}'
# done, false, "Say hello."
```

Stop the service: within fifteen seconds the section says it cannot be reached and shows no
row and no number. Start it again.

## 4. Continue a task (User Story 4)

On a task that is done, send a follow-up that refers to its first message: the reply reflects
it. On a task that is paused, the follow-up box cannot be sent and says why.

Agent tasks, with the stand-in agent:

```bash
# Terminal 3
uv run python -m tests.a2a_stub --http 9998 --name stub
```

Add it in the Agents section as `stub` at `http://127.0.0.1:9998`. Send the task
`Hand this to the stub agent: slow 40`, and approve the hand-off.

- The task's page lists one agent task for `stub`, pending, with the message it was started
  with. In the list the task is "Waiting for an agent".
- About forty seconds later, without a reload, the conversation shows the outcome and the
  agent task as completed.

## 5. Leave, come back, stop (User Story 5)

Send `Hand this to the stub agent: slow 20`, approve, and close the tab at once.

```bash
curl -s localhost:8000/threads | jq '[.threads[] | {first_message, status, stoppable}][0]'
# running, stoppable true: nobody is reading the stream
```

Open the console again half a minute later: the task is where it would be had the tab stayed
open, and its conversation is complete.

Stop. Send a task that takes many steps, such as
`List the workspace, then read every file under skills one by one and summarize each.`, and
press Stop while it runs.

- The page says the run is stopping, then shows the task as stopped. The step that was under
  way is in the conversation with its result; nothing after it ran.
- The follow-up box can be used: `Go on.` continues the task.

```bash
curl -s -X POST localhost:8000/threads/by-curl/stop | jq .detail.code
# "no_run"
curl -sN -X POST localhost:8000/threads/own-stream/runs -H 'content-type: application/json' \
  -d '{"message": "List the workspace, then read every file under skills one by one."}' > /dev/null &
curl -s -X POST localhost:8000/threads/own-stream/stop | jq .detail.code
# "not_stoppable": a client that reads its own stream stops its run by closing it
```

## 6. Another origin (User Story 6)

```bash
curl -s -X POST localhost:8000/threads/by-curl/stop -H 'sec-fetch-site: cross-site' | jq .detail.code
# "other_origin"
curl -s -X POST localhost:8000/threads/x1/runs -H 'sec-fetch-site: cross-site' \
  -H 'content-type: application/json' -d '{"message": "hi"}' | jq .detail.code
# "other_origin"
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/threads/x1/runs \
  -H 'content-type: text/plain' -d '{"message": "hi"}'
# 422: a body that is not sent as JSON
```

## 7. The stream through the console (research R1)

That a run's events reach the page one by one, and not all at once at the end, can be seen
with the service's own answer:

```bash
curl -sN -X POST localhost:3000/api/threads/s1/runs/unattended -H 'content-type: application/json' \
  -H 'accept-encoding: gzip' -d '{"message": "Count slowly from one to twenty."}' -D - -o /dev/null | grep -i 'cache-control\|content-encoding'
# cache-control: no-store, no-transform, and no content-encoding
```

In the browser, the reply of section 1 growing while the run is in progress is the same proof.

## Cleanup

```bash
docker compose stop postgres     # only if nothing else uses it
```

## Automated checks

```bash
uv run pytest --cov
uv run ruff check . && uv run ruff format --check .
cd web && pnpm lint && pnpm test:coverage && pnpm test:e2e
```

The opt-in Postgres tests cover the index's own statements:

```bash
docker compose exec postgres createdb -U freyja freyja_test
FREYJA_TEST_DATABASE_URI=postgresql://freyja:freyja@localhost:5432/freyja_test uv run pytest tests/integration
```
