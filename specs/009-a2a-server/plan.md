# Implementation Plan: Receive Tasks from Other Agents over A2A

**Branch**: `009-a2a-server` | **Date**: 2026-11-25 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/009-a2a-server/spec.md`

## Summary

Freyja hands work to other agents over A2A; with this feature another agent can hand work to
Freyja the same way. It is off by default, and with it off no route, answer, table row, or
log line differs from today.

The whole feature rests on one choice: **a caller's message starts the run the console
starts**, on a thread of the service's own, and everything a caller is told is read from that
thread. So the approval gate, the step limit, Stop, the record of each step, and the
cleaning of what leaves the service are the code that exists, and a received task survives
a restart because nothing of it lives outside the database
([research.md](research.md) R7, R10).

What is added around that:

- **The protocol's surface.** Freyja implements the SDK's `RequestHandler` itself; the SDK's
  own handler runs each task in a process and loses it with the process (R1). Five
  operations are served and six refuse. Routes exist only while the feature is on (R3).
- **Two addresses.** The service's own, without a credential, and a second small application
  on a second listener in the same process, which was never given any other route and asks
  for a named caller's bearer token before it reads a request (R4, R5).
- **A strict build of the orchestrator** for callers' threads, in which every message to a
  delegate agent is gated, and whose file backend gives a named caller an area of its own
  (R6, R16). A caller's thread cannot be run on the other build: the wrapper every run goes
  through refuses it.
- **Two tables**, one row per conversation and one per received task (R8,
  [data-model.md](data-model.md)). A conversation's row is what makes a thread a caller's,
  and is written before its first run.
- **Cancel**, which stops a run between two steps as Stop does and rejects a proposal nobody
  is waiting for, by the same resume a person's rejection uses (R11).
- **The console** says who started a task, on the list, the page, and the proposal's card,
  and offers no box to send into a caller's conversation (R20).

One thing needs the owner's word before it is built, and is not changed here: naming
`a2a-sdk[http-server]` among the runtime dependencies in `pyproject.toml`. It installs
nothing that is not installed today (R19).

Design details are in [research.md](research.md); the tables and the states of a received
task in [data-model.md](data-model.md); what a caller can rely on in
[contracts/a2a-server.md](contracts/a2a-server.md); the settings in
[contracts/settings.md](contracts/settings.md); the changed answers of the service's own API
in [contracts/openapi.yaml](contracts/openapi.yaml); the one key added to a run's stream in
[contracts/sse-events.md](contracts/sse-events.md); the manual walk-through in
[quickstart.md](quickstart.md).

## Technical Context

**Language/Version**: Python 3.12 for the service (`.python-version`), managed by uv with the
committed `uv.lock`, src layout. TypeScript 5 on Node 20.9 or newer for the web app, managed
by pnpm with the committed `web/pnpm-lock.yaml`.

**Primary Dependencies**: no package is added. The service: a2a-sdk 1.3.0, whose server
modules it now imports from its own code, deepagents 0.7.32 on langgraph 1.2.18 and langchain
1.4.7, fastapi 0.162.0 on starlette, uvicorn 0.54.0, sse-starlette, psycopg 3.3.6 with its
pool. The web app: Next.js 16.4.6, React 19.3, swr 2.5.1, Tailwind CSS 4, shadcn/ui.
`pyproject.toml` should name the SDK's `http-server` extra among the runtime dependencies,
which changes that file and the metadata of `uv.lock` and installs nothing; that is asked of
the owner before phase 1 (research R19).

**Storage**: two new Postgres tables, `freyja_a2a_conversations` and `freyja_a2a_tasks`, made
at startup when missing, with an in-memory store of the same protocol for the suite
([data-model.md](data-model.md)). No existing table changes. A named caller's files are in a
directory of its own under `FREYJA_A2A_SERVER_FILES_DIR`. The web app stores nothing.

**Testing**: pytest 9 with pytest-asyncio, the scripted chat model, in-memory persistence,
and the stand-in agent; the caller is the SDK's own client over the in-process transport,
wrapped in `tests/a2a_caller.py`. No network; one transport test binds a loopback port for
the outside listener. The two tables have opt-in tests under `tests/integration/`. Vitest
with jsdom for the web app's units; Playwright in Chromium and Firefox for the console's
part of every user story (research R21).

**Target Platform**: a Linux server for the service, bound to loopback, with an outside
listener where a deployment sets one; a current desktop browser for the console.

**Project Type**: a web service and the web app that is its client.

**Performance Goals**: a caller that follows a task through the instance that drives its run
sees a step named within two seconds of the step completing (SC-009); through another
instance, or for a run the service started by itself, within one reading interval of four
seconds. A send without a stream is answered within the wait limit, 30 seconds by default.
Taking a context for a thread costs one more single-row read, on or off, and the list of
threads one more query (changed by the review of the tasks; research R6).

**Constraints**: the approval gate, its three decisions, and a resume are not changed, and
no caller's operation reaches them (FR-013). A console run is stopped only between two
steps (principle I; `tests/test_unattended_stop.py` keeps passing). No token in any answer,
stream, thread, or log line (FR-028). Every refusal is a fixed rule that runs before a model
is asked (FR-026). Every existing test passes unchanged with the feature off (FR-001). No
change to a CI file, `web/vitest.config.mts`, `.gitleaks.toml`, `web/playwright.config.ts`,
or the constitution. Coverage floors: 88% of the service's lines and 90% of each web
measure.

**Scale/Scope**: at most 20 named callers, 20 received tasks in progress by default, four
streams per task. Seven new service modules and about a dozen changed ones; one new web component
and four changed ones; seven new test files on the service, one stand-in caller, and one new
end-to-end spec.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Checked against constitution 1.5.0.

| Principle | Touched? | How the plan satisfies it | Pre-research | Post-design |
|---|---|---|---|---|
| I. Human approval for environment-changing actions | Yes, central | A caller's run is the console's run on the orchestrator `build_agent` assembles, behind the same gate; nothing a caller sends reaches a resume (FR-013, R10). On a caller's thread a message to a delegate agent follows the agents' approval mode and the agent's own choice, as on every thread (FR-013a as amended by feature 010; until then every agent tool was gated in the strict build, R6), and `SegmentedGraph` refuses to run such a thread on the other build. The one resume the service makes by itself rejects every action and approves none (R11), which has no effect outside the workspace; a decision a person already took is neither undone nor carried out. A paused run is resumed by a person after any delay, as today. A cancel stops between two steps and cancels no task. | PASS | PASS |
| II. Protocol-based delegation | Yes, central | Other agents reach Freyja over A2A, through the protocol's own SDK, in the binding and version Freyja reaches them with (R2). No agent framework is imported. Freyja keeps the single voice: a caller is sent Freyja's own reply and the names of steps, never a delegate agent's words in what the service itself says (FR-018, R13). | PASS | PASS |
| III. One runtime, every environment | Yes | The outside listener is a second application in the same process, started and stopped by the lifespan however the service is started, and differing between deployments by settings alone (R4). Both builds of the orchestrator come from `build_agent`. Both stores implement one protocol. The web app stays a client: it shows who started a task from what the API returns, holds no credential, and decides nothing. | PASS | PASS |
| IV. Configuration through settings | Yes | Ten settings, all read through `Settings`, each with the safe local default and documented in `.env.example` with the phase that reads it ([contracts/settings.md](contracts/settings.md)). The feature and the outside listener are off by default; the listener has no default address. Callers and their tokens are deployment configuration from the environment, never committed, never returned, logged, or written to a thread; they are not operator-managed, since a list that decides who may call from outside would decide nothing if the unauthenticated API could change it (spec, Assumptions). | PASS | PASS |
| V. Automated quality gates | Yes | Every requirement has a test (map below). The default suites need no network, database, model, or agent. The new statements are fixed strings with bound parameters, as the index of threads has them. A proof searches every answer, stream, thread, and log line for a caller's token, with values `.gitleaks.toml` already lets through. No Ruff suppression is planned. The two tables are covered by opt-in tests that CI does not run; the in-memory store shares the protocol and one set of tests runs against both. | PASS | PASS |
| VI. Deterministic guardrails before model judgment | Yes, central | Who is calling, whether a message may be taken, which task a caller may touch, the two limits, and the hand-over count are each decided by a fixed rule on the request and on stored rows, before a thread is made or a model asked (FR-026, R5, R8, R12). Which build a thread runs on, which files it reaches, and whether a proposal may still be approved are fixed rules on the conversation's row (R6, R16, R11). | PASS | PASS |
| Technology constraints | Yes | Python 3.12, uv, src layout; Deep Agents over LangGraph; FastAPI; the Postgres pool; SSE; Ruff only. The web app stays Next.js with TypeScript, pnpm, Biome, and Vitest. | PASS | PASS |

No deviation from the constitution. Two things add complexity the governance section asks to
justify; both are in Complexity Tracking.

### Changes to the spec

Made in this pull request, and listed in the spec under "Plan, 2026-11-25".

| Requirement | Change | Why |
|---|---|---|
| FR-026a | Two errors where it named one for "every other refusal of a message": invalid parameters for what is wrong with the message itself, and the unsupported-operation error, with a reason code, for "not now". | The protocol has no error for "not now". With one error for both, a caller could not tell a message to send again later from one that will never be taken (R12). |
| FR-016, Edge Cases | An agent task that waits for input, and whose question was reported, is not awaited. | Otherwise the received task would wait for an answer that only its caller can give and that FR-010 does not let it send (R7). |
| FR-017 | At most four streams follow one task at once on one instance. | Each stream is a reader that looks at the thread every few seconds; the spec bounded tasks and not what follows them (R13). |
| FR-022a | Until a proposal is rejected by rule, an approval or an edit of it is refused. | Rejecting by rule takes a moment; the refusal is what makes sure nobody approves in between (R11). |
| Edge Cases | A cancel that rejects a subagent's proposal: nothing of it runs, and the subagent's step completes before the run stops. | A subagent runs inside one step of Freyja's, and a run stops between steps (R11). |

Not a change, and worth saying: with the feature on, the `message` events of a run on a
caller's thread carry one more key, `metadata.reply`
([contracts/sse-events.md](contracts/sse-events.md)). The spec did not ask how Freyja's own
reply is told from everything else a run streams; this is how.

### Review of the design (2026-11-25)

An independent reviewer read the research, the data model, the contracts, and this plan
against the code and the installed libraries before they were committed. It found no way
for a caller to have an action approved or run without a person, and none to reach another
caller's threads, and it confirmed that every run goes through the one wrapper the strict
build is enforced in, that every run gives its lease back through the one method R9 hooks,
that a rejection is followed by a boundary, and that the file backend is reached only
through its methods. Its findings are in the documents now:

| Finding | Where it went |
|---|---|
| High: a task's state was read from four reads with nothing around them. A look between acceptance and the lease, or one a run started during, could end a task wrongly. | R7, "Three fences"; `started_at` and `base_checkpoint` in the data model |
| High: the index's note of a failed or stopped run outlives that run. | R7: a note counts only when it is the task's |
| High: a rejection by rule that fails would be tried again at once, without end. | R11: counted, spaced, at most five |
| Medium: a subagent goes on after its proposal is rejected. | R11, and a new edge case in the spec |
| Medium: a decision already taken, then a cancel, left a proposal nobody could clear. | R11: the same decisions sent again are let through |
| Medium: a cancel whose function was lost was never looked at again. | R9: the sweep |
| Medium: a cancel that arrives before a person's continuation is marked did not stop it. | R10: a run asks whether its task was canceled |
| Medium: the pieces sent to a caller would have included a tool's messages, a call's arguments, and a summarizing call. | R13: `metadata.reply`, in place of a mark for subagents |
| Medium: the model is told the thread's id, and a caller could ask for it. | R6: an alias on the strict build |
| Medium: a token sent in a query would be in the web server's access log. | R15: tokens out of every log record; R4: the outside listener's access log off |
| Medium: the whole body was read before the length rule. | R5: 256 KiB |
| Medium: a canceled task with a pending proposal counted against the limits, which FR-025 does not say. | Data model: `released_at` is for the limits only |
| Medium: the outside listener and named callers came two phases before the limits and the proofs. | The limits in phase 2; the listener, named callers, and their proofs together, now phase 5 |
| Low: the check at send time read the agent's record, not the build's gate; what was last built was kept once for two builds; no files key meant the shared workspace; a run's end did not wake a follower; a lifespan entered twice kept the first routes; a files directory that contains the workspace; `ReceivedTasks` in `runtime/` would have imported a route module. | R6, R16, R9, R3, settings, and the structure above |

### Checklist review (2026-11-25)

`/speckit-checklist` wrote [checklists/security.md](checklists/security.md): 46 items on who
may call, what a caller can cause without a person, what a caller is given, what bounds a
caller, and the state a task is read from. Every item was reviewed against the spec and the
documents of this plan. Twenty-six were satisfied as written. Twenty were not, and the
documents were changed until they were:

| Item | What was missing | Where it went |
|---|---|---|
| CHK002, CHK022 | With the feature off, a thread a caller started while it was on would have been run on the ordinary build: its hand-offs ungated by FR-013a, a named caller's task in the shared workspace. | Such a thread is not run while the feature is off: spec FR-001 and a new edge case; research R6; `a2a_server_off` in the contract. A deployment that never had the feature on made no lookup; the review of the tasks changed that (below). |
| CHK003, CHK046 | A caller the deployment removes kept its tasks, and a person could still approve what it had asked for. | Its tasks are canceled at the next start, as its own cancel would: spec FR-021 and Edge Cases; research R9. The console says the agent is no longer configured. An instance that is given other callers than the rest does the same, which the spec now says. |
| CHK001, CHK043 | That the console's forwarding reaches the A2A operations at the service's own address was in the research and not in the spec. | Spec, Assumptions: whoever reaches the console can call as the local caller, as it can already start a run. |
| CHK006 | Each refused credential was a warning, with no bound on what an unauthenticated party can make the service log. | One warning, then at most one in ten seconds for each of the two addresses, with a count: spec FR-028; research R5. |
| CHK011 | The order of the checks in front of an operation was stated in pieces. | One list: research R5, and the contract. |
| CHK021 | The size of a request was bounded in the research and absent from the spec's refusals. | Spec FR-008 and FR-026a. |
| CHK005 | Where a caller's conversation id may appear was not said. | Data model: returned to its caller and nowhere else. |
| CHK008 | The protocol's `tenant` field and a history length were not mentioned. | Contract: both ignored; who calls comes from the credential alone. |
| CHK009, CHK032 | The file area was specified for the file tools and the skills, and not for a subagent, the shell, and the graph's store. | Research R6 and R16: the subagent has the same gate and the same backend; the shell answers with an error on every thread; nothing reads the store today, and a test holds the strict build's tools to a list. |
| CHK016 | The alias was "a hash". | Research R6: SHA-256, first 16 hexadecimal characters, and why it cannot be turned back. |
| CHK017 | Which clock a task's start and a run's note are written by, and what a difference between instances does. | Research R7 and the data model: one clock for the message's own run; a difference can turn a continuation's reason into "interrupted", and cannot complete a task or leave one working. |
| CHK035 | Reusing a removed caller's name gives its conversations; the files were not mentioned, nor the deployment told. | Spec, Edge Cases; the settings' contract. |
| CHK040 | The limits bound what is in progress; what a caller does over time was not said to be in or out. | Spec, Assumptions: out of scope, named: tasks one after another, and how much a file area holds. |
| CHK041 | The time an answer takes, for a task that is another caller's. | Research R12: not made equal, and why there is no id to try it on. |
| CHK004 | The list of what a caller causes with no person left out the subagent and the model's own work. | Spec, Assumptions. |
| CHK037 | A token sent somewhere other than the header, at the service's own address. | Contract: not a credential; the request is one without. |

Two of these change what is built and when. The first adds a rule for the off state to
phase 1. The second needs cancel to exist before a caller can be named, so cancel (User
Story 4) is now phase 4 and named callers with the outside listener (User Story 3) phase 5.

### Review of the tasks (2026-11-25)

An independent reviewer read `tasks.md` against these documents and the code before it was
committed. It found one way for a caller's hand-off to be sent without a person: on a
database shared by instances with the feature on and off (the first row below). It found
no other, and several gaps of the tasks' own. Each is fixed in the documents now:

| Finding | Where it went |
|---|---|
| High: a service started with the feature off asked once whether any conversation existed. With several instances on one database, as during a rolling restart that turns the feature on, an instance that is off would run a conversation another instance started afterwards on the ordinary build, with its hand-offs ungated and a named caller in the shared workspace. | Research R6: every context taken for a thread looks it up, on or off, one single-row read; the spec's edge case, the data model, the performance goals, and the list's description in the contract follow |
| High: no task cleaned what is read from the thread before a caller is sent it; the pieces were cleaned by the console's runs, and the whole reply and `GetTask`'s artifact were not (R13). | `tasks.md`: built and tested in request 2 |
| High: a caller's run can pause from phase 2 on, and how that looks to its caller was built in phase 2 and tested in phase 3. | Phase 2 builds and tests it |
| Medium: a message to a caller's thread through the service's own routes (`caller_thread`), the console's missing box, and another message in a conversation can all be reached from phase 2 on, and were built or tested in phases 3 and 4. | Phase 2 |
| Medium: a caller's task stopped in the console stayed counted against the limits until phase 4, and accepting a message did not look first at the tasks that still count (data model, step 3). | A task a look ends is released in the same write; accepting looks first; phase 1 |
| Low: the card's address per request, the outside application's other-origin answer, a call with no row on the strict build, a cancel's hand-over count, the message ids sent from a caller's thread, the order of the checks, and test helpers that do not exist yet. | `tasks.md` |

Two refusals are absent between phases, and a person still decides in both windows: the
hand-over count is checked from phase 6, and a proposal a run comes to after a caller's task
was stopped in the console is rejected by rule from phase 4.

## Project Structure

### Documentation (this feature)

```text
specs/009-a2a-server/
├── plan.md              # This file
├── research.md          # Phase 0: decisions R1 to R21, what was verified and what is to verify
├── data-model.md        # Phase 1: the two tables, a received task's states, accepting a message
├── quickstart.md        # Phase 1: the manual walk-through, one section per user story
├── contracts/
│   ├── a2a-server.md    # what a caller can rely on: addresses, the card, operations, refusals
│   ├── settings.md      # the ten settings and what stops startup
│   ├── openapi.yaml     # the service's own API: `caller` on a row, two new refusals
│   └── sse-events.md    # `metadata.reply` on a caller's thread
├── checklists/
│   ├── requirements.md  # the spec quality checklist, from /speckit-specify
│   └── security.md      # from /speckit-checklist, all 46 items reviewed
└── tasks.md             # Phase 2 output (/speckit-tasks; not created by /speckit-plan)
```

### Source Code (repository root)

Every file below exists unless it is marked new.

```text
src/freyja_agent/
├── config.py                  # the ten settings, A2aCallerConfig, and what stops startup (R18)
├── runtime/
│   ├── callers.py             # new: Callers (who a token is, in constant time); taking
│   │                          # callers' tokens out of a text and out of every log record
│   │                          # (R5, R15)
│   ├── received_tasks.py      # new: ReceivedTaskStore, its Postgres and in-memory stores,
│   │                          # the two tables, accepting a message under one lock (R8)
│   ├── runtime.py             # begin_run and begin_read take a thread id and return the
│   │                          # strict build for a caller's thread; what was last built,
│   │                          # kept per build; the store, set up with the others; the
│   │                          # lease's release passed on (R6, R8, R9)
│   ├── unattended.py          # UnattendedRuns.join and start(stop_first=...); should_stop
│   │                          # also asks whether a caller's task was canceled;
│   │                          # SegmentedGraph knows its build, refuses a caller's thread on
│   │                          # the other and every entry but astream, and puts the files
│   │                          # key and the count into the run's config
│   │                          # (R6, R10, R16, R17)
│   └── thread_index.py        # IndexedRunLocks calls a function after a release (R9)
├── llm/
│   └── active.py              # ModelManager builds the strict orchestrator beside the other
│                              # while the feature is on (R6)
├── agent/
│   ├── caller_files.py        # new: CallerFiles, the backend that gives a named caller its
│   │                          # area, and the skills behind a wrapper that only reads (R16)
│   ├── context.py             # on the strict build the model is told an alias, not the
│   │                          # thread's id (R6)
│   └── factory.py             # build_agent takes the backend and the thread's name to tell
├── delegates/
│   ├── a2a_tools.py           # a message from a caller's thread carries the count (R17);
│   │                          # the check at send time follows the build's gate (R6)
│   └── delegation_worker.py   # takes its contexts with the thread's id
└── api/
    ├── runs.py                # new: the two functions that start a run and a resume, moved
    │                          # out of threads.py unchanged, plus `requester_gone` and the
    │                          # rest a caller's thread must be at; the function that starts
    │                          # a console run; the one that asks a stop (R10, R11)
    ├── received.py            # new: ReceivedTasks, the service of this feature: accept,
    │                          # where a thread stands, ending a task, cancel, rejecting by
    │                          # rule, following, the sweep (R7, R9, R11, R13, R14)
    ├── a2a_server.py          # new: the card and the two routes; the checks in front of the
    │                          # SDK's dispatcher; FreyjaRequestHandler; the protocol's types
    │                          # made from a task and from a run's events (R1, R3, R5, R12, R13)
    ├── a2a_listener.py        # new: the outside application, and its server in the
    │                          # lifespan (R4)
    ├── app.py                 # the lifespan adds the routes, starts the listener and the
    │                          # sweep, and takes them down again
    ├── threads.py             # its two run routes call api/runs.py; `caller_thread` (R20)
    ├── thread_index.py        # its routes call api/runs.py; `caller` on a row (R20)
    ├── thread_index_schemas.py  # ThreadSummary.caller
    └── streaming.py           # take_context passes the thread's id; `metadata.reply` on
                               # a run of the strict build (R6, R13)

tests/
├── a2a_caller.py              # new: the stand-in caller, the SDK's client; also a command
├── a2a_stub.py                # served by hand, prints the count a message carried
├── conftest.py                # make_a2a_server_app
├── test_a2a_server_send.py        # new: User Story 1, the card, the feature off
├── test_a2a_server_approval.py    # new: User Story 2, every operation against a proposal
├── test_a2a_server_callers.py     # new: User Story 3, the two addresses, the settings
├── test_a2a_server_tasks.py       # new: User Story 4, conversations, restarts, cancel
├── test_a2a_server_limits.py      # new: User Story 5, isolation, files, limits, the count
├── test_a2a_server_nowhere.py     # new: no token anywhere; what a caller is never given
├── test_received_tasks.py         # new: the store's rules, on the in-memory store
├── test_threads_other_origins.py, test_allowed_hosts.py   # unchanged; their helpers are
│                                  # used by the new files on an application with the
│                                  # feature on
└── integration/
    └── test_received_tasks_postgres.py   # new: the same rules on Postgres, two at once

web/
├── lib/tasks.ts               # ThreadSummary.caller
├── components/tasks/
│   ├── started-by.tsx         # new: who started a task, in words, and whether that agent
│   │                          # is still configured
│   ├── task-list.tsx          # a row says who started it
│   ├── task-view.tsx          # the page says who started it and whether the agent canceled;
│   │                          # no follow-up box for a caller's task, and why
│   └── proposal-card.tsx      # the card says that an agent, and which, started the run;
│                              # `requester_gone` in the console's words
├── __tests__/components/tasks/   # started-by, task-list, task-view, proposal-card
├── __tests__/tasks-testing.ts    # the fixture of a row: caller null unless a test gives one
└── e2e/
    ├── tasks-callers.spec.ts  # new: the console's part of the five stories
    └── tasks-api.ts           # rows with a caller; the two refusals

.env.example, README.md, AGENTS.md, web/README.md   # what exists once each phase is built
docs/a2a-server.md             # new: the feature's user documentation, from phase 2
docs/web-console.md            # the console's Tasks section: who started a task
Dockerfile, .gitignore         # the files directory: writable in the image, ignored by git
pyproject.toml, uv.lock        # the SDK's extra named, with the owner's word (R19)
```

**Structure Decision**: the layout of the repository as it is. What the feature keeps is a
store in `runtime/`, beside the other stores, and the runtime asks that store, and nothing
above it, which caller started a thread. What the feature does is `ReceivedTasks` in
`api/received.py`: it starts and resumes runs, which is what `api/` does, and the delegation
worker already runs the stream from there. The functions that start a run and a resume move
from `api/threads.py` to `api/runs.py` without a change, so that the thread routes, the
console's routes, and `ReceivedTasks` import one module and no module imports a route
module. The protocol's types are used in `api/a2a_server.py` only, so the rest of the
feature is tested without them.

Files of other features that this one changes, and whose owners should know:

- `runtime/unattended.py`, `runtime/thread_index.py`, `api/thread_index.py`, and the web's
  `components/tasks/` are feature 007's. The changes add a reader and a flag to
  `UnattendedRuns`, a function called after a release, and a key on a row; nothing a console
  run does today changes.
- `api/threads.py` and `api/streaming.py` are feature 001's, changed by 003 and 005. The
  bodies of two handlers move to `api/runs.py` with no change of behavior, and the tests of
  every existing refusal keep passing unchanged.
- `agent/context.py` and `agent/factory.py` are features 001's and 002's. The model of every
  thread but a caller's is told what it is told today.
- `llm/active.py` and `runtime/runtime.py` are features 004's and 005's. With the feature
  off, one orchestrator is built, as today, and each context taken for a thread makes one
  lookup of who started it (research R6).
- `delegates/a2a_tools.py` and `delegates/delegation_worker.py` are features 003's and 005's,
  and feature 008 is being built in them now. This feature's changes there are two lines of
  metadata and the thread's id in two calls; they go in last (phase 6 and phase 1), on the
  `main` of their day.

### Phases

Each phase is one pull request, and leaves the suite passing and the README and `docs/` true.
The feature stays off by default throughout, and a deployment that turns it on after any phase
gets what that phase's row says and nothing less safe.

| Phase | What it builds | Stories | Shown working by |
|---|---|---|---|
| 1 | The foundations, with nothing served yet: the settings this phase reads and what stops startup; a caller's thread not run while the feature is off, and `a2a_server_off`; the two tables and both stores, with accepting a message and the two limits; `api/runs.py`; `ReceivedTasks` with where a thread stands and ending a task; `UnattendedRuns.join`; the release passed on; the strict build, with its file backend and the alias; the thread's id in `begin_run`; the refusal to run a caller's thread on the other build; the stand-in caller. | none alone | `tests/test_received_tasks.py`, the settings' tests, and the tests of the strict build on a thread marked by hand |
| 2 | The card and the two routes at the service's own address, for the local caller: send, with and without a stream; reading a task; another message in a conversation; how a task waiting for a person looks to its caller; a message's form and the limits, each refused before a model is asked; the size of a request; the feature off answering as an unknown path; `caller` on a thread's row, the console saying who started a task and offering no box on it, and `caller_thread`. A request with an `Authorization` header is refused until phase 5; cancel and subscribe answer as operations that are not offered until phase 4. | 1, and the limits of 5 | Section 1 of the quickstart and the limits of section 5; `tests/test_a2a_server_send.py`, `tests/test_a2a_server_limits.py`, and the parts of `tests/test_a2a_server_approval.py` (`caller_thread`) and `tests/test_a2a_server_tasks.py` (another message) that came forward; the web's tests of the list and the page; `web/e2e/tasks-callers.spec.ts` |
| 3 | Every operation against a proposal; a person's decision followed by the caller; that a status and a reason hold no argument, result, or agent's words; the proposal's card saying who started the run. | 2 | Section 2; `tests/test_a2a_server_approval.py`; `web/e2e/tasks-callers.spec.ts` |
| 4 | Subscribing; a task read after a restart and through another instance; cancel; rejecting by rule and `requester_gone`; the sweep. | 4 | Section 4; `tests/test_a2a_server_tasks.py` |
| 5 | Named callers and their tokens, at both addresses, with `Callers` and the settings of callers and of the outside listener; the outside listener; the card at each address; tokens out of messages and log records; the tasks of a caller that is no longer named, canceled at startup. With it, in the same phase, the proofs of what it opens: no token anywhere, one named caller against another's tasks, and a named caller's files. | 3, and the isolation of 5 | Section 3; `tests/test_a2a_server_callers.py`, `tests/test_a2a_server_nowhere.py`, and the files' and the callers' tests in `tests/test_a2a_server_limits.py` |
| 6 | The hand-over count, on messages in and out; the proof of what a caller is never given, over every operation and every kind of task the phases before made possible. | the rest of 5 | Section 5; `tests/test_a2a_server_limits.py`, `tests/test_a2a_server_nowhere.py` |
| 7 | The quickstart walked by hand and corrected; `README.md`, `docs/a2a-server.md`, `docs/web-console.md`, `AGENTS.md`, and `web/README.md` read against the code. | all | Every section of the quickstart |

Why this order:

- The strict build, its file backend, and the alias are in phase 1, before anything is
  served, so that no phase lets a caller's task hand work to an agent without a person, or
  gives a caller the shared files.
- What bounds a caller, the form of a message, the two limits, and the size of a request,
  comes with the first phase that accepts a message.
- Cancel comes before named callers, although the spec gives the story of named callers
  the higher priority: taking a caller away cancels its tasks, and that needs cancel and the
  rejection by rule to exist. Until phase 4, cancel and subscribe are answered as operations
  that are not offered, and the card is true all the same: it promises streaming, which a
  send has from phase 2.
- A named caller can call from phase 5 and not before: until then a request that carries a
  credential is refused, so every caller is the local one. The proofs about named callers,
  tokens, and the listener are in that same phase, not a later one.
- Phase 6 depends on phase 5 only for the proof's two named callers.

What phase 1 must not leave half done: a conversation's row, the lookup in `begin_run`, and
the refusal in `SegmentedGraph` go in together, since a caller's thread that is run on the
other build is the one failure the design exists to prevent.

### Requirement to test map

| Requirement | Test |
|---|---|
| FR-001, SC-002 (off by default; nothing changes) | `test_a2a_server_send.py`: the two paths against an unknown path, byte for byte, for each method; no row made. The whole existing suite, unchanged |
| FR-001, Edge Cases (off, with callers' threads) | `test_a2a_server_tasks.py`: a second application with the feature off on the same stores, started before and after the conversation was made: a message and every kind of decision refused with `a2a_server_off`, the worker leaving the thread alone, the state still read, nothing run |
| FR-002, FR-005 (two addresses; the credential) | `test_a2a_server_callers.py`: no header, a wrong token, another scheme, a token in the body or a query, a `tenant` that names another caller, each at both addresses; one answer for all; nothing made; the body not read; the order of the checks, one test per neighbouring pair |
| FR-003, SC-005 (the outside listener serves A2A alone) | `test_a2a_server_callers.py`: every route of the main application asked at the outside application; the bind failure; the listener with the service's shutdown |
| FR-004 (callers' rules; what stops startup) | the settings' tests in `test_a2a_server_callers.py`, one per row of [contracts/settings.md](contracts/settings.md); no value in any error |
| FR-006 (the card) | `test_a2a_server_send.py`, `test_a2a_server_callers.py`: the card at each address, read by the SDK's client; nothing of the deployment in it |
| FR-007 (five operations; six refuse) | `test_a2a_server_send.py`: each refusal of [contracts/a2a-server.md](contracts/a2a-server.md); a request above the size limit |
| FR-008 (a message's form) | `test_a2a_server_limits.py`: each row of "the message itself", with the model's call count at zero |
| FR-009, FR-010 (a conversation; continuing it) | `test_a2a_server_tasks.py`: a new thread, never the caller's id; a follow-up with the earlier messages in mind; refused while anything is in progress; two at once |
| FR-009a, SC-007 (a named caller's files) | `test_a2a_server_limits.py`: two named callers and a console task, each writing a file; listing, reading, writing, and deleting across them, by Freyja and by its subagent; the skills read and not changed; the shell answering with an error; the strict build's tools, held to a list |
| FR-011 (a send without a stream) | `test_a2a_server_send.py`: ended within the limit; not ended, with the limit at zero and with `returnImmediately`; the run goes on |
| FR-012, SC-010 (kept across restarts and instances) | `test_a2a_server_tasks.py`: a second application on the same stores; `test_a2a_server_send.py`: the mark that cannot be written refuses the message |
| FR-013, FR-015, SC-003 (the gate; only a person decides) | `test_a2a_server_approval.py`: every operation and every field a message has against a pending proposal; approve, edit, and reject by a person |
| FR-013a, as amended by feature 010 (a message to a delegate agent waits while the agents' approval mode or the agent is Manual) | `test_a2a_server_approval.py`: a hand-off on a caller's thread waits while the mode is Manual and is sent to an Auto agent while it is Auto; a caller's thread on the other build is refused; `tests/test_agents_mode.py` for the mode itself |
| FR-014 (how waiting looks) | `test_a2a_server_approval.py`: the sentence, the names, no argument |
| FR-016, SC-014 (four states; none stays working) | `test_a2a_server_tasks.py`: each row of the table in research R7, among them a run of the worker's that is cut off, fails, and is given up, and a continuation that fails with its proposal pending; each of the three fences: a look between acceptance and the lease, a run that starts during a look, an earlier run's note |
| FR-017, SC-009 (what a follower is sent) | `test_a2a_server_send.py`: the order of events; pieces before the reply is complete; a subagent's pieces, a tool's messages, and a call's arguments not sent; `test_a2a_server_tasks.py`: the fifth stream refused |
| FR-018, FR-019, SC-008 (what a caller is never given) | `test_a2a_server_nowhere.py`: everything two callers received, searched for a key, an address, a thread's id, also after asking Freyja for it; every status and reason searched for an argument, a result, and an agent's words; a model's own error not in a reason |
| FR-020, SC-007 (a caller's own) | `test_a2a_server_limits.py`: every operation of one caller against the other's task, each answer equal to that for an unused id |
| FR-021, FR-022, FR-022a, SC-011 (cancel) | `test_a2a_server_tasks.py`: during a model call, a tool step, and an approved action; on a pending proposal, the orchestrator's and a subagent's; while an agent task is awaited; a cancel with a decision, before and after the run is marked; a decision already taken, and the same decisions sent again; `requester_gone`; a rejection by rule that fails, tried again later and no more than five times; a cancel whose function was lost, found by the sweep; the tasks of a caller the settings no longer name, canceled at startup, with a waiting proposal rejected. `tests/test_unattended_stop.py` unchanged |
| FR-023, FR-024, SC-004 (the console) | the web's unit tests and `web/e2e/tasks-callers.spec.ts`; `caller_thread` in `test_a2a_server_approval.py` |
| FR-025, FR-026, SC-012 (the limits; rules before a model) | `test_a2a_server_limits.py`: each limit, with waiting tasks counted; one reason for both; the model's call count |
| FR-026a (each refusal one named way) | one test per row of the two tables in [contracts/a2a-server.md](contracts/a2a-server.md) |
| FR-027 (the hand-over count) | `test_a2a_server_limits.py`: no count, each bad form, the limit; the stand-in agent's record of the count it was sent |
| FR-028, SC-006 (no token anywhere) | `test_a2a_server_nowhere.py`: two recognizable tokens, every operation and refusal, a token pasted into a message; answers, streams, threads, and the log at every level |
| FR-028 (what is logged) | `test_a2a_server_nowhere.py`: a line for a task accepted, a cancel, a refused credential, and a refused limit, each naming the caller; a hundred refused credentials giving one warning and then one more with their count; the web server's own access line for a request with a token in its query |
| FR-029 (the warning) | `test_a2a_server_callers.py` |
| FR-030 (documentation) | read against the code in phase 7 |
| FR-031 (how it is tested) | this map; `web/e2e/tasks-callers.spec.ts` for the console's part of each story |
| The two tables | `test_received_tasks.py` on the in-memory store and `integration/test_received_tasks_postgres.py` on Postgres, the same cases |
| SC-001, SC-013 (how little is set) | the quickstart, sections 1 and 3 |

## Complexity Tracking

| What is added | Why it is needed | The simpler way, and why not |
|---|---|---|
| A second build of the orchestrator while the feature is on | FR-013a asked that a message to a delegate agent pauses on a caller's thread whatever the agent's approval choice; only the gate in the graph pauses a run. Since feature 010 amended FR-013a, the build is kept for a named caller's file area and the alias, which the graph's backend and its prompt also hold | One graph with a condition per call: the catalog, the allowed decisions, and the check at send time would each have to learn the thread's kind, and a condition that answers differently at a resume fails the resume (research R6) |
| A second listener in the process | FR-003: callers on other hosts must reach A2A and nothing else of an API that has no authentication | One listener with a filter: every route of the API would be one mistake away from the outside. A second process: a second way to start the service (research R4) |

## What is asked of the owner

1. **Before phase 1**: may `pyproject.toml` name `a2a-sdk[http-server]` among the runtime
   dependencies? It moves the extra's name from the development group's entry to the
   service's own, changes the metadata in `uv.lock`, and installs no package (research R19).

Nothing else on the list of things to ask about is touched: no CI file, no
`web/vitest.config.mts`, no `.gitleaks.toml`, no `web/playwright.config.ts`, no constitution.

## Implementation Notes

### Request 1 (2026-11-25): the foundations

**Starting point (T001)**, on the branch as made from `main`: the service suite passed 2011
tests and skipped 133, with 91.51% of lines covered; the web app's 790 unit tests passed,
with 97.46% of statements, 94.39% of branches, 97.2% of functions, and 97.96% of lines.

**The opt-in Postgres tests (T002)** run against `freyja_test_a2a`, made in the shared
container with `docker exec freyja-agent-postgres-1 createdb -h 127.0.0.1 -p 5434 -U freyja
freyja_test_a2a`: its server listens on loopback, on 5434, and `docker compose` from the
worktree is another compose project.

**The SDK's extra (T003, research R19).** The owner said yes on 2026-11-25. `pyproject.toml`
names `a2a-sdk[http-server]` among the runtime dependencies and no longer in the development
group; `uv lock` changed the lock's metadata only. The packages a production install takes
(`uv export --no-dev`) are the same, at the same versions, and the installed set did not
change.

**Built otherwise than the tasks say, and why:**

- Existing tests reach three seams by name, and they pass unchanged: the console's run
  routes still call `threads.start_run` and `threads.resume_run` and keep `_open_first` and
  `_go_on`, now thin wrappers over `api/runs.py`; `take_context` calls `begin_run()` with no
  argument for a thread no caller started; and the runtime makes `SegmentedGraph` with two
  arguments and sets `strict` and `lookup` after. `runs.start_console_run` is the one the A2A
  service calls. The console's resume through `api/runs.py` is not built: request 4 needs it.
- The store has no read of "whether any conversation exists": the review of the tasks made
  every context look a thread up, on or off, so nothing asks it.
- `FREYJA_A2A_SERVER` is a plain boolean, as the other switches, since `StrictBool` refuses
  the environment's `true`. The refusal is `RefusedError` (Ruff's N818).
- The log lines of FR-028 for an accepted task and a limit are written from this request, by
  `ReceivedTasks`, which nothing serves yet.

**What the tests found that the design did not say:**

- A link in a named caller's area that leads out of it makes deepagents' backend raise, as it
  does on the shared workspace: the run fails with an internal error, and nothing is read.
  The task ends failed with `internal` (`test_a_link_out_of_the_area_reads_nothing_and_fails_the_run`).
- Two changes of one path in one model step are refused by deepagents itself ("parallel file
  mutations"), before the read-only skills are asked.
- `CallerFiles` learns its caller in its async methods, on the event loop, before it hands
  the call on, and the graph reaches only those, so the question of `get_config()` in a
  worker thread (R16, "to verify") does not arise.

**Cases of T013 not written end to end, with what covers them:** a run of the worker's on a
caller's thread that is cut off, fails, or is given up (SC-014), and a continuation that
fails while its proposal is pending. The first ends as a thread with a next step or with no
agent task awaited, and both rows are tested (`test_a_thread_left_with_a_next_step_ends_its_task_as_interrupted`,
`test_which_agent_tasks_a_received_task_waits_for`); the second reads as the pending
proposal it leaves, a tested row. Both come again in request 4, whose tests drive the
worker's runs on callers' threads with cancel. T009's rebuild on a change of profile or of
agents is shown by construction: both builds are made in one `_apply`, which the test shows
gives one build, and the feature off gives one orchestrator.

**Review of request 1.** An independent reviewer read the change against the design and the
installed libraries before its first commit. It found no path by which a caller's thread
runs on the ordinary build or without its files key, no message to an agent that leaves
without a person's approve or edit, and nothing that reaches outside a named caller's area
or changes a skill. What it found:

| Finding | What was done |
|---|---|
| Medium: a completed task whose run wrote no reply took the previous task's reply, since the reply was looked for back to the thread's start. | The reply is looked for since the caller's last message only (`test_a_task_whose_run_wrote_no_reply_is_never_given_an_earlier_one`) |
| Low: one look that failed, before the limits were counted, refused every caller's message. | Each look is guarded and logged; its task stays counted (`test_a_look_that_fails_keeps_its_task_counted_and_blocks_no_message`) |
| Low: `CallerFiles` took a caller's name as a path; the rule for names comes in request 5. | A name that is not an agent's name's form reads nothing (`test_a_caller_s_name_that_is_no_directory_name_reaches_no_file`) |
| Low: with the feature off, `/runs/unattended` looked a thread's row up before refusing it. | Refused first |
| Low: the fallback of a read named the wrong cause when the lookup failed; `last_task` did not clean its conversation id as `accept` does. | Both fixed |
| Low: an agent's card can be read for a call on the strict build before the call is refused, when no row records a decision; no message or cancel leaves. | Left: the gate prevents such a call reaching the tool |
| Low: a runtime put together by hand has no fence. | Left: only tests put one together; the service always has both |
| Low: with the feature off every run reads `freyja_a2a_conversations`, up to three times, and fails when that read fails. | Kept: it fails closed, as the review of the tasks decided (research R6) |
| Low: an acceptance that takes over 60 seconds before its run starts can be failed by another instance's look; `mark_started` then raises and the caller is told. | Left: the row stays consistent |
| Low: no sweep yet, so a task whose release function did not run waits for the next acceptance to be looked at. | Request 4 builds the sweep (R9) |

### Request 2 (2026-11-25): an agent hands Freyja a task and reads the result

**Built otherwise than the tasks say, and why:**

- The routes are the SDK's `Route`s, which both walkers of the existing suite already know
  (`tests/test_threads_other_origins.py`, `tests/test_allowed_hosts.py`): neither file
  changed. The new tests import their `_routes` and their headers, and run them on an
  application with the feature on, inside its lifespan, where the routes exist.
- The size limit reads the body itself, piece by piece, and hands the dispatcher a request
  that replays it; a `Content-Length` above 256 KiB, or one that is no number, is refused
  without reading. The caller's name reaches the handler through the request's scope, which
  the context builder reads; a request that reached the dispatcher another way is refused
  with the internal error.
- `FreyjaRequestHandler` validates a message's form itself, in the contract's order, and not
  with the SDK's `validate_request_params`, whose required-field check would answer a
  message with no parts with its own sentence.
- The fifth stream of one task (`stream_limit`) is not refused yet: a task has one stream
  until subscribing exists, which request 4 builds with the limit.
- The hand-over count is not read: every task of this request keeps 0, until request 6.
- A request with an `Authorization` header is refused without a log line; the sparing
  warning of FR-028 comes with named callers in request 5. `caller.configured` is true for
  `local` alone until then.
- A send without a stream follows its task (`ReceivedTasks.settle`): a task waiting for a
  person has not ended, so the send waits out `FREYJA_A2A_SERVER_WAIT_SECONDS`, as the
  contract has it. A reader of a run that nobody follows, as after `returnImmediately`, is
  read to its end by a task of its own (`leave`), so the run never fills its backlog.
- What is read from the thread is cleaned of every address the scrubber knows and of the
  keys of the active profile and the environment's (`ReceivedTasks.clean`), and a result is
  kept cleaned once its task has ended. A key of a saved profile that was never active
  while its task was read is not known without decrypting every profile, and is not looked
  for. Pieces are sent up to their last whitespace and cleaned there (`_ready`): an address
  or a key holds none, so one written over several pieces is cleaned whole.
- A tool name the catalog does not have is named `a tool Freyja does not have`, never as
  the model wrote it. A task id that is not 32 hexadecimal characters is answered as not
  found without a lookup.
- `metadata.reply` is true for an AI message with text, a whole one too, so that a model
  that does not stream still has its reply marked; the library's mark of an internal call
  is `lc_internal_call` in the call's metadata, with `lc_source` beside it.
- A no-usable-model run on a new thread writes no state, so the thread has no row (007).
  The test of the row's reason therefore sends a follow-up on a thread with state, after a
  restart with another secret.
- The tests share their requests in `tests/a2a_requests.py`, and the streaming model is
  `StreamingChatModel` in `tests/fakes.py`. The tests were written alongside the code, not
  before it; those of the review's findings were seen to fail without their fix (below).

**Review of request 2.** An independent reviewer read the change against the design and
the installed SDK before its first commit. It found no field of any operation that reaches
a resume or a decision, no caller's thread run on the ordinary build, nothing different
with the feature off apart from the planned `caller: null` and its read, `caller_thread`
before the lease on both routes, and the console's text in text nodes. What it found:

| Finding | What was done |
|---|---|
| High: each piece of a stream was cleaned by itself, so an address or a key a model writes over two pieces, as providers stream a token at a time, reached the caller. | Pieces are sent up to their last whitespace and cleaned there (`test_an_address_and_a_key_written_over_several_pieces_are_cleaned_in_them`) |
| Medium: at shutdown the service waited for the reader of a run nobody follows to reach the run's end, so such a run went on to its end instead of stopping at its next boundary. | The readers are cancelled at close; the run stops at its next boundary (`test_the_service_stopping_stops_a_run_nobody_follows_at_its_next_step`) |
| Medium: a result was kept raw and cleaned with the keys active when read; after another profile was activated, `GetTask` returned the earlier one's key. | Kept cleaned (`test_a_reply_is_kept_cleaned_so_another_profile_activated_later_changes_nothing`) |
| Low: a tool name the catalog does not have went out as the model wrote it. | Named by a fixed text (`test_a_tool_the_catalog_does_not_have_is_not_named_as_the_model_wrote_it`) |
| Low: a task id holding a NUL would make Postgres raise, an internal error in place of not found. | The id's form is checked first (`test_a_task_id_the_service_could_not_have_made_is_not_looked_up`) |
| Low: the card read the package's version from disk on every request. | Read once |
| Low: tests that did not show what they named: the wake after a run's end, the continuation's `reply`, a hand-off named in a stream, the startup line, a recognizable profile in the card; a hold set after the run started. | Each written to show it (`test_a_follower_waiting_on_a_person_is_woken_when_the_decision_s_run_ends` fails without the wake) |
| Low: the SDK checks a method's name and its params' form before the version, so a request without the version header and an unknown method gets `-32601`. | Left: the SDK's dispatcher decides it; every operation Freyja serves or refuses is checked for the version first |
| Low: a value that is no number in `FREYJA_A2A_SERVER_WAIT_SECONDS` stops startup with the feature off, as request 1's limits do. | Left, as request 1's limits; the README says the limits are not checked, not that any text is taken |
| Low: a busy conversation found under the lease has its task row written and removed again. | Left: the data model's design (accepting a message, step 4) |

Each test above failed before its fix.

**Checked by hand (T055)**, on a check instance from `.env.example` on port 8020, with the
database `freyja_check_009` (not `freyja_check_a2a`, which tasks.md's standing rules name and
which is not this feature's), the stand-in model as the active profile `call-tools`, the
stand-in agent on 9978 added to run without approval, and the console on 3020: section 1
(the card, a stream with `steps 1`, a send, the console's list and page, and the feature off
answering the card, `POST /a2a`, and an unknown path with the same 404) and the limit of
section 5 (five tasks waiting for a person, the sixth `task_limit`, one rejected in the
console, the next accepted; the stand-in agent received nothing). The quickstart now says to
send `steps 1` with the stand-in model, and that a send prints a refusal too.

### Request 3 (2026-11-25): a person decides what a caller's task proposes

**Built otherwise than the tasks say, and why:**

- A follower that waits on a person is handed a reader of the run a decision starts before
  that run's first event: `UnattendedRuns.on_start` is told of every run as it starts, and
  `ReceivedTasks.on_run_started` joins one reader for each follower of the thread and wakes
  it (T058). Joining when woken would miss the steps a fast continuation takes first. A
  reader that is handed over and never read, because its follower ended, is read to its end
  by `leave`, as a run nobody follows is.
- A follower on another instance, or of a run the worker drives, still sees a decision's
  run by its looks, every four seconds, and its end; R13 has that.
- The proposal's card says who started the run and that no person asked for the action, in
  one sentence under its heading (T057, T059).
- The console still labels the calling agent's message "You", as it labels a person's: no
  task of this request changes it.
- T056's tests put a follower's look 30 seconds away, so that what a stream receives of a
  continuation can only come from following the decision's run; the two that check the
  continuation's steps failed before T058, and pass with it. The others passed against the
  code of request 2: they show what request 2 already did.

**Review of request 3.** An independent reviewer read the change before its first commit.
It found no operation or field of a caller that decides a proposal or runs an action, no
change for a thread no caller started or with the feature off, and the readers handed over
cleaned up. What it found:

| Finding | What was done |
|---|---|
| Medium: a decision sent through `POST /threads/{thread_id}/resume` starts a run that is not the console's, so no follower is handed it; the docs said a caller follows any decision's run on its instance. | The docs say which decisions are followed step by step (the console's way, `/resume/unattended`) and that the others are seen by looks and at their end. R13 follows console runs only |
| Medium: the test of a proposal that waits three days moved no clock the received tasks read. | It moves their store's clock too (`test_a_proposal_waits_days_and_the_task_goes_on_when_it_is_decided` fails with a one-day limit put in) |
| Low: the rejection's test passed without T058. | It checks the continuation's pieces |
| Low: after a decision the caller still read "Waiting for a person's approval of ..." until the next step. | A follower sends `Working.` when it starts reading another run of the thread |
| Low: a second run that started before the follower read the first was joined late, from where it had got to. | Each follower keeps its handed readers in order, and reads one at once rather than waiting (`test_runs_that_start_one_after_another_are_read_in_order`) |
| Low: a branch that could not run; no test of a handed reader never read, of `on_start` raising, or of the card's wording for several actions. | Removed; tests written (`test_a_follower_closed_with_a_reader_it_never_read_leaves_nothing_behind`, `test_a_failure_told_of_a_run_s_start_leaves_the_run_alone`) |
| Low: a follower whose task's end was not written yet when the caller's next message starts a run is handed that run, and streams the next task's events under the earlier task's id. | Left: one caller's own conversation, as before this request, and the window is the moment between a run's end and its look |

Each test above failed without its fix.

**Checked by hand (T062)**, on a check instance from `.env.example` on port 8020 (database
`freyja_check_009`, dropped afterwards), with the stand-in model active, the stand-in agent
added to run without approval, and the console on 3020: a stream of `handoff stub say hello`
stopped at `Waiting for a person's approval of: agent stub.` with the agent having received
nothing; the console counted one task to decide and its card said who started the run and
that no person asked for it; approved there, the open stream received `Completed: agent
stub.`, the reply, and the task completed, and the agent received the message once; a second
one rejected there completed with Freyja's reply and the agent received nothing more.

### Request 4 (2026-11-26): continue, come back to, or cancel a task

**Starting point**, on the branch as made from `main` with feature 010: the service suite
passed 2262 tests and skipped 156, with 91.37% of lines covered; the web app's 821 unit tests
passed, with 97.55% of statements, 94.38% of branches, 97.37% of functions, and 98.08% of
lines.

**Built otherwise than the tasks say, and why:**

- A rejection by rule that could not run is tried again by the sweep also after its task was
  released. A pending proposal does not keep a canceled task counting (data-model.md), so a
  sweep over the rows not released would never see such a task again. The store has one
  more read and one more write on the existing columns, and no migration: `to_reject`, the
  rows that ended with `reject_after` set and fewer than five attempts, the earliest first,
  and `rejection_done`, which clears `reject_after` once a look finds no proposal. The sweep
  reads each list up to the total limit, so a round looks at up to twice that many rows; the
  second read has no index and reads the whole table, which is small today; an index would
  be migration 3.
- The console's resume through `api/runs.py` is built (`start_console_resume`), for the
  rejection by rule. The console's route still goes through `threads.resume_run` and
  `_go_on`, which existing tests patch.
- `UnattendedRuns.canceled` is a function the application sets while the feature is on, as
  it sets `on_start`: with the feature off a boundary reads nothing more (FR-001).
- A cancel looks at its task first, so a task whose run has ended and whose end is not
  written yet is answered `-32002` and keeps its own end; the write names `working` as the
  state it replaces. It also marks the index's stop where the run is marked, so the console
  says the run is stopping; the run stops at its next boundary by the cancel either way.
- `requester_gone` is asked only while the feature is on: with it off, `a2a_server_off`
  already refuses every decision on a caller's thread.
- The rejection's reason reaches the thread in the gate's own words: "User rejected the tool
  call for `<tool>` with reason: The requesting agent canceled the task." The console's note
  on a canceled caller's task says that the cancel rejected it and not a person.
- A send's stream is the first of its task's four streams. A send without a stream that
  waits for its task is not a stream.
- A follower whose reader the console's runs disconnect for falling behind ends without the
  task's end, and its caller subscribes again. It tells by its reader ending without a run's
  last event while the run is still in progress on the instance.
- Request 3's open finding is fixed: a follower whose task has ended is not handed a later
  run of the thread, such as the caller's next message's; it reads its task's end. A
  subscription's follower starts from the status it was sent with the task, so the walk's
  repeated `Waiting for agent stub.` is gone.
- `SubscribeToTask` and `CancelTask` on a task that has ended answer with the SDK's own
  message for `-32004` and `-32002`: the contract gives no sentence for either.
- The sweep's first round does not cancel the tasks of callers the settings no longer name:
  named callers come with request 5.
- Three tests that earlier requests of this feature wrote changed with what they describe;
  no test of another feature changed (FR-001): cancel and subscribe of the id `t` are now
  `-32001` in `test_what_is_not_offered_is_refused_as_the_contract_says`;
  `test_nothing_a_caller_sends_decides_a_pending_proposal` tries a subscription, which
  leaves the proposal, and no longer a cancel, which rejects it by rule (part 7); and
  `test_a_message_is_refused_while_a_proposal_is_pending_and_while_a_decision_runs` holds
  the rule off for its proposal on a conversation at rest, which the rule would reject.
- The 28 service tests of this request (25 new, 3 changed) were written first and each
  failed before the code. The web's unit tests were written before the components changed,
  the end-to-end part after.

**Cases of T066 not written end to end, with what covers them:** a decision a person already
took, which needs a subagent whose run fails after its gate: the test replaces
`decisions_taken` so that the proposal reads as decided, and 005's tests
(`tests/test_subagent.py`) cover the real case. A cancel and a decision at once is shown in
each order: the decision first in both cases of
`test_a_cancel_around_a_person_s_continuation_stops_it_at_its_first_boundary`, and the
rejection first in `test_a_cancel_rejects_a_pending_proposal_by_rule_and_nothing_of_it_runs`,
whose late approval is refused.

**Feature 010's change to FR-013a in this feature's documents.** Corrected in this request:
quickstart section 4, and the README's row of `FREYJA_A2A_SERVER`. Left for request 7's
reading of the documents against the code: the quickstart's setup and sections 2 and 5,
which rely on a hand-off to an agent that runs without approval waiting all the same (with
the mode on Auto it is sent at once); this plan's Constitution Check row for principle I and
its requirement map's row of FR-013a; research R6 ("every agent tool is gated in the strict
build"); and tasks.md's term "the strict build" and its line that every hand-off from a
caller's thread waits for a person.

**Review of request 4.** An independent reviewer read the change before its first commit.
It found no field or operation of a caller that approves or edits, no rejection by rule that
approves, edits, or runs on a thread no caller started or while another run holds the lease,
no leaked stream slot, nothing different with the feature off, and SQL only as fixed
strings with bound parameters. What it found:

| Finding | What was done |
|---|---|
| Medium: a caller's next message writes its task before its run is refused; in between, `requester_gone` and the boundary's question read that task as working, so a person's approval of the canceled task's proposal went through and ran. | Both ask the last task whose run started, the only one a proposal can be of (`last_started_task_of_thread`; `test_a_caller_s_next_message_does_not_let_a_person_approve_the_canceled_task_s_proposal`) |
| Medium: an action that no longer needs approval, as after the mode turned Auto, takes approve only, so the rule's reject is refused, a person's approve is `requester_gone`, and the conversation stays busy; the console and the docs said the proposal could be rejected. | Nothing of it runs, as before. The log says to set the mode to Manual, and the card, the console's answer to an approval, and the docs say it can be rejected only then (`test_a_proposal_whose_action_no_longer_needs_approval_is_not_run_by_a_cancel`, the card's test) |
| Medium: a task that ended was tidied without a guard when read, so `GetTask`, a subscription, or a send's wait answered an internal error when its thread could not be read. | It is answered from its row; the tidy's failure is logged (`test_a_task_that_ended_is_answered_from_its_row_when_its_thread_cannot_be_read`) |
| Medium: the console said that none of what a canceled task proposed ran, also after an approved action ran before the cancel, and while a rejection still waited. | It says nothing can be approved any more, that Freyja rejects it by itself, and that what ran before the cancel is in the conversation (the view's test) |
| Low: two instances that read one row each counted an attempt and tried it. | An attempt is counted only at the count it was read at (`count_rejection` names it; `test_two_instances_sweeping_at_once_make_one_attempt`, and the store case on Postgres) |
| Low: the rule's own rejection, taken by a subagent's gate whose run then failed, was taken for a person's decision, so the proposal stayed for good. | Rejections a gate already took are repeated, the only decisions that continue the thread; an approval or an edit still stops the rule (`test_the_rule_repeats_its_own_rejection_that_a_gate_already_took`) |
| Low: a failed write of the index's stop skipped the rejection until the next sweep. | Each is tried by itself (`test_a_cancel_whose_index_cannot_be_written_still_rejects_the_proposal_at_once`) |
| Low: the sweep reads up to twice the total limit. | Kept, and the test checks both lists' limit: the second list keeps the retries of released tasks from waiting on a full first list |
| Low: a release reads the lease first and writes last, so a run that starts between can find the task already released. | Left: the limit's count is loose by that one task for that moment |
| Low: tests that do not show what they name: the "after the run is marked" case passes without the boundary's question, since the cancel also marks the index; the shutdown test shows behavior request 2 built; the decided-proposal test asserted nothing of actions; the "at once" order where the rejection holds the lease was missing; a cancel's metadata was no longer tried. | The "before" case fails without the question; the shutdown test stays as the restart's proof; the other three are added to the tests (`at_once`, `actions`, the cancel with approving metadata) |
| Low: the docs left out that a run the service starts by itself is not stopped, and the spacing of a subagent's second rejection. | Both said |

Each test above failed without its fix, apart from the three added assertions, which pass
against both.

**Checked by hand (T079)**, on a check instance from `.env.example` on port 8020 (database
`freyja_check_009`, not `freyja_check_a2a`, which tasks.md's standing rules name and which
is not this feature's; dropped afterwards), with the stand-in model active, the stand-in
agent added to run without approval, and the console on 3020: a conversation of two messages
made one task with both (the stand-in model answers `ready`); a hand-off sent at once in
Auto mode reached `Waiting for agent stub.`, the service was stopped with Ctrl-C and started
again, and `caller follow` found the task and followed it to Freyja's reply to the agent's
outcome (a first try killed the process during the hand-off's step, and the task ended as
failed and interrupted, which is right; the quickstart now says to wait for the status);
`steps 8` canceled after its first step ended canceled after the step under way, and the
console showed it stopped; with the mode on Manual, a hand-off canceled while it waited was
rejected by rule, the count to decide went from 1 to 0, the page said the agent canceled, and
the stand-in agent received nothing.

### Request 5 (2026-11-26): an agent on another host calls with its token

**Starting point**, on the branch as made from `main` with request 4: the service suite passed
2297 tests and skipped 159; the web app's 826 unit tests passed.

**Built otherwise than the tasks say, and why:**

- The callers' tokens are cleaned by the one record factory of `install_log_scrubber`, as
  R15 asks: `delegates/addresses.py` keeps a second, exact-text list, `TOKENS`, beside the
  addresses, which `Callers` sets to the running service's callers (each token as written,
  and as a query or a path writes it encoded) and clears when the service stops. It is set,
  not added to, so that a test process that runs many services keeps no token of one that
  has stopped; the suite reuses `sk-recognizable-key-0001` as a profile's key. What a caller
  is sent is cleaned of tokens too (`ReceivedTasks.clean`).
- The rules of named callers are checked in the settings' model rule, not on the field, so
  that with the feature off nothing of them is applied (contracts/settings.md). An error names
  a caller whose name breaks its rule by its place, "caller 2", and every other by its name;
  none repeats a token or a value. The two errors of the listener that hold with the feature
  off are checked first.
- `create_app` takes `listen`. The suite gives `False` and drives the outside application
  in-process as `app.state.a2a_outside` (`ServerApp.outside`); one test binds a free loopback
  port. The application is built whenever the listener is set, and the socket is bound only
  with `listen`.
- The outside listener is a FastAPI application with no documentation routes, so that an
  unknown path there answers `{"detail": "Not Found"}` as at the service's own address. A
  stream open when the service stops is closed after five seconds (`GRACE_SECONDS`).
- The sweep's first round cancels the working tasks of callers the settings no longer name,
  through the cancel a caller makes, logged as a warning that names the caller and says why;
  the store has one more read, `working_of_others`, and no migration.
- `caller.configured` is read from the settings of the instance that answers: with the
  feature off no caller is named, so a caller's row then says it is no longer configured.
- With the stand-in model the quickstart's file steps cannot be walked; the suite covers them
  through A2A (`tests/test_a2a_server_limits.py`, part 3).
- The tests of the settings, the credential, the listener, and the proofs were written before
  the code and failed against it for want of the settings, the module, or the routes; the
  web's test of `configured` failed before its change.
- A shape error of `FREYJA_A2A_SERVER_CALLERS`, a name given twice, or a value that is no
  JSON object, names the setting only: the callers are read by the service itself
  (`NoDecode`, a wrapping rule), since a key may be a token put in the wrong place and a
  token can have a name's form. With the feature off such a value is not used and not
  checked; a value of the wrong type in another setting of the feature still stops startup,
  as request 1's limits do.

**Review of request 5.** An independent reviewer read the change before its first commit. It
found no request at the outside listener that does anything without a named caller's token,
no token honored anywhere but the `Authorization` header, a comparison with every caller's
token without an early end, no address or caller taken from anything a request says of
itself, the order of the checks kept at both addresses, and no operation of one caller that
reaches another's task, conversation, files, or a console thread. What it found:

| Finding | What was done |
|---|---|
| Medium: building the listener's `uvicorn.Config` with `access_log=False` emptied the `uvicorn.access` logger the main server shares, so opening the listener silenced the service's own access log. | The listener serves with a protocol of its own that writes no access line, and leaves the logger as it is (`test_the_outside_listener_binds_its_port_serves_and_stops_with_the_service` checks the logger's handler and propagation, and now its no-access-line check means something) |
| Medium: a token written as uvicorn writes a path (`/` kept), as `encodeURIComponent` writes it, with lowercase hex, or escaped by a `repr` was not cleaned from log lines. | Every such form is cleaned (`test_a_token_is_cleaned_in_every_way_a_path_a_query_or_a_log_line_writes_it`) |
| Low: the cancel of a removed caller's tasks ran once and was not tried again when the store failed, and read no more than the total limit as set now. | The first round is done only once it has run through, and reads up to the total limit's own bound (`test_a_removed_caller_s_tasks_are_canceled_once_the_store_answers`) |
| Low: the origin check at the outside listener warned once a request, without a token. | Warned of sparingly there, as a refused credential is (`refuse_other_origins(quiet=True)`; `test_pages_of_another_origin_at_the_outside_listener_are_warned_of_sparingly`) |
| Low: a removed caller's task was said, in the console, to be canceled by the agent. | The page and the proposal's card say that the deployment no longer names the agent (the view's and the card's tests). The thread and the caller's view keep the caller's cancel's words: nobody is left to read the second, as data-model.md has it |
| Low: settings: a shape error repeated a key, a name given twice was kept as the last, `localhost` beside `127.0.0.1` was not the same address, a blank host bound every interface, and the bind error repeated the address. | All five fixed (`test_callers_in_the_wrong_shape_are_refused_without_their_keys_and_ignored_when_off`, `test_the_listener_on_the_service_s_own_loopback_port_is_refused`, `test_a_blank_listener_host_is_refused`) |
| Low: the listener's lifecycle: a server that stops by itself is not noticed; the bind looks a host name up on the event loop; the docs promised five seconds to an open stream, where the streams' own shutdown ends it sooner; during a SIGTERM the listener accepts until the lifespan ends. | Logged when it stops by itself; bound in a thread; the docs say a stream is closed when the service stops. The last is left: the window is the main server's own drain |
| Low: `TOKENS` is the process's: the last service set it and any that stops clears it, which only the suite can see; a removed caller's old token is no longer cleaned. | Left, recorded: one service runs in a process, and a token the deployment no longer names is no longer known |
| Low: tests: part 5 did not show that the local caller's and a still named caller's tasks stay; the bound test did not show the stream open at the stop and could go through a proxy; the e2e row check matched the other row too; the Postgres store's new read had no test. | Each added (`others`, `reader.is_alive()`, `trust_env=False`, an exact text, the store case on Postgres) |

Each test above failed without its fix, apart from the store case and the e2e check, which
show what was untested.

**Checked by hand (T097)**, on a check instance from `.env.example` on port 8020 with
`freyja_check_009` (dropped afterwards), the stand-in model active, the stand-in agent added,
the console on 3020, and the outside listener on 127.0.0.1:8443 with `artex` and `heimdall`:
startup logged 2 callers and the listener on, with the `http` warning; there, the card asked
for a bearer token, artex's send completed, no token and a wrong one got the same 401 with
`WWW-Authenticate: Bearer`, `/threads` and `/healthz` answered 404, and heimdall's `GetTask`
of artex's task answered as not found; the console listed the task as started by agent artex.
heimdall's `notify hello` waited for a person; with heimdall removed and the service started
again, its task was canceled with a warning naming it, its proposal rejected by rule, the
count to decide went from 1 to 0, the console said heimdall is no longer configured, and its
token was refused. With the callers removed and the listener set, startup stopped naming
`FREYJA_A2A_SERVER_CALLERS`. With every `FREYJA_A2A_SERVER` line removed, a caller's waiting
task was listed and read, and both decisions were refused with `a2a_server_off`; with the
settings back it waited again. No token appeared in any log. The quickstart's file steps need
a model that writes files and were not walked; with an unquoted JSON value `uv run
--env-file` dropped the callers and every line after them without a word, which the
quickstart now says.

### Request 6 (2026-11-27): the hand-over count, and what a caller is never given

**Starting point**, on the branch as made from `main` with request 5: the service suite passed
2363 tests and skipped 160, with 91.50% of lines covered. The web app is not changed by this
request.

**Built otherwise than the tasks say, and why:**

- The count a run passes on is that of the conversation's last task **whose run started**
  (`ReceivedTaskStore.last_started_task_of_thread`, read by `Runtime.hops_of`), not of its
  last task. They differ only between a new message's task row and its run taking the lease;
  a run in that moment, such as the one that reports a delegate agent's outcome or the
  rejection by rule after a cancel, continues the earlier task, and the new message is then
  refused as `conversation_busy`. Request 4 read who a proposal belongs to the same way. The
  contract, research R17, and data-model.md now say so; FR-027's wording is unchanged.
- The wire name and the run's config key are named once, in `agent/context.py`:
  `HOPS_METADATA` (`freyja.hops`) and `HOPS_KEY` (`freyja_hops`), beside `thread_alias`, so
  that `api/a2a_server.py`, `runtime/unattended.py`, and `delegates/a2a_tools.py` share them
  without one importing another. `SegmentedGraph` reads the count through `count`, a
  function the runtime sets after making it, as it sets `lookup`; a count that cannot be read
  refuses the run (`CallerThreadError`), as a lookup that fails does.
- One function makes the message id of a hand-off and of a cancel (`_message_id`): a thread
  whose run carries the count is named by its alias. The send ledger never makes an id
  again; a step that runs again returns what the ledger recorded.
- The stand-in agent keeps what each cancel request carried through a subclass of the SDK's
  handler (`cancel_hops`), since the SDK does not give the executor a cancel's request. Served
  by hand it writes `Received, hand-over count <n>: <text>` to its terminal, through
  `sys.stdout` (Ruff's T20 rules out `print`).
- Part 3 of the proof gives its LLM profiles a key of their own, `sk-recognizable-key-0042`:
  the suite's usual key is also alpha's token, which is cleaned as a token first.
- Left as they are, and recorded: a whole number too large for a double (such as 10^400) is
  refused by the protocol library's own parse of the request, with `-32602` and the SDK's
  `Invalid params`, before Freyja's handler and with nothing made; a count written with more
  digits than a double holds is read as the double it rounds to (`2.0000000000000001` is 2),
  and no count at or above the limit rounds below it; a count anywhere but the message's own
  metadata, such as the request's metadata or a part's, is not read, which the docs now say.
- The tests of the count (part 4, 28 cases) and the proof (part 3) were written before the
  code: 17 of part 4's first 21 failed against it, the other four being the counts of 0 that
  `main` already keeps and the order after the conversation id, which `main` already refused;
  the proof stopped at the count's first refusal.

**Review of request 6.** An independent reviewer read the change before its first commit. It
found every refusal decided before a thread, a row, or a model; the count right in every run
on a caller's thread, the subagent's included; no thread's id in anything sent to an agent;
nothing different with the feature off; and a count that cannot be read failing closed. What
it found, all low:

| Finding | What was done |
|---|---|
| A count too large for a double gets the SDK's sentence, not the contract's. | Left, recorded above: it is refused before Freyja's handler, with nothing made |
| Doubles round a few values (`1e-400` is 0, `2.0000000000000001` is 2). | Left, recorded above; the limit holds |
| A count in the request's metadata or a part's is ignored, not refused. | As the contract has it; `docs/a2a-server.md` says only the message's own is read |
| The code reads the last task whose run started; the documents said the last message. | The contract, R17, and data-model.md say what the code does (above) |
| Part 4 drove only hand-offs in the task's own run: nothing held a person's approval through either resume route, the worker's run, or a subagent's hand-off. | Three tests added (`test_a_hand_off_a_person_approves_carries_the_count_plus_one`, `test_the_run_that_reports_an_agent_s_outcome_carries_the_count_plus_one`, `test_a_subagent_s_hand_off_carries_the_count_plus_one`); each fails without the count read |
| No case showed a count's form is judged before its limit; NaN and Infinity were not tried. | `4.5` and the three raw forms added; `4.5` fails with the two checks swapped |
| Two searches of the proof could not fail: a file no run listed, and a tool's and an agent's words searched in statuses only. | Each caller's run lists its files, the test asserts the model was shown every searched text, and the searches cover everything a caller was sent |
| The spec still said the count is not built. | Both places corrected |
| A docstring line of 128 characters; `HOPS_KEY` named two keys. | Reflowed; the stand-in's constant is `HOPS_METADATA` |

Mutation checks, each restored after: without the `+ 1`, without the alias, without the count
read, and without the check of a message's count, part 4 fails; with the limit checked before
the form, the `4.5` case fails; with a reply not cleaned, the proof fails.

Under the commit hook's first run, two tests of feature 008 (`tests/test_a2a_progress.py`,
which give the stand-in agent 0.3 seconds) failed while another session ran four suites on
the machine; the file passed alone and the hook's second run passed. No test of this request
was involved.

**Checked by hand (T106)**, on a check instance from `.env.example` on port 8020 (database
`freyja_check_009`, dropped afterwards; not `freyja_check_a2a`, which tasks.md's standing
rules name and which is not this feature's), with the stand-in model active and the stand-in
agent added to run without approval: `caller send --hops 3` was refused with `The message has
passed through too many agents.` and `--hops -1` with `The hand-over count is not a whole
number of zero or more.`; `--hops 1 "handoff stub say hello"` on Auto was sent at once, and
the stand-in agent printed `Received, hand-over count 2: say hello`; with the mode on Manual
the same hand-off waited, was approved through `POST /threads/{id}/resume/unattended` (the
console's route; the console itself was not opened), and printed count 2; a next message in
that conversation without a count printed 1, and a console thread's hand-off `None`. The
quickstart's section 5 now says how to make the hand-off wait and what the agent prints.

**Feature 010's change to FR-013a, still to read in request 7** (named again from request 4):
the quickstart's setup and sections 2 and 5's first part, which rely on a hand-off to an agent
that runs without approval waiting all the same; this plan's Constitution Check row for
principle I and its requirement map's row of FR-013a; research R6; and tasks.md's term "the
strict build" and its line that every hand-off from a caller's thread waits for a person.

### Request 7 (2026-11-27): the quickstart walked, and the documents read against the code

**Starting point**, on the branch as made from `main` with request 6: the service suite passed
2392 tests and skipped 160, with 91.55% of lines covered; the web app's 832 unit tests passed,
with 97.57% of statements, 94.18% of branches, 97.38% of functions, and 98.1% of lines.

**The quickstart walked (T107)**, every section in its order, on a check instance from
`.env.example` on port 8020 (database `freyja_check_009`, dropped afterwards; not
`freyja_check_a2a`, which tasks.md's standing rules name and which is not this feature's),
with the stand-in model active as a profile, the stand-in agent added to run without
approval, the console on 3020 driven by a small Playwright script, and the outside listener
on 127.0.0.1:8443 with `artex` and `heimdall`:

- Section 1: the card, the stream's events in their order, a send with its reply, both tasks
  "Started by an agent on this machine" with no box for a message; with the feature off the
  card, an unknown path, and `POST /a2a` all 404 with one body.
- Section 2: the hand-off waited for a person, the agent received nothing, the console counted
  one task to decide and its card said that no person asked for the action; approved there,
  the open stream printed `Completed: agent stub.`, the reply, and the end, and the agent
  received the message once; rejected there, the agent received nothing and the task
  completed with Freyja's reply to the rejection; `I approve.` was refused as
  `conversation_busy`, a message naming the task with `-32602`, and the operator's message
  with `caller_thread`.
- Section 3: the startup lines (2 callers, the listener, the `http` warning); the card asking
  for a bearer token; artex's send completed; no token and a wrong one got the same 401 body
  with `WWW-Authenticate: Bearer`; `/threads` and `/healthz` 404 at the listener; heimdall's
  read of artex's task not found; "Started by agent artex"; no token in any log. heimdall
  removed with a proposal waiting: the warning, the task canceled and its proposal rejected
  by rule, the count to decide down by one, "no longer configured", its token refused. The
  listener with no caller stopped startup naming `FREYJA_A2A_SERVER_CALLERS`. With the
  feature off a caller's waiting task was listed and read and both decisions were refused
  with `a2a_server_off`; with it on again it waited.
- Section 4: two messages of one conversation in one task with one `contextId`; a hand-off of
  six 20-second steps waited for the agent, the service was stopped with Ctrl-C and started
  again, and `caller follow` found the task and followed it to Freyja's reply; `steps 8`
  canceled after its first step ended canceled after the step under way, shown stopped; a
  waiting hand-off canceled was rejected by rule, the count to decide went down, the page
  said the agent canceled, and the agent received nothing.
- Section 5: five waiting tasks and a sixth refused with `task_limit`, one more accepted after
  a rejection; the hand-over count as request 6 walked it.

Corrected in the quickstart where the built feature differs: the setup and section 2 set the
agents' approval mode to Manual before a hand-off is to wait (feature 010; with Auto it is
sent at once); the stand-in's reply arrives as a piece up to its last space and then whole;
with the feature off only a named caller's task reads as no longer configured, the local
caller is always configured (`api/thread_index.py`); the mode is switched beside the Send
button, not in the sidebar; section 5's limit needs the mode on Manual and no other task of
the caller in progress, and the five waiting tasks are decided before its second part. Not
walked: section 3's three file steps, which need a model that writes files (the suite covers
them, `tests/test_a2a_server_limits.py` parts 1 and 3), and adding the agent on the Agents
page (done through `POST /agents`, feature 005's own walk). The walk found no behavior the
tests do not cover; what it corrected was the quickstart's text.

**The documents read against the code (T108).** A reader given the files and the questions
read `README.md`, `docs/a2a-server.md`, `docs/web-console.md`, `AGENTS.md`, and
`web/README.md`. It found every point of FR-030 covered, every setting of the feature in
`.env.example` and the README with the code's defaults and ranges, nothing planned described
as built, and the Next links chained. Each finding was checked against the code first:

| Finding | What was done |
|---|---|
| Medium: the README said that with the feature off no setting of it is checked; the listener's host and port still stop startup set alone or while off. | Said, in the README's row of `FREYJA_A2A_SERVER` |
| Medium: `docs/a2a-server.md`'s opening said every action waits for a person, without the agents' approval mode. | It says a message to a delegate agent waits as on every thread, unless the mode and the agent are both Auto |
| Medium: AGENTS.md's reading order of `docs/` left out `a2a-server.md`. | Added in its place |
| Low: the README said the listener serves the card to named callers only; the card is served to anyone. | Corrected |
| Low: AGENTS.md's command of the stand-in caller named four operations and no token. | All six and `--token` |
| Low: the lists of end-to-end specs in the README and `web/README.md` left out `tasks-callers.spec.ts` (and older specs). | Added, with `agents-mode.spec.ts` and the Tasks section's specs |
| Low: `.env.example` did not give the listener port's range. | Given |
| Low: FR-030 still asked to document that every message to an agent from a caller's thread waits. | FR-030 says when it waits, as FR-013a was amended |
| Low, found after the reading: AGENTS.md said the wrapper puts a caller's files key into a caller's run's config, and not the hand-over count it puts there since request 6. | Said, in `src/freyja_agent/runtime/AGENTS.md`, where that paragraph now is |

AGENTS.md's Layout has since moved, its text unchanged, into an `AGENTS.md` of each
directory; what the reading found holds for those files.

Feature 010's change to FR-013a, read in the documents as requests 4 and 6 named it:
corrected in the quickstart (setup, sections 2, 4, and 5); in this plan, the Constitution
Check row of principle I, the requirement map's row of FR-013a, and the Complexity Tracking
row of the second build; research R6, with a note of the amendment above its first points;
tasks.md's term "the strict build" and its two lines on every hand-off waiting; and the
spec's FR-030, User Story 2's independent test, and two lines of its notes, where the
clarification's own answer is kept as it was given and the amendment said beside it.

**Coverage (T109).**

| | T001 (`main` before the feature) | Now |
|---|---|---|
| Service, lines | 91.51% (2011 tests) | 91.55% (2436 tests) |
| Web, statements | 97.46% (790 tests) | 97.57% (832 tests) |
| Web, branches | 94.39% | 94.18% |
| Web, functions | 97.2% | 97.38% |
| Web, lines | 97.96% | 98.1% |

Both rows are measured as T001's were, without the Postgres tests. CI now runs the service's
suite with them, and its floor is set for that run: with them, on the branch rebased on the
latest `main`, the service is at 96.67% (2596 tests) against a floor of 95%, and
`runtime/received_tasks.py`, this feature's store, at 99.64%. Without them it stays above the
commit hook's floor of 89%: of its 675 lines that no test runs then, about 400 are in the
eight modules that hold a Postgres store, 62 of them this feature's `PostgresReceivedTaskStore`.
The web stays above its floor of 90% for each measure; its branches are 0.21 points lower
than at T001. No floor is raised here, since that needs the owner's word.

**Gates (T110)** on the finished branch: the commit hook over all files passed, every hook
(the file checks, gitleaks, Ruff, pyright, pytest with coverage, Biome, and Vitest);
`pnpm test:e2e` passed 332 tests in Chromium and Firefox; the Postgres tests against
`freyja_test_a2a` passed 160, and the whole suite with them 2596; Semgrep found nothing and
Trivy nothing over the source; and the service's image, built with `--network host`, had no
HIGH or CRITICAL vulnerability in Trivy's scan, and was removed afterwards.

**What was built differently from the plan (T111).** Each request's notes above say what it
built otherwise and why; the differences from the plan that last are these:

- The strict build no longer gates every agent tool (R6): feature 010 amended FR-013a after
  the plan, so a message to a delegate agent from a caller's thread follows the agents'
  approval mode and the agent's choice. The second build stays for a named caller's file area
  and the alias.
- Seams the existing tests reach by name were kept (request 1): the console's routes still
  call `threads.start_run` and `threads.resume_run`, `take_context` calls `begin_run()` with
  no argument for a thread no caller started, and `SegmentedGraph` is made with two
  arguments, `strict`, `lookup`, and `count` set after.
- The route reads the body itself under the size limit, and the handler checks a message's
  form itself in the contract's order, not with the SDK's validation, whose sentences are not
  the contract's (request 2).
- A follower is handed the reader of a decision's run before that run's first event
  (`UnattendedRuns.on_start`, request 3), since joining when woken misses a fast
  continuation's first steps.
- Who a proposal belongs to, and the hand-over count a run passes on, are read from the
  conversation's last task whose run started, not its last task (requests 4 and 6): a new
  message's task row exists before its run is refused.
- A rejection by rule that could not run is tried again also after its task was released,
  with one more read and write on the existing columns and no migration (request 4).
- Tokens are cleaned by the one record factory, through a second exact-text list beside the
  addresses (`TOKENS`), set by the running service and cleared at its stop (request 5).
- The outside listener writes no access line through a protocol of its own, since the
  `uvicorn.access` logger is the whole process's (request 5).
- The tasks of a caller the settings no longer name are canceled by the sweep's first round,
  through the cancel a caller makes (request 5).
- A count too large for a double is refused by the SDK's own parse, with its sentence, before
  Freyja's handler (request 6).

Open, the owner's to decide: whether to raise the web's coverage floors, and whether the
console should label a calling agent's message other than "You", as it labels a person's.

### Convergence (2026-11-27)

`/speckit-converge` read the spec, this plan, and the tasks against the code once all 111
tasks were done. Four reviewers, each given one area (who may call and the listener; a
message, a task's states, and its stream; the approval gate, a cancel, and the console; what
a caller is never given, the limits, the documents, and the tests), read the code and
reported gaps. Each finding was checked against the code before it was kept. They found no
gap of principle I or of a P1 story, and 17 findings: two MEDIUM, T112 and T113, and fifteen
LOW, T114 to T128. One was rated lower than its reviewer rated it: FR-028 asks to log "each
message refused for a limit", and FR-026 names "a limit" apart from "a message's form" and
"a hand-over count", so the limits FR-028 means are FR-025's, which are logged; T128 logs the
other two all the same, as LOW.

The owner asked to run converge and implement until only LOW findings remain, so T112 and
T113 are done here and T114 to T128 are left; T116 and T118 need the owner's word first.

- T112: a stream that followed a task through a cancel sent the step under way after the
  cancel's answer, a piece of the reply and `Freyja is using: ls.` as working, and only then
  the task canceled. A follower that reads a run now stops reading it once the task's caller
  has canceled it, the one end written while a run may still be in progress: a cancel on this
  instance wakes it, and a cancel sent to another instance, which wakes no follower here, is
  read from the task's row before a new status is sent. Every other end is written once the
  run has ended, and a follower behind the run still reads what it left. The run goes on as
  before; the follower's last change is `Ended`. When the cancel is sent to another instance,
  pieces of the step under way can still be sent before the end, as the contract now says; a
  status cannot. `test_a_stream_sends_nothing_of_the_run_once_its_task_was_canceled` fails
  without the wake check, `test_a_stream_says_no_step_once_another_instance_canceled_its_task`
  without the read of the row, and
  `test_a_follower_behind_its_run_when_its_task_completes_is_sent_every_step` when any end
  stops the reading. A row that cannot be read then is taken as not canceled, and logged, so
  that a failed read keeps no status back
  (`test_a_row_that_cannot_be_read_while_a_run_is_followed_is_taken_as_not_canceled`, which
  fails without the guard).
- T113: the five lines of the spec that still said every message to a delegate agent from a
  caller's thread waits now say FR-013a as amended: User Story 2's scenario 3, FR-031's test,
  SC-003, the assumption on what a caller can cause with no person involved, and the line on
  feature 005's FR-022. Four more were found by the second pass below: the edge case of the
  feature turned off, which named "the wider gate" among the rules that make a caller's
  thread safe and now names the caller's files; the edge case of a delegate agent's question,
  whose answer Freyja passes on with a person's approval unless the mode and the agent are
  both Auto; FR-013a's own last sentence, now in the past tense; and the assumption that
  every message to a delegate agent from a caller's thread waits, now the rule as it was
  specified, with the amendment after it.

The second pass of converge read the change itself, given the diff and the questions. It
found one MEDIUM gap, a regression of T112's first version, which stopped reading a run on
any end of the task, so that a follower behind its run when the task completed dropped the
steps and pieces the run left; only a caller's cancel stops the reading now. It also found
five LOW: the contract's sentence on another instance, which read as a stream through
another instance; the three lines of the spec above; and a test of the failed read that
called the helper and not the follower. All five are done here, but one: a cancel wakes its
followers only once it has marked the stop and tidied the thread, so a piece of the step
under way can still be sent in that moment. It is T133, LOW; T129 to T132 are the rest.

The third pass read the code once more, given the same questions. It found nothing above
LOW, so the loop ends here, with two LOW findings. A test that shows a follower behind a
run a person stopped still sent its step, since a Stop also ends the task canceled and only
the caller's cancel stops the reading, is T134, done here; without the reason in the check it
fails. A stream on the instance that runs the task learns of the cancel with the run's next
event, so the task canceled reaches it once the step under way completes, as before this
change, though `CancelTask` and `GetTask` say canceled at once: T135, LOW, left.

Left for the owner, all LOW: T114 to T128, T133, and T135; T116 and T118 need the owner's
word first.
