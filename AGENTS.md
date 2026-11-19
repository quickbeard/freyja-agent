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
- `uv run pre-commit run --all-files` runs the commit hook over the whole repo: Ruff and
  pytest, then Biome and Vitest for the web app. The web hooks run when a file under `web/` is
  staged and need Node and pnpm on the path and the dependencies installed in `web/`.
- In `web/`: `pnpm install` installs the web app's dependencies, `pnpm dev` and `pnpm build`
  run and build it, `pnpm lint` runs `biome check`, `pnpm format` formats in place, and
  `pnpm test` runs the unit tests with Vitest.
- In `web/`: `pnpm test:e2e` runs the end-to-end tests with Playwright in Chromium and Firefox,
  the browsers `web/playwright.config.ts` names. Playwright builds the app and serves it on port
  3100 itself, so nothing needs to be running, the Freyja service included. `pnpm exec
  playwright install chromium firefox` downloads the two browsers once per machine. The commit
  hook does not run these tests; CI does.
- `docker compose up -d postgres` starts the local Postgres the service and the integration
  tests use; its credentials equal `FREYJA_DATABASE_URI` in `.env.example`.

`.env` is git-ignored; copy it from `.env.example`. Settings do not read `.env` themselves, so
pass `--env-file .env` to `uv run` whenever a command needs it. The service needs Postgres at
startup and exits with an error naming the connection when it is unreachable.

## Layout

- `src/freyja_agent/config.py`: the `Settings` class (pydantic-settings, `FREYJA_*` prefix) and
  the shared `settings` instance. `FREYJA_MODEL` names the model, and
  `FREYJA_OPENAI_RESPONSES_API` says whether an `openai:` model is called through the Responses
  API or through chat completions. `McpServerConfig` is one entry of `FREYJA_MCP_SERVERS`;
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
  lists the thread's pending agent tasks in the prompt, and the `check_agent_tasks` tool; both
  are added only when an agent is configured (`build_agent(agent_tasks=...)`).
- `src/freyja_agent/delegates/`: what the orchestrator reaches over a protocol. `mcp_servers.py`
  connects to the configured MCP servers (`open_tool_servers`, `ToolServer`, `default_connect`),
  applies the call time limit, records each server's status, and redacts `pass_env` values from
  everything a server link emits. `mcp_tools.py` turns each listed tool into a `DelegateTool`,
  classifies it from the `read_only` list (`classify`), and turns a result into text.
  `a2a_agents.py` reads and checks each agent's card at startup (`open_agents`, `AgentLink`),
  pins every request to the configured address, sends messages and reads and cancels tasks under
  the call time limit, records each agent's status, and removes the address from everything it
  emits (`describe`). `a2a_tools.py` builds each agent's `handoff_to_<agent>` and
  `cancel_<agent>_task` tools over the send ledger, so a step that runs again never sends twice,
  and turns a reply into text. `delegation_worker.py` is the `DelegationWorker`: each poll it
  claims pending tasks, checks or gives them up, and starts a delivery for each ready outcome: a
  background run of `run_stream`, retried when it fails. The loop never waits for a delivery;
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
  around a placeholder model when the active profile is not usable, so that the service
  starts and runs end with `no_usable_model`, and which guards every model it builds;
  `check.py` is a profile's test (`check_model`: two requests under one time limit, the
  second carrying earlier turns and a tool call) and the count of tests in progress
  (`CheckSlots`).
- `src/freyja_agent/runtime/`: `persistence.py` opens the shared Postgres pool, checkpointer,
  and store; `locks.py` is the per-thread run lease (`PostgresRunLocks`, `InMemoryRunLocks`);
  `agent_tasks.py` is the `AgentTaskStore` for agent conversations, the send ledger, and agent
  tasks (`PostgresAgentTaskStore` with its three `freyja_agent_*` tables, and
  `InMemoryAgentTaskStore`); `runtime.py` is the `Runtime` dataclass with the catalog, the tool
  servers, the agent links, the task store, and the delegation worker. `Runtime.assemble` opens
  the tool servers and the agents, builds the agent, and runs the worker; `Runtime.open` adds
  Postgres and is what the FastAPI lifespan enters. Both take `connect`, the seam the tests swap
  to reach stub servers, and `agent_transport`, the httpx transport each agent link uses, which
  the tests swap to reach stand-in agents. `assemble` also takes `model_factory`, which turns
  an LLM profile into a chat model and is the seam the profile tests swap. Every run calls
  `Runtime.model_for_run()` first, which brings `agent` and `catalog` in step with the active
  profile. `Runtime.environment` is the environment's profile as the list shows it, described
  once at startup. `Runtime.model_to_check` builds the model a profile's test asks, and
  `Runtime.checks` and `Runtime.environment_test` hold the tests in progress and the
  environment's last result, in memory.
- `src/freyja_agent/api/`: `app.py` is `create_app(open_runtime)`, `/healthz`, and the
  `freyja-api` entry point; `threads.py` the three thread routes (runs, state, resume);
  `tools.py` the catalog route `GET /tools`, which reads memory only and lists the agents;
  `llm.py` the profile routes under `/llm`: `GET /llm/profiles`, `POST /llm/profiles`,
  `PATCH` and `DELETE /llm/profiles/{id}`, and `POST /llm/profiles/{id}/activate`, none of which
  asks a model anything, and `POST /llm/profiles/{id}/test` and `POST /llm/test`, which hold a
  profile's test with its endpoint; an activation is applied on this instance at once; its
  routes answer a refused request without repeating the value (`NoInputRoute`), and turn
  away a request that changes something when a browser marks it as made by a page of another
  origin (`refuse_other_origins`, 403 `other_origin`); `streaming.py`
  the SSE run loop with the lease heartbeat, also driven by the worker for background runs,
  which turns a run away with `no_usable_model` before the graph when no usable model is active,
  and takes the profile's key out of a model error before it is logged or sent; `outcomes.py`
  records pending tasks' outcomes in their thread exactly once and answers messages to agents
  that a stop left in flight; `decisions.py` resume validation, including edits checked against
  a server tool's JSON Schema and pinned arguments; `schemas.py` and `serialize.py` the request,
  response, and event shapes.
- `workspace/`: the orchestrator's file root (`FREYJA_WORKSPACE_DIR`). `skills/` is committed
  source; `cases/` is written at runtime and git-ignored.
- `web/`: the Next.js web app (pnpm, Biome, Tailwind CSS), the console. `web/app/layout.tsx` is the
  shell: the sidebar (`web/components/app-sidebar.tsx`) beside the page. `/` redirects to `/llm`;
  `web/app/llm/` lists the LLM profiles as cards and `web/app/agents/` is a placeholder.
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
  jsdom; `web/__tests__/testing.ts` stubs `fetch` for them. No test file sits beside the code.
  `web/AGENTS.md` holds Next.js's own rules for coding agents; read it before changing code
  there. `web/CLAUDE.md` only imports it for Claude Code.
- `web/e2e/`: the web app's end-to-end tests, `*.spec.ts` files run by Playwright
  (`web/playwright.config.ts`). Playwright reads nothing outside this directory, and Vitest is
  told to skip it (`web/vitest.config.mts`), so neither runner picks up the other's files.
  `console.spec.ts` covers the shell, `llm.spec.ts` the LLM section as it is read, and
  `profiles.spec.ts` adding, activating, testing, changing, and deleting; `api.ts` holds what
  the service would answer (`answerProfiles` for a list, `ProfileService` for a page that
  changes profiles, which keeps what the page saved and activated) and the locators the specs
  share. The app under test forwards `/api/...` to an address nothing listens on, so a test that
  hands a page no answer sees the console as it is with the service stopped.
- `.github/workflows/` and `.gitlab-ci.yml`: the same CI jobs for GitHub and for GitLab.
  `lint.yml` runs Ruff (`ruff check` and `ruff format --check`) and Biome (`pnpm lint`), and
  `unit-tests.yml` runs pytest and Vitest, both on every pull request and on `main`;
  GitLab's `ruff`, `biome`, `pytest`, and `vitest` jobs do the same for merge requests.
  `playwright.yml` and GitLab's `playwright` job run the end-to-end tests when a file under
  `web/` changes. The GitLab Playwright image's version must equal the `@playwright/test`
  version in `web/pnpm-lock.yaml`; change both together. A change to one side's jobs goes
  to the other side too. The linters only report in CI; the commit hook is where files get
  fixed. CI does not run the opt-in Postgres tests.
- `docker-compose.yml`: local Postgres for development and the integration tests.
- `tests/`: pytest suite. `conftest.py` supplies the required environment and the in-memory
  runtime fixtures, so no `.env` is needed; `make_mcp_app` assembles an app the production way
  with stub tool servers, `make_a2a_app` with stand-in agents, and `make_llm_app` with LLM
  profiles and a model factory. `fakes.py` holds the scripted chat model, a labelled one that
  can hold a call open, and the test model factory; `llm_stub.py` the stand-in model
  endpoint, OpenAI-compatible, which the suite reaches through the real client;
  `mcp_stub.py` the stub MCP server, which
  records every call it
  receives; `a2a_stub.py` the stand-in A2A agent, built with the SDK's server classes, which
  records every message and cancel and behaves by a keyword at the start of the message;
  `asgi_stream.py` the httpx transport that runs a stand-in in the test process and streams
  its replies as they are sent; `integration/` holds the Postgres tests, skipped unless
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
- Write the tests before opening a pull request or merge request, not after. Its description
  has a Tests section that names the tests covering the change, new ones and existing ones
  alike. A change that needs no test, such as documentation or a dependency bump, says so in
  that section and gives the reason.
- A Ruff suppression carries a reason in its comment and is removed once the cause is fixed.
- A stored API key appears nowhere beyond its last four characters: in no response, stream,
  thread, or log. Text that may hold one, such as a provider's error, goes through `redact`
  first, and a model is guarded before it is used (`guard_model`), so its errors hold no key
  when LangGraph is given them. `tests/test_llm_secrets_nowhere.py` searches everything the
  service gives back and everything the checkpointer keeps. A new route or log line about
  profiles is added to that test.
- A route that changes something and takes no body can be reached by a form on any web page.
  The `/llm` routes refuse such a request by what the browser says of it
  (`refuse_other_origins`); a new router that changes operator-managed configuration does the
  same.
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
