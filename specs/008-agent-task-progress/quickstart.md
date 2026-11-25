# Quickstart: Progress of Pending Agent Tasks

Validation guide for the feature. Each section maps to a user story in [spec.md](spec.md). The
changed shape of the thread's state is in [contracts/openapi.yaml](contracts/openapi.yaml),
the check's result in [contracts/check-agent-tasks.md](contracts/check-agent-tasks.md), the
stream's change in [contracts/sse-events.md](contracts/sse-events.md), and the two columns in
[data-model.md](data-model.md).

The only agent used here is `tests/a2a_stub.py`, the suite's stand-in, which does nothing
outside its own process. Do not follow these steps with an agent you would not hand work to:
a hand-off that is approved here is really sent.

## Prerequisites

- Everything the [007 quickstart](../007-web-tasks/quickstart.md) needs: Docker with Compose
  v2, `uv`, Node 20.9 or newer with `pnpm`, `curl`, `jq`, and a model that calls tools,
  either the environment's or an active LLM profile.
- `.env` with no `FREYJA_A2A_AGENTS`, and with the two limits at their defaults
  (`FREYJA_A2A_CALL_TIMEOUT_SECONDS=30`, `FREYJA_A2A_POLL_INTERVAL_SECONDS=10`). The seconds
  named below follow from them.

## Setup

```bash
uv sync
docker compose up -d postgres

# Terminal 1: the service
uv run --env-file .env freyja-api

# Terminal 2: the web app (add -p <port> if 3000 is taken)
cd web && pnpm install && pnpm dev               # http://localhost:3000

# Terminal 3: the stand-in agent, which streams
uv run python -m tests.a2a_stub --http 9998 --name stub
```

Add the agent in the console's Agents section as `stub` at `http://127.0.0.1:9998`, and
leave it requiring approval. The commands below call the service on `localhost:8000`.

The stand-in's keyword `steps <n> <seconds>` keeps a task working, reports "Step k of n"
every `<seconds>`, and then completes. It acts on the keyword only at the start of the message
it receives, and a model often words a hand-off its own way ("Please handle the following:
steps 6 25. ..."). So each hand-off below is approved with its message edited to the keyword
alone: on the proposal card, **Edit the arguments**, make them `{"message": "steps 6 25"}`
(or the section's own keyword), and **Approve handoff_to_stub with these arguments**. When
the model proposed the keyword alone, approve it as proposed.

## 1. Read how far a pending agent task is (User Story 1)

In the Tasks section, send the task `Hand this to the stub agent: steps 6 25` and approve
the hand-off with the message `steps 6 25`, as said above. Copy the task's id from the page's
address into `T`.

- The run waits thirty seconds for the agent and ends with the note that the agent task is
  pending.

```bash
T=<the id>
curl -s localhost:8000/threads/$T/state | jq '.agent_tasks[0] | {status, last_checked_at, progress}'
# status "working", and progress with the text "Step 1 of 6" and a learned_at about five
# seconds before last_checked_at: the task was recorded with the progress its hand-off last
# received, dated when the hand-off received it
```

Run the same command every few seconds:

- `progress.text` moves on to a later step within about ten seconds of the agent reporting
  it, and `learned_at` moves with it.
- Between two changes, `last_checked_at` moves every ten seconds and `learned_at` does not:
  the time says since when the agent has reported nothing new. The agent reports a step
  every twenty-five seconds, so two or three checks see each one.

Count what the agent received: terminal 3 shows one `POST /` for the hand-off and one for
each check, every ten seconds, and no more than a task without progress gets.

When the six steps are done, about two and a half minutes after the approval:

```bash
curl -s localhost:8000/threads/$T/state | jq '.agent_tasks[0] | {status, progress}'
# status "completed"; progress is still the last text learned while it was working,
# "Step 5 of 6": the stand-in completes right after its last step, so no check sees it
```

The conversation holds the outcome, recorded by a run of the service's own, and no message
for any step of the progress.

A restart keeps it: stop the service in terminal 1 while a task is pending, start it again,
and the same command shows the progress it had. The checks resume once the stopped
service's claim on the task has lapsed, up to thirty seconds later; a step the agent
reported and replaced in that time is not seen.

## 2. See an agent's progress on the task's page (User Story 2)

Send `Hand this to the stub agent: steps 30 10` and approve the hand-off with the message
`steps 30 10`. Stay on the task's page.

- Once the run has ended, the agent task is listed as pending, "The agent is working", and
  under it the page shows what the agent last reported, "Step 2 of 30" or a later one, and
  how long ago, "just now".
- Without a reload and without touching the page, the text changes to a later step within
  about half a minute of the agent reporting it: the service looks every ten seconds and the
  page reads every twelve.
- Stop the stand-in (Ctrl+C in terminal 3). The text stays, and its age grows: "1 minute
  ago", "2 minutes ago". The agent task stays pending. Hover over the age: the exact time
  is shown.
- Start the stand-in again. It has forgotten the task, so the next check ends the agent task
  as failed. The page then shows no progress for it, and the conversation shows the outcome.

A text that looks like markup, sent by an agent, is shown as its characters. The automated
tests cover this with the stand-in; by hand it needs an agent that reports such a text.

Hide the tab for a minute while a task is pending and show it again: the page reads the task
and shows the progress as it then is.

## 3. Ask Freyja how an agent task is going (User Story 3)

With a task pending as in section 2, send a follow-up on it: `How far is the agent?`

- The run shows a call of `check_agent_tasks`. Its result lists the agent task, then the
  line "Progress the agent reported, in its own words (learned ...):", then "Step ... of 30"
  indented by four spaces.
- Freyja answers in its own words with that step.
- Terminal 3 shows no request made because of the question: only the checks every ten
  seconds.

## 4. Progress in a run's stream from an agent that does not stream (User Story 4)

Stop the stand-in and start it without streaming:

```bash
# Terminal 3
uv run python -m tests.a2a_stub --http 9998 --name stub --no-streaming
```

In the Agents section, check the agent `stub` so that its card is read again. The task's page
shows the steps of a run and not an agent's updates, so this story is read from the stream
itself. The run pauses for the hand-off, and the decision, sent with the message edited to
the keyword, continues the run in the answer to the resume, which carries the hand-off:

```bash
Q=q4-$(date +%s)
curl -sN -X POST localhost:8000/threads/$Q/runs -H 'content-type: application/json' \
  -d '{"message": "Hand this to the stub agent: steps 5 4"}' | grep '^event: interrupt'
I=$(curl -s localhost:8000/threads/$Q/state | jq -r .pending.interrupt_id)
curl -sN -X POST localhost:8000/threads/$Q/resume -H 'content-type: application/json' \
  -d '{"interrupt_id": "'"$I"'", "decisions": [{"type": "edit", "args": {"message": "steps 5 4"}}]}' \
  | grep '"node": *"agent"'
```

- After the `update` with `node: "agent"` for the task beginning to work, "Working on it.",
  one arrives for "Step 1 of 5", then one for "Step 2 of 5", and so on, each within about a
  second of the agent reporting it, until the task completes after twenty seconds. The
  stand-in completes right after its fifth step, so a read seldom finds that step: the
  update after "Step 4 of 5" is usually the one for the task completed. Before this feature
  there was only the first, when the task began to work, and nothing until the hand-off
  returned.
- No two lines in a row hold the same state and the same text.

## Cleanup

Remove the agent `stub` in the Agents section, stop the three terminals, and:

```bash
docker compose stop postgres     # only if nothing else uses it
```

## Automated checks

```bash
uv run pytest --cov
uv run ruff check . && uv run ruff format --check .
cd web && pnpm lint && pnpm test:coverage && pnpm test:e2e
```

The opt-in Postgres tests cover the two columns: that they are added to a table created
without them, and that progress is read back.

```bash
docker compose exec postgres createdb -U freyja freyja_test
FREYJA_TEST_DATABASE_URI=postgresql://freyja:freyja@localhost:5432/freyja_test uv run pytest tests/integration
```
