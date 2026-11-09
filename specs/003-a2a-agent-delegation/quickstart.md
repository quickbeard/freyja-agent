# Quickstart: A2A Agent Delegation

Validation guide for the feature. Each scenario maps to a user story in [spec.md](spec.md). The
changed API shapes are in [contracts/openapi.yaml](contracts/openapi.yaml), the agent list in
[contracts/agent-config.md](contracts/agent-config.md), the progress event in
[contracts/sse-events.md](contracts/sse-events.md), and the entities in
[data-model.md](data-model.md).

Two agents are used. `hello` is the A2A project's own reference agent, run from its official
samples at a pinned commit; it answers every message at once. `stub` is the suite's stand-in
agent served on loopback; it can ask a question, run long, fail, or reject, chosen by a keyword
in the task message. No published agent runs locally without a key, so the reference agent is
used in its place, as decided at plan review ([research R17](research.md#r17-the-quickstart-agent)).

## Prerequisites

- Everything the [001 quickstart](../001-thread-runs-api/quickstart.md) needs: Docker with
  Compose v2, `uv`, and a model provider key in `.env`.
- `curl` and `jq`.
- No account or token. Nothing below edits a credential (SC-008).

## Setup

```bash
uv sync
docker compose up -d postgres

# The reference agent: two files from the official samples, at a pinned commit.
mkdir -p /tmp/freyja-hello && cd /tmp/freyja-hello
base=https://raw.githubusercontent.com/a2aproject/a2a-samples/1bb0871294/samples/python/agents/helloworld
curl -sSfO "$base/__main__.py" && curl -sSfO "$base/agent_executor.py"
cd -
```

Start both agents, each in its own terminal:

```bash
# Terminal A: the reference agent on 127.0.0.1:9999.
cd /tmp/freyja-hello && uv run --no-project --with 'a2a-sdk[http-server]==1.1.0' --with uvicorn python __main__.py

# Terminal B: the stand-in agent on 127.0.0.1:9998.
uv run python -m tests.a2a_stub --http 9998
```

Add three lines to `.env`. The short call limit makes a slow task become pending quickly:

```bash
FREYJA_A2A_AGENTS='{"hello": {"url": "http://127.0.0.1:9999", "read_only": true}, "stub": {"url": "http://127.0.0.1:9998"}}'
FREYJA_A2A_CALL_TIMEOUT_SECONDS=5
FREYJA_A2A_POLL_INTERVAL_SECONDS=5
```

Start the service:

```bash
uv run --env-file .env freyja-api
```

Expected: after the persistence line, one line per agent names it, says whether it is
read-only, and gives the number of skills on its card, such as
`Agent hello: read-only, 1 skill` and `Agent stub: gated, 2 skills`. Then the service listens
on 127.0.0.1:8000 (User Story 1, scenario 1).

## Scenario 1: agents in the catalog (User Story 5)

```bash
curl -s localhost:8000/tools | jq '{agents, agent_tools: [.tools[] | select(.agent != null)]}'
```

Expected:

- `agents` has `hello` and `stub`, both `reachable: true`, with each card's name, description,
  version, and skills under `card`. No address appears anywhere in the response.
- `handoff_to_hello` and `cancel_hello_task` are `read_only` with `agent: hello`;
  `handoff_to_stub` is `gated` with approve, edit, and reject; `cancel_stub_task` is `gated` with
  approve and reject.
- The tools from before this feature are unchanged.

## Scenario 2: a read-only hand-off (User Story 1)

```bash
curl -N -X POST localhost:8000/threads/a2a-1/runs \
  -H 'content-type: application/json' \
  -d '{"message": "Ask the hello agent to say hello to the ops team."}'
```

Expected: an `update` with the `handoff_to_hello` call, one or more `update` events with
`node: "agent"` and `agent: "hello"` (the agent's "Processing request..." first), an `update` of
the `tools` node whose result contains "Hello, World! I have received your request", no
`interrupt`, and `event: end`. The reply relays the greeting in Freyja's own words.

```bash
curl -s localhost:8000/threads/a2a-1/state | jq '.agent_tasks'
```

Expected: one task for `hello`, `status: completed`, `ended_at` set, `outcome_delivered: true`.

## Scenario 3: a gated hand-off (User Story 2)

```bash
curl -N -X POST localhost:8000/threads/a2a-2/runs \
  -H 'content-type: application/json' \
  -d '{"message": "Hand this to the stub agent: summarize last week'"'"'s deployments."}'
```

Expected: the stream ends with `event: interrupt`; the action request names
`handoff_to_stub`, `agent: stub`, `server: null`, and shows `message` and `context`. The stub's
terminal (B) shows no request yet.

Approve, with an edit:

```bash
id=$(curl -s localhost:8000/threads/a2a-2/state | jq -r .pending.interrupt_id)
curl -N -X POST localhost:8000/threads/a2a-2/resume \
  -H 'content-type: application/json' \
  -d "{\"interrupt_id\": \"$id\", \"decisions\": [{\"type\": \"edit\", \"args\": {\"message\": \"Summarize last week's deployments in three bullets.\", \"context\": null, \"task_id\": null}}]}"
```

Expected: the stub logs one received message with the edited text, and the stream ends with
`event: end`. An edit that sets a `task_id`, an empty `message`, or a key such as `agent` is
refused with 422 and the proposal stays (User Story 2, scenario 3). On a new thread, a reject
with a reason sends nothing and the reply repeats the reason (scenario 4).

## Scenario 4: the same conversation, across a restart (User Story 1)

On thread `a2a-2`, ask a follow-up and approve it the same way:

```bash
curl -N -X POST localhost:8000/threads/a2a-2/runs \
  -H 'content-type: application/json' \
  -d '{"message": "Ask the stub agent which conversation this is."}'
```

Stop the service (Ctrl+C), start it again, and ask once more on `a2a-2`. Expected: every
message the stub received for `a2a-2` carries the same context id; the stub replies with it,
so the replies show the same id. A message on a new thread `a2a-3` gets a different one.

## Scenario 5: the agent asks a question (User Story 3)

```bash
curl -N -X POST localhost:8000/threads/a2a-4/runs \
  -H 'content-type: application/json' \
  -d '{"message": "Hand this to the stub agent exactly as written: ask: plan the release"}'
```

Approve the hand-off. Expected: the continuation's `tools` result is marked as needing input
with the stub's question, the reply asks you that question in Freyja's voice, and
`agent_tasks` lists the task as `input_required`.

```bash
curl -N -X POST localhost:8000/threads/a2a-4/runs \
  -H 'content-type: application/json' \
  -d '{"message": "Friday at 10."}'
```

Expected: the run pauses on `handoff_to_stub` with the answer as the orchestrator wrote it and
the same `task_id`; approve it, and the stub finishes the same task. The task is `completed`.

## Scenario 6: a long-running task (User Story 4)

```bash
curl -N -X POST localhost:8000/threads/a2a-5/runs \
  -H 'content-type: application/json' \
  -d '{"message": "Hand this to the stub agent exactly as written: slow 60: compile the weekly report"}'
```

Approve the hand-off. Expected: after about 5 seconds the `tools` result is a note that the task
is pending, the run ends with `event: end`, and `agent_tasks` lists the task as `working` with
`outcome_delivered: false`.

Stop the service now, wait until the stub's terminal shows the task finished (about a minute),
and start the service again. Within one poll interval:

```bash
curl -s localhost:8000/threads/a2a-5/state | jq '{task: .agent_tasks[0], last: .messages[-3:]}'
```

Expected: the task is `completed` with `outcome_delivered: true`; the messages end with a
`check_agent_tasks` call, its result holding the stub's report, and the reply from a background
run. Exactly one such result is in the thread (User Story 4, scenarios 2 and 6).

A message sent on a thread whose task finished while the thread was idle gets the outcome in
that run instead (scenario 11): start another `slow 30` task on `a2a-6`, wait until it is done,
and send "Is the report ready?" before the next poll. The reply answers with the report, and no
background run adds it again.

## Scenario 7: cancel and give up (User Story 4)

Start `slow 600` on thread `a2a-7` and approve it. Then:

```bash
curl -N -X POST localhost:8000/threads/a2a-7/runs \
  -H 'content-type: application/json' \
  -d '{"message": "Cancel that task."}'
```

Expected: the run pauses on `cancel_stub_task` with the task id and offers approve and reject
only; approve, and the task is listed as `canceled`.

To see a give-up, restart the service with `FREYJA_A2A_TASK_MAX_AGE_SECONDS=20`, start
`slow 600` on `a2a-8`, and wait half a minute. Expected: a background run reports the task as
given up, says the agent may still be working, and offers a cancel; the task is `given_up`; the
stub received no cancel.

## Scenario 8: a misbehaving agent (User Story 6)

Each of these, set in `.env` one at a time, stops startup with an error naming the agent and
the reason, and the service does not listen:

| `FREYJA_A2A_AGENTS` | Expected |
|---|---|
| `{"hello": {"url": "http://127.0.0.1:9997"}}` (nothing listens) | `Agent 'hello'`: card could not be read |
| `{"hello": {"url": "http://10.0.0.5:9999"}}` | settings validation error for `a2a_agents.hello`, before startup |
| `{"git": {"url": "http://127.0.0.1:9999"}}` with a tool server named `git` | the name is already a tool server |

Stop the stub (Ctrl+C in terminal B) while the service runs and hand it a task on a new thread.
Expected: the hand-off returns an error result naming `stub`, the run ends with `event: end`,
the log names the agent, and `/tools` shows `stub` with `reachable: false`. Start the stub again
and hand off once more: it succeeds without restarting the service, and `/tools` shows it
reachable (User Story 6, scenario 5).

## Scenario 9: the suite and the gates (User Story 7)

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run pre-commit run --all-files
```

Expected: all pass with the network disconnected and no agent running. The suite talks to the
stand-in agent inside the test process; one transport test serves it on loopback (SC-009).

## Cleanup

Stop both agents and the service, remove the three lines from `.env`, then:

```bash
rm -rf /tmp/freyja-hello
docker compose stop postgres
```
