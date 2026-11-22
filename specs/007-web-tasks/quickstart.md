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
  that calls tools is needed for sections 2, 4, and 5.
- Or no provider key at all: the stand-in model endpoint (`tests/llm_stub.py`), under its
  model id `call-tools`, asks for tools by a keyword at the start of a message. Serve it
  with `uv run --env-file .env python -m tests.llm_stub --http 9997`, and either save and
  activate a profile in the LLM section (provider `openai`, model `call-tools`, base URL
  `http://127.0.0.1:9997/v1`, any key, the Responses API off), or start the service with
  `FREYJA_MODEL=openai:call-tools`, `OPENAI_BASE_URL=http://127.0.0.1:9997/v1`, any
  `OPENAI_API_KEY`, and `FREYJA_OPENAI_RESPONSES_API=false`. Then send these in place of
  the sentences below, which are written for a model that reads them:
  - `notify The nightly report is ready.` where a notification to `#ops` is asked for;
  - `handoff stub slow 40` and `handoff stub slow 20` where work is handed to the agent
    `stub`;
  - `steps 8` where a task of many steps is asked for: eight steps, a second apart.

  Such a model answers `ready` to anything else, so the replies of sections 1 and 4 say
  nothing of what was asked: what those sections show of the console holds all the same.
- Node 20.9 or newer and `pnpm`, for the web app.
- `curl` and `jq`.
- `.env` with no `FREYJA_A2A_AGENTS` and no `FREYJA_MCP_SERVERS` to begin with, and with
  `FREYJA_SECRET_KEY` set, for the profile section 1 saves.

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

Without a usable model. A profile that is not usable cannot be activated; the state arises
when the active profile stops being usable, as when the key it was saved with can no longer
be read. To see it: save a profile in the LLM section and activate it, then start the
service again with another `FREYJA_SECRET_KEY`. The LLM section shows the profile as active
and not usable. Send a new task: its page says that the run failed because no usable model
is active, points to the LLM section, and offers the text again in a box with Send again;
`GET /threads` lists no new thread. Start the service with the first key again: the profile
is usable once more, with nothing to activate. Press Send again: the task runs.

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
- Edit them to text that is not JSON: the button that would send it cannot be pressed, and
  the page says that the text is not valid JSON.
- Open a paused task in two windows and approve in one. A window that is given the focus
  reads its task again and then shows no card, so to send the second decision from a page
  that still shows the card, press its Approve as the first thing in the other window, or
  send the decision with curl. The second decision changes nothing: the page says that
  nothing is pending on the task any more, since the proposal was decided elsewhere, and
  shows the task as it is. While the first window's continuation is still in progress it
  says that a run is in progress and the decision was not applied, and when another
  proposal has taken the first one's place, that the proposal is no longer the pending one.

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
`Hand this to the stub agent: slow 40`, and approve the hand-off, whose card says that it
is sent to the agent `stub`.

- The run waits for the agent for thirty seconds, the limit of one hand-off
  (`FREYJA_A2A_CALL_TIMEOUT_SECONDS`), and then ends. From then on the task's page lists one
  agent task for `stub`, pending, with the message it was started with, and says that it
  reads the task again every 12 seconds. The task's status, on its page and in the
  list, is "Waiting for an agent".
- About a minute after the approval, without a reload, the conversation shows the outcome
  and the agent task as completed: the agent takes forty seconds, the service looks every
  ten (`FREYJA_A2A_POLL_INTERVAL_SECONDS`) and records the outcome in a run of its own, and
  the page reads every twelve.

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
  way is in the conversation: a reply of Freyja's as it was written, or the actions of one
  round with their results. Nothing after it ran. When the step under way was a reply that
  asks for an action that needs no approval, that reply is the last entry and the action
  did not run.
- When the run ends by itself before the stop takes effect, the task is shown as it ended,
  never as stopped, and the page says that the run ended before the stop took effect. A run
  ends so when the reply under way asks for nothing more, when it asks for an action that
  needs approval, on whose proposal the run pauses, and when it fails.
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
curl -sI localhost:3000/tasks | grep -i 'content-security-policy\|x-frame-options'
# frame-ancestors 'none' and DENY: no page of another origin can show the section in a frame
```

The same is refused when it is sent through the console's address, `localhost:3000/api/...`,
by a page of another origin.

A page that reaches the service or the console under a name its own author controls (DNS
rebinding) is the same origin to the browser, so the refusal above does not see it. It is
refused by the name it asks for, a request that only reads included:

```bash
curl -s -H 'Host: attacker.example:8000' localhost:8000/threads
# 403 unknown_host
curl -s -H 'Host: attacker.example:3000' localhost:3000/api/threads
# 403 unknown_host: the console's server passes the name on
curl -s -X POST -H 'Host: attacker.example:3000' -H 'Origin: http://attacker.example:3000' \
  -H 'Sec-Fetch-Site: same-origin' -H 'Content-Type: application/json' \
  -d '{"message": "hi"}' localhost:3000/api/threads/t-rebound/runs/unattended
# 403 unknown_host, and GET /threads lists no such thread
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
