# Quickstart: Agents Section of the Web Console

Validation guide for the feature. Each scenario maps to a user story in [spec.md](spec.md). The
API shapes are in [contracts/openapi.yaml](contracts/openapi.yaml), the one new stream code in
[contracts/sse-events.md](contracts/sse-events.md), and the entities in
[data-model.md](data-model.md).

Every agent here is `tests/a2a_stub.py`, the suite's stand-in, served on loopback. It acts on
the first word of a message: `slow <seconds>` stays working that long, `ask` asks a question,
`fail` fails, and anything else completes at once; the list is at the top of the file. It
needs no account and does nothing outside its own process. Do not point these steps at an
agent you would not hand work to: a hand-off that is approved here is really sent.

## Prerequisites

- Everything the [001 quickstart](../001-thread-runs-api/quickstart.md) needs: Docker with
  Compose v2, `uv`, and a model provider key in `.env` for the environment's model.
- Node 20.9 or newer and `pnpm`, for the web app.
- `curl` and `jq`.
- `.env` with no `FREYJA_A2A_AGENTS` to begin with; section 6 adds one.

## Setup

```bash
uv sync
docker compose up -d postgres

# Terminal 1: the service
uv run --env-file .env freyja-api

# Terminal 2 and 3: two stand-in agents
uv run python -m tests.a2a_stub --http 9998 --name stub      # http://127.0.0.1:9998
uv run python -m tests.a2a_stub --http 9996 --name other     # http://127.0.0.1:9996

# Terminal 4: the web app (add -p <port> if 3000 is taken)
cd web && pnpm install && pnpm dev               # http://localhost:3000
```

The commands below call the service on `localhost:8000`. A helper for runs:

```bash
run() { curl -sN -X POST "localhost:8000/threads/$1/runs" -H 'content-type: application/json' -d "{\"message\": \"$2\"}"; }
```

## 1. See the agents (User Story 1)

Open `http://localhost:3000` and choose Agents.

- With nothing configured, the page says that no agent is configured and offers Add. The
  sidebar's Agents entry carries no warning.

```bash
curl -s localhost:8000/agents | jq '{limit, unusable, agents: [.agents[] | {id, name, source}]}'
# limit 20, unusable 0, no agents
```

Stop the service and reload the page: it says the service cannot be reached and offers to try
again, and shows no card. Start the service again.

## 2. Add an agent and hand work to it (User Story 2)

Press Add. The approval choice is already on requiring approval. Enter the name `stub` and the
address `HTTP://LocalHost:9998/` and save.

- A card `stub` appears with the address as `http://localhost:9998`, "requires approval",
  reachable, and the stand-in's own name, description, version, and skills.
- The stand-in's terminal shows one `GET` for its card and no `POST`: no message was sent.

```bash
curl -s localhost:8000/agents | jq '.agents[] | {name, approval, url, version, state, pending_tasks}'
curl -s localhost:8000/tools | jq '[.tools[] | select(.agent == "stub") | {name, classification}]'
# handoff_to_stub and cancel_stub_task, both gated; no address anywhere in /tools
```

Hand work to it, with no restart in between:

```bash
run t1 "Hand this to the stub agent: say hello."
# the stream ends with a pause naming agent "stub"
INTERRUPT=$(curl -s localhost:8000/threads/t1/state | jq -r .pending.interrupt_id)
curl -sN -X POST localhost:8000/threads/t1/resume -H 'content-type: application/json' \
  -d "{\"interrupt_id\": \"$INTERRUPT\", \"decisions\": [{\"type\": \"approve\"}]}"
# the stand-in's terminal shows one POST; the reply reflects its answer
```

Refusals, each of which leaves no card behind:

- The address `http://127.0.0.1:9995`, where nothing listens: "Nothing answered at the
  address."
- The address `http://localhost:8000`, the service itself: "…it served no agent card."
- The name `stub` again, at `http://127.0.0.1:9996`: the name is taken.
- The name `twin` at `http://127.0.0.1:9998`: another agent is at this address.
- The address `http://example.com:9998`, `http://user:pw@localhost:9998`,
  `http://localhost:9998/?x=1`, and `http://localhost:9998/a/..`: each is refused at the
  address, naming the rule and not repeating what was typed.

Restart the service: the card is still there, and a new run can hand work to `stub`.

## 3. An agent that is down (User Story 3)

Stop the `stub` stand-in and restart the service.

- The service starts. Its log has a warning naming `stub` and the reason.
- On any section of the console, the sidebar's Agents entry carries a warning with the number
  1. In the Agents section, a warning above the cards names `stub`, and its card is marked
  and says "Nothing answered at the address." with the time. The card's details are still
  shown.

```bash
curl -s localhost:8000/healthz                       # ok
curl -s localhost:8000/agents | jq '{unusable, stub: (.agents[] | select(.name == "stub") | {state, reason})}'
```

Press Check on the card: the console says the agent cannot be used and why. Start the stand-in
again and press Check: it can be used, the card is reachable, and all three warnings are gone.

Stop the stand-in once more and, without pressing Check, start it again. A run that hands off
to `stub` and is approved reaches it: no restart and no check was needed.

## 4. The approval choice (User Story 4)

Open `stub`'s Edit, choose "runs without approval", and save.

- The form asks for confirmation. It names `stub`, says that every message to it will be sent
  without a person seeing it first, from the next run, and that a proposal already waiting can
  then only be approved. Cancel: nothing changed, and a hand-off still pauses.
- Save again and confirm. A new run that hands off to `stub` completes with no pause.

Tightening holds at once:

```bash
run t2 "Hand the stub agent the message 'slow 20', and when it answers hand it 'hello'."
```

While the first hand-off is under way, switch `stub` back to requiring approval and confirm.
The confirmation says it holds at once, also for runs in progress.

- The first hand-off ends as it began. The second is not sent: the run gets an error result
  saying that `stub` now requires approval, and continues.
- The stand-in's terminal shows the `POST` of the first hand-off and none after it.
- The next run that hands off to `stub` pauses.

## 5. Move and remove (User Story 5)

Start a task that stays pending (the call limit is thirty seconds), and approve it:

```bash
run t3 "Hand the stub agent the message 'slow 600'."
# approve as in section 2; the run ends saying the task is pending
```

Open `stub`'s Edit and change the address to `http://127.0.0.1:9996`.

- The confirmation says one pending task will be given up, and that a proposal waiting for a
  decision will go to the new address once approved.
- Confirm. The console says one task was given up. The card shows the new address and the
  details of the stand-in named `other`.

```bash
curl -s localhost:8000/threads/t3/state | jq '.agent_tasks[] | {agent, status}'
# given_up; within a poll interval a background run adds a reply saying the agent was moved
```

Neither stand-in's terminal shows a request after the change was confirmed, until a new
hand-off to `stub` reaches the stand-in on port 9996.

Change the address to `http://127.0.0.1:9995`: refused, and hand-offs still reach port 9996.

A change from values that are no longer current is refused. The console reads the list again
whenever its window gets the focus, so the plain way to see this is to send an old version:

```bash
ID=$(curl -s localhost:8000/agents | jq -r '.agents[] | select(.name == "stub") | .id')
curl -s -X PATCH "localhost:8000/agents/$ID" -H 'content-type: application/json' \
  -d '{"version": 1, "approval": "none"}' | jq .detail.code              # "agent_changed"
```

In the console: open `stub`'s Edit, and while it is open change the agent with a `PATCH` at
its current version from a terminal. On the next reading of the list the form says the agent
was changed elsewhere and takes its confirmation back.

Remove `stub` and confirm.

- The card is gone, `/tools` lists neither of its tools, and it is still gone after a restart.
- Thread `t3` still lists its task.

## 6. Environment agents (User Stories 1 and 3)

Stop the service, add to `.env`

```bash
FREYJA_A2A_AGENTS='{"hello": {"url": "http://127.0.0.1:9998", "read_only": true}}'
```

and start it again with the stand-in on 9998 running.

- The Agents section shows `hello` marked as coming from the environment, running without
  approval, with no address and no Edit or Remove, only Check.
- Add a saved agent named `hello`: refused, the name is taken. Add one at
  `http://localhost:9998`: refused, another agent is at this address.

```bash
curl -s -X PATCH localhost:8000/agents/env.hello -H 'content-type: application/json' \
  -d '{"version": 1, "approval": "required"}' | jq .detail.code           # "agent_read_only"
```

Stop the stand-in and restart the service: it starts, warns in its log, and the console warns
in all three places. Break the entry itself, for example `"url": "http://example.com:9998"`,
and restart: the service does not start and names the agent, as before this feature.

## 7. The address stays in the Agents section (User Story 6)

Add a saved agent named `other` at `http://127.0.0.1:9996` and make a few runs that hand off
to it: one that completes, one left paused, and one while the stand-in is stopped.

```bash
curl -s localhost:8000/tools | grep -c 9996                              # 0
for t in t1 t2 t3; do curl -s localhost:8000/threads/$t/state; done | grep -c ':9996'   # 0
```

The service's log is at WARNING, and the warnings about `other` hold no `9996`. The log at
every level, the libraries' lines included, is what `tests/test_agents_address_nowhere.py`
and `tests/test_a2a_transport.py` search.

From another origin, nothing can be changed and the console cannot be framed:

```bash
curl -s -X POST localhost:8000/agents -H 'content-type: application/json' -H 'Origin: http://evil.example' \
  -d '{"name": "x", "url": "http://127.0.0.1:9996"}' | jq .detail.code   # "other_origin"
curl -s -X POST localhost:8000/agents -H 'content-type: text/plain' -d '{"name": "x"}' -o /dev/null -w '%{http_code}\n'   # 422
curl -sI localhost:3000/agents | grep -i -E 'content-security-policy|x-frame-options'
# frame-ancestors 'none' and DENY
```

## 8. Nothing changed for an environment-only deployment (FR-031)

With `FREYJA_A2A_AGENTS` as in section 6, no saved agent, and the stand-in running, the
[003 quickstart](../003-a2a-agent-delegation/quickstart.md) behaves as written there, apart
from its section on an agent that is not running at startup, which now starts the service.

## Automated checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest
FREYJA_TEST_DATABASE_URI=postgresql://freyja:freyja@localhost:5432/freyja_test uv run pytest tests/integration
cd web && pnpm lint && pnpm test && pnpm test:e2e
```
