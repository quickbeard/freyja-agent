# Use the API

A thread is a conversation keyed by a client-chosen id (1 to 128 characters: letters, digits, hyphen, underscore, dot; not only dots). Three endpoints work on it. The full contracts are in `specs/001-thread-runs-api/contracts/`.

**Start a run** and stream it as server-sent events:

```bash
curl -N -X POST localhost:8000/threads/demo-1/runs \
  -H 'content-type: application/json' \
  -d '{"message": "Summarize what you can do and which skills you have loaded."}'
```

Each event has an `event:` line naming its kind and a `data:` line holding one JSON object. The first event is always `start`; its `profile` names the [LLM profile](models.md) the run uses, as `id`, `name`, and `source`. Then come `update` (one per orchestrator step, with messages, tool calls, and tool results), `message` (the reply as it is produced), and, with `?debug=true`, `debug` (raw LangGraph detail; it never changes the run). Exactly one terminal event closes the stream: `end` when the reply is finished, `interrupt` when the orchestrator proposes an action that needs approval, or `error` with a `code` of `step_limit`, `model_error`, `internal`, `no_usable_model`, or, for a resume only, [`approval_now_required`](agents.md#change-whether-an-agent-needs-approval). A later message to the same thread continues the conversation, including after a service restart.

One run per thread: a second run while one streams gets `409` with code `run_active`. The marker is a lease in the database, so it holds across service instances and expires after `FREYJA_RUN_LOCK_TTL_SECONDS` if the instance holding it dies. A clean stop (Ctrl-C or SIGTERM) does not wait for a run read through these two routes: with the installed sse-starlette the server ends every open event stream at once, and the run with it, in the middle of its step; the lease is given back as the stream ends. A run started through the console's routes (below) is not tied to its stream, and a clean stop waits for its step under way. Only a crash leaves a lease behind, and it expires.

**Read a thread's state**: the full transcript in order, whether a run is active, and the pending proposal if the thread is paused. A never-used id is `404`.

```bash
curl -s localhost:8000/threads/demo-1/state | jq
```

**Decide on a proposal.** Freyja's own action with an effect outside the workspace is `send_notification`, a placeholder that writes one log line; every tool server tool not listed as read-only is gated the same way (see [Tool servers](tool-servers.md)). When the orchestrator proposes one, the stream ends with `interrupt` listing the proposed arguments, and the thread stays paused until a decision arrives, however long that takes. A new message on a paused thread is refused with `409` and code `decision_pending`. Resume with the proposal's `interrupt_id`, which the `interrupt` event and the thread state both report, and one decision per proposed action, in order. The continuation streams with the same event kinds as a run:

```bash
ID=$(curl -s localhost:8000/threads/demo-1/state | jq -r .pending.interrupt_id)
curl -N -X POST localhost:8000/threads/demo-1/resume \
  -H 'content-type: application/json' \
  -d "{\"interrupt_id\": \"$ID\", \"decisions\": [{\"type\": \"approve\"}]}"
# or edit the arguments: "decisions": [{"type": "edit", "args": {"channel": "#ops-staging", "message": "..."}}]
# or reject with an optional reason: "decisions": [{"type": "reject", "message": "Not needed."}]
```

The tool runs only after a recorded approve or edit. An edit keeps the action and replaces its arguments, validated against the tool's schema; the original proposal stays in the transcript and the tool result records that a person changed it. An edit that carries an argument the action does not name is refused, so nothing an approver sends is dropped silently. A reject skips the tool and the orchestrator is told why.

The `interrupt_id` ties a decision to one proposal: a request that names any other id is `409` with code `proposal_mismatch`, so a decision sent twice cannot approve a later proposal. Resume on a thread with nothing pending is `409` with code `nothing_pending`. A malformed decision is `422`. In each of these cases nothing runs and a pending proposal stays decidable.

A decision that was taken stands. When a resume decides a proposal of the subagent and its run then fails before it ends, the thread can still show that proposal, because the step that runs the subagent did not complete. A resume after that is `409` with code `already_decided` when it sends other decisions than the earlier ones, which the message names by type, and nothing runs; the same decisions continue the thread.

On a thread another agent started over A2A (see [Receive tasks from agents](a2a-server.md)), a resume that approves or edits an action is `409` with code `requester_gone` once that agent canceled its task, or whenever the conversation has no task that still works; nothing runs. A resume that rejects every action is taken, and so is one that repeats decisions already taken.

**List the threads.** `GET /threads` answers with one row per thread that has had a run, whichever client started it, and `GET /threads/{thread_id}` with one row. They are the service's side of the Tasks section of the web console (feature 007); the console calls a thread a task, and the API keeps the word thread.

```bash
curl -s localhost:8000/threads | jq '{needs_decision, total, more, threads: [.threads[] | {thread_id, first_message, status}]}'
curl -s localhost:8000/threads/demo-1 | jq
```

A row holds the thread's id, the first 200 characters of its first message, when it was first seen and when it last changed, and a `status` the service works out, first match: `running` (a run holds the thread's lease, on any instance), `needs_decision` (a proposal is pending), `failed`, `stopped` (the last run ended with steps still to take: its client stopped reading, the service stopped, or it failed), `waiting_for_agent` (an [agent task](agents.md#pending-tasks) of the thread was pending when its last run ended), and `done`. `failed`, with the run's `error`, is known only for a run that goes on without a reader (below), and an agent's address is taken out of every event such a run sends its reader, its debug events included, and so of the error's message before it is kept for the list; a run started the earlier way that failed is listed as `stopped`, since its error went to whoever read its stream. The list puts every thread that needs a decision first, then the others with the most recently changed first, up to 100 rows in all; `needs_decision` counts every thread, listed or not, and `more` says whether there are others. A row also has `runs`, how many times the thread's run lease was taken, a refused request included: its status is of the last run. And `caller`: `null`, or, for a thread another agent started over A2A (see [Receive tasks from agents](a2a-server.md)), the caller's `name` (`local` for an agent that called the service's own address), whether it `canceled` its last task, and whether the settings still name it (`configured`). Such a thread takes no message through `POST /threads/{thread_id}/runs` or `/runs/unattended`: they answer `409` with code `caller_thread`, before the lease (`a2a_server_off` while the feature is off), and only its caller adds messages to it.

The rows are kept in the table `freyja_threads`, through the run lease: every run takes the lease first and gives it back last, so a run the delegation worker starts is listed like one started with curl. What a run left behind is read when a row is next asked for, by one read of the thread's state, and kept. Neither route contacts a model, an agent, or a tool server, and neither changes a thread. A thread from before the table existed gets its row the first time `GET /threads/{thread_id}` is asked for it, or at its next run; that route reads the thread's state again each time while no run is in progress, so a row that was kept wrong is put right by being opened. When the table cannot be read both routes answer `500` and never an earlier list. When one thread cannot be read, the list leaves that thread out, counts it in `total`, and logs a warning. When a run cannot be recorded in the table, the run goes on and the service logs a warning; the lease waits for the table five seconds at most.

**Runs that go on without a reader.** The console starts and continues runs through two more routes, which take the same body, give the same refusals, and stream the same events as the two above, from the same code: `POST /threads/{thread_id}/runs/unattended` and `POST /threads/{thread_id}/resume/unattended`. What differs is that the run goes on to its end whether or not anyone reads the stream: a client that disconnects, or never reads, changes nothing about how the run ends, and the thread's row and state say how it did. A run that ends with an error is listed as `failed` with its `error`. The answer says `Cache-Control: no-store, no-transform`, so that the console's forwarding passes each event as it is sent, and carries a `: ping` line every 10 seconds; a reader that falls 1,000 events behind is disconnected and the run goes on.

```bash
curl -N -X POST localhost:8000/threads/demo-2/runs/unattended \
  -H 'content-type: application/json' -d '{"message": "Summarize what you can do."}'
```

**Stop a run.** `POST /threads/{thread_id}/stop` takes no body and asks such a run to stop: `202` once it is asked, with `run`, which of the thread's runs the stop is for (a client that follows the run by reading compares it with the row's `runs`), `409` with code `no_run` when no run is in progress (a paused thread has none: a proposal is declined by rejecting it), `409` with code `not_stoppable` for a run started the earlier way or by the service itself, and for a console run in the moment before its first event, and `404` for a thread the list does not know. The run ends after the step under way: a model call or a tool step that has started completes and is recorded, no further step starts, and its stream ends with `end` and `{"status": "stopped"}`. An action under way is never cut off; an action that was approved and had not started is not started. A run that pauses, finishes, or fails before the stop is applied ends as it would have. The stop is a mark in `freyja_threads`, so it reaches the run from any instance; the row says `stopping` until the run has ended. Such a run counts the step limit at the pauses between steps, so it can report `step_limit` up to two steps before a run started the earlier way would; the limit is never exceeded. A clean stop of the service treats each such run as if a stop had been asked and waits for its step under way, which can take as long as one step is allowed to.

**Is it up, is it ready.** `GET /healthz` says the process is up and nothing else; it answers without the database or a model. `GET /readyz` says whether a run could be started now, which is whether the service reaches its database. It asks the database one statement that reads nothing, through the service's own connection pool, and answers within two seconds, the wait for a free connection included: `200` with `{"status":"ok"}` when the database answered, and `503` with `{"status":"unavailable"}` when it did not, or not in time. The answer says nothing of the connection, and the log has one warning that names the kind of failure and no more. It contacts no model, tool server, or agent: none of them stops the service from starting, and a run says so itself when one of them cannot be used (`no_usable_model`, an agent listed as not reachable).

**Other origins.** Like the LLM and Agents routes, the thread routes turn away a request that changes something when a browser marks it as made by a page of another origin: `POST .../runs`, `POST .../resume`, their two `unattended` forms, and `POST .../stop` answer `403` with code `other_origin` before a lease is taken or anything of the thread is read; only a body that cannot be read as JSON is answered first, with `422`. A client that is not a web page, such as curl, sends none of the headers a browser sends and is served as before. A body that is not sent as JSON is `422`.

**Host names.** Every request is asked which host name it was made for, and one made for a name the service is not set up to answer for is refused with `403` and code `unknown_host`, a request that only reads included, before any route sees it. `localhost` and an IP address written as one are always answered; every other name is listed in `FREYJA_ALLOWED_HOSTS`. The service reads `Host` and, on a request the console's server forwarded, the name the browser asked the console for, which arrives in `X-Forwarded-Host`: each has to be an answered one. This is what keeps out a page under a name its own author controls, once that name is pointed at this machine (DNS rebinding). To the browser such a page and the console are one origin, so the page could read every task and approve a proposal, and nothing but the name tells its requests from the console's. `GET /healthz` is answered under any name; `GET /readyz` is held to the list like any other route, so a probe that asks by an IP address or as `localhost` is answered, and one that asks under another name needs that name listed. Each refusal is logged with the name it was for, so a deployment that left one out finds it in the service's log.

**Next:** [Tool servers](tool-servers.md): the MCP servers whose tools the orchestrator calls.
