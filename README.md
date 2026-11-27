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

`.env` is git-ignored. The compose file and `.env.example` carry the same local-only database credentials, so the copied file connects without edits; only the model key needs filling in. To save [LLM profiles](docs/models.md) the service also needs `FREYJA_SECRET_KEY`, which is left unset there. uv stops reading `.env` at the first line it cannot parse, such as a model line still holding a `<...>` placeholder, and only warns, so every line after it is ignored: fill in or comment out the block you do not use.

## Run the dev server

With auto-reload, listening on `127.0.0.1:8000`:

```bash
uv run --env-file .env uvicorn freyja_agent.api.app:app --reload
```

Or through the console script, which reads the bind address from settings:

```bash
uv run --env-file .env freyja-api
```

Startup opens one Postgres connection pool, creates the LangGraph checkpointer and store tables if they are missing, and brings Freyja's own tables to this build's schema version (see [The database schema](#the-database-schema)): the `freyja_run_locks` table, the four agent tables (`freyja_agent_conversations`, `freyja_agent_sends`, `freyja_agent_tasks`, `freyja_agent_calls`), the two tables of saved agents (`freyja_agents`, `freyja_agents_state`), the two LLM profile tables (`freyja_llm_profiles`, `freyja_llm_state`), the table of the thread index (`freyja_threads`), and the two tables of tasks received from other agents (`freyja_a2a_conversations`, `freyja_a2a_tasks`, made with `FREYJA_A2A_SERVER` off too, so that turning it on needs no step of its own). If the database cannot be reached, startup fails with an error that names the connection (password redacted) and the process exits instead of serving. A model that cannot be used does not stop startup: the service starts, logs which LLM profile is not usable and why, and ends every run with `no_usable_model` until a usable profile is active (see [No usable model](docs/models.md#no-usable-model)).

Check it is up:

```bash
curl localhost:8000/healthz
# {"status":"ok"}
```

Interactive API docs are at <http://localhost:8000/docs>. There is no authentication: anyone who can reach the port acts as a user, which is why the server binds `127.0.0.1` by default.


### The database schema

Freyja's tables are made and changed by migrations: an ordered list in `src/freyja_agent/runtime/migrations.py`, applied when the service starts. The table `freyja_schema_migrations` keeps one row for each migration a database has had, so a start that finds nothing to apply changes nothing and waits for no table.

- **A new database** gets every migration in order.
- **A database from before there were migrations** is taken through the first one, which makes only what is missing: a task table created by an earlier version gains `agent_ref`, `progress`, and `progress_at`, and its rows are kept.
- **Two instances that start together** take turns, so a migration is applied once.
- **Each migration is one transaction.** It is applied whole or not at all.

Startup stops with an error, and changes nothing, when:

- **the database was migrated by a newer build.** A build is never run against a schema it does not know. Run the newer build, or point this one at another database. This also happens on a development database after a branch with a newer migration was run on it.
- **a migration the database had is not the one this build has**, or the database skipped one.
- **a migration cannot get a table within five seconds** because another session holds it, such as a long report or a backup. Start the service again once that session has ended.

Migrations only go forward. To undo a schema change, add another migration or restore a backup taken before it. So that an older build keeps working while a newer one is rolled out, a migration adds and does not take away: a new column is nullable or has a default, and a column is dropped only by a later release than the one that stopped using it.

## Docs

With the service running, start a run and stream its events:

```bash
curl -N -X POST localhost:8000/threads/demo-1/runs \
  -H 'content-type: application/json' \
  -d '{"message": "Summarize what you can do and which skills you have loaded."}'
```

The rest is documented by topic under `docs/`, listed in the order a newcomer needs it. Each doc ends with a link to the next one, and each also stands alone as a reference:

1. [Models](docs/models.md): saving, testing, and activating LLM profiles, the ways to reach a model, and how their keys are kept secret. Nothing runs without a usable model.
2. [Use the API](docs/api.md): threads, runs and their events, deciding on a proposal, the list of threads, runs that go on without a reader, stopping a run, and the host names the service answers for.
3. [Tool servers](docs/tool-servers.md): connecting MCP servers, which of their tools run without approval, what happens when one fails, and the catalog.
4. [Agents](docs/agents.md): handing work to A2A agents, their pending tasks, and adding, changing, moving, checking, and removing agents while the service runs.
5. [Receive tasks from agents](docs/a2a-server.md): other agents handing Freyja work over A2A, what a caller is told, conversations, and the limits.
6. [Web console](docs/web-console.md): the console under `web/`, with its Tasks, Agents, and Models sections.

## Lint, format, and types

Ruff handles both linting and formatting for Python. Configuration lives in `pyproject.toml` under `[tool.ruff]`.

```bash
uv run ruff check .            # lint
uv run ruff check . --fix      # lint and apply safe autofixes
uv run ruff format .           # format in place
uv run ruff format --check .   # report unformatted files without changing them (CI mode)
```

[pyright](https://github.com/microsoft/pyright) checks the types of the service's code under `src/` and of the tests under `tests/`. Its settings are in `pyproject.toml` under `[tool.pyright]`, and its version is pinned there to one exact release, so that it reports the same everywhere it runs. It needs nothing but uv: Node comes with the package, and pyright itself is downloaded the first time it runs, which needs the network. One rule is off for the tests alone, `reportOptionalMemberAccess`: a test that reads an attribute of `None` fails when it runs, so the rule adds nothing there.

```bash
uv run pyright                 # check the types of src/ and tests/; changes no file
```

A type error is fixed in the code or in its annotations. Where a library's own types say less than what the library does, the line is silenced with `# pyright: ignore[<rule>]` and the reason beside it; a bare `# type: ignore` silences nothing. A test that hands a function a value its signature refuses, to show the refusal, types that value as `Any`.

Biome does the same as Ruff for the web app. Configuration lives in `web/biome.json`.

```bash
cd web
pnpm lint                      # biome check: lint and report unformatted files
pnpm format                    # format in place
```

The pre-commit hook first checks the staged files themselves: it removes whitespace at the end of a line, makes a file end with one newline, and stops a commit that holds YAML that does not parse, a marker a merge left behind, or a secret (see [Secrets](#secrets)). It then runs `ruff check --fix` and `ruff format` on staged Python files, then pyright over `src/` and `tests/` when a file under `src/` or `tests/`, `pyproject.toml`, or `uv.lock` is staged, then the Python unit test suite. When a file under `web/` is staged, it also runs `biome check --write` over the web app and then the web unit tests. If Ruff or Biome changes a file, the commit is blocked: re-stage the file and commit again. If pyright reports an error or a test fails, the commit is blocked until it passes.

The web hooks run the scripts in `web/package.json` with pnpm, so they need Node and pnpm on the path and the dependencies installed in `web/`. Without pnpm they fail with ``Executable `pnpm` not found``. To run the hook over the whole repo:

```bash
uv run pre-commit run --all-files
```

CI runs both linters and pyright on every pull request and merge request and on `main`: `.github/workflows/lint.yml` on GitHub, and the `ruff`, `pyright`, and `biome` jobs of `.gitlab-ci.yml` on GitLab. There they only report: `ruff check`, `ruff format --check`, `pyright`, and `biome check` change no file, so a failure is fixed locally with the commands above.

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
uv run pytest --cov            # full suite with coverage; fails below the threshold, which needs the Postgres tests
uv run pytest --cov --cov-fail-under=89   # the same without a database, as the commit hook runs it
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

It acts on a keyword at the start of the message, such as `ask`, `slow <seconds>`, `steps <n> <seconds>` (a task that reports "Step k of n" every `<seconds>`, then completes), `fail`, or `reject`; the list is at the top of `tests/a2a_stub.py`. It writes a line for each message it receives, with the hand-over count the message carried (`None` for none). The stand-in model endpoint is served the same way; see [Try it with the stand-in endpoint](docs/models.md#try-it-with-the-stand-in-endpoint).

The Postgres integration tests under `tests/integration/` are skipped unless `FREYJA_TEST_DATABASE_URI` names a database that may be wiped. Create it once in the compose container, then run them:

```bash
docker compose exec postgres createdb -U freyja freyja_test
FREYJA_TEST_DATABASE_URI=postgresql://freyja:freyja@localhost:5432/freyja_test uv run pytest tests/integration
```

CI runs both unit test suites on every pull request and merge request and on `main`: `.github/workflows/unit-tests.yml` on GitHub, and the `pytest` and `vitest` jobs of `.gitlab-ci.yml` on GitLab. The `pytest` job of both starts a Postgres of its own and runs the Postgres tests under `tests/integration` with the rest of the suite. On a developer's machine and in the commit hook they stay opt-in.

### Coverage

Both suites have a floor, and the commit hook and CI run them with coverage, so a change that falls below it is stopped in all three places.

| Project | Measured | Floor | Where it is set |
|---|---|---|---|
| Service | 96.64% of lines | 95% | `fail_under` under `[tool.coverage.report]` in `pyproject.toml` |
| Service, without the Postgres tests | 91.50% of lines | 89% | `--cov-fail-under` in the commit hook's command, in `.pre-commit-config.yaml` |
| Web app | 95.05% of statements, 93.67% of branches, 95% of functions, 96.74% of lines | 90% each | `coverage.thresholds` in `web/vitest.config.mts` |

The service's numbers were measured on 2026-11-27 and the web app's on 2026-11-19, as CI runs the suites. For the service that means with the Postgres tests, which both CI systems run against a Postgres the job starts. The commit hook has no database, so it skips those tests, which leaves the Postgres stores unmeasured, and it holds the lower floor of the second row. Nothing of either project is left out of the count, except `web/components/ui/`, which shadcn generates. A run without coverage (`uv run pytest`, `pnpm test`) is not measured, so running one file is not held to the whole suite's number. Raise a floor as its number rises.

The service's lines are counted with Python 3.12's own monitoring (`core = "sysmon"`), which adds about a second to the suite; on 3.12 that counts lines and not branches.

### Deep Agents canary

Once a week, and for no pull request or merge request, CI runs pyright and the Python suite against a newer [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview) than `uv.lock` names, so that what an upgrade breaks is learned the week a release comes out: `.github/workflows/deepagents-canary.yml` on GitHub, which can also be started by hand, and the `deepagents-canary` job of `.gitlab-ci.yml` on GitLab, which runs once a pipeline schedule for `main` is made in the project's settings (Build, Pipeline schedules). One leg takes the newest release the pin in `pyproject.toml` allows (`uv sync --upgrade-package deepagents`), the other the newest release of all, past the pin (`uv pip install --upgrade-package deepagents deepagents`); the two test the same release until one is published outside the pin. Each gate then runs with `uv run --no-sync`, without which `uv run` puts back what the lock file names. Neither leg commits what it upgrades.

## End-to-end tests

The web app's end-to-end tests run with [Playwright](https://playwright.dev) in Chromium and Firefox:

```bash
cd web
pnpm exec playwright install chromium firefox   # once per machine: downloads the two browsers
pnpm test:e2e                  # playwright test
```

Playwright builds the app and serves it on port 3100 itself, so nothing needs to be running first, the Freyja service included: each test hands the page what the service would answer (`web/e2e/api.ts`), and the tests about a stopped service let the request through to an address nothing listens on. The tests are `*.spec.ts` files under `web/e2e/`: `console.spec.ts` for the sidebar and the sections, `models.spec.ts` for the Models section as it is read, `profiles.spec.ts` for adding, activating, testing, changing, and deleting profiles, `agents.spec.ts` for the Agents section as it is read, its warnings, and that an agent's address stays in it, `agents-manage.spec.ts` for adding an agent, checking one, changing one's approval choice, moving one, and removing one, `agents-mode.spec.ts` for the agents' approval mode, and `tasks.spec.ts` and the other `tasks-*.spec.ts` for the Tasks section, `tasks-callers.spec.ts` among them for the tasks other agents started. Playwright reads nothing outside that directory, and Vitest skips it, so `pnpm test` and `pnpm test:e2e` never run each other's files. After a run, `pnpm exec playwright show-report` opens the report. To test in another browser as well, add a project for it in `web/playwright.config.ts`.

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

The service container reads the same `FREYJA_*` variables as `freyja-api` does anywhere; `FREYJA_DATABASE_URI` is the one it cannot start without. Give them one by one or from the orchestrator's own configuration: `docker run --env-file` keeps the quotes that `.env.example` puts around a JSON value. To reach either from outside its container, for example through `docker run -p` or a Kubernetes Service, set `FREYJA_HOST=0.0.0.0` for the service or `HOSTNAME=0.0.0.0` for the console, and do it only where the network around the container already decides who may connect: whoever reaches the console can change the service's configuration. A console that is opened under a name, or that reaches the service by one, as with `FREYJA_API_URL=http://freyja-agent:8000`, needs each such name in the service's `FREYJA_ALLOWED_HOSTS`: the service refuses a request made for a name it does not list. A proxy in front of the console, or of the service, has to pass on the `Host` the browser sent (nginx: `proxy_set_header Host $host;`). One that replaces it hides from the service which name was asked for: nginx's `proxy_pass` sends the upstream's address by default, and since an address is always answered, every name is then served. Such a proxy has to refuse unknown names itself.

**The web image holds the service's address.** Next.js writes `FREYJA_API_URL` into the build, so it is given when the image is built and a container cannot change it. The default, `http://127.0.0.1:8000`, is right for the shared network namespace above. For another layout, build the image for it:

```bash
docker build --build-arg FREYJA_API_URL=http://freyja-agent:8000 -t freyja-web web
```

The base image and the image uv is taken from can come from a mirror: `--build-arg BASE_IMAGE=...` for both Dockerfiles and `--build-arg UV_IMAGE=...` for the service's. The service image carries Python and the service alone. A tool server that Freyja launches with a command (`FREYJA_MCP_SERVERS` with `command`) needs that command in the image, and `uvx`, `npx`, and `git` are not there: use a server with a `url`, or build an image of your own on top of this one.

Each image has a health check: the service's asks `/healthz`, the console's its own Models page (`/models`), both on loopback.

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
| `FREYJA_ALLOWED_HOSTS` | `[]` | The host names the service answers for, as a JSON list of names alone (no scheme, port, or path). `localhost` and IP addresses need no entry. A request made for any other name is refused with `403` `unknown_host`; see **Host names** under [Use the API](docs/api.md). A deployment lists the name its console is opened under and, where the console reaches the service by a name, that name. An entry that is no host name stops startup. |
| `FREYJA_DATABASE_URI` | none, required | Postgres connection for the checkpointer, store, and run locks. No default because it carries credentials; `.env.example` has the value the compose container uses. |
| `FREYJA_MODEL` | `anthropic:claude-sonnet-5-5` | Model for the orchestrator, as a LangChain `provider:model` string. Installed providers: `openai`, `anthropic`, `openrouter`. With the provider's variables below it is the environment's [LLM profile](docs/models.md), used until a saved profile is activated. |
| `FREYJA_OPENAI_RESPONSES_API` | `true` | Whether an `openai:` model is called through OpenAI's Responses API or, when `false`, through chat completions. Set `false` for an OpenAI-compatible server whose Responses API answers a first message and refuses every later one. Other providers ignore it. |
| `FREYJA_WORKSPACE_DIR` | `workspace` | Root of the orchestrator's file tools. `skills/` inside it is committed source; `cases/` is written at runtime and git-ignored. |
| `FREYJA_MAX_STEPS` | `50` | Maximum orchestrator steps per run. Reaching it ends the run with an `error` event of code `step_limit`; the thread stays usable. A plain reply takes about 7 steps and each tool call about 4 more, so the default allows roughly ten tool calls per turn and values below 11 stop every tool call. |
| `FREYJA_RUN_LOCK_TTL_SECONDS` | `60` | Expiry of the per-thread run lease. A run renews it while streaming; a crashed instance frees its threads after this long. |
| `FREYJA_MCP_SERVERS` | `{}` | Tool servers as a JSON object; see [Tool servers](docs/tool-servers.md). The default is none. |
| `FREYJA_MCP_CALL_TIMEOUT_SECONDS` | `30` | Time limit for one tool server call, including connecting. A call over it returns an error result and the run continues. |
| `FREYJA_MCP_STARTUP_TIMEOUT_SECONDS` | `20` | Time limit per server for connecting and listing its tools at startup. Servers connect concurrently. |
| `FREYJA_A2A_AGENTS` | `{}` | Agents as a JSON object; see [Agents](docs/agents.md). The default is none. |
| `FREYJA_A2A_CALL_TIMEOUT_SECONDS` | `30` | How long one hand-off, answer, or cancel waits within a run. A task still working at the limit becomes pending. |
| `FREYJA_A2A_STARTUP_TIMEOUT_SECONDS` | `20` | Time limit per agent for reading its card at startup. Agents start concurrently. A card read asked for through the Agents routes (an add, a move, a check) takes this long at most, and never more than twenty seconds. |
| `FREYJA_A2A_POLL_INTERVAL_SECONDS` | `10` | How often pending tasks are checked and waiting outcomes delivered. |
| `FREYJA_A2A_TASK_MAX_AGE_SECONDS` | `86400` | When a pending task is given up, counted from its start. No cancel is sent. |
| `FREYJA_BACKGROUND_RUN_ATTEMPTS` | `3` | Attempts for one background run, the first included. |
| `FREYJA_BACKGROUND_RETRY_SECONDS` | `60` | Wait between background run attempts. |
| `FREYJA_BACKGROUND_RUN_CONCURRENCY` | `4` | Background runs under way at once on one service instance; further ones wait their turn. |
| `FREYJA_SECRET_KEY` | unset | The secret that protects the API keys of saved LLM profiles: a Fernet key, 44 URL-safe base64 characters. Unset, the service starts and saves no profile; a value that is not a Fernet key stops startup. See [The secret](docs/models.md#the-secret). |
| `FREYJA_LLM_TEST_TIMEOUT_SECONDS` | `60` | The most a test of an LLM profile may take, both requests together. Greater than 0 and at most 60. |
| `FREYJA_A2A_SERVER` | `false` | Receiving tasks from other agents over A2A; see [Receive tasks from agents](docs/a2a-server.md). With it on, the service serves the card at `/.well-known/agent-card.json` and the operations at `POST /a2a`, and builds a second orchestrator for the threads callers start: on it a message to a delegate agent follows the agents' approval mode as on any thread, a named caller's runs work in a directory of their own, and the model is told an alias in place of its thread's id. A thread a caller started is never run on the other orchestrator, and with the feature off it is not run at all: a message or a decision on it is refused with `409` `a2a_server_off`, and its state is still read. With it off, the settings below are not used, and their limits are not checked, apart from `FREYJA_A2A_SERVER_HOST` and `FREYJA_A2A_SERVER_PORT`: either set without the other, or both set while the feature is off, stops startup. |
| `FREYJA_A2A_SERVER_WAIT_SECONDS` | `30` | The most a send without a stream waits for its task to end, 0 to 600. A task that has not ended by then is answered as working, and goes on. |
| `FREYJA_A2A_SERVER_CALLER_TASKS` | `5` | Received tasks in progress at once for one caller, 1 to 100. |
| `FREYJA_A2A_SERVER_TASKS` | `20` | Received tasks in progress at once for all callers: at least the caller's limit, at most 1000. |
| `FREYJA_A2A_SERVER_MAX_HOPS` | `3` | The hand-over count at which a caller's message is refused, 1 to 16. A message carries its count in its metadata as `freyja.hops`, and every message Freyja sends to an agent from a caller's thread carries the count plus one; see [The hand-over count](docs/a2a-server.md#the-hand-over-count). |
| `FREYJA_A2A_SERVER_FILES_DIR` | `caller-files` | Where a named caller's files are kept, one directory per caller, read and written by that caller's runs only; the skills are read from the workspace and cannot be changed there. It must not be the workspace, be inside it, or contain it, and is git-ignored. |
| `FREYJA_A2A_SERVER_CALLERS` | `{}` | Named callers: a JSON object of a caller's name to `{"token": "..."}`. A name is lowercase letters, digits, hyphens, and underscores, starting with a letter, at most 32 characters, and not `local`; a token is 16 to 4,096 visible ASCII characters, each caller's its own; at most 20. A caller is who sends its token as `Authorization: Bearer <token>`; see [Receive tasks from agents](docs/a2a-server.md#named-callers). |
| `FREYJA_A2A_SERVER_HOST` | unset | The address the outside listener binds; set with `FREYJA_A2A_SERVER_PORT`, or neither. It serves the card and the operations and nothing else: the card to anyone, the operations to named callers only. It needs the feature on, a named caller, and `FREYJA_A2A_SERVER_URL`. |
| `FREYJA_A2A_SERVER_PORT` | unset | The outside listener's port, 1 to 65535, and not the service's own on the same address. |
| `FREYJA_A2A_SERVER_URL` | unset | The address callers reach the outside listener under, as its card gives it: `http` or `https`, a host, and at most a port. With `http` the service warns at startup that tokens can be read on the network path; it does not encrypt itself. |
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

Each phase becomes its own pull request with a `feat:` message, for example `feat: runtime foundation for thread runs (001, phases 1-2)`. After it merges, **recreate the branch** before continuing with the next phase. Before the PR: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, and the checkpoint in `tasks.md` for that phase.

**8. Converge.** After the last phase, compares the code against the spec, plan, and tasks and appends any unbuilt work to `tasks.md` so one more implement pass can finish it.

```text
/speckit-converge
```

### Codex CLI

The same skills live under `.agents/skills/` for Codex CLI, installed with `specify integration install codex`. Replace `/speckit-<step>` with `$speckit-<step>`; everything else, including the branch and PR routine, is identical.
