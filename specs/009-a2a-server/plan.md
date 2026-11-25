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
| I. Human approval for environment-changing actions | Yes, central | A caller's run is the console's run on the orchestrator `build_agent` assembles, behind the same gate; nothing a caller sends reaches a resume (FR-013, R10). On a caller's thread the gate is only ever wider: every agent tool is gated in the strict build (FR-013a, R6), and `SegmentedGraph` refuses to run such a thread on the other build. The one resume the service makes by itself rejects every action and approves none (R11), which has no effect outside the workspace; a decision a person already took is neither undone nor carried out. A paused run is resumed by a person after any delay, as today. A cancel stops between two steps and cancels no task. | PASS | PASS |
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
| FR-013a (every message to a delegate agent waits) | `test_a2a_server_approval.py`: an agent that runs without approval pauses on a caller's thread and not on a console thread; a caller's thread on the other build is refused |
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
| A second build of the orchestrator while the feature is on | FR-013a asks that a message to a delegate agent pauses on a caller's thread whatever the agent's approval choice; only the gate in the graph pauses a run | One graph with a condition per call: the catalog, the allowed decisions, and the check at send time would each have to learn the thread's kind, and a condition that answers differently at a resume fails the resume (research R6) |
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
