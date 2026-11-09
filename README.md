# Freyja

General-purpose agent orchestrator built on [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview) and served with FastAPI. The orchestrator routes work to the right delegate agent and keeps a human in the loop for any action with effects outside its workspace.

## Setup

```bash
uv sync                         # creates .venv and installs runtime + dev dependencies
cp .env.example .env            # pick one model block and set its key; leave the database line as is
docker compose up -d postgres   # local Postgres; its credentials equal FREYJA_DATABASE_URI in .env.example
uv run pre-commit install       # one-time per clone: runs Ruff and the unit tests on every commit
```

`.env` is git-ignored. The compose file and `.env.example` carry the same local-only database credentials, so the copied file connects without edits; only the model key needs filling in.

## Run the dev server

With auto-reload, listening on `127.0.0.1:8000`:

```bash
uv run --env-file .env uvicorn freyja_agent.api.app:app --reload
```

Or through the console script, which reads the bind address from settings:

```bash
uv run --env-file .env freyja-api
```

Startup opens one Postgres connection pool and creates the LangGraph checkpointer and store tables plus the `freyja_run_locks` table if they are missing. If the database cannot be reached, startup fails with an error that names the connection (password redacted) and the process exits instead of serving.

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

Each event has an `event:` line naming its kind and a `data:` line holding one JSON object. The first event is always `start`. Then come `update` (one per orchestrator step, with messages, tool calls, and tool results), `message` (the reply as it is produced), and, with `?debug=true`, `debug` (raw LangGraph detail; it never changes the run). Exactly one terminal event closes the stream: `end` when the reply is finished, `interrupt` when the orchestrator proposes an action that needs approval, or `error` with a `code` of `step_limit`, `model_error`, or `internal`. A later message to the same thread continues the conversation, including after a service restart.

One run per thread: a second run while one streams gets `409` with code `run_active`. The marker is a lease in the database, so it holds across service instances and expires after `FREYJA_RUN_LOCK_TTL_SECONDS` if the instance holding it dies. A clean stop (Ctrl-C or SIGTERM) waits for in-flight runs to finish before closing the database pool, because uvicorn's graceful shutdown has no timeout by default; pass `--timeout-graceful-shutdown` to uvicorn to cap the wait. Only a crash leaves a lease behind, and it expires.

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

`GET /tools` lists every tool the orchestrator was given at startup and the status of every server. It answers from memory and never contacts a server.

```bash
curl -s localhost:8000/tools | jq
```

Each tool has its `name`, its `server` (`null` for Freyja's own tools), `description`, `input_schema`, `classification`, `allowed_decisions`, and `server_hints`. The classification is `read_only` (runs without a pause), `workspace` (Freyja's own file-writing and subagent tools, which change only the workspace), or `gated` (every call pauses). `server_hints` are what the server says about its own tool, such as a read-only label; they are shown as its claim and never change the classification. Each server has its `connection` form (`command` or `address`), `tool_count`, and `reachable`, `checked_at`, and `error` from the most recent attempt to use it, at startup or in a call. The catalog never shows a server's command, arguments, address, or variable names.

A proposal for a server tool names its server: each entry of `action_requests` in the `interrupt` event and in the thread state's `pending` has `server` set to the server's name, and `null` for Freyja's own tools.

## Lint and format

Ruff handles both linting and formatting. Configuration lives in `pyproject.toml` under `[tool.ruff]`.

```bash
uv run ruff check .            # lint
uv run ruff check . --fix      # lint and apply safe autofixes
uv run ruff format .           # format in place
uv run ruff format --check .   # report unformatted files without changing them (CI mode)
```

The pre-commit hook runs `ruff check --fix` and `ruff format` on staged files, then the unit test suite. If Ruff changes a file, the commit is blocked: re-stage the file and commit again. If a test fails, the commit is blocked until it passes. To run the hook over the whole repo:

```bash
uv run pre-commit run --all-files
```

## Unit tests

```bash
uv run pytest                  # full suite
uv run pytest -q               # quiet output
uv run pytest tests/test_health.py::test_healthz   # a single test
```

Tests live in `tests/`. The suite runs the production routes through FastAPI's test client with in-memory persistence and a scripted model, so it needs neither a database nor a model provider. Tool servers are covered by a stub MCP server, `tests/mcp_stub.py`, which most tests reach inside the test process; the transport tests launch it over pipes and serve it on the loopback interface. The suite needs no network and no installed MCP server.

The Postgres integration tests under `tests/integration/` are skipped unless `FREYJA_TEST_DATABASE_URI` names a database that may be wiped. Create it once in the compose container, then run them:

```bash
docker compose exec postgres createdb -U freyja freyja_test
FREYJA_TEST_DATABASE_URI=postgresql://freyja:freyja@localhost:5432/freyja_test uv run pytest tests/integration
```

## Configuration

Settings are read from `FREYJA_*` environment variables by `src/freyja_agent/config.py`. Load them with `uv run --env-file .env <command>`.

| Variable | Default | Purpose |
|---|---|---|
| `FREYJA_HOST` | `127.0.0.1` | Bind address for `freyja-api`. Containers set `0.0.0.0`. |
| `FREYJA_PORT` | `8000` | Bind port for `freyja-api`. |
| `FREYJA_DATABASE_URI` | none, required | Postgres connection for the checkpointer, store, and run locks. No default because it carries credentials; `.env.example` has the value the compose container uses. |
| `FREYJA_MODEL` | `anthropic:claude-sonnet-5-5` | Model for the orchestrator, as a LangChain `provider:model` string. Installed providers: `openai`, `anthropic`, `openrouter`. |
| `FREYJA_WORKSPACE_DIR` | `workspace` | Root of the orchestrator's file tools. `skills/` inside it is committed source; `cases/` is written at runtime and git-ignored. |
| `FREYJA_MAX_STEPS` | `50` | Maximum orchestrator steps per run. Reaching it ends the run with an `error` event of code `step_limit`; the thread stays usable. A plain reply takes about 7 steps and each tool call about 4 more, so the default allows roughly ten tool calls per turn and values below 11 stop every tool call. |
| `FREYJA_RUN_LOCK_TTL_SECONDS` | `60` | Expiry of the per-thread run lease. A run renews it while streaming; a crashed instance frees its threads after this long. |
| `FREYJA_MCP_SERVERS` | `{}` | Tool servers as a JSON object; see [Tool servers](#tool-servers). The default is none. |
| `FREYJA_MCP_CALL_TIMEOUT_SECONDS` | `30` | Time limit for one tool server call, including connecting. A call over it returns an error result and the run continues. |
| `FREYJA_MCP_STARTUP_TIMEOUT_SECONDS` | `20` | Time limit per server for connecting and listing its tools at startup. Servers connect concurrently. |
| `OPENAI_API_KEY`, `OPENAI_API_BASE` | | Key for `openai:` models. `OPENAI_API_BASE` is optional: set it for self-hosted OpenAI-compatible servers or to route through a gateway. |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_API_URL` | | Key for `anthropic:` models. `ANTHROPIC_API_URL` is optional: set it to route through a gateway. |
| `OPENROUTER_API_KEY`, `OPENROUTER_API_BASE` | | Key for `openrouter:` models; the model id is OpenRouter's slug. `OPENROUTER_API_BASE` is optional: set it to route through a proxy. |

`FREYJA_TEST_DATABASE_URI` is not a setting: it is read only by the integration tests, as described above.

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
