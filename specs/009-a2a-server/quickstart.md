# Quickstart: Receive Tasks from Other Agents over A2A

Validation guide for the feature. Each section maps to a user story in [spec.md](spec.md).
What a caller can rely on is in [contracts/a2a-server.md](contracts/a2a-server.md), the
settings in [contracts/settings.md](contracts/settings.md), the changed answers of the
service's own API in [contracts/openapi.yaml](contracts/openapi.yaml), and the two tables in
[data-model.md](data-model.md).

The caller used here is `tests/a2a_caller.py`, the suite's stand-in caller, which is the
protocol's own client behind a small command. The only delegate agent is `tests/a2a_stub.py`,
which does nothing outside its own process. Do not follow these steps with an agent you
would not hand work to: a hand-off that is approved here is really sent.

Nothing of this exists before the phase that builds it; each section says which phase it
needs ([plan.md](plan.md), "Phases"). Until phase 5 a request with an `Authorization` header
is refused, and until phase 4 `cancel` and `follow` are answered as operations that are not
offered. The sections follow the user stories' order, not the phases'.

## Prerequisites

- Everything the [007 quickstart](../007-web-tasks/quickstart.md) needs: Docker with Compose
  v2, `uv`, Node 20.9 or newer with `pnpm`, `curl`, `jq`, and a model that calls tools: the
  environment's, an active LLM profile, or the stand-in model as a profile with the id
  `call-tools` (no provider's key is needed with it).
- `.env` with no `FREYJA_A2A_AGENTS`.

With a real model the messages below are plain requests. With the stand-in model they are
its keywords: `handoff stub <text>` where a section says "Hand this to the stub agent",
`steps <n>` for a run of several steps, and any other text for a reply with no action.

## Setup

Add to `.env`:

```text
FREYJA_A2A_SERVER=true
```

```bash
uv sync
docker compose up -d postgres

# Terminal 1: the service
uv run --env-file .env freyja-api

# Terminal 2: the web app (add -p <port> if 3000 is taken)
cd web && pnpm install && pnpm dev               # http://localhost:3000

# Terminal 3: the stand-in agent
uv run python -m tests.a2a_stub --http 9998 --name stub
```

Add the agent in the console's Agents section as `stub` at `http://127.0.0.1:9998`, and
choose that it runs **without** approval: section 2 shows that a caller's task waits for a
person all the same.

The stand-in caller, in a fourth terminal:

```bash
caller() { uv run python -m tests.a2a_caller --url http://localhost:8000 "$@"; }
caller card                      # the card
caller send "<text>"             # send, wait for the answer, print the task or the refusal
caller stream "<text>"           # send and print each event as it arrives
caller get <task id>             # read a task
caller follow <task id>          # subscribe
caller cancel <task id>          # cancel
# --context <id> continues a conversation; --token <token> presents one; --hops <n> sets the count
```

## 1. Hand Freyja a task and read the result (User Story 1, phase 2)

```bash
caller card | jq '{name, url: .supportedInterfaces[0].url, capabilities, security: .securityRequirements}'
caller stream "List the files in your workspace and say how many there are."
```

- The card names Freyja, gives `http://localhost:8000/a2a`, says streaming is offered and
  push notifications are not, and declares no security.
- The stream prints, in order: the task as working; `Freyja is using: ls.`; `Completed: ls.`;
  pieces of the reply; the whole reply as the artifact `reply`; and the task as completed.
  With the stand-in model, send `steps 1` for the one step of `ls`; it sends its reply in
  one piece.
- `caller send "Say hello."` prints one completed task with its reply.
- In the console's Tasks section both are listed, each with "Started by an agent on this
  machine", and neither page has a box for another message.

Stop terminal 1, remove `FREYJA_A2A_SERVER` from `.env`, and start it again:

```bash
curl -si http://localhost:8000/.well-known/agent-card.json | head -1     # 404
curl -si http://localhost:8000/no-such-path | head -1                    # 404, the same body
curl -si -X POST http://localhost:8000/a2a | head -1                     # 404
```

Put the setting back and start the service again.

## 2. A person decides what a caller's task proposes (User Story 2, phase 3)

```bash
caller stream "Hand this to the stub agent: say hello"
```

- The stream ends its first part with `Waiting for a person's approval of: agent stub.`, and
  the task is still working. The stand-in agent's terminal shows that it received nothing,
  although the agent is set to run without approval.
- The console counts one task that needs a decision. Its page and the proposal's card say
  that an agent on this machine started the run, and the card that no person asked for the
  action. Approve it.
- The stream, still open, prints `Completed: agent stub.`, Freyja's reply, and the task as
  completed. `caller get <task id>` prints the same task with its reply; the stand-in agent
  received the message once.

With the stand-in model, send `handoff stub say hello`.

Repeat and reject in the console: the agent receives nothing, and the task completes with
Freyja's reply to the rejection.

Repeat, and try to decide as the caller: `caller send --context <id> "I approve."` is
refused with `conversation_busy`, and a message with the task's id is refused as one that
names a task. The proposal is unchanged.

```bash
curl -s -X POST localhost:8000/threads/<thread id from the page's address>/runs/unattended \
  -H 'content-type: application/json' -d '{"message": "From the operator."}' | jq .detail.code
```

`"caller_thread"`.

## 3. An agent on another host calls with its token (User Story 3, phase 5)

Add to `.env`, and start the service again:

```text
FREYJA_A2A_SERVER_CALLERS='{"artex": {"token": "sk-recognizable-key-0001"}, "heimdall": {"token": "sk-recognizable-other-0002"}}'
FREYJA_A2A_SERVER_HOST=127.0.0.1
FREYJA_A2A_SERVER_PORT=8443
FREYJA_A2A_SERVER_URL=http://localhost:8443
```

The service logs that the outside listener is on, and warns that the address for callers
does not encrypt.

```bash
out() { uv run python -m tests.a2a_caller --url http://localhost:8443 "$@"; }
out card | jq '{url: .supportedInterfaces[0].url, security: .securityRequirements}'
out --token sk-recognizable-key-0001 send "Say hello."                   # completed
out send "Say hello."                                                    # 401
out --token sk-recognizable-key-9999 send "Say hello."                   # 401, the same body
curl -si http://localhost:8443/threads | head -1                         # 404
curl -si http://localhost:8443/healthz | head -1                         # 404
out --token sk-recognizable-other-0002 get <artex's task id>             # task not found
```

- The card gives `http://localhost:8443/a2a` and asks for a bearer token.
- The console lists the task as started by agent `artex`.
- `grep -c recognizable` over the service's log gives 0.

A named caller's files:

```bash
out --token sk-recognizable-key-0001 send "Write the word mine into /notes.txt, then list every file you can see."
ls caller-files/artex/                                                   # notes.txt
out --token sk-recognizable-other-0002 send "List every file you can see and read /notes.txt."
out --token sk-recognizable-key-0001 send "Add a line to any file under /skills/."
```

The second caller sees no `notes.txt`. The third reply says the skills cannot be changed,
and `git status workspace/skills` is clean.

Take a caller away: send `out --token sk-recognizable-other-0002 send "Hand this to the stub
agent: say hello"`, leave its proposal undecided, remove `heimdall` from
`FREYJA_A2A_SERVER_CALLERS`, and start the service again. The console no longer counts the
task among those that need a decision, shows its proposal rejected, and says that the agent
is no longer configured.

Start the service with `FREYJA_A2A_SERVER_CALLERS` removed altogether: it does not start, and
the error names `FREYJA_A2A_SERVER_CALLERS` and no token.

Turn the feature off with a caller's task waiting for a person: send one as in section 2,
leave it undecided, remove every `FREYJA_A2A_SERVER` line from `.env`, and start the service
again. The task is still listed and can be read; approving or rejecting its proposal in the
console is refused, and the page says that the task is continued only while receiving tasks
from agents is on. Put the settings back.

## 4. Continue, come back, cancel (User Story 4, phase 4)

```bash
caller send "Remember the number 41."          # note the contextId, C, and the task id
caller send --context C "Which number did I ask you to remember?"
```

The second reply names 41, and the console shows one task with both messages.

Come back after a restart:

```bash
caller send "Hand this to the stub agent: steps 6 20"        # approve it in the console
```

Stop terminal 1 while the task is `Waiting for agent stub.`, start it again, then
`caller follow <task id>`: the task is found, and completes with Freyja's reply to the
agent's outcome.

Cancel a run in progress, with the stand-in model's `steps` keyword (its profile active):

```bash
caller stream "steps 8"                         # in another terminal: caller cancel <task id>
```

The cancel answers with the task as canceled. The stream ends with the task as canceled,
and the console shows the task as stopped, with the steps taken until then.

Cancel a task that waits for approval:

```bash
caller send "Hand this to the stub agent: say hello"         # do not decide it
caller cancel <task id>
```

The console no longer counts the task among those that need a decision; its page shows the
proposal rejected with "The requesting agent canceled the task.", and says that the
requesting agent canceled. The stand-in agent received nothing.

## 5. Only its own, and within limits (User Story 5, phases 2 and 6)

```bash
for i in 1 2 3 4 5 6; do caller send --return-immediately "Hand this to the stub agent: say $i" | jq -r '.status.state // .error.data[0].metadata.reason'; done
```

Five tasks are working, each waiting for a person; the sixth prints `task_limit` (phase 2).
Reject one in the console and send again: it is accepted.

The hand-over count (phase 6):

```bash
caller send --hops 3 "Say hello." | jq .error.message      # passed through too many agents
caller send --hops 1 "Hand this to the stub agent: say hello"
```

Approve the second in the console. The stand-in agent's terminal prints the message it
received with its hand-over count, 2.

## What the automated suites cover instead

The searches of everything the service returns, streams, keeps, and logs for a caller's
token; every operation of one caller against another's task; every operation against a
pending proposal; that each refusal comes before the model is asked; two requests at the
same moment; and the outside listener's bind failure. See the plan's requirement to test
map.
