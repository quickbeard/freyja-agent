# Tasks: Receive Tasks from Other Agents over A2A

**Input**: Design documents from `/specs/009-a2a-server/`

**Prerequisites**: plan.md, spec.md, research.md (R1 to R21), data-model.md,
contracts/a2a-server.md, contracts/settings.md, contracts/openapi.yaml,
contracts/sse-events.md, quickstart.md, checklists/requirements.md and
checklists/security.md (every item ticked)

**Tests**: Included. FR-031 asks for automated tests of the service and the console that need
no network and no agent installed or running, with a stand-in caller built from the
protocol's own client, an end-to-end test of the console's part of every user story, and
eight named searches and trials; principle V asks that new behavior ship with tests; the
plan's "Requirement to test map" names the file for each requirement. In each phase the test
tasks come first: write them, watch them fail, then implement. An end-to-end task comes after
the code it drives.

**Organization**: Tasks follow the plan's phases, which are the order the feature is built
and merged in, and not the user stories' priority order. Two departures are on purpose
(plan, "Why this order"): User Story 4 (P4, cancel) is built before User Story 3 (P3, named
callers), because taking a caller away cancels its tasks and needs cancel and the rejection
by rule first; and User Story 5 is built in three parts: its limits with User Story 1, its
isolation with User Story 3, and the hand-over count and the full proof last. Research
references (R1 to R21) point at research.md.

Each setting is added in the request that first reads it, and goes into `.env.example` with
it (contracts/settings.md), so that no request documents a setting that does nothing yet:
the feature's switch, the two limits, and the files directory in request 1;
`FREYJA_A2A_SERVER_WAIT_SECONDS` in request 2; the callers, `Callers`, and the outside
listener's three settings in request 5; `FREYJA_A2A_SERVER_MAX_HOPS` in request 6. The
plan's phase 1 lists "the settings" and `Callers`; nothing before request 5 reads a caller's
token, so both come with the first request that does.

Three things the plan puts in a later phase are built and tested in request 2, because from
request 2 on a caller can reach them (review of these tasks, plan.md): how a task waiting
for a person looks to its caller (plan phase 3), the refusal of a message to a caller's
thread through the service's own routes and the console's missing box (`caller_thread`,
plan phase 3), and another message in a conversation (plan phase 4, T041 here).

Two refusals are absent between requests, and a person still decides in both windows: the
hand-over count is not checked before request 6, and every hand-off from a caller's thread
waits for a person from request 1 on (FR-013a; since feature 010, while the agents' approval
mode or the agent is Manual); a proposal that a run comes to after a
caller's task was stopped in the console is not rejected by rule before request 4, and a
person can still decide it.

Terms used below:

- **A caller's thread**: a thread with a row in `freyja_a2a_conversations`.
- **The strict build**: the second orchestrator built while the feature is on, in which
  files go through `CallerFiles` and the model is told an alias in place of the thread's id
  (R6, R16). Until feature 010 every agent tool was also gated there; since then its agent
  tools are classified as the ordinary build's are. **The ordinary build** is the one built today.
- **The store**: `ReceivedTaskStore` in `src/freyja_agent/runtime/received_tasks.py`, with
  `InMemoryReceivedTaskStore` and `PostgresReceivedTaskStore`.
- **`ReceivedTasks`**: the feature's service in `src/freyja_agent/api/received.py`.
- **A look**: one reading of where a caller's thread stands and what it means for its task
  (R7).
- **The stand-in caller**: `tests/a2a_caller.py`. **The stand-in agent**: `tests/a2a_stub.py`.
  **The scripted model**: `ScriptedChatModel` in `tests/fakes.py`. **The stand-in model**:
  `tests/llm_stub.py`.
- **An app with the feature on**: one made by `make_a2a_server_app` in `tests/conftest.py`
  (T004).
- **A thread marked by hand**: a thread whose conversation row a test wrote through the store
  before its first run, as an accepted message would. A test in request 1 runs such a thread
  through the functions of `api/runs.py` or through `ReceivedTasks.accept`, never through a
  thread route, which refuses a caller's thread from request 2 on (T050).
- **Refused before the model**: the test asserts that the scripted model's call count did
  not move and that no thread state, conversation row, or task row was made.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1 to US5)
- Every task names the file it touches

## Path Conventions

Two projects in one repo: the service in the src layout (`src/freyja_agent/`, `tests/`,
repo-root files) and the web app under `web/` (`web/components/`, `web/lib/`). Web unit
tests are `*.test.ts` and `*.test.tsx` files under `web/__tests__/`, which follows the layout
of the code it covers. End-to-end tests are `*.spec.ts` files under `web/e2e/`.

## Pull requests

Each pull request goes to GitHub with a matching merge request on GitLab, is built on a
branch recreated from the latest `main`, and leaves both suites passing, the README and
`docs/` true, and the feature off by default. Request N is the plan's phase N; the task
phases below are numbered one higher, since Phase 1 is the setup. They follow the plan's
phases:

| Request | Plan phase | Task phases | Tasks | What it delivers |
|---|---|---|---|---|
| 1 | 1 | Phase 1 and Phase 2 | T001 to T031 | The foundations; nothing is served |
| 2 | 2 | Phase 3 | T032 to T055 | User Story 1, the limits of User Story 5, and what a caller reaches from here on (the MVP) |
| 3 | 3 | Phase 4 | T056 to T062 | User Story 2 |
| 4 | 4 | Phase 5 | T063 to T079 | User Story 4 |
| 5 | 5 | Phase 6 | T080 to T097 | User Story 3 and the isolation of User Story 5 |
| 6 | 6 | Phase 7 | T098 to T106 | The rest of User Story 5 |
| 7 | 7 | Phase 8 | T107 to T111 | The quickstart walked; the documents read against the code |

`/speckit-converge` follows request 7.

## Standing rules for every task

- **Approval (principle I).** The gate, its three decisions, `api/decisions.py`, and a
  resume's validation are not changed. No operation of a caller, and nothing in a message, a
  status, a metadata field, or a token, reaches a resume (FR-013). The one resume the service
  makes by itself rejects every action and approves none (R11). A test that shows an action
  ran asserts it ran once, and only after a person approved or edited it.
- **Fail closed (R6).** A caller's thread is never run on the ordinary build, and a lookup of
  who started a thread that fails refuses the run. The conversation row, the lookup in
  `begin_run`, and the refusal in `SegmentedGraph` go in together, in request 1.
- **Rules before a model (principle VI, FR-026).** Every refusal this feature adds is a fixed
  rule on the request and on stored rows, decided before a thread, a conversation row, or a
  task row is made and before a model is asked. Each test of a refusal shows it is refused
  before the model.
- **Feature off (FR-001, SC-002).** With `FREYJA_A2A_SERVER` unset, no route, answer, table
  row, or log line differs from `main`. Run `uv run pytest` at the end of every phase. An
  existing test may change only where a task names it (T039: the web fixture of a row gains
  `caller: null`); any other existing test that fails is a defect of that phase.
- **Tokens (FR-028).** No token in a log line at any level, an answer, a stream, a thread, or
  an error. A token is a `SecretStr` from the moment it is read. Tests use
  `sk-recognizable-key-0001` and `sk-recognizable-other-0002`, which `.gitleaks.toml` lets
  through by exact pattern; that file is not changed.
- **What a caller is told (FR-017 to FR-019).** Every status sentence, reason, and error is
  one of the fixed texts of contracts/a2a-server.md and data-model.md, copied verbatim. A
  status names tools and agents; no argument, result, or text a tool, a subagent, or an agent
  wrote is read into a sentence. A caller is never given a thread's id, a proposal's id, a
  token, a key beyond its last four characters, an agent's address, or another caller's name.
- **Where the protocol lives.** Only `api/a2a_server.py` and `api/a2a_listener.py` import the
  SDK's server modules and types (plan, Structure Decision); `runtime/received_tasks.py`,
  `runtime/callers.py`, `api/received.py`, and `api/runs.py` do not, and are tested without
  them. No module imports a route module.
- **SQL.** Every statement is a fixed string with bound parameters. One composed with
  psycopg's `sql` module carries `# nosemgrep: <rule id>` with its reason (AGENTS.md). Text
  that came from a caller is cleaned of what a text column does not take (NUL) before it is
  kept.
- **Async.** No blocking I/O in async code (`ASYNC` rules). No new Ruff suppression.
- **Ask the owner first**: a dependency in either lock file, a CI file, `pyproject.toml`,
  `web/vitest.config.mts`, `.gitleaks.toml`, `web/playwright.config.ts`, the constitution.
  Only T003 touches one of them, and only with the owner's word.
- **Files of other features** (plan, "Files of other features that this one changes"):
  `delegates/a2a_tools.py` and `delegates/delegation_worker.py` are where feature 008 was
  built. Their changes go in on the `main` of their day; a conflict there keeps 008's code
  and makes this feature's change again on top of it.
- **Web.** Before writing code under `web/`, read `web/AGENTS.md`. No Server Action, Route
  Handler, `use cache`, or clock read during render. All text of a task is put in text nodes,
  never rendered as markup. Files under `web/components/ui/` are not edited by hand. Unit
  tests stub `fetch`; end-to-end tests hand the page the service's answers through
  `web/e2e/tasks-api.ts` and never need the service. Before `pnpm test:e2e`, nothing listens
  on port 3100 or 3109.
- **Hand checks.** Never start the service from the owner's `.env`, never hand ARTEX work,
  and never print a value from `.env`. A check instance runs from an environment file built
  from `.env.example` (chmod 600, deleted afterwards), with no `FREYJA_A2A_AGENTS`,
  `FREYJA_MCP_SERVERS`, model, provider, or LangSmith variable; its own `FREYJA_PORT=8020`
  and `FREYJA_DATABASE_URI` to the database `freyja_check_a2a` in the compose Postgres on
  port 5434 (made with `docker exec freyja-agent-postgres-1 createdb -p 5434 -U freyja
  freyja_check_a2a`, dropped afterwards); the stand-in model on 9977 saved and activated as
  an LLM profile with the id `call-tools`; the stand-in agent on 9978; the console on 3020
  with `FREYJA_API_URL=http://127.0.0.1:8020`; the outside listener on 8443; and a scratch
  workspace (a copy of `workspace/skills`) and files directory in the scratchpad. Stop what
  was started by its port (`fuser -k <port>/tcp`), never by a process pattern. Never stop the
  compose Postgres, and never use the database `freyja_test`.
- **Git.** Work only in the worktree `/home/minhnh71/works/freyja-agent-a2a-server`. One
  author per remote: what goes to GitHub is authored and committed as the GitHub account,
  for the one command, rebases included; the GitLab twin is rebuilt from the latest
  `vsf/main` under the repository's identity. No `Co-authored-by` line for the other remote,
  and no AI attribution in a commit, pull request, or merge request. Fetch both remotes and
  rebase on `origin/main` before every push.
- **Review.** Before the first commit of requests 1, 2, 4, 5, and 6, which touch who may
  call, what a caller can cause, what it is given, or the strict build, the diff is read by
  an independent reviewer given the files and the questions, not the conclusions, told to
  read only from the worktree, to change nothing, and never to run `pnpm` from a copy of
  `web/`. Each finding is checked against the code before it is acted on, and a test added
  for a finding is shown to fail without the fix.
- **Gates** for every request: the commit hook; the coverage floors (`fail_under` in
  `pyproject.toml`, the thresholds in `web/vitest.config.mts`); Semgrep as AGENTS.md gives
  it, with new files staged first and changed test files also scanned by name; Trivy over
  the source; `pnpm test:e2e` when `web/` changes; and the opt-in Postgres tests against
  `freyja_test_a2a` (T002) when a store changes.
- Each request's description has the sections Summary, What is new, Decisions I made on your
  behalf, Review focus, Tests, Gates, and Checked by hand; the Tests section names the tests
  covering the change. One description serves the pull request and the merge request, so it
  holds no backslash and no `#` followed by a digit.
- A task that says "record in plan.md" writes under a heading `Implementation Notes` in
  `specs/009-a2a-server/plan.md`, created the first time. A name this file gives a function,
  method, or parameter that is not in the plan may be chosen otherwise; the name chosen is
  recorded there.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: know the starting point and settle the one question for the owner. No code
changes apart from T003's, with the owner's word.

- [X] T001 Run `uv run pytest --cov` and, in `web/`, `pnpm test:coverage` on the branch as
      made from `main`, and record the two coverage numbers in plan.md; T109 compares the
      finished feature with them
- [X] T002 [P] Make a database of this feature's own for the opt-in Postgres tests:
      `docker exec freyja-agent-postgres-1 createdb -p 5434 -U freyja freyja_test_a2a` (the
      compose project belongs to the main checkout, so `docker compose exec` from the
      worktree does not find it; the server in that container listens on 5434 on this
      machine, so `-p 5434`, and if that is refused, the port its command line names), then
      `FREYJA_TEST_DATABASE_URI=postgresql://freyja:freyja@localhost:5434/freyja_test_a2a uv
      run pytest tests/integration` passes. Never use `freyja_test`, which the 008 session
      uses, and never stop the container
- [X] T003 Ask the owner whether `pyproject.toml` may name `a2a-sdk[http-server]` among the
      runtime dependencies (plan, "What is asked of the owner"; R19). Only with a yes: move
      the extra from the development group's entry to the service's own in `pyproject.toml`,
      run `uv lock`, and show that `uv sync` installs and removes no package (the list of
      installed packages is the same before and after); `uv.lock` changes in its metadata
      only. Nothing in request 1 imports the SDK's server modules; the first that does is
      T045, so without an answer request 2 stops there

**Checkpoint**: both suites pass, unchanged; the owner's answer is recorded in plan.md.

---

## Phase 2: Foundational (Blocking Prerequisites, plan phase 1)

**Purpose**: everything a caller's message will rest on, with nothing served: the settings
this phase reads; the two tables and both stores, with accepting a message under one lock
and the two limits; the strict build with its file backend and its alias; who started a
thread, asked by `begin_run` and enforced by `SegmentedGraph`; a caller's thread not run
while the feature is off; `api/runs.py`; `ReceivedTasks` with where a thread stands and
ending a task; `UnattendedRuns.join`; the lease's release passed on; and the stand-in
caller.

**⚠️ CRITICAL**: no user story can start before this phase is complete, and none of it is
left half done: a caller's thread run on the ordinary build is the one failure the design
exists to prevent.

### Tests

- [X] T004 In `tests/conftest.py`, add the fixture `make_a2a_server_app`: an application
      assembled the production way, as `make_a2a_app` does, with `FREYJA_A2A_SERVER` on and
      any setting of this feature given as a keyword, the stand-in agent (approval choice
      given, run without approval by default), the scripted model, the suite's clock, and an
      `InMemoryReceivedTaskStore`; it returns the app, its runtime, the store, the stand-in
      agent's record, and the model, and a way to make a second application on the same
      stores with other settings (the feature off, other callers). Its first lines say
      which tasks extend it: T090 adds the outside application
- [X] T005 [P] Write `tests/a2a_caller.py`, the stand-in caller: the SDK's own client
      (`a2a.client`) behind a small class that takes an httpx transport or a base URL, an
      optional token sent as `Authorization: Bearer`, and offers `card()`, `send(text,
      context=None, hops=None, return_immediately=False)`, `stream(text, ...)` yielding each
      event, `get(task_id)`, `follow(task_id)`, and `cancel(task_id)`; and a command,
      `python -m tests.a2a_caller --url <address> [--token T] [--context C] [--hops N]
      [--return-immediately] card|send|stream|get|follow|cancel [...]`, that prints each
      task or event as JSON, as quickstart.md uses it. In `tests/test_a2a_server_send.py`,
      part 1: the stand-in caller reads the stand-in agent's card and sends it a message over
      `StreamingASGITransport` (`tests/asgi_stream.py`), so the client's plumbing is shown
      before Freyja serves anything; and, on an application with the feature off and the
      tables empty, a run, a resume, a read of the state, the list of threads, and a round
      of the worker answer as on `main`, each context taken for a thread makes one lookup of
      who started it (a spy on the store), and nothing of this feature writes a row or a log
      line
- [X] T006 [P] Create `tests/test_a2a_server_callers.py` with its module docstring and part 1,
      the settings this phase reads, one test per rule of contracts/settings.md:
      `FREYJA_A2A_SERVER` defaults to `false`; `FREYJA_A2A_SERVER_CALLER_TASKS` defaults to
      5 and is "1 to 100"; `FREYJA_A2A_SERVER_TASKS` defaults to 20 and is "At least the
      caller's limit, at most 1,000"; `FREYJA_A2A_SERVER_FILES_DIR` defaults to
      `caller-files` and stops startup when it "is the workspace, is inside it, or contains
      it", compared after both are resolved, a relative one from the working directory as
      `FREYJA_WORKSPACE_DIR` is; each error names the variable and holds no value. With
      `FREYJA_A2A_SERVER` off these four rules are not applied (contracts/settings.md: "Off:
      every other variable here may be set and is not used"); the errors that hold with the
      feature off concern the outside listener and come in T080
- [X] T007 [P] Create `tests/test_received_tasks.py`, the store's rules, each case a function
      that takes a store so that T008 runs the same cases on Postgres, here on
      `InMemoryReceivedTaskStore` with the suite's clock: `setup()` twice changes nothing;
      accepting a message for a new caller and conversation id makes the conversation with a
      `thread_id` of "32 hexadecimal characters" and a task row `working` with `started_at`
      null, `hops` as given ("0 when it carried none"), and a `task_id` of "32 hexadecimal
      characters" that is not a thread's id; the same conversation id from two callers makes
      two conversations and two threads ("Primary key: (`caller`, `context_id`)"); a
      conversation whose last task is `working` refuses the message as busy and writes
      nothing; one whose last task ended takes a new task on the same thread; the two limits
      count the rows with `released_at` null, for the caller and in all, refuse at the limit
      with one answer for both while telling the code which (for the log), count a canceled
      task until it is released, and stop counting a released one; two accepts at once for
      one new conversation give one task, one refusal, and one thread, and two at once at a
      limit of one give one task; `mark_started` writes `started_at` and `base_checkpoint`
      once; an end names the state it replaces, so a second end that expects `working`
      writes nothing and says so; a row whose `state` is not `working` "never changes again,
      apart from `released_at` and the two columns that count rejections by rule";
      `ended_at` is "Set with every state other than `working`"; `result` is kept only with
      `completed`; a reason is one of `no_usable_model`, `model_error`, `step_limit`,
      `interrupted`, `internal`, `stopped`, `canceled`, and any other is refused; removing a
      task (a run that was refused) leaves its conversation; counting a rejection by rule
      raises `reject_attempts` by one and sets `reject_after`; who started a thread is one
      read by `thread_id` and gives `None` for a thread no caller started; the callers of a
      list of threads, with whether each conversation's last task is canceled, is one read;
      whether any conversation exists; a conversation's last task; the rows not released,
      at most a given number; a task read with another caller's name is not found
      (FR-020); a context id and a result with a NUL character are kept without it
- [X] T008 [P] Create `tests/integration/test_received_tasks_postgres.py`: every case of
      `tests/test_received_tasks.py` against `PostgresReceivedTaskStore` on
      `FREYJA_TEST_DATABASE_URI`, skipped without it as the other integration tests are; the
      two tables have the columns, types, nullability, keys, and defaults of data-model.md
      (read from `information_schema`), `thread_id` unique, `reject_attempts` "integer, not
      null, default 0", and the three indexes, the one on `released_at` partial "where it
      is null"; a second `setup()` leaves tables that exist alone; two accepts at once
      through two stores on two pools, as two instances, give one task for one new
      conversation and respect a limit of one (the advisory lock)
- [X] T009 [P] Create `tests/test_a2a_server_approval.py` with its module docstring and part
      1, the strict build on threads marked by hand, with the stand-in agent set to run
      without approval and runs started through `ReceivedTasks.accept` or the functions of
      `api/runs.py` (FR-013a, R6): a hand-off proposed on a caller's thread pauses, the
      agent's tools are gated in the strict build's catalog, and the agent has received
      nothing; the same hand-off on a console thread is sent without a pause, as today; a
      hand-off proposed by the subagent on a caller's thread pauses too; approved through the
      resume function of `api/runs.py`, it is sent once; the same holds on the paths that
      `SegmentedGraph` does not run in segments: a run of the resume function that no
      console reader started, and a run of the worker's that reports an agent's outcome and
      comes to a hand-off; on the strict build the check at send time refuses a call whose
      row records no approve or edit, and a call with no row, whatever the agent's record
      says; `begin_run(thread_id)` and
      `begin_read(thread_id)` give the strict build's graph and catalog for a caller's
      thread and the ordinary build's otherwise, and `begin_read`'s fallback gives the
      build's own last one; the ordinary build's `SegmentedGraph` refuses a caller's thread
      and runs nothing, whether or not the thread is armed for a console run; a lookup that
      raises refuses the run on either build; `ainvoke`,
      `invoke`, `astream_events`, and `stream` on a `SegmentedGraph` raise; with the feature
      on, a change of the active profile and of the agent set rebuilds both builds, and with
      it off one orchestrator is built per revision, as today
- [X] T010 [P] Create `tests/test_a2a_server_limits.py` with its module docstring and part 1,
      `CallerFiles` on the strict build (FR-009a, R16), with threads marked by hand for two
      named callers, `alpha` and `beta`, for the local caller, and a console thread, run
      through the start function of `api/runs.py`, and the scripted model calling the file
      tools: a `write_file` of `/notes.txt` on each lands in
      `<files dir>/alpha/notes.txt`, `<files dir>/beta/notes.txt`, and the workspace's
      `notes.txt` (the local caller's and the console's); `ls`, `read_file`, `glob`, `grep`,
      and `edit_file` on `alpha`'s thread see only `alpha`'s files; `/skills/` is listed and
      read there, and a write, an edit, or a delete under it answers with an error and leaves
      the skill as it was; `..`, an absolute path, and a link in the area that points outside
      it reach nothing outside the area, and what the model is given for the link is recorded
      in plan.md; the subagent on `alpha`'s thread writes into `alpha`'s area, and so does a
      run of the worker's on it that reports an agent's outcome; `execute`
      answers with an error on every thread; the backend called under a config without the
      key raises and reads and writes nothing; the skills are loaded for a caller's thread
      through the route (the model's system prompt lists them); `get_config()` gives the
      run's config inside `asyncio.to_thread` and in the pool the sync `glob` uses; the
      strict build's tool names equal a fixed list in the test, so that a tool that comes to
      read the graph's store is noticed
- [X] T011 [P] Create `tests/test_a2a_server_nowhere.py` with its module docstring and part 1,
      the alias (R6), with threads run through the start function of `api/runs.py`: on a
      caller's thread every model call, the subagent's included, is told
      `c-` and the first 16 hexadecimal characters of the SHA-256 of the thread's id, and no
      message any model call receives holds the thread's id; a second runtime on the same
      stores tells the same alias; a console thread's model is told the thread's id, as today
- [X] T012 Create `tests/test_a2a_server_tasks.py` with its module docstring and part 1, the
      feature off with callers' threads (FR-001 and its edge case, R6): an app with the
      feature on makes a caller's thread paused on a `send_notification` proposal, and
      another whose stand-in agent's outcome is ready and not delivered; a second app with
      the feature off on the same stores then answers `POST /threads/{id}/runs`,
      `/runs/unattended`, `/resume`, and `/resume/unattended`, with an approval and with a
      rejection, each with 409 and the code `a2a_server_off`, changing nothing; reads the
      thread's state and lists the thread as before; leaves the second thread alone in a
      round of the worker (no lease taken, the outcome still not delivered); its
      `SegmentedGraph` refuses the thread; the model's call count does not move; and a
      console thread on it runs as today. The same holds for an application with the feature
      off that was started before any conversation existed, while an application with the
      feature on, on the same stores, then accepts a caller's message: a decision on that
      thread through the application that is off is refused with `a2a_server_off`, and its
      worker leaves the thread alone (an instance that is off never runs a thread another
      instance gave a caller)
- [X] T013 Add part 2 to `tests/test_a2a_server_tasks.py`, where a thread stands and ending a
      task (FR-016, SC-014, R7, R9), on an app with the feature on, with tasks accepted through
      `ReceivedTasks.accept` and looks made by the release function or called directly. One
      test per row of R7's table, in its order: a task whose run has not taken the lease is
      working whatever the thread says, also when an earlier reply is the thread's last
      message; a held lease is working; an interrupt is working and waiting for a person, with
      the actions' names; an agent task awaited (working, an outcome ready and not in the
      thread, a run to report it to be tried again) is working and waiting for the agent, and
      one waiting for input whose question was reported is not awaited; this task's note
      `failed` is failed with the code mapped from the note's (`no_usable_model`,
      `model_error`, `step_limit`, and `internal` for any other); this task's note `stopped` is
      canceled with `stopped`; a thread still at `base_checkpoint` with no note of the task's
      is failed with `interrupted`; a thread with a next step is failed with `interrupted`;
      otherwise completed, with Freyja's last reply as the result. The three fences: a look
      between acceptance and the lease writes nothing; a look during which a run takes the
      lease, or the checkpoint moves, writes nothing and the task is looked at again; an
      earlier run's note, older than the task's start or than the thread's last checkpoint, is
      not read; two looks at once write one end. A row `working` with `started_at` null for
      more than a minute on the suite's clock is failed with `interrupted`. A run of the
      worker's on a caller's thread that is cut off, fails, or is given up leaves no task
      working (SC-014). A continuation that fails while its proposal is still pending, as when
      no usable model is active at the resume, leaves the task working and waiting for a person
      (Edge Cases). A task a look ends, completed, failed, or canceled as `stopped`, is
      released in the same write, since a look ends a task only when nothing of its thread is
      in progress, pending, or awaited; a console Stop of a caller's run therefore frees its
      place in the limits at once. Accepting a message first looks at the tasks that still
      count, the caller's and the others' ("for the caller and in all", data-model.md), so a
      task whose end was not yet written (its release function lost) does not count once its
      thread is at rest (data-model.md, "Accepting a message", step 3). The release function
      runs after the lease is given back, for a console run, a run of the routes, and a run of
      the worker; a look that blocks does not delay the release; one that raises is logged and
      the lease is released all the same
- [X] T014 [P] In `tests/test_unattended_runs.py`: `UnattendedRuns.join(thread_id)` gives one
      more reader of a run in progress on this instance, from the events that follow, ending
      with the run's last event; it gives `None` when no run of the thread is in progress
      here; a joined reader that falls `READER_BACKLOG` events behind is disconnected and the
      run goes on. In `tests/test_thread_index.py`: `IndexedRunLocks` calls the function it
      was given with the thread's id after the inner release and after the index was told,
      not after an acquire that failed, and a function that raises is logged and the release
      still returns

### Implementation

- [X] T015 [P] In `src/freyja_agent/config.py`, add `a2a_server: bool = False`,
      `a2a_server_caller_tasks: int = 5`, `a2a_server_tasks: int = 20`, and
      `a2a_server_files_dir: Path = Path("caller-files")`, with the rules of
      contracts/settings.md checked only while `a2a_server` is on, and errors that name the
      variable and no value (T006). In `.env.example`, document each, commented out, with
      what it does as of this request and nothing it will do later
- [X] T016 [P] Create `src/freyja_agent/runtime/received_tasks.py`: the records of a
      conversation and a received task with the fields of data-model.md, the reason codes as
      one fixed set, the `ReceivedTaskStore` protocol, `InMemoryReceivedTaskStore` (one
      `asyncio.Lock` for accepting, the suite's clock), and `PostgresReceivedTaskStore` on the
      shared pool: the two `CREATE TABLE IF NOT EXISTS` statements and the three indexes of
      data-model.md; accepting a message in one transaction under one
      `pg_advisory_xact_lock` with a fixed key, in data-model.md's four steps ("Find the
      conversation", "its last task must not be `working`", "Count the rows with
      `released_at` null", "Write the task's row, `working`, with `started_at` null"); an end
      that names the state it replaces; and the reads T007 lists. The module docstring says
      that the conversation row is what makes a thread a caller's, and that its
      `context_id` "is returned to its caller and appears nowhere else"
- [X] T017 [P] Create `src/freyja_agent/agent/caller_files.py`: `CallerFiles`, a backend of
      deepagents' protocol that reads `configurable["freyja_caller_files"]` from
      `langgraph.config.get_config()` on each call and delegates: the empty string to the
      shared workspace's backend (the same object the ordinary build has); a caller's name
      to a `CompositeBackend` whose default is a `FilesystemBackend` rooted at
      `<files dir>/<name>` in virtual mode, made on first use and kept per name, and whose
      route `/skills/` is the shared skills directory behind a wrapper that reads and answers
      every write, edit, and delete with an error result; no key at all to an error, reading
      and writing nothing (R16). A caller's area is made when a run first writes there and
      is never removed
- [X] T018 [P] In `src/freyja_agent/agent/context.py`, let `ThreadContextMiddleware` be told
      to name the thread by an alias: `c-` and the first 16 hexadecimal characters of the
      SHA-256 of the thread's id; by default it names the thread's id, as today (R6)
- [X] T019 In `src/freyja_agent/agent/factory.py`, give `build_agent` two keyword parameters,
      the file backend (default: the `FilesystemBackend` on `workspace_dir` built today) and
      whether the thread is named by its alias (default: no), passed to the orchestrator and
      to its subagent alike; every existing caller of `build_agent` is unchanged
- [X] T020 [P] In `src/freyja_agent/delegates/a2a_tools.py`, let `agent_tools` be told that
      it builds for the strict build; there, `_still_allowed` refuses a call whose row
      records no approve or edit, and a call with no row, whatever the agent's record says
      (today a call with no row follows the tool), so that the last check
      before a message leaves agrees with the strict build's gate (R6). Everything else of
      the module is unchanged
- [X] T021 [P] In `src/freyja_agent/llm/active.py`, let `ModelManager` be given a second build
      function; when it has one, every rebuild builds the strict orchestrator beside the
      ordinary one around the same model and agent set, and `current` gives both. Without
      one, as with the feature off, it builds what it builds today
- [X] T022 [P] In `src/freyja_agent/runtime/unattended.py`: `SegmentedGraph` is told whether
      it wraps the strict build and is given the lookup of who started a thread; in
      `astream`, before the check of whether the thread is armed, so for every run and not
      only a console run's, it looks the thread up, raises for a caller's thread on the
      ordinary build and for a lookup that fails, and, for a caller's thread on the strict
      build, puts `freyja_caller_files` into the run's config (`""` for `local`, the
      caller's name otherwise); `ainvoke`, `invoke`, `astream_events`, `stream`, and the
      batch entries raise; `astream` and every attribute that only reads are as today. Add
      `UnattendedRuns.join(thread_id)` (R10)
- [X] T023 [P] In `src/freyja_agent/runtime/thread_index.py`, let `IndexedRunLocks` be given a
      function that it calls with the thread's id after a release, once the inner lease is
      back and the index was told; it starts the call as a task of its own so the release is
      never held up, and logs its failure by kind (R9)
- [X] T024 In `src/freyja_agent/runtime/runtime.py`: `_build` takes whether it builds the
      strict orchestrator (every agent tool `gated=True` and built for the strict build,
      `CallerFiles` as the backend, the alias on); `Runtime` holds the store
      (`received_tasks`), set up with the other stores in `assemble` and `open`, Postgres in
      `open` and in memory otherwise; while the feature is on, the model manager gets the
      second build function; `begin_run(thread_id=None)` and `begin_read(thread_id=None)`
      look the thread up and give the strict build's graph and catalog for a caller's
      thread; `agent`, `catalog`, and the segmented wrapper are kept per build, so that
      `last_built` never answers with the other build's; with the feature off, the lookup is
      made all the same, one single-row read per context taken for a thread, so that an
      instance that is off never runs a thread another instance on the same database gave a
      caller (research R6, as the review of these tasks changed it); with it off only one
      orchestrator is built
- [X] T025 Create `src/freyja_agent/api/runs.py`: move the bodies of `start_run` and
      `resume_run` out of `src/freyja_agent/api/threads.py` without a change of behavior, as
      two functions that take the runtime, the thread's id, and the request's values; move
      the function that hands a run's events to `UnattendedRuns` (`_go_on` in
      `src/freyja_agent/api/thread_index.py`) and the body of `stop_run` beside them; the
      routes of both modules call these functions. Add, for a caller's thread while the
      feature is off, a refusal of a message and of every decision with 409 and the code
      `a2a_server_off`, before the lease, with a sentence of the service's own that such a
      task is continued only while receiving tasks from agents is on; and, under the lease
      in the function that starts a run, when `ReceivedTasks` asks for it by a parameter and
      only then, the refusal of a caller's thread with an agent task awaited, which
      `ReceivedTasks.accept` turns into "not now" (R8); the routes never pass the parameter,
      so they answer with no code the contracts do not define
- [X] T026 Create `src/freyja_agent/api/received.py` with `ReceivedTasks`: `accept(caller,
      context_id, text, hops)`, which accepts the message through the store and starts the
      run through `api/runs.py` as a console run, removes the task's row when the run is
      refused, and writes `started_at` and `base_checkpoint` once the run holds the lease,
      and which first looks at the tasks that still count, the caller's and the others'
      (data-model.md step 3);
      the look of R7, with its table, its three fences, and the minute rule; the end,
      written once from a consistent look and released in the same write; and
      the function the lease's release calls while the feature is on, which looks at the
      thread's last task. It imports nothing from `a2a`
- [X] T027 Give the thread's id to every context taken for one thread:
      `take_context` in `src/freyja_agent/api/streaming.py`, `read_state` in
      `src/freyja_agent/api/threads.py`, the three in `src/freyja_agent/api/thread_index.py`,
      and the delivery's look in `src/freyja_agent/delegates/delegation_worker.py`, which
      also leaves a caller's thread alone while the feature is off, as it leaves a paused
      one. The worker's round, `api/tools.py`, and `api/agents.py` take contexts for no
      thread and are unchanged
- [X] T028 In `src/freyja_agent/api/app.py`, the lifespan makes `ReceivedTasks` after the
      runtime is open when the feature is on, and gives its release function to the lease;
      with the feature off it makes nothing of this feature
- [X] T029 [P] Keep a named caller's files out of git and writable in the image: add
      `caller-files/` to `.gitignore`; in `Dockerfile`, make `/app/caller-files` owned by 65532
      as `/app/workspace/cases` is; add a test to `tests/test_container_images.py` that the
      `Dockerfile` makes both directories and gives them to 65532. Build the image (`docker
      build --network host -t freyja-agent .`) and scan it as AGENTS.md gives it
- [X] T030 Run `uv run pytest` and the opt-in Postgres tests against `freyja_test_a2a`: every
      test of this phase passes, and no existing test changed
- [X] T031 [P] In `AGENTS.md`, Layout: the new modules (`runtime/received_tasks.py`,
      `agent/caller_files.py`, `api/runs.py`, `api/received.py`), the changes to
      `SegmentedGraph`, `IndexedRunLocks`, `begin_run`, and `build_agent`, and the new test
      files and `tests/a2a_caller.py`; Conventions: a caller's thread is never run on the
      ordinary build, and why. In `README.md`, Configuration: the settings T015 added, with
      what they do as of this request and nothing more

**Checkpoint**: request 1. Nothing is served; with the feature off the service is what it
was; a thread marked by hand runs only on the strict build.

---

## Phase 3: User Story 1 - An agent hands Freyja a task and reads the result (Priority: P1) 🎯 MVP

**Plan phase 2.** Also the limits of User Story 5 (T038), and three things the plan puts
later, because a caller reaches them from this request on: how a task waiting for a person
looks to its caller, `caller_thread` with the console's missing box (User Story 2), and
another message in a conversation (T041, User Story 4).

**Goal**: the card and the two routes at the service's own address, for the local caller:
send, with and without a stream; reading a task; another message in a conversation; a
message's form and the limits, each refused before a model is asked; the size of a request;
the feature off answering as an unknown path; `caller` on a thread's row, the console saying
who started a task and offering no box on it, and `caller_thread`. A
request with an `Authorization` header is refused until request 5; cancel and subscribe are
answered as operations that are not offered until request 4.

**Independent Test**: quickstart.md section 1: read the card with the stand-in caller, send
with a stream and see the task working, a status for each step, the reply in pieces, and the
task completed with the reply; send without a stream and read the task; the console lists
both, started by an agent on this machine; with the feature off, the card and `POST /a2a`
answer as an unknown path.

### Tests for User Story 1

- [X] T032 [US1] Add part 2 to `tests/test_a2a_server_send.py`, the card (FR-006): read by
      the stand-in caller at the service's own address, it has the name `Freyja`, the
      description of contracts/a2a-server.md verbatim, the service's version, one
      `supportedInterfaces` entry with the address it was asked under and `/a2a`,
      `JSONRPC`, and `1.0`, `streaming` true, `pushNotifications` and `extendedAgentCard`
      false, `text/plain` as the one input and output mode, the one skill of the contract,
      and no security; with an agent, a tool server, a model, and a profile of recognizable
      names configured, none of them is in it; it is the same for every request
- [X] T033 [US1] Add part 3 to `tests/test_a2a_server_send.py`, a send with a stream (FR-017,
      SC-009): the events arrive in the contract's order: the task working with `Working.`;
      `Freyja is using: ls.` and `Completed: ls.`; `artifactUpdate` pieces of the artifact
      `reply` with `append: true`, the first before the reply is complete (add to
      `tests/fakes.py` a scripted model that streams a reply in chunks and can hold between
      two of them, since every fake there only generates whole replies); one
      `artifactUpdate` with the whole reply,
      `append: false` and `lastChunk: true`; the task completed. A subagent's pieces, the
      messages a tools step returns, a call's arguments as they are written, and a
      summarizing call's text are in no event (each made recognizable); an agent's tool is
      named `agent <name>` and the subagent `a subagent`; a step is named within two seconds
      of its update; a message with no `contextId` is answered with one the service made
      (FR-009); a caller that stops reading leaves the run going on, and the task reads as
      completed afterwards. A run that pauses on `send_notification` with a recognizable
      argument (FR-014): the stream's last event so far is the task working with `Waiting for
      a person's approval of: send_notification.`, and no event holds the argument or the
      interrupt's id
- [X] T034 [US1] Add part 4 to `tests/test_a2a_server_send.py`, a send without a stream and
      `GetTask` (FR-011, FR-019): the task is answered completed with its result when it
      ends within `FREYJA_A2A_SERVER_WAIT_SECONDS`; with the limit at 0, and with
      `returnImmediately`, it is answered working and the run goes on to its end; `GetTask`
      answers the task as it stands, its result once it has ended, and no history whatever
      `historyLength` asks; a run with no usable model active is failed with `No usable model
      is active.`, and the thread and the list of threads keep the run's own reason; a model
      error is failed with `The model could not be used.`, and no text of the error is in
      anything the caller received; the step limit gives `The run reached its step limit.`;
      when the conversation row cannot be written, the message is refused with `An internal
      error.` and nothing is started (FR-012); a task paused on a proposal reads as working
      with the waiting sentence and nothing of the proposal's arguments or id. What is read
      from the thread is cleaned before it is sent (R13, FR-018): with a saved agent whose
      reply holds its address and a scripted reply of Freyja's that repeats it, and with a
      model that repeats its profile's recognizable key (`LeakyModel`, `tests/nowhere.py`),
      neither the address nor the key beyond its last four characters is in the whole-reply
      artifact, `GetTask`'s artifact, or any piece
- [X] T035 [US1] Add part 5 to `tests/test_a2a_server_send.py`, the refusals of
      contracts/a2a-server.md that this request makes (FR-007, FR-026a), each refused before
      the model: `ListTasks` with -32004; the four push notification methods, and a push
      configuration in a send, with -32003; `GetExtendedAgentCard` with -32007; a request
      without `A2A-Version: 1.0` with -32009; `CancelTask` and `SubscribeToTask` with -32004
      until request 4; a task id never used, and a console thread's id given as a task id,
      with -32001; a request with an `Authorization` header with 401, `WWW-Authenticate:
      Bearer`, and the contract's body verbatim, until request 5; a body above 256 KiB with
      413, by its `Content-Length` and by a body sent without one that grows past the limit,
      the body not read beyond it; an exception the handler did not expect with -32603 and
      `An internal error.`, logged by its kind with its text cleaned; a `tenant` field
      ignored; the order of the checks this request builds, one test per neighbouring pair of
      the contract's list (a page of another origin with an `Authorization` header gets 403;
      a header with an oversized body gets 401; an oversized body without the version header
      gets 413; then the version, the operation, the message's form, the conversation and the
      task, and the limits, each before the next)
- [X] T036 [US1] Add part 6 to `tests/test_a2a_server_send.py`, the feature off and the routes
      (FR-001, FR-002, SC-002, R3): with the feature off, `GET` and `POST` of both paths, and
      every other method, are answered byte for byte as an unknown path is, and no row and
      no log line about a caller is made; with it on, the routes exist only while the
      lifespan runs, and an application whose lifespan is entered twice serves no route of
      a closed runtime; the helpers of `tests/test_threads_other_origins.py` show `POST /a2a`
      refused with 403 `other_origin` for a request a browser marks as another origin's, and
      those of `tests/test_allowed_hosts.py` show both paths refused under a host name the
      service does not answer for, on an application with the feature on. Where a walker
      meets the SDK's Starlette `Route` as a kind it does not know, it learns it, held to the
      same rule, and the change is named in the request
- [X] T037 [US1] Add part 7 to `tests/test_a2a_server_send.py`: `metadata.reply`
      (contracts/sse-events.md) is on every `message` event of a run on a caller's thread,
      true only for text of an AI chunk of Freyja's own `model` node that is not an internal
      call, and false for the subagent's, a tool's, a call's arguments, and a summarizing
      call; the continuation through `/resume/unattended` has it too; a console thread's
      events, and every event with the feature off, have no such key. A thread's row
      (FR-023, contracts/openapi.yaml): `GET /threads` and `GET /threads/{id}` give a
      caller's conversation `caller` `{"name": "local", "canceled": false, "configured":
      true}` and the caller's first message as `first_message`, a console thread `caller`
      null, and answer 500 as they do for the index when the callers' rows cannot be read,
      never showing a caller's thread as a person's
- [X] T038 [P] [US5] Add part 2 to `tests/test_a2a_server_limits.py`, a message's form and the
      limits (FR-008, FR-025, FR-026a, SC-012), each refused before the model: no text and
      only blank text with -32602 `The message holds no text.`; 32,000 characters accepted
      and 32,001 refused with `The message is longer than 32,000 characters.`; a message
      naming a task with `A message names no task: a task takes one message. Continue a
      conversation by its contextId.`; a `contextId` of 128 characters accepted and of 129
      refused with `The contextId is longer than 128 characters.`; a file part and a data
      part with -32005; `acceptedOutputModes` of `["application/json"]` with -32005, and
      `["text/*"]`, `["*/*"]`, and none accepted; two text parts joined by a line break;
      with `FREYJA_A2A_SERVER_CALLER_TASKS=2`, two tasks waiting for a person count and the
      third is refused with -32004, `A limit on tasks in progress was reached. Send again
      when one has ended.`, and `reason` `task_limit` in the error's data; a task that ended
      frees its place; the log line of the refusal names the caller and which limit. The
      total limit with two callers is shown in T084, once a second caller can exist
- [X] T039 [US1] In `web/__tests__/tasks-testing.ts`, the fixture of a row (`summary`)
      gains `caller: null` unless a test gives one. Create
      `web/__tests__/components/tasks/started-by.test.tsx`: `local` reads "Started by an
      agent on this machine" and a named caller "Started by agent <name>", as text; null
      renders nothing. In `web/__tests__/components/tasks/task-list.test.tsx` and
      `task-view.test.tsx`: a caller's row and page say who started the task; a console
      task's say nothing new
- [X] T040 [US2] Add part 2 to `tests/test_a2a_server_approval.py`, `caller_thread` (FR-024):
      `POST /threads/{id}/runs` and `/runs/unattended` on a caller's thread answer 409
      `caller_thread` with the message of contracts/openapi.yaml verbatim, before the lease
      (the same answer while another run holds it), and add nothing to the thread; a lookup
      that fails answers 500 and starts nothing; the thread's state, a decision, and Stop
      through the service's own routes are unchanged
- [X] T041 [US4] Add part 3 to `tests/test_a2a_server_tasks.py`, conversations (FR-009,
      FR-010, FR-012): a message in a conversation whose last task ended and whose thread is
      at rest makes a new task on the same thread, and the model's input holds the earlier
      messages; a message is refused with `conversation_busy` while the last task works,
      while a run started by a person's decision is in progress, while a proposal is pending,
      and adds nothing to the thread; two
      messages at once under one new conversation id make one thread and one refusal; a
      conversation id the caller never used starts a conversation
- [X] T042 [US2] In `web/__tests__/components/tasks/task-view.test.tsx`: a caller's task
      has no follow-up box and says that only the agent that started it sends messages on
      it; a console task keeps the box (FR-024)

### Implementation for User Story 1

- [X] T043 [P] [US1] In `src/freyja_agent/config.py`, add `a2a_server_wait_seconds: float =
      30`, "0 to 600"; document it in `.env.example`
- [X] T044 [US1] In `src/freyja_agent/api/received.py`: the status sentences and reasons of
      contracts/a2a-server.md and data-model.md as fixed texts; a task's view (state, status
      sentence, result), with every text read from the thread, the result above all,
      cleaned before it is sent of every agent address the scrubber knows (`SCRUBBER`,
      `delegates/addresses.py`) and of stored keys (`redact`, `llm/secrets.py`) (R13); the
      status of a task waiting for a person, naming each action of the interrupt by its
      catalog name, `agent <name>` for an agent's tool, and `a subagent`, with no argument
      and no id; the follower of R13, one loop that reads a console run of the
      thread on this instance through `UnattendedRuns.join` and turns its events into
      changes by the table of R13, reading names only, and otherwise waits to be woken by
      the release function or for `FOLLOW_SECONDS` (4) and looks; the wait of R14; the log
      lines of FR-028 for a task accepted (its id, its thread, the caller) and a message
      refused for a limit (the caller and which limit)
- [X] T045 [US1] Create `src/freyja_agent/api/a2a_server.py`: the card of
      contracts/a2a-server.md, built per request from the address it was asked under and
      served by a route of Freyja's own at `GET /.well-known/agent-card.json` (the SDK's
      `card_modifier` is given the card and not the request); `POST /a2a` from the SDK's
      `create_jsonrpc_routes`; in front of the dispatcher, in the contract's order, the
      refusal of other origins (`refuse_other_origins("Tasks")`), the
      refusal of any `Authorization` header (401, until request 5), and the size limit
      (413, before the body is read whole); a context builder that keeps
      `state["headers"]` and names the caller `local`; `FreyjaRequestHandler`, a
      `RequestHandler` whose `on_message_send`, `on_message_send_stream`, and `on_get_task`
      validate the message's form and call `ReceivedTasks`, whose other methods refuse as
      the contract says, and whose every method ends in one place that turns anything
      unexpected into -32603 with `An internal error.`; the protocol's types made from a
      task's view and from the follower's changes. Needs T003's yes
- [X] T046 [P] [US1] In `src/freyja_agent/api/streaming.py`, put `metadata.reply` on the
      `message` events of a run on the strict build, from the chunk's namespace, node, type,
      and the library's mark of an internal call (R13); every other run's events are
      unchanged
- [X] T047 [US1] In `src/freyja_agent/api/app.py`, the lifespan adds the two routes after the
      runtime is open when the feature is on and removes them when it ends (R3), and logs at
      INFO that the feature is on, how many callers are named (none until request 5), and
      that the outside listener is off
- [X] T048 [US1] In `src/freyja_agent/api/thread_index_schemas.py`, add the caller's
      summary (`name`, `canceled`, `configured`) and `ThreadSummary.caller`, with the
      descriptions of contracts/openapi.yaml; in `src/freyja_agent/api/thread_index.py`,
      fill it from the store in one read for the listed threads, on or off, so that a
      caller's thread is never shown as a person's, and answer 500 when that read fails
- [X] T049 [US1] In `web/lib/tasks.ts`, add `caller` to `ThreadSummary`; create
      `web/components/tasks/started-by.tsx`, who started a task in words; use it in
      `web/components/tasks/task-list.tsx` on a row and in
      `web/components/tasks/task-view.tsx` on the page
- [X] T050 [US2] In `src/freyja_agent/api/threads.py` and
      `src/freyja_agent/api/thread_index.py`, refuse a message to a caller's thread in the two
      message routes with 409 `caller_thread`, before the lease, and not in the function of
      `api/runs.py` that `ReceivedTasks` calls (R20); 500 when the lookup fails
- [X] T051 [US2] In `web/components/tasks/task-view.tsx`, offer no follow-up box on a
      caller's task and say why
- [X] T052 [US1] Create `web/e2e/tasks-callers.spec.ts` with User Story 1's part: a caller's
      task is listed and opened with "Started by an agent on this machine", its page has no
      follow-up box and says why, and a console task is not marked and keeps its box; in
      `web/e2e/tasks-api.ts`, let `TaskService` answer rows with a caller
- [X] T053 [US1] Run `uv run pytest`, `pnpm test`, and `pnpm test:e2e`; no existing test
      changed apart from T039's fixture
- [X] T054 [P] [US1] Create `docs/a2a-server.md`, "Receive tasks from agents": how to turn it
      on, the card and the operation's address, what a caller can do in this request, that a
      send without a stream can be answered with a task that is still working, continuing a
      conversation, how a task waiting for a person looks to its caller, the form of a
      message, the two limits, and that only the caller sends messages on its conversation
      (`caller_thread`) (FR-030, as far as built). List it in the README's Docs after Agents,
      and make the **Next:** link of `docs/agents.md` lead to it and its own lead to
      `docs/web-console.md`, as AGENTS.md's rule for `docs/` has it. The new setting goes
      in the README's Configuration table with the others. In `AGENTS.md`, the stand-in
      caller's command among the Commands and the new modules in Layout; in
      `docs/web-console.md`, Tasks: who started a task and why its page has no box; in
      `web/README.md`, Layout: `components/tasks/started-by.tsx`
- [X] T055 [US1] Walk quickstart.md section 1 and the limit of section 5 on a check instance
      by the standing rules, and correct the sections where the built feature differs

**Checkpoint**: request 2. An agent on the service's machine hands Freyja a task and reads
the result; the console says who started it.

---

## Phase 4: User Story 2 - A person decides what a caller's task proposes (Priority: P2)

**Plan phase 3.**

**Goal**: every operation against a proposal; a person's decision followed by the caller;
that a status and a reason hold no argument, result, or agent's words; the proposal's card
saying who started the run. How waiting looks to a caller, the missing box, and
`caller_thread` came in request 2.

**Independent Test**: quickstart.md section 2: a hand-off to an agent that runs without
approval pauses; the caller sees `Waiting for a person's approval of: agent stub.`; the
console's card says an agent on this machine started the run; approve, and the task
completes; reject, and the agent receives nothing; the caller cannot decide.

### Tests for User Story 2

- [X] T056 [US2] Add part 3 to `tests/test_a2a_server_approval.py`, through A2A (FR-013,
      FR-013a, FR-014, FR-015, SC-003): a message that leads to `send_notification` with a
      recognizable argument ends the stream's first part with `Waiting for a person's
      approval of: send_notification.`, the task working, and the argument in no event;
      `GetTask` says the same; the list counts the task as needing a decision; every
      operation and every field of a message tried against the pending proposal (a send in
      the conversation, refused with `conversation_busy`; a send naming the task; texts and
      metadata that say a person approved; `GetTask`; `ListTasks`; a push method; the
      extended card; cancel and subscribe, not offered yet) leaves the interrupt and its
      actions as they were and runs nothing; approved through the console's route, the
      action runs once with the person's arguments and the caller's open stream receives the
      continuation and the task completed with Freyja's reply; edited, it runs once with the
      edited arguments; rejected, it does not run, and the task completes with Freyja's reply
      to the rejection, with nothing of who decided; with the suite's clock moved three days,
      the task is still working and readable, and continues when decided; a person's Stop
      during a run gives the caller `The run was stopped before it finished.`; a hand-off to
      the stand-in agent set to run without approval pauses through A2A and the agent
      receives nothing until a person approves
- [X] T057 [P] [US2] In `web/__tests__/components/tasks/proposal-card.test.tsx`: the card of a
      caller's task says who started the run and that no person asked for the action

### Implementation for User Story 2

- [X] T058 [US2] In `src/freyja_agent/api/received.py`, a follower whose task waits for a
      person joins the continuation a person's decision starts on this instance, and sends
      its steps, its pieces, and its end; the waiting status itself is T044's
- [X] T059 [P] [US2] In `web/components/tasks/proposal-card.tsx`, say who started the run with
      `started-by.tsx`
- [X] T060 [US2] Add User Story 2's part to `web/e2e/tasks-callers.spec.ts`: a caller's task
      that needs a decision is counted, its card says who started the run, approving and
      rejecting work as for any task, and Stop is offered for a run in progress;
      `web/e2e/tasks-api.ts` answers the paused caller's task
- [X] T061 [US2] Run `uv run pytest`, `pnpm test`, and `pnpm test:e2e`. In
      `docs/a2a-server.md`, how an approval looks to a caller, where a person decides it,
      that every message to a delegate agent from a caller's thread waits for a person; in
      `docs/web-console.md`, Tasks: what the proposal's card says of a caller's task
- [X] T062 [US2] Walk quickstart.md section 2 on a check instance by the standing rules, and
      correct it where the built feature differs

**Checkpoint**: request 3. A caller's task that proposes an action waits for a person, and
only a person decides.

---

## Phase 5: User Story 4 - Continue a conversation, come back to a task, or cancel it (Priority: P4)

**Plan phase 4.** Built before User Story 3 on purpose: taking a named caller away (request
5) cancels its tasks, which needs cancel and the rejection by rule.

**Goal**: subscribing; a task read after a restart and through another instance; cancel;
rejecting by rule and `requester_gone`; the sweep. Another message in a conversation came in
request 2.

**Independent Test**: quickstart.md section 4: a follow-up remembers the first exchange; a
task waiting for an agent is found and completes after a restart; a cancel during a run stops
it between two steps; a cancel of a task waiting for a person rejects its proposal, and the
console no longer counts it.

### Tests for User Story 4

- [X] T063 [US4] Add part 4 to `tests/test_a2a_server_tasks.py`, following (FR-017):
      `SubscribeToTask` gives the task as it stands and every change to its end; on a task that
      ended, -32004; a fifth stream of one task on one instance is refused with `stream_limit`
      and its sentence, and a fourth is not; a follower through a second instance on the same
      stores receives each change of state, of the status, and the result, and no pieces; a
      follower that falls `READER_BACKLOG` events behind is disconnected and the run goes on
- [X] T064 [US4] Add part 5 to `tests/test_a2a_server_tasks.py`, restarts (FR-012, SC-010): a
      second application on the same stores, and a runtime opened again on them, read a
      task with the same id, state, and result, and subscribe to and cancel one that works;
      a caller's run in progress when `UnattendedRuns.close` runs ends canceled with `The run
      was stopped before it finished.`; nothing runs by itself afterwards; a follow-up
      continues with the stopped run's work in mind
- [X] T065 [US4] Add part 6 to `tests/test_a2a_server_tasks.py`, a cancel of a run (FR-021,
      SC-011), as `tests/test_unattended_stop.py` stops runs: during a model call, a tool
      step, and an approved action, the step completes, the run stops before the next one,
      the thread keeps what was done, and the cancel is answered with the task canceled with
      `The caller canceled the task.`; the task stays canceled when the run ended by itself
      before the stop took effect; a cancel of a task that ended answers -32002, and of an
      id never used -32001; each accepted cancel is logged with the caller and the task's id
- [X] T066 [US4] Add part 7 to `tests/test_a2a_server_tasks.py`, a cancel and a proposal
      (FR-021, FR-022, FR-022a, R11): a pending proposal of Freyja's is rejected by rule, every
      action with `The requesting agent canceled the task.`, none runs, the rejecting run stops
      at its first boundary without asking the model, and the list no longer counts the task; a
      subagent's proposal is rejected, the subagent's step completes, a proposal it comes to in
      that step is rejected too, and the run stops at Freyja's next boundary; a cancel during a
      step whose reply proposes an action leaves that proposal rejected; a task waiting for a
      delegate agent is canceled, the agent task goes on, its outcome is recorded, a message in
      its conversation is refused with `conversation_busy` while the agent task is awaited, and
      a proposal the worker's run comes to is rejected by rule; a proposal with a person's
      decision already taken is neither rejected nor resumed, and the same decisions sent again
      continue the thread; `requester_gone` (contracts/openapi.yaml, verbatim) answers an
      approval or an edit through `/resume` and `/resume/unattended` while the conversation has
      no task that works, and a rejection is accepted; a cancel that arrives before a person's
      continuation is marked, and one after, stop it at its first boundary; a cancel and a
      decision at once, in both orders, have exactly one effect; a rejection by rule that fails
      (no usable model, no context) is tried again no sooner than 30 seconds later and at most
      five times, after which nobody can approve and a person can reject; a decision sent
      through `/threads/{id}/resume`, the route whose run ends when its client stops reading,
      is applied as today, a cancel that follows cannot stop that run, and the task is
      canceled all the same, with what the run does left in the thread (Edge Cases)
- [X] T067 [US4] Add part 8 to `tests/test_a2a_server_tasks.py`, releases and the sweep (R9):
      a canceled task is released once its thread has no run in progress and no agent task
      awaited, and a pending proposal does not keep it counting; the sweep, run on the
      suite's clock every 30 seconds, finds a canceled task whose release function was lost
      and rejects its proposal, and looks at no more rows than the total limit;
      `tests/test_unattended_stop.py` passes unchanged
- [X] T068 [P] [US4] In `tests/test_unattended_runs.py`: a run started with `stop_first=True`
      stops at its first boundary; `should_stop` asks whether a caller's last task was
      canceled, for a caller's thread only, and a console run on such a thread stops at its
      next boundary whether or not the index was marked
- [X] T069 [P] [US4] In `web/__tests__/components/tasks/task-view.test.tsx`: a task whose
      `caller.canceled` is true says that the requesting agent canceled, and its rejected
      proposal is said to be rejected by that cancel and not by a person. In
      `web/__tests__/components/tasks/proposal-card.test.tsx`: an approval answered with
      `requester_gone` is said in the console's words, and rejecting is still offered

### Implementation for User Story 4

- [X] T070 [US4] In `src/freyja_agent/runtime/unattended.py`: `UnattendedRuns.start(...,
      stop_first=False)`, a run that stops at its first boundary; `should_stop` also asks a
      function it is given whether a caller's thread's last task was canceled (R10)
- [X] T071 [US4] In `src/freyja_agent/api/runs.py`, under the lease in the resume function,
      refuse with 409 `requester_gone` an approval or an edit on a caller's thread whose
      conversation has no task that works, unless every decision is a rejection or the
      request repeats the decisions already taken (R11)
- [X] T072 [US4] In `src/freyja_agent/api/received.py`: `cancel`, which writes the task
      canceled first and then acts on the thread as R11 says; the rejection by rule, a resume
      rejecting every action with the fixed reason, started as a console run with `stop_first`,
      counted and spaced in the task's row; R11 applied at every look of a thread with a
      proposal and no task that works; the release of a canceled task; subscribing, with at
      most four followers of a task on this instance; the sweep, every 30 seconds, over the
      rows not released
- [X] T073 [US4] In `src/freyja_agent/api/a2a_server.py`, serve `on_cancel_task` and
      `on_subscribe_to_task`, and refuse them as the contract says for a task that ended
- [X] T074 [US4] In `src/freyja_agent/api/app.py`, start the sweep after the runtime is open
      when the feature is on, and stop it before the runtime is closed
- [X] T075 [P] [US4] In `src/freyja_agent/api/thread_index.py`, `caller.canceled` is true
      while the conversation's last task is one its caller canceled
- [X] T076 [P] [US4] In `web/components/tasks/task-view.tsx` and
      `web/components/tasks/proposal-card.tsx`, say that the requesting agent canceled, that
      a proposal was rejected by that cancel, and `requester_gone` in the console's words
- [X] T077 [US4] Add User Story 4's part to `web/e2e/tasks-callers.spec.ts`: a canceled
      caller's task is not counted among those that need a decision and says the agent
      canceled; an approval refused with `requester_gone` is said so; `web/e2e/tasks-api.ts`
      answers both
- [X] T078 [US4] Run `uv run pytest`, `pnpm test`, and `pnpm test:e2e`. In
      `docs/a2a-server.md`, subscribing, reading a task after a restart, and cancel, with
      what it does to a run and to a proposal; in `docs/web-console.md`, Tasks: what a
      canceled caller's task shows
- [X] T079 [US4] Walk quickstart.md section 4 on a check instance by the standing rules, and
      correct it where the built feature differs

**Checkpoint**: request 4. A caller continues, comes back to, and cancels its tasks; nobody
can approve what a canceled task proposed.

---

## Phase 6: User Story 3 - An agent on another host calls with its token (Priority: P3)

**Plan phase 5.** Also the isolation of User Story 5 (T084), proven in the same request as
what opens it.

**Goal**: named callers and their tokens at both addresses; the outside listener; the card
at each address; tokens out of messages and log records; the tasks of a caller that is no
longer named, canceled at startup; and the proofs of no token anywhere, of one named caller
against another's tasks, and of a named caller's files.

**Independent Test**: quickstart.md section 3: with two named callers and the outside
listener, the card is read there without a token, a send with the first token completes, a
send without one or with a wrong one is refused, the threads route answers 404 there, the
second caller cannot read the first's task, a named caller sees only its own files, and
removing a caller cancels its task.

### Tests for User Story 3

- [X] T080 [P] [US3] Add part 2 to `tests/test_a2a_server_callers.py`, the settings of this
      request (FR-003, FR-004, FR-029, contracts/settings.md), one test per rule:
      `FREYJA_A2A_SERVER_CALLERS` is a JSON object of names, each with `token` and nothing
      else; a name of "lowercase letters, digits, hyphens, and underscores, starting with a
      letter, at most 32 characters; not `local`"; a token of "16 to 4,096 characters, each
      a visible ASCII character (no space, no control character)"; two callers with one
      token; more than 20; a caller named as a configured delegate agent is accepted and
      neither name gives anything of the other (Edge Cases); `FREYJA_A2A_SERVER_HOST` without
      `_PORT` and the other way round;
      the outside listener set while the feature is off, while no caller is named, and
      while `FREYJA_A2A_SERVER_URL` is unset; a URL that is not `http` or `https` with a
      host and at most a port; a port outside 1 to 65535, or the service's own on the same
      address; each error names the variable and the caller and holds no token and no value;
      a token neither prints nor serializes
- [X] T081 [US3] Add part 3 to `tests/test_a2a_server_callers.py`, the credential (FR-002,
      FR-005), at both addresses: no header, a wrong token, another scheme, and a token in
      the body, a query, or another header are answered alike at the outside listener with
      401, `WWW-Authenticate: Bearer`, and the contract's body verbatim, make nothing, and
      leave the body unread; at the service's own address a request without the header is
      the local caller's, one with a named caller's token is that caller's, and one with a
      token no caller has is the same 401; a `tenant` naming another caller is ignored; the
      presented token is compared with every caller's by `hmac.compare_digest`, without
      stopping at a match; the order of the checks with a named caller's token, at both
      addresses, one test per neighbouring pair of the contract's list that T035 could not
      show without one (a wrong token from a page of another origin gets 403; a wrong token
      with an oversized body gets 401; a right token with an oversized body gets 413)
- [X] T082 [US3] Add part 4 to `tests/test_a2a_server_callers.py`, the outside listener
      (FR-003, FR-006, FR-029, SC-005): every route of the main application, `/healthz`
      included, asked at the outside application, answers as an unknown path; the card there
      gives `FREYJA_A2A_SERVER_URL` and `/a2a`, declares one HTTP `bearer` scheme and
      requires it, and is given without a token; a request a browser marks as another
      origin's is refused there; one test binds a free loopback port, reads the card and
      completes a send through the socket, and stops with the service's shutdown also while
      a stream is open; a taken port stops startup with an error that names the setting; the
      outside server writes no access log line; startup logs at INFO that the listener is on
      and where, and warns for an `http` address for callers and not for `https`
- [X] T083 [US3] Add part 5 to `tests/test_a2a_server_callers.py`, callers that change
      (FR-021, Edge Cases): a named caller's task waiting for a person, then a second
      application on the same stores without that caller: its first sweep cancels the task,
      rejects the proposal by rule, and logs the caller and why; the row says `configured:
      false` and `canceled: true`; the caller's token is refused; named again, the caller has
      its conversations and its files again; a replaced token gives the same conversations
      and the old one is refused
- [X] T084 [P] [US5] Add part 3 to `tests/test_a2a_server_limits.py`, isolation (FR-009a,
      FR-020, SC-007): as one named caller, every operation on the other's task id, and on a
      console thread's id, answers in every byte but the request's id as for an id never
      used; a message under the other's `contextId` starts a conversation of the sender's
      own; through A2A, two named callers and a console task each write a file of
      recognizable name and text, and each caller's runs, Freyja's and its subagent's, list,
      read, write, and delete only in their own area, read the skills, change none, and get
      an error from the shell; the total limit with two callers refuses with the same
      sentence and `task_limit`, and its log line says which limit
- [X] T085 [P] [US3] Add part 2 to `tests/test_a2a_server_nowhere.py`, no token anywhere
      (FR-028, SC-006), with the two recognizable tokens: through every operation and every
      refusal at both addresses, and a token pasted into a message, which is cleaned before
      it is used, no answer, stream, thread state, checkpoint (`kept`), or log record at any
      level of any logger (`logged`) holds either token; a record of the `uvicorn.access`
      logger made as uvicorn makes one, with a path whose query holds a token, is cleaned by
      the installed record factory (the test client writes no access line itself); one line
      each for a task accepted, a cancel, a refused credential, and a refused limit, naming
      the caller; a hundred refused credentials at one of the two addresses give one
      warning, and one more with their count once ten seconds passed on the suite's clock,
      counted for the service's own address and for the outside listener apart, so that the
      count is bounded whoever sends them (FR-028: "for each address" is read as each of the
      service's two addresses, as the spec uses the word)
- [X] T086 [P] [US3] In `web/__tests__/components/tasks/started-by.test.tsx`: a caller whose
      `configured` is false is said to be no longer configured

### Implementation for User Story 3

- [X] T087 [P] [US3] In `src/freyja_agent/config.py`, add `A2aCallerConfig` (a `token` held as
      `SecretStr`) and `a2a_server_callers`, `a2a_server_host`, `a2a_server_port`, and
      `a2a_server_url`, with the rules of T080 and errors that name the variable and the
      caller only; document the four in `.env.example` with the example of
      contracts/settings.md
- [X] T088 [P] [US3] Create `src/freyja_agent/runtime/callers.py`: `Callers`, built from the
      settings, which turns an `Authorization` header into a caller's name, `local` for no
      header, or a refusal, comparing the bytes of the presented token with every caller's
      by `hmac.compare_digest`; taking every named caller's token, by its exact text, out of
      a text and out of every log record, installed with the record factory of
      `install_log_scrubber`; and the limiter of refused-credential warnings, one and then at
      most one in ten seconds for each of the two addresses, with the count (R5, R15)
- [X] T089 [US3] In `src/freyja_agent/api/a2a_server.py`, take the caller from `Callers` at
      both addresses in place of request 2's refusal of every header; build the card per
      address, the outside one from `FREYJA_A2A_SERVER_URL` with its bearer scheme; clean a
      caller's message of tokens before it is used; pass the caller's name in the call
      context
- [X] T090 [US3] Create `src/freyja_agent/api/a2a_listener.py`: the outside application, built
      with `outside=True`, the two routes and nothing else (a FastAPI application with
      `docs_url`, `redoc_url`, and `openapi_url` set to `None`, so that
      `refuse_other_origins("Tasks")` answers 403 with its `detail` body as on the service's
      own address), other origins refused, and no `AllowedHosts`; a `uvicorn.Server` whose
      `capture_signals` does nothing, with `lifespan="off"`, `log_config=None`, the access log
      off, and `timeout_graceful_shutdown`; the socket bound by the lifespan and passed to
      `serve`, with an error naming the setting when it cannot be (R4). In `tests/conftest.py`,
      `make_a2a_server_app` gives the outside application when the listener is set
- [X] T091 [US3] In `src/freyja_agent/api/app.py`, start the outside listener after the routes
      are added, stop it before the runtime is closed, and log the startup lines and the
      warning of FR-029
- [X] T092 [US3] In `src/freyja_agent/api/received.py`, scope every task and conversation by
      caller (FR-020), and let the sweep's first round cancel every working task of a named
      caller the settings no longer name, as that caller's cancel would, logging the caller
      and why
- [X] T093 [P] [US3] In `src/freyja_agent/api/thread_index.py`, `caller.configured` is whether
      the settings name the caller now, always true for `local`
- [X] T094 [P] [US3] In `web/components/tasks/started-by.tsx`, say when the caller is no
      longer configured
- [X] T095 [US3] Add User Story 3's part to `web/e2e/tasks-callers.spec.ts`: a task started by
      agent `artex`, and one whose caller is no longer configured; `web/e2e/tasks-api.ts`
      answers both
- [X] T096 [US3] Run `uv run pytest`, `pnpm test`, and `pnpm test:e2e`. In
      `docs/a2a-server.md`, how to name a caller, how to open the outside listener and what
      it serves, the token at both addresses, the warning, what encrypts the connection, a
      named caller's file area, and what removing or renaming a caller does; in the README's
      Configuration table, the four settings T087 adds; in `AGENTS.md`, `runtime/callers.py` and
      `api/a2a_listener.py`, and a convention that a caller's token appears nowhere,
      searched by `tests/test_a2a_server_nowhere.py`, to which a new operation or log line
      about callers is added
- [X] T097 [US3] Walk quickstart.md section 3 on a check instance by the standing rules, with
      the outside listener on 8443, and correct it where the built feature differs

**Checkpoint**: request 5. Agents on other hosts call with their tokens, at an address that
serves nothing else; one caller reaches nothing of another's.

---

## Phase 7: User Story 5 - A caller sees only what is its own, and no more than it should (Priority: P5)

**Plan phase 6.** The rest of User Story 5; its limits came in request 2 and its isolation in
request 5.

**Goal**: the hand-over count on messages in and out, and the proof of what a caller is never
given, over every operation and every kind of task the requests before made possible.

**Independent Test**: quickstart.md section 5: a message with a count at the limit is
refused; a hand-off approved from a caller's thread carries the count plus one; and the
automated proof of FR-018 and FR-019 passes.

### Tests for User Story 5

- [X] T098 [P] [US5] Add part 4 to `tests/test_a2a_server_limits.py`, the hand-over count
      (FR-027, R17), each refusal before the model: no count is kept as 0 and 2 as 2; -1,
      1.5, a text, a boolean, and a list are refused with -32602 `The hand-over count is not
      a whole number of zero or more.`; 3, at `FREYJA_A2A_SERVER_MAX_HOPS`' default, is
      refused with `The message has passed through too many agents.`; `MAX_HOPS` is "1 to
      16"; every message to the stand-in agent from a caller's thread, a hand-off, a
      follow-up, and an answer, carries `freyja.hops` one more than the conversation's last
      message from its caller, also after a follow-up with another count; a cancel carries
      none, since it is a request about a task and starts no work; a message from a console
      thread carries none; the message id of a message from a caller's thread holds the
      thread's alias and not its id
- [X] T099 [P] [US5] Add part 3 to `tests/test_a2a_server_nowhere.py`, what a caller is never
      given (FR-018, FR-019, SC-008), with two named callers, an LLM profile with a
      recognizable key (`LeakyModel` of `tests/nowhere.py`), the stand-in agent at a
      recognizable address, a tool whose arguments and result hold a recognizable text, an
      agent reply that holds one, and files of recognizable names: every operation, as each
      caller, refusals included, through tasks that complete, pause, fail with a model error
      that holds a recognizable text, and are canceled; everything either caller received
      holds no token, no key beyond its last four characters, no address, no thread's id
      (also after asking Freyja for its thread's id), no proposal's id, and not the other
      caller's name; no status sentence and no reason holds an argument, a result, or an
      agent's words; no reason holds the model error's text
- [X] T100 [P] [US5] In `tests/a2a_stub.py`, keep the hand-over count each message carried in
      the stand-in agent's record and, served by hand, print it with each message, as
      quickstart.md section 5 reads it; document it in the module docstring

### Implementation for User Story 5

- [X] T101 [P] [US5] In `src/freyja_agent/config.py`, add `a2a_server_max_hops: int = 3`, "1
      to 16"; document it in `.env.example`
- [X] T102 [US5] In `src/freyja_agent/api/a2a_server.py`, read `metadata["freyja.hops"]` (a
      protobuf `Struct` holds numbers as doubles), refuse the forms and the limit of T098
      with their sentences, and pass the count to `ReceivedTasks.accept`
- [X] T103 [US5] In `src/freyja_agent/runtime/unattended.py`, `SegmentedGraph` puts the count
      of the conversation's last message from its caller into the run's config of a
      caller's thread, beside the files key (R17)
- [X] T104 [US5] In `src/freyja_agent/delegates/a2a_tools.py`, every message an agent's tool
      sends from a run whose config carries the count has `metadata["freyja.hops"]` set to
      the count plus one, and the message ids of messages and cancels from such a run name
      the thread by its alias (R6) where they name it by its id today, made by the same
      function wherever the send ledger makes them again; messages from every other thread
      are sent as today
- [X] T105 [US5] Run `uv run pytest`. In `docs/a2a-server.md`, the hand-over count's name,
      its limit, and that it is a partial guard (FR-030); in the README's Configuration
      table, `FREYJA_A2A_SERVER_MAX_HOPS`
- [X] T106 [US5] Walk the hand-over count of quickstart.md section 5 on a check instance by the
      standing rules, and correct it where the built feature differs

**Checkpoint**: request 6. Every user story is built.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Plan phase 7.**

- [X] T107 Walk the whole of quickstart.md on a check instance by the standing rules, in its
      order, and correct it where the built feature differs; record in plan.md anything the
      walk found that no test covers
- [X] T108 [P] Read `README.md`, `docs/a2a-server.md`, `docs/web-console.md`, `AGENTS.md`,
      and `web/README.md` against the code: they describe only what exists, name every file
      this feature added, and cover every point of FR-030; every setting is in `.env.example`
      and in the README's Configuration table
- [X] T109 [P] Compare the two coverage numbers with T001's. Raise `fail_under` in
      `pyproject.toml` or a threshold in `web/vitest.config.mts` only with the owner's word;
      say in the request what the opt-in Postgres tests, which CI does not run, leave out of
      the service's number
- [X] T110 Run every gate once more on the finished branch: the commit hook over all files
      (`uv run pre-commit run --all-files`, touching nothing while it runs), `pnpm test:e2e`
      in both browsers, the opt-in Postgres tests against `freyja_test_a2a`, Semgrep, Trivy
      over the source, and the service's image built with `--network host` and scanned
- [X] T111 [P] Record in plan.md what was built differently from the plan, with the reason

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies. T003 waits for the owner; only T045 needs its
  answer.
- **Foundational (Phase 2)**: depends on Phase 1. Blocks every story.
- **User Story 1 (Phase 3)**: depends on Phase 2. The MVP, with what a caller reaches from
  this request on: the waiting status, `caller_thread` and the missing box, and follow-ups.
- **User Story 2 (Phase 4)**: depends on Phase 3 (a caller's message, the follower, the
  card).
- **User Story 4 (Phase 5)**: depends on Phase 4 (a paused caller's task to cancel, the
  console's card it changes).
- **User Story 3 (Phase 6)**: depends on Phase 5: a caller that is no longer named has its
  tasks canceled, which needs cancel and the rejection by rule (plan, "Why this order").
- **User Story 5, the rest (Phase 7)**: depends on Phase 6 for the proof's two named callers.
- **Polish (Phase 8)**: depends on all.

The requests are merged in order, each before the next branch is made from `main`, since
requests are squash-merged and their branches deleted.

### Within Each Phase

- Tests first; they fail before the implementation starts. T004 and T005 come before the
  other tests of Phase 2, which use them.
- `tests/test_a2a_server_send.py` grows in T005 and T032 to T037;
  `tests/test_a2a_server_tasks.py` in T012, T013, T041, and T063 to T067;
  `tests/test_a2a_server_approval.py` in T009, T040, and T056;
  `tests/test_a2a_server_limits.py` in T010, T038, T084, and T098;
  `tests/test_a2a_server_nowhere.py` in T011, T085, and T099;
  `tests/test_a2a_server_callers.py` in T006 and T080 to T083;
  `web/__tests__/components/tasks/task-view.test.tsx` in T039, T042, and T069.
- `src/freyja_agent/api/received.py` is changed in T026, T044, T058, T072, and T092;
  `api/a2a_server.py` in T045, T073, T089, and T102; `runtime/unattended.py` in T022, T070,
  and T103; `api/runs.py` in T025 and T071; `api/app.py` in T028, T047, T074, and T091;
  `api/thread_index.py` in T025, T027, T048, T050, T075, and T093;
  `delegates/a2a_tools.py` in T020 and T104; `config.py` in T015, T043, T087, and T101;
  `web/components/tasks/task-view.tsx` in T049, T051, and T076.
- Phase 2: T019 follows T017 and T018; T024 follows T016 and T019 to T023; T025 follows
  T024; T026 follows T016 and T025; T027 follows T024; T028 follows T026.
- Phase 3: T044 follows T043; T045 follows T044 and T003; T047 follows T045; T050 follows
  T048; T051 follows T049; T052 follows T051.
- Phases 4 to 7: the service's implementation tasks run in their listed order; the web tasks
  marked `[P]` run beside them; the end-to-end task follows the web code it drives.

### Parallel Opportunities

- Phase 1: T002 beside T001.
- Phase 2: T005 to T011 and T014 together once T004 is done; T015 to T018 and T020 to T023
  together; T029 and T031 beside the rest.
- Phase 3: T038 beside T032 to T037, T040, and T041; T043 and T046 beside T044 and T045; T054
  beside T053.
- Phase 4: T057 beside T056; T059 beside T058.
- Phase 5: T068 and T069 beside T063 to T067; T075 and T076 beside T070 to T074.
- Phase 6: T080, T084, T085, and T086 together; T087 and T088 together; T093 and T094 beside
  T089 to T092.
- Phase 7: T098, T099, and T100 together; T101 beside them.
- Phase 8: T108, T109, and T111 together.

---

## Parallel Example: Phase 2

```text
# After T004 (the fixture), the tests, each in a file of its own:
T005 tests/a2a_caller.py and tests/test_a2a_server_send.py
T006 tests/test_a2a_server_callers.py
T007 tests/test_received_tasks.py
T008 tests/integration/test_received_tasks_postgres.py
T009 tests/test_a2a_server_approval.py
T010 tests/test_a2a_server_limits.py
T011 tests/test_a2a_server_nowhere.py
T014 tests/test_unattended_runs.py and tests/test_thread_index.py

# Then the modules that depend on nothing new:
T015 src/freyja_agent/config.py
T016 src/freyja_agent/runtime/received_tasks.py
T017 src/freyja_agent/agent/caller_files.py
T018 src/freyja_agent/agent/context.py
T020 src/freyja_agent/delegates/a2a_tools.py
T021 src/freyja_agent/llm/active.py
T022 src/freyja_agent/runtime/unattended.py
T023 src/freyja_agent/runtime/thread_index.py
```

## Parallel Example: User Story 1

```text
# Beside the tests in tests/test_a2a_server_send.py (T032 to T037):
T038 tests/test_a2a_server_limits.py
T040 tests/test_a2a_server_approval.py
T041 tests/test_a2a_server_tasks.py
T039 then T042 web/__tests__/

# Beside the service's code (T044, T045):
T043 src/freyja_agent/config.py
T046 src/freyja_agent/api/streaming.py
T048 then T050 src/freyja_agent/api/thread_index_schemas.py, thread_index.py, threads.py
T049 then T051 web/lib/tasks.ts and web/components/tasks/
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. Phase 1, then Phase 2 (request 1): the foundations, with nothing served.
2. Phase 3 (request 2): an agent on the service's machine hands Freyja a task, continues the
   conversation, and reads the result, within the limits; a task waiting for a person says
   so; only the caller adds messages to its conversation.
3. **Stop and validate**: quickstart.md section 1 and the limit of section 5. A deployment
   can turn the feature on for agents on its own machine. Every hand-off from a caller's
   thread waits for a person from request 1 on, since the strict build exists before
   anything is served (since feature 010, while the agents' approval mode or the agent is
   Manual); what request 3 adds is every operation tried against a proposal and
   the proposal's card in the console.

### Incremental Delivery

1. Request 1: the foundations.
2. Request 2: User Story 1, the limits, and what a caller reaches from there (the MVP).
3. Request 3: User Story 2, approval as a caller and the console see it.
4. Request 4: User Story 4, following, restarts, and cancel.
5. Request 5: User Story 3 and the isolation of User Story 5, opened and proven together.
6. Request 6: the rest of User Story 5.
7. Request 7: the walk-through and the documents, then `/speckit-converge`.

A deployment that turns the feature on after any request gets what that request's row says
and nothing less safe, apart from the two windows named under Organization.

### Parallel Team Strategy

Within a request, one person can take the web tasks while another takes the service's. The
requests themselves are built one after another: each depends on the one before.

---

## Notes

- 111 tasks: 3 of setup, 28 for the foundation, 18 for User Story 1, 11 for User Story 2 (4
  in request 2, 7 in request 3), 18 for User Story 4 (1 in request 2, 17 in request 4), 17
  for User Story 3, 11 for User Story 5 (1 in request 2, 1 in request 5, 9 in request 6),
  and 5 of polish.
- `[P]` tasks touch different files and depend on nothing unfinished. A task that shares a
  file with another of its phase carries no `[P]`.
- A test task lists the cases it must hold; a case that cannot be written as listed is
  recorded in plan.md with the reason, not dropped.
- Commit after each task or each small group of tasks; the commit hook runs the suites.
- Stop at any checkpoint to validate the phase by hand before its request.

## Phase 9: Convergence

- [X] T112 Stop a follower that reads a run from sending a working status or a piece of the reply once its task has ended, as after a cancel, so that the next thing its stream sends is the task canceled, and test a stream followed through a cancel during a step, in `src/freyja_agent/api/received.py` (`follow`) and `tests/test_a2a_server_tasks.py`, per FR-016, FR-017 (partial; MEDIUM)
- [X] T113 Reword `specs/009-a2a-server/spec.md` to FR-013a as amended by feature 010, where five lines still say every message to a delegate agent from a caller's thread pauses: User Story 2's scenario 3, FR-031's test of an agent that runs without approval, SC-003, the assumption on what a caller can cause with no person involved, and the line on feature 005's FR-022 under "Earlier requirements this feature changes", per FR-013a (contradicts; MEDIUM)
- [ ] T114 Try a cancel of a removed caller's task that failed again in a later round of the sweep, not only a failed read of the list, in `src/freyja_agent/api/received.py` (`sweep`, `_cancel_unnamed`), per FR-021 (partial; LOW)
- [ ] T115 Count only the attempts to reject a proposal by rule that failed toward `REJECT_ATTEMPTS`, so that a sixth proposal after a cancel is still rejected by rule, in `src/freyja_agent/api/received.py` and `src/freyja_agent/runtime/received_tasks.py`, per FR-022a (partial; LOW)
- [ ] T116 Settle with the owner what FR-022a asks of a proposal that cannot be rejected by rule because the agents' approval mode turned Auto after it was proposed: try again once it can be, without using up the attempts, or amend FR-022a, per FR-022a (partial; LOW)
- [ ] T117 Offer a proposal's decisions on a task's page only once the task's row has been read, so that a caller's proposal is never shown without who started it, in `web/components/tasks/task-view.tsx`, per FR-023, SC-004 (partial; LOW)
- [ ] T118 Label a calling agent's message on a caller's thread with the caller, not "You", once the owner has decided it, in `web/components/tasks/message.tsx`, per FR-023 (partial; LOW)
- [ ] T119 Reword the module docstring of `src/freyja_agent/runtime/runtime.py`, which still says every message to a delegate agent waits on the strict build, to FR-013a as amended, per FR-013a (contradicts; LOW)
- [ ] T120 Report the count of refused credentials whose ten seconds closed with no later refusal, in `src/freyja_agent/runtime/callers.py` (`refused`), per FR-028 (partial; LOW)
- [ ] T121 With the feature off, ignore a value of its other settings that does not parse, as the callers' value is ignored, or amend FR-001 and `contracts/settings.md`, in `src/freyja_agent/config.py`, per FR-001 (contradicts; LOW)
- [ ] T122 Name the entry by its place when `FREYJA_A2A_SERVER_CALLERS` names a caller twice or an entry has the wrong shape, in `src/freyja_agent/config.py` (`_callers_read`), per FR-004, US3/AC7 (partial; LOW)
- [ ] T123 Read the service's version for the card once at startup, not on the event loop in the card's first request, in `src/freyja_agent/api/a2a_server.py` (`_version`), per Constitution V (contradicts; LOW)
- [ ] T124 Ask the outside listener also for the main application's documentation routes (`/docs`, `/redoc`, `/openapi.json`) in the test of what it serves, in `tests/test_a2a_server_callers.py` (`_paths`), per SC-005 (partial; LOW)
- [ ] T125 Start the held tail of the reply's pieces afresh at each new message, so that a piece never joins text of two model steps, in `src/freyja_agent/api/received.py` (`follow`), per FR-017 (partial; LOW)
- [ ] T126 Test that a task handed to an agent completes with the reply of the run that reports the agent's outcome, and fails as interrupted when that run fails and is given up, driving the delegation worker, in `tests/test_a2a_server_tasks.py`, per SC-014 (partial; LOW)
- [ ] T127 Say in FR-018, SC-007, and `docs/a2a-server.md` that the local caller works in the shared workspace, console tasks' case folders included, per FR-018, SC-007 (contradicts; LOW)
- [ ] T128 Log a message refused at the hand-over limit or for its length, naming the caller and never the text, in `src/freyja_agent/api/a2a_server.py`, per FR-028 (partial; LOW)

## Phase 10: Convergence

- [X] T129 Stop reading a run for a follower only once the task's caller has canceled it, not on any end of the task, so that a follower behind its run when the task completes still sends every step and piece the run left, and test it, in `src/freyja_agent/api/received.py` (`follow`, `_canceled_now`) and `tests/test_a2a_server_tasks.py`, per FR-017 (contradicts; MEDIUM)
- [X] T130 Reword the contract's sentence on pieces that can still be sent after a cancel as a cancel sent to another instance, not a stream through one, in `specs/009-a2a-server/contracts/a2a-server.md`, per FR-017 (contradicts; LOW)
- [X] T131 Reword to FR-013a as amended the edge case of a delegate agent's question, FR-013a's own last sentence, and the assumption that every message to a delegate agent from a caller's thread waits, in `specs/009-a2a-server/spec.md`, per FR-013a (contradicts; LOW)
- [X] T132 Test through `follow` that a row that cannot be read while a run is followed keeps no status back, in `tests/test_a2a_server_tasks.py`, per FR-017 (partial; LOW)
- [ ] T133 Wake a cancel's followers as soon as the task's end is written, not only after the stop is marked and the thread tidied, so that no piece of the step under way is sent in between, in `src/freyja_agent/api/received.py` (`cancel`), per FR-016 (partial; LOW)

## Phase 11: Convergence

- [X] T134 Test that a follower behind a run a person stopped in the console is still sent the step that completed, so that only the task's caller's cancel, and not every end that says canceled, stops the reading, in `tests/test_a2a_server_tasks.py`, per FR-017 (partial; LOW)
- [ ] T135 Send a stream on the instance that runs the task the task canceled when its caller's cancel is written, not only with the run's next event, which waits for the step under way, or say in the contract and `docs/a2a-server.md` that it comes then, in `src/freyja_agent/api/received.py` (`follow`), per FR-016, FR-017 (partial; LOW)
