# Research: Tasks Section of the Web Console

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Decisions the plan rests on, each with its reason and what was set aside. Facts marked
*verified* were checked on 2026-11-20 against the code on `main` at 1b1726c and the packages in
`uv.lock` (langgraph 1.2.18, deepagents 0.7.32, fastapi 0.162.0, sse-starlette 3.6.0) and
`web/pnpm-lock.yaml` (Next.js 16.4.6, swr 2.5.1). Facts marked *run* were seen that day in a
run: of the real factory with a scripted model, in memory, or of the built console in a
browser. Facts about Next.js come from the documentation bundled with the installed version
(`web/node_modules/next/dist/docs/`), as `web/AGENTS.md` requires, or from its installed
code where the documentation is silent. The prototypes were thrown away; each is described
well enough here to be written again, and the tests of the implementation repeat them. What
could not be checked, and what is knowingly not covered, is listed at the end.

While this plan was written, feature 005's Phase 7 was merged (`main` at dac8f77). The files
the plan relies on were compared again afterwards: `api/threads.py` is unchanged, and the
changes to `api/streaming.py` and `runtime/runtime.py` touch nothing the plan uses. An error
the run loop now words for a blocked resume passes through `SegmentedGraph` as any error
does (R4).

The spec named three things as not proven. R1 settles the first, R4 the second, and R16 the
third. No reviewer other than the author has read this plan.

## R1. A run's stream through the console's forwarding

**Question**: the console has only ever sent plain JSON through the `/api/...` forwarding in
`web/next.config.ts`. Does a server-sent event stream pass it as it is produced, in Chromium
and in Firefox?

**Method** (*run*): a stand-in upstream on port 8020 that sends events at chosen moments and
logs when a connection closes; the console built with `FREYJA_API_URL` set to it and served
with `pnpm start` on port 3020; and a script that opens the console in each browser and reads
`POST /api/...` with `fetch` and `response.body.getReader()`, noting when each chunk arrived.
The same was repeated against `pnpm dev`.

**Findings**:

| What was sent | Chromium | Firefox |
|---|---|---|
| Six events one second apart, answered with sse-starlette's own headers (`Cache-Control: no-store`) | One chunk, at 6.0 s, `content-encoding: gzip` | The same |
| The same events with `Cache-Control: no-store, no-transform` | Six chunks, at 1.0, 2.0, 3.0, 4.0, 5.0, and 6.0 s, not compressed | The same, within 10 ms |
| One event, then nothing for 45 s | Network error at 30.0 s | Error at 30.0 s |
| One event, then a `: ping` comment every 15 s for 70 s | Every chunk arrived; ended at 70.0 s | The same |
| A reader that aborts after 3.5 s | The upstream saw the connection close at its next write, within 1.5 s | The same |

So, as the service answers today, a stream does **not** pass as it is produced: Next.js
compresses what it forwards, and the compressor holds the events back until the stream ends.
The dev server does the same. The compressor leaves alone a response that says
`Cache-Control: no-transform`, and then every event arrives when it is sent.

The forwarding also ends a stream that is silent for 30 seconds. That is Next.js's own limit
for a forwarded request (`proxyTimeout`, 30,000 ms when not set; *verified* in
`next/dist/server/lib/router-utils/proxy-request.js`). A comment line counts as activity.

**Decision**: the two routes this feature adds for runs answer with
`Cache-Control: no-store, no-transform` and send a ping every 10 seconds, so that two pings
fall inside every 30 seconds. Both are set by the service on its own answer; nothing in
`web/next.config.ts` changes. The existing routes are left as they are (FR-027): a client
that is not a browser behind the console is not affected by either finding.

A stream that is cut anyway, by a proxy in front of a deployment or a network that drops, no
longer ends the run (R2). The page then follows the task by reading it again (FR-013).

**Alternatives considered**: `compress: false` in `web/next.config.ts`, which turns
compression off for every page and asset to fix one kind of answer. Raising
`experimental.proxyTimeout`, an experimental setting, where a ping already does it. A Route
Handler that relays the stream: Next.js's guide says a Route Handler proxy holds the whole
response in memory (004 research R13), and it would put code on the web server that
principle III keeps out.

## R2. A run that goes on without a reader

**Question** (FR-021): the service ends a run when its client stops reading. How does a run
go on?

**Finding** (*verified*): a run is the async generator `run_stream` (`api/streaming.py`). It
is tied to a reader only because the route hands it to the response, and the server cancels
it when the connection closes. Nothing in it needs a reader: the delegation worker already
drives it with `async for _event in stream: pass` for its background runs
(`delegates/delegation_worker.py`).

**Decision**: a new object of the runtime, `UnattendedRuns` (`runtime/unattended.py`), drives
a run's events in a task of its own and hands a copy of each event to whoever is reading. A
reader that leaves is dropped; the task goes on to the generator's end, where `run_stream`
releases the lease and the context as it always does. A reader that does not keep up is
dropped as well, once 1,000 events are waiting for it, so that a stalled connection cannot
make the service hold a run's whole output in memory; its page follows the task by reading
(FR-010). The lease heartbeat, the step limit,
the gate, and the checkpoints are inside `run_stream` and are untouched (FR-021).

**Run**: with a model scripted for five tool calls and a reader that left at the first
`update`, the run driven this way ended with the model's last reply after six model calls.
The same script through the existing route, with the same reader, made no model call after
the reader left.

**Alternatives considered**: starting the run as a background run of the delegation worker.
The worker belongs to the session building features 005 and 006, a background run takes no
input from a person, and its stream reaches nobody.

## R3. Reusing the existing routes' checks

**Question**: starting a run and continuing one with a decision do a dozen things before the
stream starts: the lease, the context, the refusal of a paused thread, the outcomes of agent
tasks, the proposal id, the validation of each decision. The new routes must do exactly the
same. How, without copying it and without rewriting `api/threads.py`?

**Finding** (*verified*, *run*): the two handlers, `start_run` and `resume_run`, are plain
async functions. Each raises its refusal as an `HTTPException` before it returns, and returns
an `EventSourceResponse` whose `body_iterator` is the `run_stream` generator, not yet started
(`sse_starlette/sse.py`, line 301).

**Decision**: the new routes declare the same path and body types, call the existing handler,
and take the generator out of the response it returns. Every refusal is the existing one,
raised by the existing code. `api/threads.py` is not changed for this.

**Run**: a route built this way answered an empty message with the same 422 as the existing
route, and streamed the same events for a run that completed, for one that paused, and for a
proposal raised inside the subagent.

A test pins `body_iterator`, so an upgrade of sse-starlette that renames it fails loudly.

**Alternatives considered**: splitting each handler in `api/threads.py` into a part that
prepares a run and a part that answers. It is the cleaner shape, and it rewrites two
functions in a file both sessions change. Copying the two handlers: forty lines that decide
what a decision may do, kept equal by hand.

## R4. Stopping a run after the step under way

**Question** (FR-022): today the service ends a run only by cancelling it, wherever it is. Can
it end one at a boundary, so that no action is cut off?

**Finding**: a consumer of `astream` is not told where a step ends. The stream carries each
task's output as it arrives, and nothing marks the last output of one step from the first of
the next; how far the graph's loop has gone when an output is read was not established, and
the design does not rest on it. Cancelling a run in the middle of a tool step can leave an
action's effect without its result in a checkpoint, which feature 001 names as an accepted
limit (its edge case "Continuation fails, or its client disconnects"). So a stop has to come
from the graph's own loop. LangGraph has that (*run*): a call of `astream` can name nodes to
pause before (`interrupt_before`), which ends the call after the last step's checkpoint,
with the paused nodes as the thread's `next`; calling again with no input goes on from
there.

**Decision**: a run started through the new routes is run in **segments**. Each segment is
one call of the real graph's `astream` that ends before the next model call or the next tool
step (`interrupt_before=["model", "tools"]`). Between two segments, and nowhere else, the
service asks whether a stop was requested. If not, the next segment starts. If so, the run
ends there. A run that is not started through the new routes is passed straight to the real
graph and is not touched.

This is done by `SegmentedGraph` (`runtime/unattended.py`), which stands for the compiled
graph in the `RunContext` a run is given and passes everything but `astream` through. It is
put there in one place, `Runtime.begin_run` (R16), so `api/streaming.py` is not changed: it
calls `context.agent.astream` as before.

**Run**, with the real factory and a scripted model, a plain run beside a segmented one:

- The thread's messages, the number of steps, the thread's `next`, and whether it paused were
  the same, for a script with single tool calls and for one with three tool calls in one
  reply, through a pause on `send_notification` and its approval to the end.
- The events were the same. With single tool calls they came in the same order. With three
  tool calls in one reply, the order of the three results differed, and so did the order of
  two plain runs of that script: two orders in five runs.
- A pause raised inside the subagent gave the same events and the same proposal.
- Stopped before a model call, the thread had a result for every tool call, no proposal, and
  `next` of `("model",)`. A new message then ran normally on it.
- Stopped before a tool step, the thread's last message asked for a tool that had not run.
  A new message then ran normally: the orchestrator's own middleware marks such a call as not
  carried out, as it does today after a client goes away.
- Approved and then stopped before the approved action started: the action did not run, no
  proposal was pending, and the decision was spent. The conversation shows the call as not
  carried out once the task is continued.

So after a stop is accepted, the step under way completes and is recorded, and no further
step starts (FR-022, SC-012). An action that is under way always finishes; an action that had
not started is not started, also when it was approved a moment before.

**The step limit.** LangGraph counts the limit per call of `astream`. Each segment is
therefore given what is left of `FREYJA_MAX_STEPS`: the limit less the steps the run has
taken, read from the thread's last checkpoint before the segment. LangGraph takes no limit
below 1, so a segmented run reports `step_limit` at the last boundary inside the limit, at
most two steps earlier than a plain run would (*run*: limit 9, a plain run ended at step 9
and a segmented one at step 7). The limit is never exceeded.

**Cost**: before each segment, one read of the thread's last checkpoint and one read of the
stop request. Both are small beside the model call or the tools that follow.

**The node names.** `model` and `tools` are the library's names (*run*: the graph's nodes are
`model`, `tools`, and five middleware nodes). `SegmentedGraph` checks that the graph it
is handed has both. At startup a graph that lacks one stops the service with an error naming
it; after a later rebuild, a run on such a graph ends with an error and the log names the
node. So an upgrade that renames one cannot silently leave runs that never reach a
boundary.

**Alternatives considered**: cancelling the run when a stop arrives, as a disconnect does.
It cuts actions off, which FR-022 forbids. Pausing only before a model call: a reply that is
under way when Stop is pressed would still have its tool calls run, a hand-off to an agent
that runs without approval among them. A check inside the orchestrator's middleware: that
code belongs to the other session, and it would change every run.

## R5. Asking for a stop

**Decision**: a stop is a mark on the thread's row in the index (R6), holding the owner of
the run it was asked for. The instance that runs the thread reads it between segments, so a
stop asked of any instance reaches the run (FR-022). A mark for a run that has ended is
ignored, because its owner is no longer the row's.

`POST /threads/{thread_id}/stop` answers 202 once the mark is set. It answers 409 `no_run`
when no run is in progress and 409 `not_stoppable` when the run was not started through the
new routes. A second stop of the same run answers 202 again and changes nothing (FR-022a).

When a run ends because of the mark, `run_stream` sees the graph end and sends `end` with
`completed`. `UnattendedRuns` knows the stop was applied and sends `end` with `stopped` in
its place, and records the run as stopped (R7). When the run ended by itself first, by
finishing, pausing, or failing, the mark was never applied and the run is recorded as it
ended.

## R6. The list of threads

**Question** (FR-002 to FR-005, FR-023): no route lists threads, and how a run ended is
recorded nowhere. Where does the list come from?

**Finding** (*verified*): every run on a thread, whoever starts it, takes the thread's lease
first and gives it back last: the three callers of `run_stream` (the two thread routes and
the delegation worker) call `runtime.locks.acquire`, and `run_stream` calls
`runtime.locks.release`. Nothing else is common to all runs.

**Decision**: one table, `freyja_threads`, one row per thread that has had a run, kept by a
new store, `ThreadIndex` (`runtime/thread_index.py`), with `PostgresThreadIndex` and
`InMemoryThreadIndex`, as the other stores have. The runtime wraps its `RunLocks` in
`IndexedRunLocks`, which passes every call on and, after a lease was taken or given back,
writes one row: the run's owner and when it started, or that it ended. A failure of that
write is logged and never fails the lease.

What a run left behind is read later, by the two routes that show it (R8): a row whose run
has ended and has not been looked at yet is **settled** by one read of the thread's state and
of its agent tasks, and the result is kept in the row. The read uses the same code a state
read uses today. A row whose run's owner still stands while the lease is gone belongs to a
run that died with its instance; it is settled the same way.

A thread the index has no row for, from before this feature or from a run whose row could
not be written, gets its row when it is opened: `GET /threads/{thread_id}` reads its state
and, when the thread has one, makes the row settled (FR-004, FR-023).

So the list is one query on one table, plus one read of a thread's state for each run that
ended since the list was last asked for. The number of threads that need a decision is a
count over the same table (FR-001).

**Rationale**: the lease is the one place every run passes, so runs started by another
client and by the worker are listed without changing either. Settling on read keeps the
release of a lease as fast as it is and keeps a slow or failing state read out of the end of
a run.

**Alternatives considered**: listing from the checkpointer's tables. They are the library's,
a thread's status would cost a read of its whole state at every listing, and how a run ended
is not in them. Recording from the routes alone: the worker's runs would be missed, and they
are the ones that pause while nobody watches.

## R7. A task's status, and what is known of a run

**Decision**: the service works out the status and returns it, so that the list, the
sidebar, and a task's page cannot disagree (principle III). From a settled row, first match:

1. A run holds the lease: `running`.
2. The thread's state has a proposal: `needs_decision`.
3. The run was recorded as failed: `failed`, with the code and the message of its error.
4. The run was recorded as stopped, or the thread's state has steps still to take:
   `stopped`.
5. An agent task of the thread is pending: `waiting_for_agent`.
6. Otherwise `done`.

A proposal wins over a recorded failure because the spec's order puts a decision first, and
because a continuation that failed before its action completed leaves the proposal pending
(001, edge case).

**What is recorded about how a run ended** comes from the run's last event, which only
whoever reads the stream sees. `UnattendedRuns` reads every event of the runs it drives, so
for a run started through the new routes the row holds `failed` with the error's code and
message, or `stopped`. For a run started through the existing routes or by the delegation
worker, nothing is recorded; its status is read from the thread's state alone. Such a run
that failed therefore shows as `stopped` when it left steps to take, and as what the thread
was before when it never began. Its error went to whoever read its stream, or to the log.

This narrows FR-003 as it was written, which gave the reason for every failed run. The spec
is changed in this pull request to say what is known (plan.md, "Changes to the spec").

**Whether an agent task is pending** is read when a row is settled, so it is as of the
thread's last run. Every way a pending agent task ends leads to a run on its thread, by the
delegation worker, and that run settles the row again.

**When a task last changed** is the time of the thread's last checkpoint, read when the row
is settled; while a run is in progress it is when that run started.

## R8. The API

Five routes in a router of their own, `api/thread_index.py`, with the prefix `/threads`. The
console says "task"; the API keeps "thread" (spec, Clarifications). Shapes are in
[contracts/openapi.yaml](contracts/openapi.yaml).

| Route | What it does |
|---|---|
| `GET /threads` | The list: at most 100 rows, the number that need a decision, and whether there are more. |
| `GET /threads/{thread_id}` | One row, for a task's page: status, error, whether the run can be stopped, whether a stop is under way. |
| `POST /threads/{thread_id}/runs/unattended` | Start a run that goes on without a reader. Same body, refusals, and events as `POST /threads/{thread_id}/runs`. |
| `POST /threads/{thread_id}/resume/unattended` | Decide a proposal; the continuation goes on without a reader. Same body, refusals, and events as `POST /threads/{thread_id}/resume`. |
| `POST /threads/{thread_id}/stop` | Ask the run in progress to stop (R5). |

The conversation, the proposal, and the agent tasks stay where they are,
`GET /threads/{thread_id}/state`. A task's page reads that and the row.

The two list routes contact no model, agent, or tool server and change no thread (FR-023).
They do write the index when they settle a row.

**Alternatives considered**: `/tasks/...`, which would give the API a second meaning of
"task" beside `agent_tasks`. A flag on the existing routes, which would need their handlers
changed.

## R9. Requests from another origin

**Finding** (*run*): the thread routes already refuse a body that is not sent as JSON, with
422, whichever content type it claims. A page of another origin cannot send JSON without
asking first, and is given no permission. So today such a page cannot start a run or send a
decision. A request marked `Sec-Fetch-Site: cross-site` that does arrive is served.

`POST /threads/{thread_id}/stop` takes no body, so a form on any page could send it.

**Decision**: the new router is given `refuse_other_origins`, as the `/llm` and `/agents`
routers are. The existing thread router is given the same dependency, which is one line in
`api/threads.py` (FR-024). A client that sends none of the headers a browser sends, such as
curl and the test client, is served as before (FR-027).

## R10. Keys and addresses in the new answers

The list returns two texts that come from threads: the beginning of a first message, which a
person typed, and the message of a run's error, which is the stream's own `error` event.
`run_stream` has already taken the profile's key out of that message before it is sent
(004 FR-021), and the index keeps what was sent. Nothing else of a thread is returned.

**Decision**: a test file of this feature, `tests/test_thread_index_nowhere.py`, drives the
new routes through tasks that finish, pause, and fail, with a recognizable key and a
recognizable agent address, and searches every answer with the helpers of
`tests/test_llm_secrets_nowhere.py` and `tests/test_agents_address_nowhere.py`. Those two
files are not changed: feature 005's Phase 9 rewrites the second. The three have to be joined
once 005 is done, and the pull request says so.

## R11. Web: reading a stream

**Decision**: `fetch` with `response.body.getReader()` and a `TextDecoder`, and a parser of
its own for the stream's `event:` and `data:` lines, in `web/lib/tasks.ts`. No dependency is
added. `EventSource` is not used: it only sends GET.

`web/lib/api.ts` gains one function, `stream`, beside `api`, so that it stays the one place
that calls the service. It answers refusals with the same `ApiError`.

A stream that ends without its last event, because the connection dropped, is not a failed
run. The page says the connection ended and follows the task by reading it (FR-013).

## R12. Web: a run that outlives the page that started it

**Question**: New task is on the list's page and the conversation is on the task's page.
Which one reads the stream?

**Decision**: a store outside React, `web/lib/task-runs.ts`, holds each run this browser tab
started: the events received so far and whether the stream has ended. Components read it
with `useSyncExternalStore`. Sending a new task starts the request, waits for the service's
answer, and only then goes to `/tasks/<id>`; a refusal is shown in the box, which keeps its
text (FR-008). The task's page finds the run in the store and shows it as it arrives.

A page that finds no run in the store for a thread that is running, after a reload or in
another tab, follows it by reading (FR-013).

The id is `crypto.randomUUID()` without its hyphens: 32 characters, which the service's rule
for a thread id allows.

## R13. Web: pages, components, and keeping up

- `web/app/tasks/page.tsx` is the list. `web/app/tasks/[id]/page.tsx` is a task. With Cache
  Components on, a page that reads a route parameter reads it inside `<Suspense>` (Next.js
  docs, `dynamic-routes.md`, "With Cache Components"); the page does that and hands the id to
  a Client Component.
- The list and the sidebar read `GET /threads` under one SWR key, as the Agents section and
  the sidebar share one, with a refresh every 15 seconds (FR-006).
- A task's page reads `GET /threads/{id}` and `GET /threads/{id}/state` together. It reads
  them again every 5 seconds while a run it is not reading is in progress, every 15 seconds
  while an agent task is pending, and once when a stream it is reading ends (FR-010, FR-013).
- Components under `web/components/tasks/`: the list, the new-task box, the task's view, a
  message, the proposal card, the agent tasks, the follow-up box, the Stop control, and the
  status.

## R14. Web: the proposal card

Each action shows its name, its server or agent, its description, and its arguments as
formatted JSON, all as text (FR-012, FR-014). Edit opens the arguments in a text box. The
page parses the text and refuses to send what is not a JSON object; nothing else is checked
in the browser (FR-015). The service's 422 is shown beside the box, which keeps its text.

Approve, Reject, and Stop are buttons outside any form, so Enter in a field presses none of
them, and each is disabled from its first press until the answer (FR-017).

A refusal with `proposal_mismatch`, `nothing_pending`, or `run_active` makes the page say so
and read the task again (FR-018).

## R15. Tests

**Service**, in memory, no network: the index store and its lease wrapper; the list and the
row through tasks in each status; an unattended run and resume beside the existing routes,
for equal refusals and events; a reader that leaves; a stop before a model call, before a
tool step, during an approved action, asked twice, asked too late, and asked of a run that
cannot be stopped; the step limit; the refusal of other origins on every thread route; the
answer's `Cache-Control` and ping interval; and the search of R10. The Postgres index has
opt-in tests under `tests/integration/`, against a database of this feature's own.

**Web unit** (Vitest, jsdom): the stream parser, the run store, each component, and the
sidebar's number.

**End to end** (Playwright, both browsers): each user story's console part, with the
service's answers handed to the page by a helper file of this feature,
`web/e2e/tasks-api.ts`. `web/e2e/api.ts` is not changed.

**What the end-to-end tests cannot show**: that a stream arrives as it is produced. They hand
a page its answers in the browser, whole, and the app under test forwards to an address
nothing listens on, which a test cannot serve from. Showing it automatically needs a second
address in `web/playwright.config.ts` and a stand-in behind it for every test. That file is
shared and its port is fixed by the owner, so this plan does not change it. FR-028 asked for
the proof in both browsers; R1 is that proof, made by hand, and the two things it rests on,
the header and the ping, are pinned by service tests. The spec's sentence is changed to say
so (plan.md, "Changes to the spec"), and the owner is asked in the pull request whether an
automated proof is wanted.

Existing end-to-end tests are handed no answer for `GET /api/threads`. Their pages get an
error from the web app's server for it, and the sidebar then shows no number (FR-006). No
existing spec fails on a request that got an error (*verified*: only `smoke.spec.ts` listens
for page errors, and a refused request is not one). Whether they all still pass is checked
by running them before the first web pull request.

## R16. What is changed outside this feature's own files

The third thing the spec named as not proven: can FR-021 and FR-022 be built without
changing a file of the session that builds features 005 and 006? **Yes.** None of its files
is changed: not `api/streaming.py`, `decisions.py`, `outcomes.py`, or `serialize.py`, nothing
under `agent/`, `delegates/`, or `llm/`, and no existing file under `runtime/` but
`runtime.py`.

Shared files, each with a small addition in a block of its own:

| File | Addition |
|---|---|
| `src/freyja_agent/runtime/runtime.py` | Two fields, `threads` and `unattended`. In `assemble`: the index is taken or made, the locks are wrapped, `UnattendedRuns` is made and closed on the way out. In `open`: the Postgres index. In `begin_run`: the graph is handed over as `SegmentedGraph`. |
| `src/freyja_agent/api/threads.py` | The router's `dependencies`, one line (R9). |
| `src/freyja_agent/api/app.py` | One router. |
| `web/lib/api.ts` | The `stream` function (R11). |
| `web/components/app-sidebar.tsx` and its test | The Tasks entry and its number. |
| `web/e2e/console.spec.ts` | The sidebar now lists three sections. |
| `tests/conftest.py` | Only if a fixture needs the index handed in; none is planned. |
| `README.md`, `AGENTS.md`, `web/README.md` | Paragraphs of this feature's own. |

No setting is added, so `config.py` and `.env.example` are not changed. No dependency is
added to either lock file. No CI file, `pyproject.toml`, `web/vitest.config.mts`,
`.gitleaks.toml`, or `web/playwright.config.ts` is changed.

`Runtime.begin_run` is the one line with reach: every run's graph passes through
`SegmentedGraph`. For a run that was not started through the new routes it calls the real
`astream` with the arguments it was given and yields what comes back; a test runs the
existing suite's scenarios through it to show nothing differs.

## R17. When the service stops

**Decision** (changed in the checklist review, finding F1): `UnattendedRuns.close` asks every
run it is driving to stop, by a flag of its own that `SegmentedGraph` reads between segments
beside the stop a person asks for, and waits for them. Each run ends at its next boundary:
the step under way completes and is recorded, `run_stream` releases the lease, and the
thread is settled as `stopped` (R7). `close` runs before the agents' links close and before
the database pool does.

So an ordinary stop of the service cuts no action off (FR-021). It waits for one step of
each such run and no longer; until now it waited for every run with a connected reader to
finish, since uvicorn's graceful shutdown has no time limit by default (README). Someone who
cannot wait for a step ends the process, which is the next case.

A service that is killed or fails ends a run wherever it is. An action under way can then
have taken effect without its result being recorded, as feature 001 accepts for any run. The
run's lease expires and its thread is settled as `stopped`.

**Alternatives considered**: cancelling the runs, as the delegation worker cancels its
deliveries, which was this plan's first decision. A run with a reader is not cut by an
ordinary stop today; a run without one must not be the worse for having no reader, and a
deploy would otherwise be the commonest way to cut an approved action off. A time limit on
the wait: it would need a setting, and it would turn a slow step into a cut one.

## Not verified

- A run through a real model provider that streams token by token. The scripted model
  answers in one piece; that `message` events pass the forwarding one by one follows from R1,
  where each event passed alone.
- A run of real length behind the forwarding. The longest stream read was 70 seconds.
- `SegmentedGraph` with a hand-off to a real A2A agent or a real MCP tool. The stand-ins are
  used in the tests of the implementation; nothing in a segment's boundary depends on what a
  tool does.
- The Postgres index. Its statements are written in the implementation and run by the opt-in
  tests; CI does not run them, so its lines count against the coverage number.

## Knowingly not covered

- A run that another client or the delegation worker started and that failed is not listed
  as failed (R7).
- A run cut by the service being killed or failing can leave an action without its result
  (R17). An ordinary stop of the service does not.
- No limit is set on how many runs go on at once (spec, Assumptions).
- A page opened after a run started does not read its stream (FR-013).
- An automated proof in a browser that a stream arrives as it is produced (R15).
