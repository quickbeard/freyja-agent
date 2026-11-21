# Freyja

General-purpose agent orchestrator built on Deep Agents (LangGraph) and served with FastAPI. It
routes work to delegate agents and keeps a human in the loop for any action with effects outside
its workspace.

This file is the shared instruction set for every coding agent used on the repo (Claude Code,
Codex CLI). The project principles live in the Spec Kit constitution at
`.specify/memory/constitution.md` and are binding in every session. If that file is not already
in your context, read it before planning or changing code.

## Commands

Always go through uv. Never call python, pip, or pytest directly.

- `uv sync` installs runtime and dev dependencies into `.venv`.
- `uv run --env-file .env uvicorn freyja_agent.api.app:app --reload` runs the dev server on
  127.0.0.1:8000.
- `uv run --env-file .env freyja-api` runs it through the console script, binding from settings.
- `uv run ruff check .` and `uv run ruff format .` lint and format; use `--fix` and `--check`
  variants as usual.
- `uv run pytest` runs the suite. Tests run the production routes through FastAPI's test
  client with in-memory persistence and a scripted model; no server, database, or model
  provider is needed. Tool servers are played by the stub in `tests/mcp_stub.py`, and A2A
  agents by the stand-in in `tests/a2a_stub.py`, so no MCP server or agent needs to be
  installed or running and no network is used; only the transport tests launch a stub as a
  process or serve it on the loopback interface.
- `uv run pytest --cov` runs the suite with coverage and fails below `fail_under` in
  `pyproject.toml`. The commit hook and CI run it this way. Without `--cov` nothing is
  measured, so a run of one file is not held to the suite's number.
- `uv run python -m tests.mcp_stub --http 8765` serves the stub at
  `http://127.0.0.1:8765/mcp` for a manual run against an address server.
- `uv run python -m tests.a2a_stub --http 9998` serves the stand-in agent at
  `http://127.0.0.1:9998` for a manual run; add it to `FREYJA_A2A_AGENTS` under any name.
- `uv run --env-file .env python -m tests.llm_stub --http 9997` serves the stand-in model
  endpoint for a manual run; use `http://127.0.0.1:9997/v1` as an LLM profile's base URL. It
  behaves by model id: `refuse-conversation`, `reject-key`, `repeat-key`, `never-answer`,
  `slow`, and any other id answers. A request that asks for a stream is answered as one, so
  a profile pointing at it can be run on.
- `FREYJA_TEST_DATABASE_URI=postgresql://freyja:freyja@localhost:5432/freyja_test uv run pytest
  tests/integration` runs the opt-in Postgres tests; without the variable they are skipped.
  Create the database once with `docker compose exec postgres createdb -U freyja freyja_test`.
- `uv run pre-commit run --all-files` runs the commit hook over the whole repo: the file
  checks (whitespace at the end of a line, the newline at the end of a file, YAML that
  parses, merge markers), gitleaks over what is staged, Ruff and
  pytest, then Biome and Vitest for the web app. The web hooks run when a file under `web/` is
  staged and need Node and pnpm on the path and the dependencies installed in `web/`.
  pre-commit builds gitleaks itself the first time it runs, which takes about a minute and
  needs the network.
- `docker run --rm -v "$PWD":/repo -w /repo zricethezav/gitleaks:v8.30.1 git --redact .`
  searches every commit for secrets, as CI does.
- In `web/`: `pnpm install` installs the web app's dependencies, `pnpm dev` and `pnpm build`
  run and build it, `pnpm lint` runs `biome check`, `pnpm format` formats in place, and
  `pnpm test` runs the unit tests with Vitest.
- In `web/`: `pnpm test:coverage` runs the unit tests with coverage and fails below the
  thresholds in `web/vitest.config.mts`. The commit hook and CI run it this way.
- In `web/`: `pnpm test:e2e` runs the end-to-end tests with Playwright in Chromium and Firefox,
  the browsers `web/playwright.config.ts` names. Playwright builds the app and serves it on port
  3100 itself, so nothing needs to be running, the Freyja service included. `pnpm exec
  playwright install chromium firefox` downloads the two browsers once per machine. The commit
  hook does not run these tests; CI does.
- The opt-in Postgres tests drop every table of the database they are given after each test.
  Two sessions that run them at the same time each need a database of their own, such as
  `freyja_test_tasks`, made once with `createdb` like `freyja_test`.
- `docker compose up -d postgres` starts the local Postgres the service and the integration
  tests use; its credentials equal `FREYJA_DATABASE_URI` in `.env.example`.
- `docker build -t freyja-agent .` and `docker build -t freyja-web web` build the two container
  images, the service and the web console. `--build-arg FREYJA_API_URL=...` gives the web
  image another address for the service; it is written into the build.
- `trivy fs --scanners vuln,misconfig --severity HIGH,CRITICAL --exit-code 1 --skip-dirs .venv
  --skip-dirs web/node_modules --skip-dirs web/.next .` and `uvx semgrep@1.178.0 scan
  --metrics=off --error --config p/default --config p/security-audit --config p/owasp-top-ten
  --config p/dockerfile .` are the two scans of the source CI runs, and `trivy image --scanners
  vuln --severity HIGH,CRITICAL --exit-code 1 freyja-agent` the scan of a built image. Trivy
  is installed by hand; leave its `secret` scanner off locally, where it would print `.env`.

`.env` is git-ignored; copy it from `.env.example`. Settings do not read `.env` themselves, so
pass `--env-file .env` to `uv run` whenever a command needs it. The service needs Postgres at
startup and exits with an error naming the connection when it is unreachable.

## Layout

- `src/freyja_agent/config.py`: the `Settings` class (pydantic-settings, `FREYJA_*` prefix) and
  the shared `settings` instance. `FREYJA_MODEL` names the model, and
  `FREYJA_OPENAI_RESPONSES_API` says whether an `openai:` model is called through the Responses
  API or through chat completions. `FREYJA_ALLOWED_HOSTS` lists the host names the service
  answers for beside `localhost` and IP addresses. `McpServerConfig` is one entry of `FREYJA_MCP_SERVERS`;
  `FREYJA_MCP_CALL_TIMEOUT_SECONDS` and `FREYJA_MCP_STARTUP_TIMEOUT_SECONDS` are the two time
  limits for tool servers. `A2aAgentConfig` is one entry of `FREYJA_A2A_AGENTS` (a loopback
  `url` and `read_only`); agent and server names share one namespace. Seven limits go with it:
  `FREYJA_A2A_CALL_TIMEOUT_SECONDS`, `FREYJA_A2A_STARTUP_TIMEOUT_SECONDS`,
  `FREYJA_A2A_POLL_INTERVAL_SECONDS`, `FREYJA_A2A_TASK_MAX_AGE_SECONDS`,
  `FREYJA_BACKGROUND_RUN_ATTEMPTS`, `FREYJA_BACKGROUND_RETRY_SECONDS`, and
  `FREYJA_BACKGROUND_RUN_CONCURRENCY`. Two settings belong to LLM profiles: `FREYJA_SECRET_KEY`,
  a Fernet key or unset, which protects stored API keys and stops startup when it is not a
  Fernet key, and `FREYJA_LLM_TEST_TIMEOUT_SECONDS`, the most a profile's test may take.
- `src/freyja_agent/agent/`: `factory.py` is the one place the orchestrator is assembled
  (`build_agent`; the service, the tests, and any future CLI call it). `build_agent` takes the
  tool server tools as `delegate_tools` and returns an `Orchestrator`: the compiled graph as
  `.agent` and the `Catalog` of exactly the tools it was given as `.catalog`. `catalog.py`
  holds the three classifications, the fixed table of Freyja's own tools (`OWN_TOOLS`), the
  catalog, and the interrupt config generated from it; a tool with no classification raises
  `UnclassifiedToolError` and stops startup. `prompts.py` is the system prompt, `tools.py` the
  gated placeholder tool `send_notification`. `agent_tasks.py` is `AgentTasksMiddleware`, which
  lists the thread's pending agent tasks in the prompt and, after every reply of the model,
  records which agent each call of an agent's tool was proposed for, and, in a resume, right
  after the approval gate, which of those calls a person approved or edited; and the
  `check_agent_tasks` tool; both are added only when an agent is configured
  (`build_agent(agent_tasks=...)`).
- `src/freyja_agent/delegates/`: what the orchestrator reaches over a protocol. `mcp_servers.py`
  connects to the configured MCP servers (`open_tool_servers`, `ToolServer`, `default_connect`),
  applies the call time limit, records each server's status, and redacts `pass_env` values from
  everything a server link emits. `mcp_tools.py` turns each listed tool into a `DelegateTool`,
  classifies it from the `read_only` list (`classify`), and turns a result into text.
  `a2a_agents.py` is the link to one agent (`AgentLink`): making one contacts nothing, and
  `connect` reads the agent's card and builds the client, so nothing is sent over a link that
  holds no usable card. It pins every request to the configured address, sends messages and
  reads and cancels tasks under the call time limit, records each agent's status, and removes
  every known address from everything it emits (`describe`). `agent_cards.py` reads an
  agent's card under one time limit and one size limit and says in the service's own
  sentence why a card cannot be used (`read_card`, `CardError`, the nine `CardProblem`
  codes), and cuts and cleans what a card says of its agent into the details that are kept
  and shown (`card_details`, `cleaned`). `addresses.py` is the process's one address
  scrubber (`SCRUBBER`): it is told every agent address the process is given, takes each
  out of text in every form it can be written in, its path included, and cleans every log
  record through a record factory installed at startup. `agent_registry.py` is the
  `AgentRegistry`, which holds the instance's `AgentSet`: the links in use, of environment
  agents and of saved ones, each agent's approval choice, and the saved agents that are not in
  use. It refuses an entry that is invalid in itself at startup, tries every agent at once,
  warns about one it cannot use, makes a new set when the saved agents' revision moved, and
  closes a link once no set that is held has it. `a2a_tools.py` builds each agent's
  `handoff_to_<agent>` and `cancel_<agent>_task` tools over the send ledger, so a step that
  runs again never sends twice, checks that a task belongs to the agent the tool was built
  for, that the link is connected, that the call was proposed for the agent the tool
  sends to (`_proposed_for`), and, for a saved agent, that the agent as the store has it
  now still exists, still runs without approval when no person decided the call (a person
  decided it only when its row records an approve or an edit), and,
  when it was moved since the run's tools were built, that no other saved agent has the
  address the run started with
  (`_still_allowed`, the last thing awaited before the send's first write), and turns a
  reply into text. A hand-off writes a task's row only while the task is pending, and a
  reply that names a task another agent of the name has is treated as malformed, so a task
  stays with the agent it was given to. `delegation_worker.py` is the `DelegationWorker`: each poll it
  takes a context, claims pending tasks, checks or gives them up, and starts a delivery for
  each ready outcome: a background run of `run_stream`, retried when it fails. The loop never waits for a delivery;
  `tick()` does, for the tests. While no usable model is active it starts no delivery, and what
  is ready waits.
- `src/freyja_agent/llm/`: LLM profiles (feature 004), the ways to reach a model that an
  operator saves, tests, and activates while the service runs. `providers.py` holds the
  provider table, `build_model` for a saved profile, and the environment's profile, and is
  the one module that reads a provider's own variables (`environment_key`). A saved profile's
  client is built with its provider's variables hidden (`build_model`), and for the
  provider's own address when it has no base URL, so nothing the environment sets for its own
  model reaches a saved profile's endpoint, apart from a proxy and a telemetry opt-out; the
  environment's profile is described from what its built client holds and will send
  (`environment_access`), a LangSmith gateway and a credential in the SDK's custom headers
  included; `secrets.py`
  encrypts stored API keys under `FREYJA_SECRET_KEY` and removes keys from text (`redact`);
  `guard.py` makes a model raise its errors with its profile's key already taken out
  (`guard_model`), because LangGraph shows a failed step's error in the debug stream and
  saves it with the thread; `store.py` is the `LlmProfileStore` for saved profiles
  and the active choice (`PostgresLlmProfileStore` with `freyja_llm_profiles` and
  `freyja_llm_state`, and `InMemoryLlmProfileStore`); `active.py` is the `ModelManager`, which
  rebuilds the orchestrator through `build_agent` when the active profile's revision moved,
  or when the agent set it is handed is not the one it built for,
  around a placeholder model when the active profile is not usable, so that the service
  starts and runs end with `no_usable_model`, and which guards every model it builds;
  `check.py` is a profile's test (`check_model`: two requests under one time limit, the
  second carrying earlier turns and a tool call) and the count of tests in progress
  (`CheckSlots`).
- `src/freyja_agent/runtime/`: `persistence.py` opens the shared Postgres pool, checkpointer,
  and store; `locks.py` is the per-thread run lease (`PostgresRunLocks`, `InMemoryRunLocks`);
  `agent_tasks.py` is the `AgentTaskStore` for agent conversations, the send ledger, agent
  tasks, and the calls of agents' tools that a model proposed (`PostgresAgentTaskStore` with
  its four `freyja_agent_*` tables, and `InMemoryAgentTaskStore`); `saved_agents.py` is the `SavedAgentStore` for the agents an operator
  saved (`PostgresSavedAgentStore` with `freyja_agents` and `freyja_agents_state`, and
  `InMemorySavedAgentStore`); `runtime.py` is the `Runtime` dataclass with the catalog, the tool
  servers, the agent registry, the task store, the saved-agent store, and the delegation
  worker. `Runtime.assemble` opens the tool servers, starts the registry, builds the agent, and
  runs the worker; `Runtime.open` adds
  Postgres and is what the FastAPI lifespan enters. Both take `connect`, the seam the tests swap
  to reach stub servers, and `agent_transport`, the httpx transport each agent link uses, which
  the tests swap to reach stand-in agents. `assemble` also takes `model_factory`, which turns
  an LLM profile into a chat model and is the seam the profile tests swap. Every run, and every route and round of the worker that reads the graph or the catalog, first
  takes a `RunContext` from `Runtime.begin_run()`: the graph, the catalog, the active model,
  and the agent set of one build, in step with the active profile and the saved agents, held
  until it is released. `Runtime.agent` and `Runtime.catalog` are what the instance last
  built. `Runtime.environment` is the environment's profile as the list shows it, described
  once at startup. `Runtime.model_to_check` builds the model a profile's test asks, and
  `Runtime.checks` and `Runtime.environment_test` hold the tests in progress and the
  environment's last result, in memory. `thread_index.py` is the index of threads
  (feature 007): the `ThreadIndex` store (`PostgresThreadIndex` with `freyja_threads`, and
  `InMemoryThreadIndex`), one row per thread that has had a run; `IndexedRunLocks`, which
  `Runtime.assemble` wraps the run lease in, so that every run that takes the lease and gives
  it back is told to the index, whoever starts it, and whose own writes never fail the lease
  and hold it up for `INDEX_WRITE_SECONDS` at most; and `settle_thread`, `open_thread`, and
  `status_of`, which read a thread's state once, keep what a row needs of it, and turn a row
  into its one status. `Runtime.threads` is the index. A write that is for one run names
  that run's lease owner, and one made from a reading of a thread's state names the number
  of runs the row had; either changes nothing once the row has moved on. Whether a run is in
  progress is the lease's to say, never the row's. Text is cleaned of what a text column
  does not take before it is kept. `unattended.py` is the console's runs (feature
  007): `UnattendedRuns` drives a run that goes on without a reader in a task of its own,
  hands each event to its reader and disconnects one that falls `READER_BACKLOG` events
  behind, notes in the index how the run ended (`failed` with its error, or `stopped`), and
  at shutdown (`close`) asks each run to stop at its next boundary and waits for it;
  `SegmentedGraph`, which `Runtime.begin_run` puts in every context in place of the graph,
  runs an armed thread in segments that end before each `model` and `tools` step, gives each
  segment what is left of the step limit, and asks between two segments, and nowhere else,
  whether to stop. Every other thread is passed straight to the graph. `Runtime.unattended`
  is the console's runs of this instance.
- `src/freyja_agent/api/`: `app.py` is `create_app(open_runtime)`, `/healthz`, and the
  `freyja-api` entry point; `hosts.py` is `AllowedHosts`, the middleware `create_app` puts
  before every route, which refuses a request made for a host name the service does not
  answer for (403 `unknown_host`); `threads.py` the three thread routes (runs, state, resume),
  each of which takes a context, the two that run under the thread's lease, and validates
  a decision on the orchestrator that then carries it out;
  `tools.py` the catalog route `GET /tools`, which takes a context, so it reads two rows to
  learn whether the agents or the active profile changed, contacts nothing, and lists the
  agents;
  `thread_index.py` the two routes that list threads (feature 007), `GET /threads` and
  `GET /threads/{thread_id}`, in a router of their own under the same prefix, which settle
  the rows whose run ended since they were last read, show no row a status nobody read for
  it, leave out a row that cannot be read instead of failing the list, contact nothing, and
  change no thread, and the three routes of the console's runs:
  `POST /threads/{thread_id}/runs/unattended` and `POST /threads/{thread_id}/resume/unattended`,
  which call the handlers of `threads.py`, take the run's events out of the response
  (`body_iterator`), and hand them to `UnattendedRuns`, answering with
  `Cache-Control: no-store, no-transform` and a ping every `PING_SECONDS`, and
  `POST /threads/{thread_id}/stop`, which marks the row and answers 202, 404, or 409
  (`no_run`, `not_stoppable`),
  with their shapes in `thread_index_schemas.py`;
  `agents.py` the routes of the Agents section under `/agents`: `GET /agents`, the list,
  which answers 500 when the saved agents cannot be read, `POST /agents`, which reads an
  agent's card and saves the agent when it can be used, requiring approval unless the request
  chose otherwise, `PATCH /agents/{id}`, which changes a saved agent's approval choice, its address, or
  both at the version the page read, reading the card at a new address first and giving up
  the agent's pending tasks with the move, `DELETE /agents/{id}`, which removes a saved
  agent and gives up its pending tasks, and
  `POST /agents/{id}/check`, which reads an agent's card again through its link and says
  whether the agent can be used; the first three are the only answers of the service that hold
  a saved agent's address;
  `llm.py` the profile routes under `/llm`: `GET /llm/profiles`, `POST /llm/profiles`,
  `PATCH` and `DELETE /llm/profiles/{id}`, and `POST /llm/profiles/{id}/activate`, none of which
  asks a model anything, and `POST /llm/profiles/{id}/test` and `POST /llm/test`, which hold a
  profile's test with its endpoint; an activation is applied on this instance at once;
  `guards.py` what several routers share: the LLM routes and the Agents routes answer a
  refused request without repeating the value (`NoInputRoute`), and those two and the
  thread routes turn away a request that changes something when a browser marks it as made
  by a page of another origin (`refuse_other_origins`, 403 `other_origin`); `streaming.py`
  the SSE run loop with the lease heartbeat, also driven by the worker for background runs,
  which turns a run away with `no_usable_model` before the graph when no usable model is active,
  ends a resume whose paused step came to hold a call that needs a decision with
  `approval_now_required` and the agents it is about (it knows that refusal by the approval
  middleware's sentence, which `tests/test_a2a_gate.py` pins),
  and takes the profile's key out of a model error before it is logged or sent; `outcomes.py`
  records pending tasks' outcomes in their thread exactly once and answers messages to agents
  that a stop left in flight; `decisions.py` resume validation, the decisions a subagent's gate already took
  when a resume's run stopped after it (`decisions_taken`, which the route refuses others
  against with 409 `already_decided`), and what a resume tells the step after the gate
  (`decided_calls`: the gate's namespace and the calls the person approved or edited),
  including edits checked against
  a server tool's JSON Schema and pinned arguments; `schemas.py` and `serialize.py` the request,
  response, and event shapes.
- `workspace/`: the orchestrator's file root (`FREYJA_WORKSPACE_DIR`). `skills/` is committed
  source; `cases/` is written at runtime and git-ignored.
- `web/`: the Next.js web app (pnpm, Biome, Tailwind CSS), the console. `web/app/layout.tsx` is the
  shell: the sidebar (`web/components/app-sidebar.tsx`) beside the page. `/` redirects to `/llm`;
  `web/app/llm/` lists the LLM profiles as cards and `web/app/agents/` the agents, read from
  `GET /agents` (`web/lib/agents.ts`, `web/components/agents/`). The Agents section adds an
  agent and changes a saved agent's address and approval choice (`agent-form.tsx`, which
  keeps nothing once it is sent or closed, asks for confirmation before it sends anything
  that loosens or tightens approval or moves the agent, and sends the version it opened
  with), removes a saved agent behind a confirmation (`remove-dialog.tsx`), says how many
  pending tasks a move or a removal gave up, checks an agent from
  its card (`check-result.tsx`), and warns above the cards about every agent that cannot be
  used (`agents-warning.tsx`). The sidebar reads the same
  list under the same SWR key (`AGENTS_KEY`) and shows how many agents cannot be used, so
  every page asks for `/api/agents` once.
  `web/app/tasks/` is the Tasks section (feature 007), with the list of tasks under the notice
  that the console has no users (`task-list.tsx`), and `web/app/tasks/[id]/` one task, whose
  id is read inside `<Suspense>`, as Cache Components asks of a route with a parameter; the
  sidebar's section links are read inside one too, or the build fails for that route.
  `web/lib/tasks.ts` holds the task types, the calls, and `events`, which reads a run's
  stream as it arrives; `web/lib/api.ts` has `stream` beside `api` for that answer.
  `web/lib/task-runs.ts` holds the runs this browser tab started, outside React, so that a
  run started by New task on the list's page is shown on the task's page as it arrives
  (`useRun`); nothing is stored. The list of tasks and the sidebar's number of tasks that
  need a decision are one reading of `GET /threads` under `THREADS_KEY`, read again every
  `LIST_SECONDS` by `web/hooks/use-every.ts`, a timer that goes on past a reading that
  failed, and at once by a task's view when a run it shows ends. So every page of the
  console asks for `/api/threads`. `web/components/tasks/` holds New task (`new-task.tsx`),
  the box that sends text (`send-box.tsx`), a task's view (`task-view.tsx`), which shows a
  run as it streams and the service's conversation once it ended, a message
  (`message.tsx`), the status in words (`status.tsx`), and the proposal card
  (`proposal-card.tsx`), which offers each proposed action only the decisions the service
  lists for it, sends them with `decide` to `POST /threads/{id}/resume/unattended`, checks
  no more than that edited arguments are a JSON object (`parseArguments`) and that a
  reason is within the service's length, and has its
  buttons outside any form, so that Enter in a field sends no decision. It also holds the
  follow-up box (`follow-up.tsx`), which sends another message on a task through the same
  `send-box.tsx` and is given why it cannot be sent, a run in progress or a pending
  proposal, by the view, and the list of a task's agent tasks (`agent-tasks.tsx`), whose
  `awaited` says whether a task still waits for one. The view reads its task again by one
  timer of its own: every `FOLLOW_SECONDS` while a run it does not read is in progress,
  every `AGENT_SECONDS` while an agent task is awaited, and never while the page is hidden.
  Stop (`stop-button.tsx`) is offered by the view for a run the console started that is in
  progress: a run of this tab once its `start` event arrived, and a run it follows when the
  row says `stoppable`. It sends `stopRun` on a press, never otherwise; the view says
  "stopping" until it learns how the run ended, from the run's last event when it reads the
  stream and otherwise from a reading of the row made after the stop, shows a task as
  stopped only when the row does, and says that the stop came too late when the run ended
  another way (FR-022a).
  All text of a task is put in
  text nodes and never rendered as markup. The console has no users, and the section says
  so above its list.
  `web/components/llm/` holds the list, the card, the dialog that adds a profile or changes one
  (`profile-form.tsx`, which keeps nothing once it is sent or closed), the confirmations before a
  profile is activated (`activate-dialog.tsx`) and deleted (`delete-dialog.tsx`), and how a test's
  result is shown (`test-result.tsx`). `web/lib/api.ts` is the one function that calls the service,
  always through `/api`, and its error type; `web/lib/llm.ts` holds the profile types and the calls
  that list, add, change, delete, activate, and test. Lists are read with SWR in Client Components.
  shadcn/ui is set up: `components.json`, components under `web/components/ui/`, and the `cn` helper
  in `web/lib/utils.ts`. Generated components need `pnpm format` after they are added, and three
  lint rules they do not meet are off for `web/components/ui/` only (`web/biome.json`).
  `web/next.config.ts` is the one place the web app reads its configuration: `FREYJA_API_URL`, where
  its server forwards `/api/...`, documented in `web/.env.example`; it also holds the redirect. Unit
  tests are `*.test.ts` and `*.test.tsx` files under `web/__tests__/`, which follows the layout of
  the code it covers (`web/__tests__/lib/api.test.ts` covers `web/lib/api.ts`), run by Vitest with
  jsdom; `web/__tests__/testing.ts` stubs `fetch` for them, and `web/__tests__/tasks-testing.ts`
  for the Tasks section's tests, with runs' streams built from events. No test file sits beside the code.
  `web/AGENTS.md` holds Next.js's own rules for coding agents; read it before changing code
  there. `web/CLAUDE.md` only imports it for Claude Code.
- `web/e2e/`: the web app's end-to-end tests, `*.spec.ts` files run by Playwright
  (`web/playwright.config.ts`). Playwright reads nothing outside this directory, and Vitest is
  told to skip it (`web/vitest.config.mts`), so neither runner picks up the other's files.
  `console.spec.ts` covers the shell, `llm.spec.ts` the LLM section as it is read,
  `profiles.spec.ts` adding, activating, testing, changing, and deleting, `agents.spec.ts`
  the Agents section as it is read, its warnings, and that an agent's address stays in it, and `agents-manage.spec.ts` adding an
  agent, checking one, changing one's approval choice, moving one, and removing one;
  `tasks.spec.ts` the Tasks section, and that a page of another origin cannot show it in a
  frame, `tasks-decide.spec.ts` deciding a proposal, and
  `tasks-run.spec.ts` follow-ups, agent tasks, a run the page does not read, a page closed
  during a run, and Stop (it moves the page's clock with `page.clock` in place of waiting
  five or fifteen seconds), with
  `tasks-api.ts` (`TaskService`, which answers a page's requests for tasks, its runs, and
  its decisions and keeps what each left, `pausedTask` and `continued` for a proposal and
  its decision, `followedUp` and `agentTask` for a follow-up and an agent task,
  `runningTask` and `goesOn` for a run in progress and one whose stream stops, and
  `answerThreads`; it answers a stop as the service rules it and keeps each in `stops`); and
  `stream.spec.ts`, the one spec that lets a page's requests through the app's forwarding,
  to a stand-in for the service on `STAND_IN_PORT` (3109, the address the app under test
  forwards to), to see a run's events arrive as they are produced, and that the forwarding
  passes on the three headers the service's refusal of other origins reads, and replaces an
  `X-Forwarded-Host` a page wrote with the name the console was asked under, which the
  service's refusal of unknown host names rests on. It runs in projects of
  its own, `stream-chromium` and then `stream-firefox`, after every other spec; `api.ts`
  holds what the service would answer (`answerProfiles` and `answerAgents` for a list,
  `ProfileService` and `AgentService` for a page that changes profiles or agents, which keep
  what the page saved) and the locators the specs share. The app under test forwards `/api/...` to an address nothing listens on, so a test that
  hands a page no answer sees the console as it is with the service stopped; only
  `stream.spec.ts` listens there, in its own projects.
- `.github/workflows/` and `.gitlab-ci.yml`: the same CI jobs for GitHub and for GitLab.
  `lint.yml` runs Ruff (`ruff check` and `ruff format --check`) and Biome (`pnpm lint`), and
  `unit-tests.yml` runs pytest and Vitest, both on every pull request and on `main`;
  GitLab's `ruff`, `biome`, `pytest`, and `vitest` jobs do the same for merge requests.
  `secrets.yml` and GitLab's `gitleaks` job search every commit for secrets, on every change.
  `playwright.yml` and GitLab's `playwright` job run the end-to-end tests when a file under
  `web/` changes. The GitLab Playwright image's version must equal the `@playwright/test`
  version in `web/pnpm-lock.yaml`; change both together. A change to one side's jobs goes
  to the other side too. The linters only report in CI; the commit hook is where files get
  fixed. CI does not run the opt-in Postgres tests.
- `docker-compose.yml`: local Postgres for development and the integration tests.
- `Dockerfile` and `web/Dockerfile`: the two container images, the service and the web
  console, each with its `.dockerignore` (the service's names the files that are let in).
  Both are built on Chainguard's Wolfi base, run as user 65532, and listen on loopback inside
  the container: the service because the image sets no `FREYJA_*` value, the console because
  the image sets `HOSTNAME=127.0.0.1`. The web image is a standalone Next.js server
  (`FREYJA_WEB_STANDALONE=1` in its build), with `FREYJA_API_URL` written into it when it is
  built. `.github/workflows/scan.yml` runs Trivy and Semgrep over the source (`trivy`,
  `semgrep`) and builds, runs, and scans both images (`images`); GitLab's `trivy` and
  `semgrep` jobs do the first two, and no GitLab job builds an image.
  `tests/test_container_images.py` holds the images' defaults and keeps the scanners' versions
  and questions equal on both sides.
- `tests/`: pytest suite. `conftest.py` supplies the required environment and the in-memory
  runtime fixtures, so no `.env` is needed; `make_mcp_app` assembles an app the production way
  with stub tool servers, `make_a2a_app` with stand-in agents, and `make_llm_app` with LLM
  profiles and a model factory. `fakes.py` holds the scripted chat model, a labelled one that
  can hold a call open, and the test model factory; `nowhere.py` what the three proofs
  that no key and no agent address gets out share; `llm_stub.py` the stand-in model
  endpoint, OpenAI-compatible, which the suite reaches through the real client;
  `mcp_stub.py` the stub MCP server, which
  records every call it
  receives; `a2a_stub.py` the stand-in A2A agent, built with the SDK's server classes, which
  records every message and cancel and behaves by a keyword at the start of the message;
  `asgi_stream.py` the httpx transport that runs a stand-in in the test process and streams
  its replies as they are sent; `unattended_helpers.py` what the tests of the console's runs
  share (a tool step a test holds open, a stream read in a thread of its own);
  `integration/` holds the Postgres tests, skipped unless
  `FREYJA_TEST_DATABASE_URI` is set.
- `.specify/`: Spec Kit templates, scripts, installed extensions, and the constitution. Feature
  artifacts go under `specs/<NNN-slug>/` as `spec.md`, `plan.md`, and `tasks.md`.
- `.claude/skills/speckit-*/`: the Spec Kit skills for Claude Code. Codex CLI reads its own copy
  from `.agents/skills/speckit-*/`, installed with `specify integration install codex`. Both
  directories are generated by Spec Kit; edit neither by hand.
- `AGENTS.md`: this file. `CLAUDE.md` only imports it plus the constitution for Claude Code.

## Conventions

- Configuration goes through `Settings`. Never hardcode hosts, ports, connection strings, or
  model ids; add a field with a safe local default, or no default when the value carries a
  credential, and document it in `.env.example`.
- New behavior ships with a test: in `tests/` for Python, and in a `*.test.ts` or `*.test.tsx`
  file under `web/__tests__/` for the web app.
- Coverage has a floor: `fail_under` in `pyproject.toml` for the service's lines, and the
  four thresholds in `web/vitest.config.mts` for the web app. Both are measured as CI runs the
  suites, so without the opt-in Postgres tests, and nothing of either project is left out of
  the count apart from `web/components/ui/`, which shadcn generates. A change that brings a number below its floor adds the tests it lacks. A floor
  is raised as its number rises and lowered only with the reason written beside it. Coverage
  shows what no test runs; it does not show that what runs is checked.
- Write the tests before opening a pull request or merge request, not after. Its description
  has a Tests section that names the tests covering the change, new ones and existing ones
  alike. A change that needs no test, such as documentation or a dependency bump, says so in
  that section and gives the reason.
- A Ruff suppression carries a reason in its comment and is removed once the cause is fixed.
- Trivy and Semgrep report nothing: no known vulnerability of HIGH or CRITICAL severity in a
  lock file or an image, and no Semgrep finding. A vulnerable package is upgraded in its lock
  file. A Semgrep finding that is wrong is marked `# nosemgrep: <rule id>` on the line before
  it, with the reason near it, and never by leaving a file or a rule out of the scan. SQL is
  composed with psycopg's `sql` module, which Semgrep's SQLAlchemy rule takes for string
  building; each such statement carries that mark.
- A GitHub Action is named by its commit, with its version in a comment beside it.
- The container images keep the safe local choice: neither sets an address to listen on other
  than loopback, and neither runs as root. A deployment that needs otherwise says so in its
  own configuration (`FREYJA_HOST`, `HOSTNAME`).
- A stored API key appears nowhere beyond its last four characters: in no response, stream,
  thread, or log. Text that may hold one, such as a provider's error, goes through `redact`
  first, and a model is guarded before it is used (`guard_model`), so its errors hold no key
  when LangGraph is given them. `tests/test_llm_secrets_nowhere.py` searches everything the
  service gives back and everything the checkpointer keeps. A new route or log line about
  profiles is added to that test.
- A saved agent's address appears in `url` of an answer of the Agents routes and nowhere
  else: in no other response, stream, thread, or log line, and in no card's text. An
  environment agent's appears nowhere. Text that may hold one goes through the scrubber
  (`delegates/addresses.py`), and card details are cleaned again where they are used
  (`cleaned`), because an address may become an agent's after a card that names it was read.
  `tests/test_agents_address_nowhere.py` searches everything the service gives back and
  everything the checkpointer keeps. A new route or log line about agents is added to that
  test, and a new route or refusal code also to its table `CONTRACT`: the file's last test
  fails when a route of `api/agents.py`, or a refusal the contract gives a route, is driven
  by none of its parts. A test that searches for a port looks for it as a number of its own (`holds_port` in
  `tests/conftest.py`), so that digits of an id or a timestamp are not taken for it.
- No secret is committed, and gitleaks checks it: in the commit hook over what is staged, and
  in CI over every commit. Its rules and the made-up keys the tests use are in
  `.gitleaks.toml`. A test that needs a made-up key uses a value that file lets through, such
  as `sk-recognizable-key-0001`; a new shape is added there as an exact pattern, never as a
  path, so that a real key pasted into a test is still found. The gitleaks version is named in
  `.pre-commit-config.yaml`, `.github/workflows/secrets.yml`, and `.gitlab-ci.yml`; change
  the three together.
- The file hooks leave what Spec Kit generates alone (`.specify/`, `.claude/skills/`,
  `.agents/skills/`), and the YAML check skips `web/pnpm-lock.yaml`.
- A route that changes something and takes no body can be reached by a form on any web page.
  The `/llm` routes refuse such a request by what the browser says of it
  (`refuse_other_origins`); a new router that changes operator-managed configuration does the
  same.
- The thread routes refuse a request that changes something when a browser marks it as made
  by a page of another origin, as the `/llm` and `/agents` routes do: both thread routers are
  made with `refuse_other_origins`. `tests/test_threads_other_origins.py` covers every thread
  route; a new one is added to it, and to its table `CHANGING`: the file walks every route
  the application serves and fails when a thread route that changes something is not in
  the table, when any route that changes something, under any prefix, serves a request
  marked as another origin's, when it meets a kind of route it does not know, such as a
  WebSocket route or a mounted application, and when the feature's contract has a route the
  table lacks. The rule does not cover a request that reaches the service under a name an
  attacker controls (DNS rebinding), which features 004, 005, and 007 each recorded as left
  open; the next rule does.
- Every request is held to the host names the service answers for, a request that only
  reads included: `AllowedHosts` (`api/hosts.py`) is added in `create_app`, before any
  route, and refuses one whose `Host`, or whose `X-Forwarded-Host`, names anything but
  `localhost`, an IP address, or a name in `FREYJA_ALLOWED_HOSTS`. Only `/healthz` is
  answered under any name. Nothing is added per route: a new route, router, or mounted
  application is covered because it is behind the middleware, and
  `tests/test_allowed_hosts.py` walks every route the application serves to show it. The
  suite lists `testserver`, the name FastAPI's test client asks for, in `tests/conftest.py`.
- The index of threads returns text from threads, so `tests/test_thread_index_nowhere.py`
  searches its answers and the log for a recognizable key and a recognizable agent address.
  A new route of the index is added to it. The three proofs share their tools in
  `tests/nowhere.py`: `Seen`, which keeps everything the service gave back, `kept`, what the
  checkpointer holds, `logged`, and the searches `no_key` and `no_address`. A proof imports
  them from there, never from another test file.
- A console run is stopped only between two steps, never by cancelling it, also when the
  service stops: `SegmentedGraph` is the one place that decides where (`BOUNDARY_NODES`),
  so that an action under way is never cut off (principle I). `tests/test_unattended_stop.py`
  stops a run during a model call, during a tool step, and during an approved action; a
  change to how a run is driven keeps those passing.
- An end-to-end test of the web app is a `*.spec.ts` file under `web/e2e/`. It adds to the
  unit tests and does not replace them. It never needs the Freyja service: the service's
  answers are handed to the page in the browser, through `web/e2e/api.ts`.
- Commit messages use the `type: summary` form: `feat:`, `chore:`, `docs:`.
- The README documents only what exists in the repo, never planned work.

## Spec Kit

Every feature runs all eight steps of the "Spec Kit workflow" section of `README.md`, in its
order, and skips none:

1. `speckit-specify` writes `spec.md` and the spec quality checklist,
   `checklists/requirements.md`.
2. `speckit-clarify` asks up to five questions and writes the answers into `spec.md`.
3. `speckit-plan` writes `plan.md`, `research.md`, `data-model.md`, `contracts/`, and
   `quickstart.md`. Every plan includes a Constitution Check that names the principles it
   touches and justifies any deviation.
4. `speckit-checklist` writes `checklists/<domain>.md` for one focus area. Review every item
   and tick it once the spec and the plan satisfy it; where they do not, fix `spec.md` or
   `plan.md` in the same pull request. Leave no item unchecked: `speckit-implement` stops and
   asks when it finds one.
5. `speckit-tasks` writes `tasks.md`.
6. `speckit-analyze` cross-checks the spec, the plan, and the tasks, and changes no file. Report
   what it found; what needs fixing is fixed by editing the files.
7. `speckit-implement` works through `tasks.md` one phase at a time, each phase a pull request.
8. `speckit-converge` runs after the last phase and appends any unbuilt work to `tasks.md`.

Spec Kit and the constitution name clarify, checklist, and analyze as optional gates. This
project runs all three for every feature, so none of the eight is left out because it is
optional.

The skills are named `speckit-<step>`: Claude Code invokes them as `/speckit-<step>`, Codex CLI
as `$speckit-<step>`. Do not start a Spec Kit skill unless the user asks for it; a human reviews
each pull request before the next step runs. When the user asks for a step and an earlier step
of that feature has not been run, say which one is missing before running anything, and
recommend running it first. When a step is finished, name the next one in the order above.

Specify, plan, and tasks each open a pull request, and each implementation phase is one. A gate
may share the pull request of the step before it: specify with clarify, plan with checklist,
tasks with analyze. It is then run on the same branch before that pull request is merged, with
a commit of its own, and the description covers both steps. A gate run after its step was
merged gets a pull request of its own. The README shows every step as a separate pull request;
both ways are accepted.

Pull requests are squash-merged and their branch deleted, so before every step that starts a
new pull request, apart from specify, recreate the feature branch from the latest `main`; never
run a step on `main` or on a branch left from a pull request that was merged.

The Spec Kit git extension is installed. Its `before_specify` hook creates the numbered feature
branch off `main`, so `speckit-specify` never writes a spec on `main`. Its auto-commit hooks are
optional and disabled in `.specify/extensions/git/git-config.yml`; commits stay manual and keep
the `type: summary` form. The CLI registers an extension's skills only for the active
integration, so after `specify extension add <id>` run `specify integration use codex` and then
`specify integration use claude` to give Codex CLI its copies under `.agents/skills/`.
