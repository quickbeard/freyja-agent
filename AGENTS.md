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
- `uv run pyright` checks the types of the service's code under `src/` and of the tests
  under `tests/`, with the settings in `pyproject.toml` (`[tool.pyright]`). The commit hook
  and CI run it this way. It needs nothing but uv: Node comes with the package, and pyright
  itself is downloaded the first time it runs, which needs the network.
- `uv run pytest` runs the suite. Tests run the production routes through FastAPI's test
  client with in-memory persistence and a scripted model; no server, database, or model
  provider is needed. Tool servers are played by the stub in `tests/mcp_stub.py`, and A2A
  agents by the stand-in in `tests/a2a_stub.py`, so no MCP server or agent needs to be
  installed or running and no network is used; only the transport tests launch a stub as a
  process or serve it on the loopback interface.
- `uv run pytest --cov` runs the suite with coverage and fails below `fail_under` in
  `pyproject.toml`, which is the floor of a run with the Postgres tests, as CI runs it. The
  commit hook runs it without a database and with a floor of its own, `--cov-fail-under` in
  `.pre-commit-config.yaml`; give a run without the Postgres tests that option too. Without
  `--cov` nothing is measured, so a run of one file is not held to the suite's number.
- `uv run python -m tests.mcp_stub --http 8765` serves the stub at
  `http://127.0.0.1:8765/mcp` for a manual run against an address server.
- `uv run python -m tests.a2a_stub --http 9998` serves the stand-in agent at
  `http://127.0.0.1:9998` for a manual run; add it to `FREYJA_A2A_AGENTS` under any name.
- `uv run python -m tests.a2a_caller --url http://localhost:8000 <card|send|stream|get|follow|cancel> ...`
  is the stand-in caller of feature 009, the protocol's own client, calling Freyja as another
  agent would at the service's own address (`FREYJA_A2A_SERVER=true`), or with `--token` as
  a named caller, there or at the outside listener's address; it prints each task or event,
  and a refusal, as one JSON object.
- `uv run --env-file .env python -m tests.llm_stub --http 9997` serves the stand-in model
  endpoint for a manual run; use `http://127.0.0.1:9997/v1` as an LLM profile's base URL. It
  behaves by model id: `refuse-conversation`, `reject-key`, `repeat-key`, `never-answer`,
  `slow`, `call-tools`, which asks for tools by a keyword at the start of a message
  (`notify`, `handoff <agent>`, `steps <n>`, `check`) and answers chat completions only, and any
  other id answers. A request that asks for a stream is answered as one, so
  a profile pointing at it can be run on.
- `FREYJA_TEST_DATABASE_URI=postgresql://freyja:freyja@localhost:5432/freyja_test uv run pytest
  tests/integration` runs the Postgres tests; without the variable they are skipped, so
  they are opt-in on a developer's machine and in the commit hook. CI sets it, in both
  systems, to a Postgres the `pytest` job starts for itself.
  Create the database once with `docker compose exec postgres createdb -U freyja freyja_test`.
- `uv run pre-commit run --all-files` runs the commit hook over the whole repo: the file
  checks (whitespace at the end of a line, the newline at the end of a file, YAML that
  parses, merge markers), gitleaks over what is staged, Ruff, pyright when the service's
  code, a test, or their packages changed, and pytest, then Biome and Vitest for the web app. The web hooks run when a file under `web/` is
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

What a directory holds is described beside its code, in an `AGENTS.md` of that directory, so
that a session reads only what it works on. This file is in every session. A directory's file
is not, unless the session was started in that directory or below it: read it before changing
code there. A change to what one of them describes updates that file, and a new module is
described in its directory's file, not in this one. `tests/test_agents_md.py` checks that every
path these files name exists, that each module of the service is named in its directory's
file, and that this list names every such file.

- `src/freyja_agent/config.py`: the `Settings` class (pydantic-settings, `FREYJA_*` prefix) and
  the shared `settings` instance. `src/freyja_agent/AGENTS.md` lists the settings; read it
  before adding or changing one.
- `src/freyja_agent/agent/`: the orchestrator: the factory that assembles it, the catalog of
  its tools and the approval gate generated from it, the prompt, and Freyja's own tools.
  `src/freyja_agent/agent/AGENTS.md` describes its modules; read it before changing code
  there.
- `src/freyja_agent/delegates/`: what the orchestrator reaches over a protocol.
  `src/freyja_agent/delegates/AGENTS.md` describes its modules; read it before changing code
  there.
- `src/freyja_agent/llm/`: LLM profiles (feature 004), the ways to reach a model that an
  operator saves, tests, and activates while the service runs.
  `src/freyja_agent/llm/AGENTS.md` describes its modules; read it before changing code there.
- `src/freyja_agent/runtime/`: what an instance holds while it runs: the Postgres pool and the
  schema's migrations, the stores, the run lease, the index of threads, the console's runs,
  and the `Runtime` that assembles them. `src/freyja_agent/runtime/AGENTS.md` describes its
  modules; read it before changing code there.
- `src/freyja_agent/api/`: the FastAPI application: its routes, the loop that streams a run,
  and the service other agents call over A2A. `src/freyja_agent/api/AGENTS.md` describes its
  modules; read it before changing code there.
- `workspace/`: the orchestrator's file root (`FREYJA_WORKSPACE_DIR`). `skills/` is committed
  source; `cases/` is written at runtime and git-ignored.
- `web/`: the Next.js web app (pnpm, Biome, Tailwind CSS), the console. `web/AGENTS.md` holds
  Next.js's own rules for coding agents and, after them, describes the app's sections and
  files; read it before changing code there. `web/CLAUDE.md` only imports it for Claude Code.
- `web/e2e/`: the web app's end-to-end tests, `*.spec.ts` files run by Playwright
  (`web/playwright.config.ts`). `web/e2e/AGENTS.md` describes the specs and what they share;
  read it before changing one.
- `.github/workflows/` and `.gitlab-ci.yml`: the same CI jobs for GitHub and for GitLab.
  `lint.yml` runs Ruff (`ruff check` and `ruff format --check`), pyright (`uv run pyright`),
  and Biome (`pnpm lint`), and
  `unit-tests.yml` runs pytest and Vitest, both on every pull request and on `main`;
  GitLab's `ruff`, `pyright`, `biome`, `pytest`, and `vitest` jobs do the same for merge
  requests.
  `secrets.yml` and GitLab's `gitleaks` job search every commit for secrets, on every change.
  `deepagents-canary.yml` and GitLab's `deepagents-canary` job run pyright and pytest once a
  week against a newer Deep Agents than `uv.lock` names, the newest release inside the pin
  and the newest of all, and for no pull or merge request. A scheduled run is theirs alone,
  GitLab's schedule is made in the project's settings, and
  `tests/test_deepagents_canary.py` keeps the two sides in step.
  `playwright.yml` and GitLab's `playwright` job run the end-to-end tests when a file under
  `web/` changes. The GitLab Playwright image's version must equal the `@playwright/test`
  version in `web/pnpm-lock.yaml`; change both together. A change to one side's jobs goes
  to the other side too: a job calls its script under `scripts/ci/` and runs nothing else
  of the check, so a check is changed in its script, once, and a new job is added to both
  files with a script of its own. The linters only report in CI; the commit hook is where
  files get fixed. The `pytest` job of both sides starts a Postgres of its own and runs the
  Postgres tests with the rest of the suite.
- `scripts/ci/`: what each CI job checks, one `<job>.sh` for each of `ruff`, `pyright`,
  `pytest`, `biome`, `vitest`, `playwright`, `gitleaks`, `trivy`, and `semgrep`, named after
  GitLab's job and called by that job and by GitHub's job of the same check. A script is
  POSIX `sh`, goes to the repository's root or to `web/` by its own place, and installs
  nothing: what a job installs first differs by side and stays in the CI file, which also
  names each tool's version. On GitHub `ruff.sh` writes a finding as an annotation
  (`GITHUB_ACTIONS`). `tests/test_ci_parity.py` fails when a job is on one side only and not
  in its table `ONE_SIDE` (the `images` job of `scan.yml`), when a job runs a check outside
  its script, and when a script is not called by both sides; `tests/ci_files.py` reads the
  CI files and the scripts for it and for the gate tests. The Deep Agents canary is the one
  job with a twin that calls no script, since it upgrades a package before its gate; it is
  in the table `OWN_TEST`, and its own test compares its two sides. The commit hook keeps commands
  of its own, since it changes files (`--fix`, `--write`) where CI only reports.
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
- `tests/`: pytest suite. `tests/AGENTS.md` describes its fixtures, fakes, stubs, and
  stand-ins; read it before changing a test.
- `.specify/`: Spec Kit templates, scripts, installed extensions, and the constitution. Feature
  artifacts go under `specs/<NNN-slug>/` as `spec.md`, `plan.md`, and `tasks.md`.
- `.claude/skills/speckit-*/`: the Spec Kit skills for Claude Code. Codex CLI reads its own copy
  from `.agents/skills/speckit-*/`, installed with `specify integration install codex`. Both
  directories are generated by Spec Kit; edit neither by hand.
- `README.md` and `docs/`: the user documentation. The README holds setup, running the
  service, and the development gates, and lists the topics under `docs/` in reading order:
  `models.md`, `api.md`, `tool-servers.md`, `agents.md`, `a2a-server.md`,
  `web-console.md`, and `observability.md`. Each doc
  ends with a **Next:** link to the one after it. A change to what one of them describes
  updates that file; a new doc takes its place in the README's list and in the Next links
  around it, and no file is renamed or numbered.
- `AGENTS.md`: this file. `CLAUDE.md` only imports it plus the constitution for Claude Code.
  A directory's `AGENTS.md` has a `CLAUDE.md` beside it that only imports it.

## Conventions

- Configuration goes through `Settings`. Never hardcode hosts, ports, connection strings, or
  model ids; add a field with a safe local default, or no default when the value carries a
  credential, and document it in `.env.example`.
- New behavior ships with a test: in `tests/` for Python, and in a `*.test.ts` or `*.test.tsx`
  file under `web/__tests__/` for the web app.
- Coverage has a floor: `fail_under` in `pyproject.toml` for the service's lines, and the
  four thresholds in `web/vitest.config.mts` for the web app. Both are measured as CI runs the
  suites, so with the Postgres tests, and nothing of either project is left out of
  the count apart from `web/components/ui/`, which shadcn generates. A change that brings a number below its floor adds the tests it lacks. The commit hook
  runs the service's suite without a database, so it holds a lower floor of its own, beside
  its command in `.pre-commit-config.yaml`; `tests/test_coverage_gate.py` keeps the three
  places in step. A floor
  is raised as its number rises and lowered only with the reason written beside it. Coverage
  shows what no test runs; it does not show that what runs is checked.
- Write the tests before opening a pull request or merge request, not after. Its description
  has a Tests section that names the tests covering the change, new ones and existing ones
  alike. A change that needs no test, such as documentation or a dependency bump, says so in
  that section and gives the reason.
- A Ruff suppression carries a reason in its comment and is removed once the cause is fixed.
- The service imports no name from a private module of Deep Agents, one whose path has a
  part that starts with an underscore, apart from those `tests/test_prompts.py` lists in
  `PRIVATE_IMPORTS`, each with its reason. A new one is added to that table, and a helper
  that is a few lines over public types is written here instead.
- pyright reports nothing over `src/` and `tests/`. A type error is fixed in the code or in
  its annotations. A line is silenced only where a library's own types say less, or other,
  than what the library does, with `# pyright: ignore[<rule>]` on that line and the reason
  beside it; a bare `# type: ignore` silences nothing, and none is written. No file is left
  out of the check and no rule is switched off for the service. One rule is off for the
  tests alone, `reportOptionalMemberAccess`, with its reason beside it in `pyproject.toml`:
  a test that reads an attribute of `None` fails when it runs. Every other rule holds for
  the tests as for the service. A test that gives a function what its signature refuses, to
  show the refusal, or a stand-in for what it takes, types that value as `Any`
  (`cast(Any, ...)`, or a variable annotated so); the tests' shared typed accessors are in
  `tests/conftest.py` (`detail_of`, `indexed_locks`). pyright is pinned to one exact version
  in `pyproject.toml`, so the hook and both CI systems report the same; a new version is
  taken in a change of its own, with what it newly reports fixed in it.
  `tests/test_type_check_gate.py` holds this.
- A change to the schema is a new migration at the end of `MIGRATIONS` in
  `runtime/migrations.py`, with its checksum added to `RELEASED` in
  `tests/test_migrations.py`. A migration that was merged is never edited: a database keeps
  its checksum and a start refuses another. Migrations only go forward, and one adds and
  does not take away (a new column is nullable or has a default), so that an older build
  works on the newer schema while a release is rolled out. No other module makes or
  changes a table, which `tests/test_migrations.py` checks, and
  `tests/integration/test_migrations_postgres.py` applies the list to a real Postgres; run
  it for every new migration, since the commit hook does not and CI does only once the
  change is pushed.
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
  What an agent writes in its own words is not searched for an address, as feature 005
  (FR-028) has it: its replies and progress updates, as a run's stream and the thread carry
  them, and the progress kept with its task and returned in the thread's state (feature
  008). Only the console's runs take an address out of every text of every event they hand
  on (`UnattendedRuns._scrubbed`, feature 007).
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
- A named caller's token appears nowhere: in no answer, stream, thread, or log line at any
  level. A caller's message is cleaned of every token before it is used, and every log record
  through the record factory (`TOKENS` in `delegates/addresses.py`, filled by
  `runtime/callers.py`). `tests/test_a2a_server_nowhere.py` searches everything the service
  gives back, keeps, and logs for two recognizable tokens; a new operation or log line about
  callers is added to it.
- Telemetry holds names and never content (feature 011): an attribute of a span or of a
  metric is an id the service made, a thread's id, a name the deployment or an operator
  configured, a route as the service declares it, one of the service's own words, or an
  exception's class name. No message, tool argument or result, model reply, agent's reply,
  exception's text, agent address, API key, or caller's token is ever one, and neither is
  anything of a request as it was written: its path, query, headers, body, or host name. A
  thread's id is on its run's span and nowhere else, and no metric names a run or a
  thread. Every span, metric, and attribute is written in `runtime/telemetry.py`, which
  is the only module that imports OpenTelemetry, and nothing registers a global provider,
  sets OpenTelemetry's current span, or reads a trace's id a client sent, so nothing a
  model, a tool server, an agent, or a client receives changes. Recording is done inside
  `Telemetry._safely` and never raises into a run, a step, or a request.
  `tests/test_telemetry_nowhere.py` searches every exported span and metric and holds each
  attribute to what it may be (`allowed`); a new span, metric, or attribute is added to
  it, and to `docs/observability.md`.
- A thread another agent started over A2A (feature 009) runs on the strict build and on no
  other: `SegmentedGraph` asks who started a thread before every run, of every kind, and
  refuses a caller's thread on the ordinary build and any thread whose lookup fails. A
  context taken for a thread asks the received-task store, with the feature on or off, so
  that an instance with the feature off never runs a thread another instance gave a caller.
  A new way to run a graph goes through a context's `astream`, which every other entry of
  the wrapper refuses. `tests/test_a2a_server_approval.py` holds it.
- A console run is stopped only between two steps, never by cancelling it, also when the
  service stops: `SegmentedGraph` is the one place that decides where (`BOUNDARY_NODES`),
  so that an action under way is never cut off (principle I). `tests/test_unattended_stop.py`
  stops a run during a model call, during a tool step, and during an approved action; a
  change to how a run is driven keeps those passing.
- An end-to-end test of the web app is a `*.spec.ts` file under `web/e2e/`. It adds to the
  unit tests and does not replace them. It never needs the Freyja service: the service's
  answers are handed to the page in the browser, through `web/e2e/api.ts`.
- Commit messages use the `type: summary` form: `feat:`, `chore:`, `docs:`.
- The README and `docs/` document only what exists in the repo, never planned work.

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
