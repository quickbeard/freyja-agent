# Freyja

General-purpose agent orchestrator built on [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview) and served with FastAPI. The orchestrator routes work to the right delegate agent and keeps a human in the loop for any action with effects outside its workspace.

## Setup

```bash
uv sync                         # creates .venv and installs runtime + dev dependencies
cp .env.example .env            # pick one model block and set its key; leave the database line as is
docker compose up -d postgres   # local Postgres; its credentials equal FREYJA_DATABASE_URI in .env.example
uv run pre-commit install       # one-time per clone: runs the linters and the unit tests on every commit
(cd web && pnpm install)        # web app dependencies; needs Node and pnpm
```

`.env` is git-ignored. The compose file and `.env.example` carry the same local-only database credentials, so the copied file connects without edits; only the model key needs filling in. To save [LLM profiles](#llm-profiles) the service also needs `FREYJA_SECRET_KEY`, which is left unset there. uv stops reading `.env` at the first line it cannot parse, such as a model line still holding a `<...>` placeholder, and only warns, so every line after it is ignored: fill in or comment out the block you do not use.

## Run the dev server

With auto-reload, listening on `127.0.0.1:8000`:

```bash
uv run --env-file .env uvicorn freyja_agent.api.app:app --reload
```

Or through the console script, which reads the bind address from settings:

```bash
uv run --env-file .env freyja-api
```

Startup opens one Postgres connection pool and creates the LangGraph checkpointer and store tables, the `freyja_run_locks` table, the four agent tables (`freyja_agent_conversations`, `freyja_agent_sends`, `freyja_agent_tasks`, `freyja_agent_calls`), the two tables of saved agents (`freyja_agents`, `freyja_agents_state`), the two LLM profile tables (`freyja_llm_profiles`, `freyja_llm_state`), and the table of the thread index (`freyja_threads`) if they are missing. A task table created by an earlier version gains its `agent_ref` column. If the database cannot be reached, startup fails with an error that names the connection (password redacted) and the process exits instead of serving. A model that cannot be used does not stop startup: the service starts, logs which LLM profile is not usable and why, and ends every run with `no_usable_model` until a usable profile is active (see [No usable model](#no-usable-model)).

Check it is up:

```bash
curl localhost:8000/healthz
# {"status":"ok"}
```

Interactive API docs are at <http://localhost:8000/docs>. There is no authentication: anyone who can reach the port acts as a user, which is why the server binds `127.0.0.1` by default.

## Use the API

A thread is a conversation keyed by a client-chosen id (1 to 128 characters: letters, digits, hyphen, underscore, dot; not only dots). Three endpoints work on it. The full contracts are in `specs/001-thread-runs-api/contracts/`.

**Start a run** and stream it as server-sent events:

```bash
curl -N -X POST localhost:8000/threads/demo-1/runs \
  -H 'content-type: application/json' \
  -d '{"message": "Summarize what you can do and which skills you have loaded."}'
```

Each event has an `event:` line naming its kind and a `data:` line holding one JSON object. The first event is always `start`; its `profile` names the [LLM profile](#llm-profiles) the run uses, as `id`, `name`, and `source`. Then come `update` (one per orchestrator step, with messages, tool calls, and tool results), `message` (the reply as it is produced), and, with `?debug=true`, `debug` (raw LangGraph detail; it never changes the run). Exactly one terminal event closes the stream: `end` when the reply is finished, `interrupt` when the orchestrator proposes an action that needs approval, or `error` with a `code` of `step_limit`, `model_error`, `internal`, `no_usable_model`, or, for a resume only, [`approval_now_required`](#change-whether-an-agent-needs-approval). A later message to the same thread continues the conversation, including after a service restart.

One run per thread: a second run while one streams gets `409` with code `run_active`. The marker is a lease in the database, so it holds across service instances and expires after `FREYJA_RUN_LOCK_TTL_SECONDS` if the instance holding it dies. A clean stop (Ctrl-C or SIGTERM) does not wait for a run read through these two routes: with the installed sse-starlette the server ends every open event stream at once, and the run with it, in the middle of its step; the lease is given back as the stream ends. A run started through the console's routes (below) is not tied to its stream, and a clean stop waits for its step under way. Only a crash leaves a lease behind, and it expires.

**Read a thread's state**: the full transcript in order, whether a run is active, and the pending proposal if the thread is paused. A never-used id is `404`.

```bash
curl -s localhost:8000/threads/demo-1/state | jq
```

**Decide on a proposal.** Freyja's own action with an effect outside the workspace is `send_notification`, a placeholder that writes one log line; every tool server tool not listed as read-only is gated the same way (see [Tool servers](#tool-servers)). When the orchestrator proposes one, the stream ends with `interrupt` listing the proposed arguments, and the thread stays paused until a decision arrives, however long that takes. A new message on a paused thread is refused with `409` and code `decision_pending`. Resume with the proposal's `interrupt_id`, which the `interrupt` event and the thread state both report, and one decision per proposed action, in order. The continuation streams with the same event kinds as a run:

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

**List the threads.** `GET /threads` answers with one row per thread that has had a run, whichever client started it, and `GET /threads/{thread_id}` with one row. They are the service's side of the Tasks section the web console is getting (feature 007); the console calls a thread a task, and the API keeps the word thread.

```bash
curl -s localhost:8000/threads | jq '{needs_decision, total, more, threads: [.threads[] | {thread_id, first_message, status}]}'
curl -s localhost:8000/threads/demo-1 | jq
```

A row holds the thread's id, the first 200 characters of its first message, when it was first seen and when it last changed, and a `status` the service works out, first match: `running` (a run holds the thread's lease, on any instance), `needs_decision` (a proposal is pending), `failed`, `stopped` (the last run ended with steps still to take: its client stopped reading, the service stopped, or it failed), `waiting_for_agent` (an [agent task](#pending-tasks) of the thread was pending when its last run ended), and `done`. `failed`, with the run's `error`, is known only for a run that goes on without a reader (below); a run started the earlier way that failed is listed as `stopped`, since its error went to whoever read its stream. The list puts every thread that needs a decision first, then the others with the most recently changed first, up to 100 rows in all; `needs_decision` counts every thread, listed or not, and `more` says whether there are others.

The rows are kept in the table `freyja_threads`, through the run lease: every run takes the lease first and gives it back last, so a run the delegation worker starts is listed like one started with curl. What a run left behind is read when a row is next asked for, by one read of the thread's state, and kept. Neither route contacts a model, an agent, or a tool server, and neither changes a thread. A thread from before the table existed gets its row the first time `GET /threads/{thread_id}` is asked for it, or at its next run; that route reads the thread's state again each time while no run is in progress, so a row that was kept wrong is put right by being opened. When the table cannot be read both routes answer `500` and never an earlier list. When one thread cannot be read, the list leaves that thread out, counts it in `total`, and logs a warning. When a run cannot be recorded in the table, the run goes on and the service logs a warning; the lease waits for the table five seconds at most.

**Runs that go on without a reader.** The console starts and continues runs through two more routes, which take the same body, give the same refusals, and stream the same events as the two above, from the same code: `POST /threads/{thread_id}/runs/unattended` and `POST /threads/{thread_id}/resume/unattended`. What differs is that the run goes on to its end whether or not anyone reads the stream: a client that disconnects, or never reads, changes nothing about how the run ends, and the thread's row and state say how it did. A run that ends with an error is listed as `failed` with its `error`. The answer says `Cache-Control: no-store, no-transform`, so that the console's forwarding passes each event as it is sent, and carries a `: ping` line every 10 seconds; a reader that falls 1,000 events behind is disconnected and the run goes on.

```bash
curl -N -X POST localhost:8000/threads/demo-2/runs/unattended \
  -H 'content-type: application/json' -d '{"message": "Summarize what you can do."}'
```

**Stop a run.** `POST /threads/{thread_id}/stop` takes no body and asks such a run to stop: `202` once it is asked, `409` with code `no_run` when no run is in progress (a paused thread has none: a proposal is declined by rejecting it), `409` with code `not_stoppable` for a run started the earlier way or by the service itself, and `404` for a thread the list does not know. The run ends after the step under way: a model call or a tool step that has started completes and is recorded, no further step starts, and its stream ends with `end` and `{"status": "stopped"}`. An action under way is never cut off; an action that was approved and had not started is not started. A run that pauses, finishes, or fails before the stop is applied ends as it would have. The stop is a mark in `freyja_threads`, so it reaches the run from any instance; the row says `stopping` until the run has ended. Such a run counts the step limit at the pauses between steps, so it can report `step_limit` up to two steps before a run started the earlier way would; the limit is never exceeded. A clean stop of the service treats each such run as if a stop had been asked and waits for its step under way, which can take as long as one step is allowed to.

**Other origins.** Like the LLM and Agents routes, the thread routes turn away a request that changes something when a browser marks it as made by a page of another origin: `POST .../runs`, `POST .../resume`, their two `unattended` forms, and `POST .../stop` answer `403` with code `other_origin` before anything else is looked at. A client that is not a web page, such as curl, sends none of the headers a browser sends and is served as before. A body that is not sent as JSON is `422`.

## Tool servers

The orchestrator can also call tools it did not write, provided by [MCP](https://modelcontextprotocol.io) servers on the same machine. At startup the service connects to every server listed in `FREYJA_MCP_SERVERS`, lists its tools, and gives them to the orchestrator beside its own. The full contract is in `specs/002-mcp-tool-delegation/contracts/server-config.md`.

### Try it with the reference git server

The reference [git server](https://github.com/modelcontextprotocol/servers/tree/main/src/git) runs through `uvx`, which comes with `uv`, and needs no account or token. Give it a scratch repository:

```bash
git init -b main /tmp/freyja-demo-repo
git -C /tmp/freyja-demo-repo -c user.name=demo -c user.email=demo@example.invalid \
  commit --allow-empty -m "init"
uvx mcp-server-git --help       # downloads it once, so the first start is inside the startup limit
```

Add one line to `.env` and start the service:

```bash
FREYJA_MCP_SERVERS='{"git": {"command": "uvx", "args": ["mcp-server-git", "--repository", "/tmp/freyja-demo-repo"], "read_only": ["git_status", "git_log", "git_branch"]}}'
```

```bash
uv run --env-file .env freyja-api
# ... Tool server git (command): 14 tools, 3 read-only
```

A read-only tool runs without a pause:

```bash
curl -N -X POST localhost:8000/threads/git-1/runs -H 'content-type: application/json' \
  -d '{"message": "What is the git status of the repository at /tmp/freyja-demo-repo?"}'
```

Any other git tool pauses for a decision. This run ends with an `interrupt` event for `git_create_branch`, and the branch exists only after you approve it as described under "Decide on a proposal" above:

```bash
curl -N -X POST localhost:8000/threads/git-2/runs -H 'content-type: application/json' \
  -d '{"message": "In the repository at /tmp/freyja-demo-repo, create a branch named demo."}'
git -C /tmp/freyja-demo-repo branch
```

Remove the line from `.env` to go back to a service with no tool servers.

### Describe a server

`FREYJA_MCP_SERVERS` is one JSON object on one line, in single quotes, mapping a server name (lowercase letters, digits, hyphen, underscore; starting with a letter; at most 32 characters) to an entry in one of two forms:

- **Launched**: `command` and optional `args`. The service starts the process at startup over standard input and output, keeps one connection to it, and stops it at shutdown. Nothing relaunches it if it exits; restart the service to bring it back. What it writes to its error output is logged under the server's name.
- **Address**: `url`, an `http` or `https` address of a server you run yourself, speaking MCP over Streamable HTTP. The host must be `localhost` or a loopback address; any other name or address is refused without a lookup, and a redirect is not followed. The service connects once per call, so a server that comes back is usable again without a restart, and it never stops a server it did not launch.

```bash
FREYJA_MCP_SERVERS='{"notes": {"url": "http://127.0.0.1:8765/mcp", "read_only": ["search"]}}'
```

**`read_only` is the only thing that decides approval.** A tool listed there runs without a pause. Every other tool of that server, including one the list does not mention and one the server labels read-only itself, pauses with approve, edit, and reject exactly like `send_notification`, on the orchestrator and on its subagent. An edit is validated against the input schema the server declared for the tool; a tool that declares no input allows approve and reject only.

**Credentials** go in the service's environment, never in `args` or a `url`. Name the variable in `pass_env` and the launched process receives it; otherwise it sees only `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, and `USER`. The value is replaced with `***` in everything that comes from that server: results, errors, logs, and the catalog.

```bash
TRACKER_TOKEN=<token>
FREYJA_MCP_SERVERS='{"tracker": {"command": "tracker-mcp", "pass_env": ["TRACKER_TOKEN"], "read_only": ["search_issues"]}}'
```

### When a server fails

At startup, a configuration the service cannot use stops it with an error that names the server, and nothing listens: both or neither of `command` and `url`, an unknown field, an address not on this machine, a `pass_env` variable that is unset or empty, a server that cannot be launched or reached or does not list its tools within `FREYJA_MCP_STARTUP_TIMEOUT_SECONDS`, a `read_only` name the server does not provide, or a tool name that is invalid or already taken by another server or by Freyja's own tools. A server with no tools is accepted.

During a run, a failing call becomes an error result for the orchestrator and the run goes on; the stream still ends normally and the thread's run lease is unaffected. A call that takes longer than `FREYJA_MCP_CALL_TIMEOUT_SECONDS` is abandoned and its result says the effect may or may not have happened. A server that cannot be reached gives an error result saying so. Each failure logs one warning naming the server, the tool, and `timeout`, `unreachable`, or `tool_error`. No call is ever retried: an approval authorizes one attempt.

### Read the catalog

`GET /tools` lists every tool the orchestrator has and the status of every server. It never contacts a server. Before it answers it reads two rows of the database, to learn whether the agents or the active LLM profile changed (see [Agents in the catalog](#agents-in-the-catalog)); the tool servers' tools are the ones listed at startup.

```bash
curl -s localhost:8000/tools | jq
```

Each tool has its `name`, its `server` (`null` for Freyja's own tools), `description`, `input_schema`, `classification`, `allowed_decisions`, and `server_hints`. The classification is `read_only` (runs without a pause), `workspace` (Freyja's own file-writing and subagent tools, which change only the workspace), or `gated` (every call pauses). `server_hints` are what the server says about its own tool, such as a read-only label; they are shown as its claim and never change the classification. Each server has its `connection` form (`command` or `address`), `tool_count`, and `reachable`, `checked_at`, and `error` from the most recent attempt to use it, at startup or in a call. The catalog never shows a server's command, arguments, address, or variable names.

A proposal for a server tool names its server: each entry of `action_requests` in the `interrupt` event and in the thread state's `pending` has `server` set to the server's name, and `null` for Freyja's own tools.

## Agents

The orchestrator can also hand work to agents that do their own reasoning, over the [A2A protocol](https://a2a-protocol.org). The service gives the orchestrator two tools for every agent, one to hand it a task and one to cancel a task, and reads each agent's card at startup. An agent is listed in `FREYJA_A2A_AGENTS`, or added while the service runs (see [Add an agent while the service runs](#add-an-agent-while-the-service-runs)). The full contract is in `specs/003-a2a-agent-delegation/contracts/agent-config.md`.

### Try it with the reference hello agent

The A2A project's own sample `helloworld` agent runs locally and needs no account or key. Fetch its two files at the pinned commit and run it; it listens on `127.0.0.1:9999`:

```bash
mkdir -p /tmp/freyja-hello && cd /tmp/freyja-hello
base=https://raw.githubusercontent.com/a2aproject/a2a-samples/1bb0871294/samples/python/agents/helloworld
curl -sSfO "$base/__main__.py" && curl -sSfO "$base/agent_executor.py"
uv run --no-project --with 'a2a-sdk[http-server]==1.1.0' --with uvicorn python __main__.py
```

In another terminal, add one line to `.env` and start the service:

```bash
FREYJA_A2A_AGENTS='{"hello": {"url": "http://127.0.0.1:9999", "read_only": true}}'
```

```bash
uv run --env-file .env freyja-api
# ... Agent hello: read-only, 1 skill
```

A hand-off to a read-only agent runs without a pause. The stream shows the agent's progress as `update` events with `node: "agent"`, then the tool result with its answer, and the reply relays it:

```bash
curl -N -X POST localhost:8000/threads/hello-1/runs -H 'content-type: application/json' \
  -d '{"message": "Ask the hello agent to say hello to the ops team."}'
```

Remove `"read_only": true` from the entry and restart the service. The same request now ends with an `interrupt` event for `handoff_to_hello` that shows the message Freyja wrote, and nothing reaches the agent until you approve it as described under "Decide on a proposal" above. Then the agent's answer reaches the reply.

Remove the line from `.env` to go back to a service with no agents.

### Describe an agent

`FREYJA_A2A_AGENTS` is one JSON object on one line, in single quotes, mapping an agent name (lowercase letters, digits, hyphen, underscore; starting with a letter; at most 32 characters) to an entry with two fields:

- `url`: where the agent serves its card, `http` or `https`, on `localhost` or a loopback address; any other host is refused without a lookup. Every request goes to that host and port: from the card the service keeps only the path of the agent's JSON-RPC interface, and it follows no redirect.
- `read_only`: `true` lets every message to the agent run without approval. Without it, every hand-off, answer, and cancel to that agent pauses for a decision, on the orchestrator and on its subagent. **Silence means gated**, and nothing the agent's card says about itself changes that.

```bash
FREYJA_A2A_AGENTS='{"hello": {"url": "http://127.0.0.1:9999", "read_only": true}, "planner": {"url": "http://localhost:9998"}}'
```

Agent names and tool server names share one namespace: an agent may not be named like a configured server.

### Hand-offs, answers, and cancels

Each agent contributes two tools:

- `handoff_to_<agent>` with `message` (1 to 32,000 characters), an optional `context` (up to 32,000), and an optional `task_id`. Only the message and the context reach the agent; nothing else from the thread does. Every hand-off from a thread to the same agent belongs to one conversation with it, which survives a restart; another thread has another conversation.
- `cancel_<agent>_task` with the `task_id` to cancel. It cancels only a task this thread started, and a gated cancel offers approve and reject only.

When the agent needs more input, the hand-off's result carries its question, the orchestrator asks you in its own words, and your answer goes back as a hand-off with that task's `task_id`. An answer is a message to the agent like any other: to a gated agent it pauses for a decision. An edit may change the message and the context, never the `task_id`, so an answer or a cancel stays bound to its task.

The agent's answer comes back as the tool result, in the orchestrator's voice: text and structured data as text, and a note in place of each file or link. A hand-off waits at most `FREYJA_A2A_CALL_TIMEOUT_SECONDS`.

### Pending tasks

A task the agent has acknowledged and is still working on at the call limit is **pending**: the hand-off's result says so, and the run ends normally. The service checks pending tasks every `FREYJA_A2A_POLL_INTERVAL_SECONDS`, each by exactly one service instance, also across restarts; a check only reads the task's status and never sends the agent new work. A task that waits for your answer is checked too, so one the agent ends meanwhile is reported instead of waiting until it is given up.

The thread state lists every agent task the thread has started, pending or ended, under `agent_tasks`, and never drops one:

```bash
curl -s localhost:8000/threads/hello-1/state | jq .agent_tasks
```

Each entry has the `agent`, the `task_id`, the `message` it was started with, its `status` (`working`, `input_required`, `completed`, `failed`, `rejected`, `canceled`, or `given_up`), `started_at`, `last_checked_at`, `ended_at`, and `outcome_delivered`. The orchestrator is told on every model call which of the thread's tasks are pending, and its own read-only tool `check_agent_tasks` lists them on demand; the tool exists only when at least one agent is configured.

When a pending task finishes, fails, needs input, or is canceled by the agent, its outcome is recorded in the thread exactly once, as Freyja's `check_agent_tasks` call followed by its result. Your next message gets it at the start of its run, ahead of what you wrote. If no message comes first, a **background run** delivers it as soon as the thread is free: no run in progress and no pending proposal. A background run is a run like any other, with the same gate, step limit, and run lease, so a proposal in it pauses it and you decide it through resume. Background runs do not hold up the checks of other pending tasks, and one service instance runs at most `FREYJA_BACKGROUND_RUN_CONCURRENCY` of them at once; further ones wait their turn. No client is told that a background run happened: read the thread state to see its reply or its pause. A background run that fails with a model or internal error is tried again after `FREYJA_BACKGROUND_RETRY_SECONDS`, up to `FREYJA_BACKGROUND_RUN_ATTEMPTS` attempts in all, without recording the outcome or sending any message twice.

A task still pending after `FREYJA_A2A_TASK_MAX_AGE_SECONDS`, counted from its start, is **given up**: the service stops watching it and reports that the agent may still be working on it. It sends no cancel by itself: ask for one, and the cancel goes through the gate like any message to that agent. A pending task whose agent was removed from the configuration is given up after the restart that applies the change. A saved agent's pending tasks are given up at once when the agent is moved or removed (see [Move or remove an agent](#move-or-remove-an-agent)).

### Add an agent while the service runs

An agent can be added without editing a file or restarting anything, from the Agents section of the [web console](#web-console) or with a request:

```bash
uv run python -m tests.a2a_stub --http 9998      # the stand-in agent, in another terminal
curl -s -X POST localhost:8000/agents -H 'content-type: application/json' \
  -d '{"name": "stub", "url": "http://127.0.0.1:9998"}'
```

The service reads the agent's card at the address first, within `FREYJA_A2A_STARTUP_TIMEOUT_SECONDS` and at most twenty seconds, and sends the agent nothing else: no message and no task. An agent whose card can be used is saved in the database and answered with 201. The next run on every instance sharing the database has its two tools, and a run in progress finishes with the tools it started with. The agent is still there after a restart.

Before it is used, know that:

- **An added agent requires approval unless the request says otherwise.** Every hand-off, answer, and cancel to it then pauses for a person's decision. `"approval": "none"` adds an agent that runs without approval: nothing sent to it is seen by a person first. The console asks for confirmation before it sends that; a request sent straight to the service is not asked.
- **A saved agent's name never changes.** Its approval choice and its address can be changed, and it can be removed, as the next sections say.
- **Every instance has to run this version before the first agent is saved.** An instance on an earlier version knows no saved agent and gives up the tasks of an agent it does not know.

A name is in the form the environment's agents use, and an address is `http` or `https` on this machine (`localhost` or a loopback IP address), with no user name, password, query, or fragment. It is kept in one form, such as `http://127.0.0.1:9998`. A request is refused, and nothing is saved, with:

- 422 and the field's error when a value breaks a rule, never repeating the value;
- 409 `name_taken` when another agent or a tool server has the name, or when another tool has the name of one of the agent's two tools, and `address_taken` when another agent is at the address, however this machine is written;
- 409 `agent_limit` when twenty agents are saved, and `check_busy` while four card reads are under way on the instance;
- 409 `agent_unusable` when the card cannot be used, with `reason` and the service's own sentence: nothing answered, no answer in time, no card, a card that cannot be read, a redirect, a card larger than one megabyte, or a card that requires authentication, accepts no text, or offers no JSON-RPC interface;
- 403 `other_origin` when a browser marks the request as made by a page of another origin.

`GET /agents` lists every agent, with the number of its pending tasks. It and the answers to an add and to a change are the only answers of the service that hold a saved agent's address, in `url`. The address is in no other response, no stream, no thread, and no log line, also where a card names it: its host and port are taken out after any scheme and however this machine is written, and its path with them.

A message is only ever sent to the agent it was proposed for. The service keeps each call of an agent's tool that a model proposes in `freyja_agent_calls`, from the model's reply until the message was sent. A proposal that waits for a decision while its agent leaves the configuration, and another agent is added or configured under the same name, is not sent to that other agent whatever is decided: the call's result says that the agent the message was proposed for was removed.

### Change whether an agent needs approval

A saved agent's approval choice is changed from its card in the Agents section of the [web console](#web-console), behind a confirmation, or with:

```bash
curl -s -X PATCH localhost:8000/agents/<id> -H 'content-type: application/json' \
  -d '{"version": 1, "approval": "none"}'       # or "required"
```

`version` is the one `GET /agents` shows for the agent. The answer is the agent as it is now, with `version` one higher, and `given_up`, which is 0 for this change. The agent is not contacted, so the change does not depend on whether it answers, and its pending tasks stay as they are. A request that names the choice the agent already has changes nothing.

The two directions do not take effect the same way:

- **Requiring approval holds at once**, on every instance, also for a run in progress and for a failed step that is tried again. Before a tool of a saved agent sends anything, it reads the agent from the database. A message that no person decided, to an agent that requires approval by then, is not sent: the call gets an error result, "Agent '<name>' now requires approval, so this message was not sent.", and the run continues. A message whose sending had begun is not recalled.
- **Running without approval holds from the next run.** A run in progress keeps pausing until it ends. A proposal for the agent that is already waiting for a decision can then only be approved, and is sent as proposed.
- **A paused step can become impossible to continue.** When a step waits for a decision and also holds a message to an agent that needed no approval when it was proposed and requires it now, the resume ends with an `error` event of code `approval_now_required` that names the agent. Nothing from the step ran, and the proposal stays. Switch the agent back, then decide again.

A change is refused, and nothing changes, with 409 `agent_changed` when the agent is no longer at `version`, 409 `agent_read_only` for an agent named in the environment and for a saved agent that is not in use, 404 `agent_not_found`, 422 for a body with any field but `version`, `approval`, and `url` (`name` among them), and 403 `other_origin`.

Anyone who can reach the service's port can make an agent run without approval with this request; see [what such a client can do](#saved-agents-beside-the-environments).

### Move or remove an agent

A saved agent is moved to another address, or removed, from its card in the Agents section of the [web console](#web-console), each behind a confirmation, or with:

```bash
curl -s -X PATCH localhost:8000/agents/<id> -H 'content-type: application/json' \
  -d '{"version": 2, "url": "http://127.0.0.1:9996"}'      # move; "approval" may be sent with it
curl -s -X DELETE 'localhost:8000/agents/<id>?version=3'    # remove
```

Both name the `version` that `GET /agents` shows, and both answer with `given_up`: how many of the agent's pending tasks were given up.

- **A move reads the card at the new address first**, within `FREYJA_A2A_STARTUP_TIMEOUT_SECONDS` and at most twenty seconds, and sends the agent nothing else. An agent that cannot be used there is refused with 409 `agent_unusable` and the reason, and the earlier address stays saved and in use. An address that comes to the saved form is no move: another case, a trailing slash, or a default port written out. The same place under another host name is one, such as `localhost` for an agent saved at `127.0.0.1`.
- **A move applies from the next run.** A run in progress finishes at the address it started with. A proposal for the agent that is waiting for a decision goes to the new address once approved.
- **A removal holds at once**, on every instance, also for a run in progress: a tool of a saved agent reads the agent from the database before it sends, and the result is "Agent '<name>' was removed; nothing was sent." The next run has neither of the agent's tools. An agent added again under the name is another agent, and a proposal made for the removed one is never sent to it.
- **Both give up the agent's pending tasks**, together with the change. Each task is listed in its thread as given up at once, a background run tells the thread once, and no cancel is sent: the agent may still be working. After a move the report says the agent was moved and may still be working where it was.
- **A task stays with the agent it was given to.** It is never checked, answered, or canceled at the agent's new address, nor at another agent that has its name: such a call's result is "Agent '<name>' task <id> was given up when the agent was moved or removed; nothing was sent."
- **A run does not send to an address its agent has left once another agent is there.** The result is "Agent '<name>': the address this run started with is another agent's now; nothing was sent."

A move is refused, and nothing changes, with the refusals of a change above, and with 409 `address_taken`, 409 `check_busy`, 409 `agent_unusable`, and 422 for an address that breaks a rule, as for an add. A removal is refused with 409 `agent_changed`, 404 `agent_not_found`, 409 `agent_read_only` for an agent named in the environment, 422 for a missing `version`, and 403 `other_origin`. A saved agent that is not in use can be removed, and nothing else can be done with it.

### Saved agents beside the environment's

An instance has two kinds of agent, and `GET /agents` lists both: the ones named in `FREYJA_A2A_AGENTS`, first and in their order, then the saved ones by name.

- **An agent named in the environment is read-only.** It can be checked, and nothing else: not changed, not removed. Its `id` is `env.<name>`, and its address is shown nowhere, the list included.
- **A saved agent** has an `id`, its `url`, a `version` that every change of its address or approval choice raises, and `pending_tasks`. At most twenty are saved.
- **Each agent has a `state`**, as this instance last found it: `reachable`, `unreachable` with the `reason`, `not_tried`, or `not_in_use`.
- **The environment comes first.** A saved agent cannot take the name or the address of an agent named in the environment, nor the name of a tool server, and neither of its two tool names may be another tool's. When a restart gives the environment one of those, the environment's is the one in use: the saved agent is listed as `not_in_use` with the reason, its pending tasks are given up, a proposal that waited for it is never sent to the environment's agent, and it can only be removed. When a later restart takes the conflict away, it is in use again as it was saved.
- **A change reaches every instance sharing the database** at its next run, its next reading of the list or the catalog, and its next round of watching. A run in progress keeps the tools it started with.

**What anyone who can reach the service's port can now do.** The service has no authentication. Beside starting runs and deciding proposals, such a client can add an agent, on this machine only, since an address must be `localhost` or a loopback address; switch a saved agent to run without approval, so that no person sees what is sent to it; move a saved agent to another address on this machine; remove one; and have any agent's card read again. It can read every saved agent's address. A web page of another origin can do none of this through a browser: the Agents routes refuse it, and give it no permission to read an answer. Keep the service bound to an address only its operators reach.

### Check an agent

Whether an agent can be used is checked on request, from its card in the Agents section of the [web console](#web-console) or with:

```bash
curl -s -X POST localhost:8000/agents/env.hello/check        # an agent named in the environment
curl -s -X POST localhost:8000/agents/<id>/check             # a saved agent, by its id from GET /agents
```

A check reads the agent's card again, within `FREYJA_A2A_STARTUP_TIMEOUT_SECONDS` and at most twenty seconds, and sends the agent nothing else. The answer says whether the agent can be used (`usable`), and when it cannot, which check failed (`reason`) and the service's own sentence for it (`message`), the same nine as for an add. It changes no address, no approval choice, no thread, and no task.

- An agent that can be used is listed as reachable from then on, and a hand-off reaches it.
- An agent that cannot be used is listed as not reachable with the reason. It keeps both of its tools, and a hand-off to it becomes an error result with that reason until a later read of its card succeeds, at the next check or the next hand-off.
- `card_changed` is true when the card differs from the details kept or shown before. From the next run the agent's tools are described from the new details: on every instance for a saved agent, whose details are kept in the database, and on the answering instance for an agent named in the environment.
- `recorded` is false when a saved agent was moved or removed while the check ran. Nothing was kept then.

A check is refused, and the agent is not contacted, with 409 `check_busy` while the same agent is being checked or four card reads are under way on the instance, 404 `agent_not_found` for an id no agent has, 409 `agent_read_only` for a saved agent that is not in use, and 403 `other_origin` for a request a browser marks as made by a page of another origin.

### When an agent fails

At startup, an entry that is invalid in itself stops the service with an error that names the agent, and nothing listens: an address not on this machine, a name shared with a tool server, or a tool name already taken.

An agent whose card cannot be used does not stop startup: a card that cannot be read within `FREYJA_A2A_STARTUP_TIMEOUT_SECONDS` or is redirected, or one that requires authentication, accepts no text, or offers no JSON-RPC interface. The service starts, logs a warning that names the agent and the reason, and lists the agent as not reachable with that reason. The agent keeps both of its tools, and a hand-off to it becomes an error result naming it. Nothing is sent to an agent the service has read no usable card from. All agents are tried at once, so any number of agents that are down delay startup by one time limit. An agent that answers later is used without a restart: the next hand-off to it reads its card first, inside the hand-off's own time limit, and while it has a pending task the service asks for its card once in each round of watching.

During a run, a hand-off to an agent that cannot be reached, does not acknowledge within the call limit, fails or rejects the task, asks for authentication, or replies with something malformed becomes an error result naming the agent; the run goes on, and a warning names the agent. An unacknowledged hand-off's result says the agent may or may not have received it. Nothing is ever sent twice: an approval authorizes one attempt. If the service stops while a message to an agent is under way, the next run on that thread records that the agent may or may not have received it, and the message is not sent again. An agent that answers again is usable without a restart. No error result, log line, thread message, or catalog entry contains an agent's address.

### Agents in the catalog

`GET /tools` lists each agent in use under `agents`, so not a saved agent the environment overrides: its `name`, `reachable`, `checked_at`, and `error` from the most recent attempt to use it (at startup, in a hand-off or cancel, while checking a task, in a check asked for, or when it was added or moved), and its `card`: the card's name, description, version, and skills. The card is the agent's own claim and never changes the classification. Each agent's two tools are in `tools` with `agent` set to its name; every other tool has `agent: null`. The catalog never contacts an agent and never shows its address. An agent the service has not tried yet is listed as not reachable, with a reason that says so; one whose card it has not read has an empty `card`, and its hand-off tool is described by its name alone until the card is read. Before it answers, the catalog reads two rows of the database, to learn whether the agents or the active LLM profile changed; when a read fails it answers from what it last built. With no agents configured, `agents` is empty and the tools are as before.

A proposal for an agent's tool names its agent: each entry of `action_requests` has `agent` set, and `null` for every other tool.

## LLM profiles

An LLM profile is one way to reach a model: a name, a provider (`openai` for any OpenAI-compatible endpoint, `anthropic`, or `openrouter`), a model, an optional base URL, an API key, and, for `openai`, whether the Responses API or chat completions is used. Profiles are kept in the database and changed while the service runs, through the routes under `/llm` or in the [web console](#web-console). Exactly one profile is **active**: the one every run uses. The full contracts are in `specs/004-web-llm-profiles/contracts/`.

What the environment configures (`FREYJA_MODEL`, `FREYJA_OPENAI_RESPONSES_API`, and the provider's own variables) is the **environment's profile**. Its id is `environment` and its name `Environment`; it is read-only, and it is active until a saved profile is activated. A service configured only through the environment behaves as it did before profiles existed, also when it reaches its model through the LangSmith gateway (`LANGSMITH_GATEWAY`): the card then shows the gateway's address and the last four characters of the gateway's key.

### The secret

The keys of saved profiles are stored encrypted under `FREYJA_SECRET_KEY`, a Fernet key. Generate one once, add the printed line to `.env`, and restart the service:

```bash
echo "FREYJA_SECRET_KEY=$(uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
```

Without it the service starts and the environment's profile works as before, but saving a profile is refused with `409` and code `secret_not_configured`. A value that is not a Fernet key stops startup with an error that names the setting, `secret_key`, and not its value. Every instance that shares a database needs the same value. After the secret changes, each saved profile is listed as not usable, with `unusable_reason` `key_unreadable`, until its key is entered again.

### List, add, and activate

```bash
curl -s localhost:8000/llm/profiles | jq
```

The answer has `profiles`, the environment's first and then the saved ones by name, `active_id`, `usable_model` (whether the active profile can be used), `secret_configured`, and `limit`. Each profile has its `id`, `name`, `source` (`saved` or `environment`), `provider`, `model`, `base_url`, `responses_api`, `key_hint`, `active`, `usable`, `unusable_reason`, `last_test`, `created_at`, and `updated_at`. Listing reads the database and the environment and contacts no model endpoint.

Add a profile, then make it the active one:

```bash
ID=$(curl -s -X POST localhost:8000/llm/profiles -H 'content-type: application/json' -d '{
  "name": "Local model", "provider": "openai", "model": "<served model name>",
  "base_url": "http://<host>:<port>/v1", "api_key": "<key>", "responses_api": false}' | jq -r .id)
curl -s -X POST localhost:8000/llm/profiles/$ID/activate | jq .active_id
```

A name has 1 to 64 characters and is unique without regard to case; `Environment` is taken. A model has 1 to 200 characters, a base URL is an `http` or `https` address on any host, and at most 50 profiles can be saved. A profile saved with no base URL is sent to its provider's own service (`https://api.openai.com/v1`, `https://api.anthropic.com`, or `https://openrouter.ai/api/v1`), whatever base URL the environment sets for its own model: the key never goes to an endpoint the profile does not name. Nothing else the environment sets for its provider's client reaches a saved profile either, such as `OPENAI_CUSTOM_HEADERS`, `OPENAI_ORG_ID`, or `OPENAI_PROJECT_ID`: a saved profile is its provider, model, base URL, and key. Two kinds of variable are the exception and apply to saved profiles too, because they are the deployment's: a client's proxy (`OPENAI_PROXY`, `ANTHROPIC_PROXY`), like `HTTP_PROXY`, and a telemetry opt-out (`ANTHROPIC_OPEN_TELEMETRY`). A request that breaks a rule is `422`; a name already in use is `409` with code `name_taken`, and a 51st profile `409` with `profile_limit`. A new profile is not active.

An activation applies without a restart, from the next run, on every instance that shares the database. A run in progress finishes on the model it started with. Existing conversations are sent to the new profile's endpoint from their next run, and a thread paused for a decision continues on the profile that is active when it is resumed. A profile that is not usable cannot be activated (`409`, `profile_unusable`). To go back to the environment's model:

```bash
curl -s -X POST localhost:8000/llm/profiles/environment/activate | jq .active_id   # "environment"
```

Each run names its profile in its `start` event and in one log line. Adding, activating, changing, deleting, and testing a profile each write one log line that names the profile and, for a change, the fields that changed.

### Test a profile

```bash
curl -s -X POST localhost:8000/llm/profiles/$ID/test | jq
# {"ok": true, "at": "...", "step": null, "reason": null, "recorded": true}
```

A test sends the profile's endpoint two requests under one time limit, `FREYJA_LLM_TEST_TIMEOUT_SECONDS`: a first message (`first_message`), then a request that carries earlier turns and a tool call (`conversation`), which is what a run sends after its first step. An endpoint that answers a first message and refuses every later one fails the second step. A failed test names the `step` and gives the `reason`, at most 500 characters.

The last result is kept with a saved profile, also across restarts, and is cleared when its provider, model, base URL, key, or API choice changes. The environment's result is kept in memory until the instance restarts. `POST /llm/test` tests values that are not saved and keeps nothing; it takes the fields of a new profile, and `profile_id` in place of `api_key` uses that saved profile's stored key.

One instance runs one test per profile at a time and four in all. A test beyond either limit is `409` with code `test_busy`, and nothing is sent.

### Change and delete

```bash
curl -s -X PATCH localhost:8000/llm/profiles/$ID -H 'content-type: application/json' \
  -d '{"model": "<another model>"}' | jq
curl -s -X DELETE localhost:8000/llm/profiles/$ID -o /dev/null -w '%{http_code}\n'   # 204
```

A change carries only the fields to change: a key left out keeps the stored key. A change to the active profile applies from the next run, like an activation. The active profile cannot be deleted (`409`, `profile_active`): activate another first. The environment's profile can be neither changed nor deleted (`409`, `profile_read_only`).

### Keys stay secret

A key goes in and never comes out. No route returns it: a profile shows `key_hint`, the key's last four characters, or nothing when the key has fewer than eight. The key is in no log line, stream event, or thread, and where a provider's error repeats it, in a test's reason, a run's `model_error` message, a `debug` event, or what is saved of a failed step, `<key>` stands in its place. A key shorter than eight characters is the exception: it is too short to be told from ordinary text and is not searched for. A refused request to an `/llm` route is answered without the value that was sent.

The API gives no other origin permission to call it, and every route that takes a body refuses one that is not sent as JSON. A request that changes something is also refused with `403` and code `other_origin` when the browser marks it as made by a page of another origin, by `Sec-Fetch-Site` or by an `Origin` that is not the host it asked for. That covers the routes without a body, which a plain form could otherwise reach. So a page from another site cannot change a profile through a browser that can reach the service. The console and clients that are not web pages, such as `curl`, are unaffected.

### No usable model

The active profile is not usable when its stored key cannot be read, when the service has no secret, or when the environment names a provider without a key or a model that cannot be built. The service still starts, `/healthz` answers, and profiles can be listed and changed. A run ends at once with `start` and then `error`:

```json
{"code": "no_usable_model", "message": "No usable model is active. Activate a usable LLM profile, then send the message again."}
```

Nothing is added to the thread and a pending proposal stays pending, so the same request can be sent again once a usable profile is active. The service does not switch to another profile by itself, and background runs wait until a usable one is active.

### Try it with the stand-in endpoint

The test suite's stand-in for an OpenAI-compatible endpoint can be served on loopback, so a profile can be added, tested, and run on without a second account or key:

```bash
uv run --env-file .env python -m tests.llm_stub --http 9997   # base URL http://127.0.0.1:9997/v1
```

Save a profile with provider `openai`, that base URL, any key, and `responses_api` false. The stand-in behaves by the model id it is asked for: `refuse-conversation` answers a first message and refuses a request with earlier turns, `reject-key` answers 401, `repeat-key` fails with an error that repeats the key, `never-answer` does not answer, `slow` answers after 20 seconds, and any other id, such as `ok`, answers everything.

## Web console

The web app under `web/` is the console: a client of the API above that holds no key and calls no model itself. Start the service, then:

```bash
cd web
pnpm dev                       # http://localhost:3000
```

Open <http://localhost:3000>, by that name: in development Next.js serves its dev-only assets to `localhost` only, so the page does not load fully when opened as `127.0.0.1`. The address changes to `/llm`. A sidebar on the left lists the sections, LLM, Agents, and Tasks; on a screen narrower than 768 pixels it opens from a button. Like the API, the console has no authentication.

- **LLM** shows every profile as a card: its name, provider, model, base URL, the last four characters of its key, whether it is active, whether it can be used and why not, and its last test. **Add** saves a new profile, and its form can test the values before they are saved. A card has **Test**, **Set active**, **Edit**, and **Delete**; the environment's card has Test and Set active only. Activating a profile, deleting one, and changing the active one each ask for confirmation first, and activating one that is untested or failed its last test says so and offers to test it. A key typed into a form is sent once and kept nowhere in the browser.
- **Agents** shows every agent Freyja can hand work to as a card: its name, whether its messages require approval, whether it comes from the environment, and whether the service could reach it at its most recent attempt, with the time. An agent that cannot be reached, or a saved agent the environment overrides, carries a warning with the reason. Below that is what the agent's card says of itself, shown as plain text and marked as the agent's own claim. **Add** saves a new agent by its name and address: the service reads the agent's card first and refuses an agent it cannot use, with the reason shown in the form. An agent added here requires approval unless the form's approval choice is set to run without it, which the form asks to confirm, saying that nothing sent to the agent will be seen by a person first. A saved agent's card shows its address. **Edit** on the card of a saved agent that is in use changes its address, its approval choice, or both; its name cannot be changed. Nothing is sent before **Confirm**, which says what the change means: running without approval holds from the next run, requiring approval holds at once, also for runs in progress, and a new address applies from the next run and gives up the agent's pending tasks, whose number it shows. **Remove** on a saved agent's card asks the same way, saying that the removal holds at once and how many pending tasks will be given up. After a move or a removal the section says how many tasks were given up. A form or a dialog that was opened before the agent was changed elsewhere does nothing and says so. A saved agent's card shows its number of pending tasks when it has any. **Check** on a card has the service read the agent's card again and says whether the agent can be used, with the reason when it cannot, and whether the agent's card changed. Every agent that cannot be used is warned about three times from one reading of the list: on its card, in a warning above the cards that names it, and as a number on the sidebar's Agents entry, which shows on every section. An agent named in the environment cannot be changed or removed, and a saved agent that is not in use can only be removed. The list is `GET /agents`, which contacts no agent and never shows the address of an agent named in the environment.

- **Tasks** is where a person hands Freyja work (feature 007, in progress: the list of tasks, follow-ups, and Stop come in later changes). **New task** opens one text box; sending it starts a run on a new task and opens the task's page at `/tasks/<id>`, where Freyja's reply appears as it is written and each step is listed as it completes. When the run ends, the page shows the conversation as the service holds it, with the task's status, and a reload or another browser shows the same. The run goes on if the page is closed. A message the service refuses stays in the box with the service's reason. When no usable model is active, the page says so, points to the LLM section, and offers the text again. When the run pauses on an action that needs approval, the task's page shows a card for the proposal: each action's name, the tool server or agent it goes to, its description, and its arguments in full. Each action offers the decisions the service allows for it: **Approve**, **Edit the arguments**, which opens them as JSON to change before approving, and **Reject**, with an optional reason of up to 4,000 characters. With one action a decision is sent when its button is pressed; with several, each action is decided first and **Send the decisions** sends them together, in the service's order. Text that is not a JSON object is not sent, and an edit the service refuses keeps its text with the service's reason beside it. A decision is sent only by a press of its button, never by Enter in a field, and a button pressed twice sends once. The continuation is shown on the same page as a run is, also when it pauses on another proposal. A decision the service turns away because the proposal was decided elsewhere, nothing is pending, or a run is in progress is said, and the task is shown as it is now; when no answer comes, the page says the outcome is not known and reads the task again. Decisions go through `POST /threads/{thread_id}/resume/unattended`, so every check of a decision is the service's. Everything a task holds is shown as plain text. The console has no users: everyone who can open it sees every task and can decide every proposal, and nothing records who decided; the section says so above its list. It reads tasks with the routes under `/threads` described above, and starts runs with `POST /threads/{thread_id}/runs/unattended`.

When the service cannot be reached, a section says so and offers to try again.

The browser only ever calls the web app's own origin. The web app's server forwards `/api/...` to the service at `FREYJA_API_URL`, `http://127.0.0.1:8000` by default; set the variable before `pnpm dev` when the service listens elsewhere. `pnpm build` writes the address into the build and `pnpm start` does not read it again, so a built app is built again to change it. The variable is documented in `web/.env.example` and read in `web/next.config.ts`.

## Lint and format

Ruff handles both linting and formatting for Python. Configuration lives in `pyproject.toml` under `[tool.ruff]`.

```bash
uv run ruff check .            # lint
uv run ruff check . --fix      # lint and apply safe autofixes
uv run ruff format .           # format in place
uv run ruff format --check .   # report unformatted files without changing them (CI mode)
```

Biome does the same for the web app. Configuration lives in `web/biome.json`.

```bash
cd web
pnpm lint                      # biome check: lint and report unformatted files
pnpm format                    # format in place
```

The pre-commit hook first checks the staged files themselves: it removes whitespace at the end of a line, makes a file end with one newline, and stops a commit that holds YAML that does not parse, a marker a merge left behind, or a secret (see [Secrets](#secrets)). It then runs `ruff check --fix` and `ruff format` on staged Python files, then the Python unit test suite. When a file under `web/` is staged, it also runs `biome check --write` over the web app and then the web unit tests. If Ruff or Biome changes a file, the commit is blocked: re-stage the file and commit again. If a test fails, the commit is blocked until it passes.

The web hooks run the scripts in `web/package.json` with pnpm, so they need Node and pnpm on the path and the dependencies installed in `web/`. Without pnpm they fail with ``Executable `pnpm` not found``. To run the hook over the whole repo:

```bash
uv run pre-commit run --all-files
```

CI runs both linters on every pull request and merge request and on `main`: `.github/workflows/lint.yml` on GitHub, and the `ruff` and `biome` jobs of `.gitlab-ci.yml` on GitLab. There they only report: `ruff check`, `ruff format --check`, and `biome check` change no file, so a failure is fixed locally with the commands above.

## Secrets

[gitleaks](https://github.com/gitleaks/gitleaks) looks for keys, tokens, and passwords in what is committed. The commit hook runs it over the staged changes and stops the commit when it finds one. pre-commit builds gitleaks itself the first time, which takes about a minute and needs the network; nothing is installed by hand.

CI runs it over every commit, on every pull request and merge request and on `main`: `.github/workflows/secrets.yml` on GitHub and the `gitleaks` job of `.gitlab-ci.yml` on GitLab. To do the same locally:

```bash
docker run --rm -v "$PWD":/repo -w /repo zricethezav/gitleaks:v8.30.1 git --redact .
```

The rules are gitleaks' own, extended in `.gitleaks.toml` by the made-up keys the tests use, such as `sk-recognizable-key-0001`. They are let through as exact values and not by their path, so a real key pasted into a test is still found. A test that needs a made-up key uses one of those values; a new shape is added to that file.

A finding is reported without its value. A secret that was committed is in the history even after the file is fixed: change the secret at its provider first, then remove it. The hook's file checks leave what Spec Kit generates alone (`.specify/`, `.claude/skills/`, `.agents/skills/`).

## Unit tests

```bash
uv run pytest                  # full suite
uv run pytest -q               # quiet output
uv run pytest tests/test_health.py::test_healthz   # a single test
uv run pytest --cov            # full suite with coverage; fails below the threshold
```

The web app's unit tests run with Vitest:

```bash
cd web
pnpm test                      # vitest run
pnpm test:coverage             # with coverage; fails below the thresholds
```

They are `*.test.ts` and `*.test.tsx` files under `web/__tests__/`, which follows the layout of the code it covers: `web/__tests__/lib/api.test.ts` covers `web/lib/api.ts`. They run with jsdom and reach no service: `web/__tests__/testing.ts` replaces `fetch`.

The Python tests live in `tests/`. The suite runs the production routes through FastAPI's test client with in-memory persistence and a scripted model, so it needs neither a database nor a model provider. Tool servers are covered by a stub MCP server, `tests/mcp_stub.py`, which most tests reach inside the test process; the transport tests launch it over pipes and serve it on the loopback interface. Agents are covered the same way by a stand-in A2A agent, `tests/a2a_stub.py`, reached inside the test process through a streaming transport; only `tests/test_a2a_transport.py` serves it on loopback. LLM profiles are covered by a stand-in model endpoint, `tests/llm_stub.py`, which the suite reaches inside the test process through the real OpenAI client. The suite needs no network, no installed MCP server, no running agent, and no model endpoint.

To try the stand-in agent by hand, serve it on loopback and list it as an agent:

```bash
uv run python -m tests.a2a_stub --http 9998   # http://127.0.0.1:9998
```

It acts on a keyword at the start of the message, such as `ask`, `slow <seconds>`, `fail`, or `reject`; the list is at the top of `tests/a2a_stub.py`. The stand-in model endpoint is served the same way; see [Try it with the stand-in endpoint](#try-it-with-the-stand-in-endpoint).

The Postgres integration tests under `tests/integration/` are skipped unless `FREYJA_TEST_DATABASE_URI` names a database that may be wiped. Create it once in the compose container, then run them:

```bash
docker compose exec postgres createdb -U freyja freyja_test
FREYJA_TEST_DATABASE_URI=postgresql://freyja:freyja@localhost:5432/freyja_test uv run pytest tests/integration
```

CI runs both unit test suites on every pull request and merge request and on `main`: `.github/workflows/unit-tests.yml` on GitHub, and the `pytest` and `vitest` jobs of `.gitlab-ci.yml` on GitLab. The Postgres tests under `tests/integration` stay opt-in and are skipped there.

### Coverage

Both suites have a floor, and the commit hook and CI run them with coverage, so a change that falls below it is stopped in all three places.

| Project | Measured | Floor | Where it is set |
|---|---|---|---|
| Service | 89.97% of lines | 88% | `fail_under` under `[tool.coverage.report]` in `pyproject.toml` |
| Web app | 95.05% of statements, 93.67% of branches, 95% of functions, 96.74% of lines | 90% each | `coverage.thresholds` in `web/vitest.config.mts` |

The numbers were measured on 2026-11-19, as CI runs the suites. For the service that means without the Postgres tests, which leaves the Postgres stores unmeasured; with them it is 96.62%. Nothing of either project is left out of the count, except `web/components/ui/`, which shadcn generates. A run without coverage (`uv run pytest`, `pnpm test`) is not measured, so running one file is not held to the whole suite's number. Raise a floor as its number rises.

The service's lines are counted with Python 3.12's own monitoring (`core = "sysmon"`), which adds about a second to the suite; on 3.12 that counts lines and not branches.

## End-to-end tests

The web app's end-to-end tests run with [Playwright](https://playwright.dev) in Chromium and Firefox:

```bash
cd web
pnpm exec playwright install chromium firefox   # once per machine: downloads the two browsers
pnpm test:e2e                  # playwright test
```

Playwright builds the app and serves it on port 3100 itself, so nothing needs to be running first, the Freyja service included: each test hands the page what the service would answer (`web/e2e/api.ts`), and the tests about a stopped service let the request through to an address nothing listens on. The tests are `*.spec.ts` files under `web/e2e/`: `console.spec.ts` for the sidebar and the sections, `llm.spec.ts` for the LLM section as it is read, `profiles.spec.ts` for adding, activating, testing, changing, and deleting profiles, `agents.spec.ts` for the Agents section as it is read, its warnings, and that an agent's address stays in it, and `agents-manage.spec.ts` for adding an agent, checking one, changing one's approval choice, moving one, and removing one. Playwright reads nothing outside that directory, and Vitest skips it, so `pnpm test` and `pnpm test:e2e` never run each other's files. After a run, `pnpm exec playwright show-report` opens the report. To test in another browser as well, add a project for it in `web/playwright.config.ts`.

The commit hook does not run them. CI does, when a file under `web/` changes: `.github/workflows/playwright.yml` on GitHub and `.gitlab-ci.yml` on GitLab, each keeping the report as an artifact. The GitLab job runs in Playwright's own image, whose version must equal the `@playwright/test` version in `web/pnpm-lock.yaml`.

## Container images

Two images, one per process: the service (`Dockerfile`, built from the repository root) and the web console (`web/Dockerfile`, built from `web/`).

```bash
docker build -t freyja-agent .
docker build -t freyja-web web
```

Both are built on Chainguard's Wolfi base, hold no configuration and no credential, run as an unprivileged user (65532), and need no writable file system apart from `/tmp`. The service also writes case files under `/app/workspace/cases`; mount a volume there to keep them.

**Both listen on loopback inside their container.** The API and the console have no login, so the images keep the default a developer's machine has: the service on `127.0.0.1:8000` and the console on `127.0.0.1:3000`. Two containers that share a network namespace reach each other there, which is how they are meant to run: as two containers of one pod, or on the host's network.

```bash
docker run -d --network host --read-only --tmpfs /tmp \
  -e FREYJA_DATABASE_URI=postgresql://freyja:freyja@localhost:5432/freyja freyja-agent
docker run -d --network host --read-only --tmpfs /tmp freyja-web
```

The service container reads the same `FREYJA_*` variables as `freyja-api` does anywhere; `FREYJA_DATABASE_URI` is the one it cannot start without. Give them one by one or from the orchestrator's own configuration: `docker run --env-file` keeps the quotes that `.env.example` puts around a JSON value. To reach either from outside its container, for example through `docker run -p` or a Kubernetes Service, set `FREYJA_HOST=0.0.0.0` for the service or `HOSTNAME=0.0.0.0` for the console, and do it only where the network around the container already decides who may connect: whoever reaches the console can change the service's configuration.

**The web image holds the service's address.** Next.js writes `FREYJA_API_URL` into the build, so it is given when the image is built and a container cannot change it. The default, `http://127.0.0.1:8000`, is right for the shared network namespace above. For another layout, build the image for it:

```bash
docker build --build-arg FREYJA_API_URL=http://freyja-agent:8000 -t freyja-web web
```

The base image and the image uv is taken from can come from a mirror: `--build-arg BASE_IMAGE=...` for both Dockerfiles and `--build-arg UV_IMAGE=...` for the service's. The service image carries Python and the service alone. A tool server that Freyja launches with a command (`FREYJA_MCP_SERVERS` with `command`) needs that command in the image, and `uvx`, `npx`, and `git` are not there: use a server with a `url`, or build an image of your own on top of this one.

Each image has a health check: the service's asks `/healthz`, the console's its own LLM page, both on loopback.

## Security scans

[Trivy](https://trivy.dev) looks for known vulnerabilities in what `uv.lock` and `web/pnpm-lock.yaml` install and for mistakes in the Dockerfiles, and [Semgrep](https://semgrep.dev) for insecure code. Trivy is installed by hand; Semgrep runs through uv.

```bash
trivy fs --scanners vuln,misconfig --severity HIGH,CRITICAL --exit-code 1 \
  --skip-dirs .venv --skip-dirs web/node_modules --skip-dirs web/.next .
uvx semgrep@1.178.0 scan --metrics=off --error \
  --config p/default --config p/security-audit --config p/owasp-top-ten --config p/dockerfile .
trivy image --scanners vuln --severity HIGH,CRITICAL --exit-code 1 freyja-agent   # a built image
```

Both must report nothing. A vulnerable package is upgraded in its lock file (`uv lock --upgrade-package <name>`). A Semgrep finding that is wrong is marked `# nosemgrep: <rule id>` on the line before it, with the reason near it. `--metrics=off` keeps Semgrep from sending anything about the repository; the rule sets are named because `auto` needs metrics on. Leave Trivy's `secret` scanner off locally: it reads files git ignores, `.env` among them, and prints what it finds.

CI runs the two scans of the source on every pull request and merge request and on `main`: the `trivy` and `semgrep` jobs of `.github/workflows/scan.yml` on GitHub and of `.gitlab-ci.yml` on GitLab. On GitHub a third job, `images`, builds both images, starts them beside a Postgres, asks the service and the console for a page, checks that the console does not answer on a published port unless `HOSTNAME=0.0.0.0` is set, and scans both images. GitLab has no such job: building an image needs a runner that allows it.

## Configuration

Settings are read from `FREYJA_*` environment variables by `src/freyja_agent/config.py`. Load them with `uv run --env-file .env <command>`.

| Variable | Default | Purpose |
|---|---|---|
| `FREYJA_HOST` | `127.0.0.1` | Bind address for `freyja-api`. The container image keeps the default; see [Container images](#container-images). |
| `FREYJA_PORT` | `8000` | Bind port for `freyja-api`. |
| `FREYJA_DATABASE_URI` | none, required | Postgres connection for the checkpointer, store, and run locks. No default because it carries credentials; `.env.example` has the value the compose container uses. |
| `FREYJA_MODEL` | `anthropic:claude-sonnet-5-5` | Model for the orchestrator, as a LangChain `provider:model` string. Installed providers: `openai`, `anthropic`, `openrouter`. With the provider's variables below it is the environment's [LLM profile](#llm-profiles), used until a saved profile is activated. |
| `FREYJA_OPENAI_RESPONSES_API` | `true` | Whether an `openai:` model is called through OpenAI's Responses API or, when `false`, through chat completions. Set `false` for an OpenAI-compatible server whose Responses API answers a first message and refuses every later one. Other providers ignore it. |
| `FREYJA_WORKSPACE_DIR` | `workspace` | Root of the orchestrator's file tools. `skills/` inside it is committed source; `cases/` is written at runtime and git-ignored. |
| `FREYJA_MAX_STEPS` | `50` | Maximum orchestrator steps per run. Reaching it ends the run with an `error` event of code `step_limit`; the thread stays usable. A plain reply takes about 7 steps and each tool call about 4 more, so the default allows roughly ten tool calls per turn and values below 11 stop every tool call. |
| `FREYJA_RUN_LOCK_TTL_SECONDS` | `60` | Expiry of the per-thread run lease. A run renews it while streaming; a crashed instance frees its threads after this long. |
| `FREYJA_MCP_SERVERS` | `{}` | Tool servers as a JSON object; see [Tool servers](#tool-servers). The default is none. |
| `FREYJA_MCP_CALL_TIMEOUT_SECONDS` | `30` | Time limit for one tool server call, including connecting. A call over it returns an error result and the run continues. |
| `FREYJA_MCP_STARTUP_TIMEOUT_SECONDS` | `20` | Time limit per server for connecting and listing its tools at startup. Servers connect concurrently. |
| `FREYJA_A2A_AGENTS` | `{}` | Agents as a JSON object; see [Agents](#agents). The default is none. |
| `FREYJA_A2A_CALL_TIMEOUT_SECONDS` | `30` | How long one hand-off, answer, or cancel waits within a run. A task still working at the limit becomes pending. |
| `FREYJA_A2A_STARTUP_TIMEOUT_SECONDS` | `20` | Time limit per agent for reading its card at startup. Agents start concurrently. A card read asked for through the Agents routes (an add, a move, a check) takes this long at most, and never more than twenty seconds. |
| `FREYJA_A2A_POLL_INTERVAL_SECONDS` | `10` | How often pending tasks are checked and waiting outcomes delivered. |
| `FREYJA_A2A_TASK_MAX_AGE_SECONDS` | `86400` | When a pending task is given up, counted from its start. No cancel is sent. |
| `FREYJA_BACKGROUND_RUN_ATTEMPTS` | `3` | Attempts for one background run, the first included. |
| `FREYJA_BACKGROUND_RETRY_SECONDS` | `60` | Wait between background run attempts. |
| `FREYJA_BACKGROUND_RUN_CONCURRENCY` | `4` | Background runs under way at once on one service instance; further ones wait their turn. |
| `FREYJA_SECRET_KEY` | unset | The secret that protects the API keys of saved LLM profiles: a Fernet key, 44 URL-safe base64 characters. Unset, the service starts and saves no profile; a value that is not a Fernet key stops startup. See [The secret](#the-secret). |
| `FREYJA_LLM_TEST_TIMEOUT_SECONDS` | `60` | The most a test of an LLM profile may take, both requests together. Greater than 0 and at most 60. |
| `OPENAI_API_KEY`, `OPENAI_API_BASE` | | Key for `openai:` models. `OPENAI_API_BASE` is optional: set it for self-hosted OpenAI-compatible servers or to route through a gateway. |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_API_URL` | | Key for `anthropic:` models. `ANTHROPIC_API_URL` is optional: set it to route through a gateway. |
| `OPENROUTER_API_KEY`, `OPENROUTER_API_BASE` | | Key for `openrouter:` models; the model id is OpenRouter's slug. `OPENROUTER_API_BASE` is optional: set it to route through a proxy. |

`FREYJA_TEST_DATABASE_URI` is not a setting: it is read only by the integration tests, as described above.

The web app has one variable of its own, read by `web/next.config.ts` and documented in `web/.env.example`:

| Variable | Default | Purpose |
|---|---|---|
| `FREYJA_API_URL` | `http://127.0.0.1:8000` | Where the web app's server forwards `/api/...`: the Freyja service. Read when `pnpm dev` starts and when `pnpm build` runs. |

## Spec Kit workflow

Features are built with [Spec Kit](https://github.com/github/spec-kit). Each step is a skill run inside a coding agent: Claude Code invokes them as `/speckit-<step>`, Codex CLI as `$speckit-<step>`. A human reviews every step's output in a pull request before the next step runs, and commits stay manual with the `type: summary` form.

Spec Kit finds the current feature through `.specify/feature.json`, which is git-ignored and written by `specify`. Check it before any step when more than one feature exists:

```bash
cat .specify/feature.json
# {"feature_directory": "specs/001-thread-runs-api"}
```

The constitution in `.specify/memory/constitution.md` was written once with `/speckit-constitution` and is amended through a pull request that bumps its version.

The artifacts cross-reference each other with short ids: `FR-###` is a functional requirement and `SC-###` a success criterion in `spec.md`, `US#` a user story, `R#` a decision in `research.md`, `T###` a task in `tasks.md`, and `CHK###` an item in a checklist. A task that says `(FR-006, R7)` implements that requirement following that research decision, which is how a reviewer traces any line of code back to the spec. The analyze step's report, which is printed rather than saved, numbers its findings by category: `I1` is the first inconsistency, `C1` the first coverage gap, `A1` the first ambiguity, `U1` underspecification, `D1` duplication.

### Before every step: recreate the feature branch

Pull requests are squash-merged and the branch is deleted on merge. That means **after every merge, the feature branch you were on no longer exists**, and the next step must not run on `main` or on a stale local branch. Before each step below, run:

```bash
git switch main && git pull
git switch -c 001-thread-runs-api
```

Skipping this is the most common mistake. The symptoms: a step writes files on `main`, a push is rejected because the remote branch is gone, or a PR shows commits from an earlier step that was already merged. If `git status` does not show a clean tree on a branch that was just created from the latest `main`, stop and fix that first.

The first step, specify, is the one exception: its git hook creates the branch for you.

### One feature, end to end

**1. Specify.** Describe the feature in one paragraph. The git extension creates the numbered branch, and the skill writes `specs/001-thread-runs-api/spec.md` plus a spec quality checklist, `checklists/requirements.md`.

```text
/speckit-specify Thread runs API: the minimal runtime. POST /threads/{thread_id}/runs accepts a
message and streams the run over SSE ... Out of scope: delegate agents, authentication.
```

Review `spec.md`, then commit and open the PR:

```bash
git add specs && git commit -m "docs: add thread runs API spec (001)"
git push -u origin 001-thread-runs-api && gh pr create --base main
```

Merge it. **Recreate the branch** before step 2.

**2. Clarify** (optional gate). Asks up to five questions about underspecified areas and writes the answers back into `spec.md` under a `## Clarifications` section.

```text
/speckit-clarify
```

Commit as `docs: clarify thread runs API spec (001)`, open a PR, merge. **Recreate the branch** before step 3.

**3. Plan.** Reads the spec and the constitution and writes `plan.md`, `research.md`, `data-model.md`, `contracts/`, and `quickstart.md`. Check the plan's Constitution Check names the principles it touches.

```text
/speckit-plan
```

Commit as `docs: plan thread runs API (001)`, PR, merge. **Recreate the branch** before step 4.

**4. Checklist** (optional gate). Writes `checklists/<domain>.md`: questions that test whether the requirements in one focus area are complete, clear, and consistent, not whether the code works. Name the focus area in the argument; the skill may ask up to five scoping questions first. This file is separate from the `checklists/requirements.md` that specify wrote.

```text
/speckit-checklist api: error responses, stream events, and resume decisions
```

The skill leaves every item unchecked. Review each one and tick it once the spec and plan satisfy it; where they do not, fix `spec.md` or `plan.md` in the same PR. Leave nothing open: implement stops and asks before it starts when any checklist item is unchecked. Commit as `docs: add thread runs API checklist (001)`, PR, merge. **Recreate the branch** before step 5.

**5. Tasks.** Writes `tasks.md`: setup, foundational, one phase per user story, polish, with `[P]` on tasks that can run in parallel.

```text
/speckit-tasks
```

Commit as `docs: add thread runs API tasks (001)`, PR, merge. **Recreate the branch** before step 6.

**6. Analyze** (optional gate). Read-only cross-check of spec, plan, and tasks: coverage gaps, contradictions, constitution conflicts. Fix what it finds by editing the files, then commit as `docs: reconcile spec, plan, and tasks after analysis (001)`, PR, merge. **Recreate the branch** before step 7.

```text
/speckit-analyze
```

**7. Implement.** Works through `tasks.md` phase by phase, ticking tasks as they pass. Stop at a phase checkpoint to review and ship, then continue:

```text
/speckit-implement Stop after Phase 2 (Foundational). Do not start Phase 3.
```

Each phase becomes its own pull request with a `feat:` message, for example `feat: runtime foundation for thread runs (001, phases 1-2)`. After it merges, **recreate the branch** before continuing with the next phase. Before the PR: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, and the checkpoint in `tasks.md` for that phase.

**8. Converge.** After the last phase, compares the code against the spec, plan, and tasks and appends any unbuilt work to `tasks.md` so one more implement pass can finish it.

```text
/speckit-converge
```

### Codex CLI

The same skills live under `.agents/skills/` for Codex CLI, installed with `specify integration install codex`. Replace `/speckit-<step>` with `$speckit-<step>`; everything else, including the branch and PR routine, is identical.
