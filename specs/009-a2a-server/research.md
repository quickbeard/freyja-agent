# Research: Receive Tasks from Other Agents over A2A

Decisions made for the plan, each with its reason and what was not chosen. "Verified" says what
was read in the installed code; "to verify" says what a phase has to show with a test before it
relies on it. Versions: a2a-sdk 1.3.0 (protocol 1.0), deepagents 0.7.32, langchain 1.4.7,
langgraph 1.2.18, uvicorn 0.54.0, as `uv.lock` has them on 2026-11-25.

## R1. Freyja implements the SDK's `RequestHandler` itself

**Decision**: a class of Freyja's own implements `a2a.server.request_handlers.RequestHandler`
and is given to the SDK's `create_jsonrpc_routes`. The SDK's `DefaultRequestHandler`,
`AgentExecutor`, `QueueManager`, and task stores are not used.

**Rationale**: the default handler runs each task as an in-process executor in a registry.
A restart drops the executor and leaves a task in its store that nothing drives. A received
task lasts as long as an approval takes, has to be read from any instance, and its state is
what the thread says (R7), so a second store of state would only be a copy that can disagree.
The interface has eleven methods, each `(params, context)`; five are served and six refuse
(R12).

**What Freyja then owns**, and the default handler would have done: checking required fields
(the SDK's `validate_request_params` is reusable), refusing a subscription to a task that has
ended, scoping by caller, and catching every exception. That last one matters: the dispatcher
sends the text of an exception that is not the protocol's to the client as an internal error
(`jsonrpc_dispatcher.py`, around lines 356 and 604). Every method of the handler therefore
ends in one place that turns anything unexpected into the protocol's internal error with a
fixed sentence, and logs the exception cleaned.

**Alternatives**: the default handler with a store of Freyja's own (the executor still lives
in one process); the SDK's `server/cluster` module (shares snapshots between replicas, and
still has one process run the executor).

## R2. JSON-RPC only, protocol 1.0 only

**Decision**: the JSON-RPC binding, at `POST /a2a`, and the card at
`GET /.well-known/agent-card.json`, on both addresses. `enable_v0_3_compat` stays off.

**Rationale**: it is the binding Freyja reaches its own delegate agents with. ARTEX and
heimdall were looked at on 2026-11-25: both serve 1.0, and neither has a client yet, so no
caller exists that needs 0.3. A request without the `A2A-Version` header is read by the SDK
as 0.3 and refused with its version error; the SDK's client sends the header.

**Alternatives**: the REST binding as well (a second surface to protect and test, for no
caller that asks for it); gRPC (another listener).

## R3. The routes exist only while the feature is on

**Decision**: the application's lifespan adds the two routes, after it has opened the runtime
and when the runtime's settings have the feature on. With it off they are not there, and the
two paths are answered by the application as any path it does not have, for every method.
The handler is made in the same place, with the runtime it serves.

**Rationale**: the application is created before its runtime, and with it its settings, is
opened; the suite gives each application a runtime with settings of its own
(`tests/conftest.py`), so `create_app` cannot know. Routes added in the lifespan are there
before the first request, and are taken out again when the lifespan ends, so that an
application whose lifespan is entered twice never serves a route bound to a closed runtime.
FR-001 and SC-002 ask that nothing changes while the feature is off, and nothing does: no
route, no answer, and no existing test.

**The other option, and why not**: routes that are always registered and answer `404`
themselves while off. `tests/test_threads_other_origins.py` walks every route that changes
something on an application with default settings and expects `403` for a request marked as
another origin's; an always-registered `POST /a2a` would have to answer `403` there while
off, which an unknown path does not, or the existing test would have to change. A route for
`POST` alone would also answer `405` to a `GET`.

**The two walkers** (that file and `tests/test_allowed_hosts.py`) are run once more on an
application with the feature on, by new tests that use their helpers: the two routes are then
held to the same two rules as every other route.

## R4. Two addresses: the application's own routes, and a second application on a second listener

**Decision**: the outside listener is a second, small Starlette application with the same two
routes and nothing else, served by a `uvicorn.Server` that the main application's lifespan
starts and stops, in the same process and event loop.

- The lifespan binds the socket itself and passes it to `serve(sockets=[sock])`. A bind that
  fails raises there, and startup stops with an error that names the setting. Left to
  uvicorn, a taken port calls `sys.exit` from inside a task.
- The server is a subclass whose `capture_signals` does nothing, so the main server keeps the
  process's signal handlers. Its `Config` has `lifespan="off"` and `log_config=None`.
- Stopping sets `should_exit`, then awaits the task, with `timeout_graceful_shutdown` set, so
  that a caller's open stream does not hold shutdown up without limit. The runtime is closed
  after the listener, as today.
- Its access log is off. The main server's stays as it is, and is cleaned of callers'
  tokens like every log record (R15).

**Which address a request arrived at** is a property of the application object that serves
it: the outside application is built with `outside=True`. Nothing a request says of itself
is read for it (FR-002).

**Rationale**: one runtime, started however the service is started (principle III).
`uvicorn.run` in `main()` and `uvicorn freyja_agent.api.app:app` both enter the lifespan.
The outside application has none of Freyja's other routes because it was never given them,
which is a stronger statement than a filter in front of the main application.

**The suite** drives the outside application as an object, through the in-process transport
it already has (`tests/asgi_stream.py`). One transport test binds a loopback port.

**Alternatives**: one application on two sockets with a filter by socket (every route of the
API one mistake away from the outside); a second process (a second way to start the service).

**Known and accepted**: at the service's own address, A2A is exactly as reachable as the
thread routes, the console's forwarding of `/api/...` included. Whoever reaches the console
can already start a run.

## R5. Who is calling

**Decision**: `Callers`, built from the settings, turns an `Authorization` header into a
caller's name. It compares the presented token with every named caller's by
`hmac.compare_digest`, without stopping at a match, on the bytes of both.

- Outside listener: no header, another scheme than `Bearer`, or no match: `401` with
  `WWW-Authenticate: Bearer`, a fixed JSON body, before the request's body is read.
- Own address: no header is the local caller, named `local`; a header that matches is that
  caller; any other header is the same `401`.
- The check runs in Freyja's route, in front of the SDK's dispatcher. The caller's name then
  travels in the `ServerCallContext` as its `user`, by a subclass of the SDK's default context
  builder, which must keep `state["headers"]` (the version check reads it).

**The size of a request** is bounded in Freyja's route, before the SDK's dispatcher reads the
body, which it reads whole: a `Content-Length` above 256 KiB, or a body that grows past it,
is answered `413`. A message of 32,000 characters fits several times over.

**The order of the checks**, the same at both addresses: the origin (`403`); the credential
(`401`); the size (`413`); then the protocol, in the SDK's dispatcher: the version, the
operation; then, in Freyja's handler: the message's form, the conversation and the task
named, the limits. Each is decided before the next is looked at, and all of them before a
thread is made or a model is asked.

**Refused credentials are logged sparingly.** Anyone who reaches the outside listener can
send requests without a token. One warning is written for the first refusal, and after that
at most one in ten seconds for each of the two addresses, the service's own and the outside
listener, saying how many were refused since (FR-028). Counted per client address, the
warnings and the record of them would grow with the number of clients.

The refusal of other origins runs first on both addresses, by the rule the thread routes
use (`refuse_other_origins`). `AllowedHosts` covers the own address as it covers every route
there, and is not put in front of the outside application (spec, Assumptions).

**Rationale**: a fixed rule before anything of the protocol is read (principle VI, FR-005).

**Alternatives**: Starlette's `AuthenticationMiddleware` (one more moving part for one
comparison); the token in the body or a query (FR-005 forbids both).

## R6. A second, strict build of the orchestrator for callers' threads

**Decision**: while the feature is on, `ModelManager` builds two orchestrators for each
revision: the one it builds today, and a strict one in which every agent tool is classified
as gated, whatever the agent's approval choice. `Runtime.begin_run` and `begin_read` take the
thread's id, look up whether a caller started the thread (R8), and return the strict build's
graph and catalog for one that a caller did.

- `Runtime._build` already classifies each agent's tools with `gated=agents.gated(link)`; the
  strict build passes `gated=True`, and the catalog, the allowed decisions, and the gate
  follow from the classification. `build_agent` itself gains two parameters, for the file
  backend (R16) and for the name a thread is told it has (below).
- **The check at send time follows the build.** `_still_allowed` asks today whether a saved
  agent still runs without approval, from the agent's own record. On the strict build a call
  that no person approved or edited is refused there, whatever the record says, so that the
  last check before a message leaves agrees with the gate.
- Both builds have the same nodes, so a thread's checkpoints are read by either. A caller's
  thread is run and resumed on the strict build only.
- **Fail closed**: `SegmentedGraph` is told which build it wraps. Before it runs a thread it
  asks whether a caller started it; a caller's thread on the other build raises, and so does
  a lookup that fails. It wraps `astream`, the one entry every run uses
  (`take_context`, then `run_stream`); the other ways to run a graph (`ainvoke`,
  `astream_events`, and their sync forms), which nothing uses today, raise on it, so that
  none can become a way around.
- **Two builds, kept apart.** The runtime keeps what it last built for each build
  (`agent`, `catalog`, the segmented wrapper), where it keeps one of each today, so that
  `last_built` never answers with the other build's catalog.

**The thread's id is not given to a model on a caller's thread.** `ThreadContextMiddleware`
tells the model its thread's id on every call, and a caller could ask Freyja to repeat it.
On the strict build the middleware tells an alias, `c-` and the first 16 hexadecimal
characters of the SHA-256 of the thread's id: stable for the conversation, the same from
every instance, and not an id of anything in Freyja's API (FR-018). A thread's id is 32
random hexadecimal characters, so the alias cannot be turned back into it by trying ids.

**Rationale**: FR-013a asks for a pause, which only the gate in the graph gives. A separate
build keeps the catalog, the wording of a proposal, and the tools truthful with no knowledge
of threads inside them.

**Alternatives**: the middleware's per-call condition, `InterruptOnConfig.when`
(langchain 1.4.7), on one graph. It reads the run's config, so it could work, but the
catalog, the allowed decisions, and the check at send time would each have to learn the
thread's kind as well, and a condition that answers differently at a resume makes the resume
fail. A refusal at send time in place of a pause (not what FR-013a says).

**The subagent is covered by the same two rules.** The gate that is generated from the
classification goes to Freyja's subagent as well as to Freyja (`build_agent`), and the
subagent is given the same file backend object (R16). So on a caller's thread a subagent's
message to a delegate agent pauses too, and a subagent of a named caller's task works in
that caller's area.

**With the feature off, a caller's thread is not run.** The strict build and the file
backend exist only while the feature is on, so a thread a caller started must not be run
without them. Every context taken for a thread looks the thread up, on or off: one
single-row read by `thread_id`. For a caller's thread, with the feature off, the thread
routes answer `409` with `a2a_server_off` to a message or a decision, the worker leaves the
thread alone as it leaves a paused one, and `SegmentedGraph` refuses it as it refuses any
caller's thread on the other build.

*Changed by the review of the tasks (2026-11-25).* This paragraph said that a service
started with the feature off asks once whether any conversation exists, and looks nothing up
afterwards where none does. That holds for one instance and not for several on one
database: an instance started with the feature off and an empty table would run a
conversation another instance, with the feature on, starts afterwards, on the ordinary
build, with its hand-offs ungated and a named caller in the shared workspace, as during a
rolling restart that turns the feature on. The read is made every time instead.

**Cost**: a second graph is compiled at each rebuild, only while the feature is on. Taking a
context for a thread costs one single-row read, on or off; with the feature off nothing else
is built or read.

## R7. A received task's state is read from its thread, and kept once it has ended

**Decision**: one function, `standing`, says where a caller's thread stands, from four reads:
the lease, the thread's state, its agent tasks, and its row in the index.

| Read, first match | The received task |
|---|---|
| The task's run has not taken the lease yet | working |
| The lease is held | working |
| The state holds an interrupt | working, waiting for a person: the names of the actions |
| An agent task is awaited (below) | working, waiting for an agent: the agents' names |
| The row's note is this task's (below) and says `failed` | failed, with the reason mapped from the note's code |
| The row's note is this task's and says `stopped` | canceled, stopped before it finished |
| The thread recorded nothing since the task started | failed, interrupted |
| The state has a next step (`unfinished`) | failed, interrupted |
| Otherwise | completed, with the last reply of Freyja's as the result |

A task that ends is written once, with its state, reason, and result, and never read from
the thread again (FR-016, FR-012).

**Three fences**, because the four reads are not one, and other runs come and go:

- *The task's own run.* A message is accepted, and then its run takes the lease; between the
  two the lease is free and the thread looks at rest, with the previous reply as its last. So
  the task's row records that its run started, written once `start` has the lease, together
  with the checkpoint the thread had then (`started_at`, `base_checkpoint`). Until that is
  written the task is working. A row that never gets it, because the process died in
  between, is failed as interrupted after a minute.
- *An end is written only from a consistent look.* Before an end is written, the lease is
  read again and the state's checkpoint id is compared with the one the look used. A lease
  that is held, or a checkpoint that moved, means a run started meanwhile, the worker's or a
  person's decision; nothing is written, and the task is looked at again later. The write
  itself names the state it replaces, so two instances that look together write one end.
- *Whose note.* The index keeps a note of how the last console run ended, and a new run does
  not clear it (`_RUN_STARTED` leaves `noted`); only settling the row does, which the list
  routes do. So a note is this task's only when it is not older than the task's start and
  not older than the state's last checkpoint. An earlier run's failure, or a note that a
  later run has since recorded a step past, is not read.

**Which clock.** A note's time is written by the instance that ran the run, from its own
clock, as the checkpoints of that run are (007). `started_at` is written from the clock of
the instance that started the task's run, which is the instance that then runs it, so the
first comparison is between two times of one clock for the message's own run. A
continuation that a person's decision starts may run on another instance. The instances'
clocks are assumed to agree to within a few seconds, as the lease's time limits already
assume; where they do not, a note of such a continuation can be taken for an earlier run's,
and its task then ends as failed and interrupted where it would have been failed with its
reason or canceled. No task ends as completed by that mistake, and none stays working.

"The thread recorded nothing since the task started" is the same checkpoint as
`base_checkpoint` with no note of the task's: a run that died before its first step. Without
that row a follow-up would complete with the reply before it.

**An agent task is awaited** when it is working, when its outcome is ready and not yet in
the thread, or when a run to report it is to be tried again (`state == "working"`, or
`not outcome_delivered`, or `followup_next_at` set). A task that waits for input and whose
question was reported is not awaited: Freyja's reply carries the question, the received task
completes with it, and the caller answers with another message (spec change, plan).

**The two points the checklist left for the plan** are settled here.

- *A stopped run and one cut off, for runs that are not console runs.* Only a console run can
  be stopped between two steps: the stop route refuses every other (`not_stoppable`), and the
  worker's runs are cancelled where they are when the service stops. So a run that is not a
  console run and leaves its thread unfinished was cut off or failed, and the task is failed.
  A console run that was stopped notes `stopped` in the index, and the task is canceled.
- *No task stays working with nothing left* (SC-014). A run of the worker's that fails is
  tried again and then given up; either way the agent task stops being awaited, the thread
  is unfinished, and the table gives failed.

**When it is read**: when the instance that streams a run to a caller sees it end; when any
run on a caller's thread gives its lease back (R9); when a caller reads or subscribes; when
the limits are counted; and by a sweep of the tasks that are not released (R9).

**Rationale**: the thread is the one record every instance shares and every kind of run
writes. Deriving from it is what makes a task readable after a restart with no state of the
A2A layer to restore.

**Alternatives**: keeping the state as events arrive (lost with the process, and blind to
runs the worker or another instance drives); `status_of` as it is (it puts a noted failure
before an awaited agent task, which FR-016 does not, and reads a note whoever's it is).

## R8. Two tables, and what is written before a run starts

**Decision**: `freyja_a2a_conversations` and `freyja_a2a_tasks`, with an in-memory store of
the same protocol for the suite ([data-model.md](data-model.md)). A message is accepted in
one transaction, under one advisory lock: the conversation is found or made, with a new
thread id; the conversation's last task must have ended; the two limits are counted; the
task's row is written. Only then is the run started, by the function the thread route uses, which takes the lease
first. It is that function, under the lease, which refuses a thread that is not at rest: a
run in progress, a proposal pending, and, for a caller's thread, an agent task awaited. A
run that is refused removes the task's row again, and the message is refused as "not now".

That a caller started a thread is the conversation's row, written before the first run and
required: when it cannot be written, nothing starts (FR-012). The index of threads is not
changed: its writes are best effort (007), which is the wrong kind of record for this. The
list of threads reads which of its rows are callers' in one more query, while the feature
is on.

**Rationale**: the limits, the one-task-per-conversation rule, and the mark have to hold
when two requests arrive together, on one instance or two. One lock around a few statements
is the least that does.

**Alternatives**: a column on `freyja_threads` (best effort, and written after the lease);
the SDK's database task store (its model, not ours, and scoped by its own owner string).

## R9. Told when any run on a caller's thread ends, and a sweep behind it

**Decision**: `IndexedRunLocks.release` calls a function it was given, after the lease is
back and the index was told, with the thread's id. While the feature is on, the function
starts a task of the A2A service's own that looks at the thread (R7), applies R11, and wakes
the followers of the thread's task on this instance. It never holds the release up, and its
failures are logged.

Behind it, each instance looks every 30 seconds at the received tasks that are not released
(`released_at` null): at most as many as the total limit, a few reads each. The sweep does
what the function does, and is what makes the rest true when the function did not run:
the process stopped or died with the task under way, or the look failed. Its first round,
when the service starts, also cancels every working task of a named caller that the
settings no longer name, as that caller's cancel would (R11): a deployment that takes a
caller away takes away what it asked for.

**Rationale**: every run gives its lease back through this one method, whoever started it:
the console, a decision, the worker, a client of the API. It is the one place that sees a
run of the worker end on a caller's thread, which no stream of the A2A layer follows. The
function is what makes an end reach a follower at once: at a run's `end` event the lease is
still held, so a look made then says working. The sweep is what makes sure.

**Alternatives**: the sweep alone (a caller that follows would wait for it); the function
alone (a canceled task whose function was lost is never read again, since its caller has no
reason to, and its proposal and its place in the limits would stay); a hook in the worker
(one of several kinds of run).

## R10. The run a message starts is the console's run

**Decision**: each of the two handlers of `api/threads.py` that run a thread is split into a
function that takes the runtime and the route that calls it. The A2A service calls the
function, and hands the events to `UnattendedRuns` exactly as
`POST /threads/{id}/runs/unattended` does, through one shared function.

`UnattendedRuns` gains two things:

- `join(thread_id)`: one more reader of a run in progress on this instance, from the events
  that follow. This is how a subscription, and the stream of a send, read a run, also the
  continuation a person's decision started in the console.
- `start(..., stop_first=True)`: a run that is to stop at its first boundary, for R11. The
  mark in the index is written when the run's owner is known, which is after it started; a
  flag on the run is there from the start.
- A second thing `should_stop` asks, for a caller's thread: whether the conversation's last
  task was canceled by its caller. A console run on such a thread stops at its next boundary
  whether or not the index was marked. The index's mark is written at a run's first event; a
  cancel that arrives before it, while a person's decision already holds the lease, would
  otherwise find no run it can stop (FR-022).

Where the functions live: the two that start a run and a resume move out of the route module
into `api/runs.py`, which `api/threads.py`, `api/thread_index.py`, and the A2A service all
import; no module imports a route module.

**Rationale**: everything FR-009 asks of the run is what a console run already is: it goes
on without a reader, is stopped only between two steps, is noted in the index, and has its
events cleaned of agents' addresses.

## R11. Cancel, and a proposal nobody is waiting for

**Decision**: a cancel writes the task as canceled first, which is what its caller is
answered with. Then it acts on the thread as it stands:

- a console run in progress: stopped at its next boundary, because the run asks whether the
  task was canceled (R10), and by the index's mark where the run is already marked;
- a proposal pending, with no decision taken for it: rejected by rule (below);
- a run of another kind in progress, or an agent task awaited: nothing.

**Rejecting by rule** is a resume with a reject for every action, each with the fixed reason
"The requesting agent canceled the task.", started as a console run with `stop_first`. The
gate records the rejections and routes on: to the model, or to the tools step when a call
that needs no approval shares the reply. There is a boundary before either, so the run stops
there. The thread is then unfinished and noted as stopped, and a later message continues
from it as from any stopped run.

It is applied in one more place: whenever a caller's thread is looked at (R9) and holds a
proposal while the conversation has no task that is still working. That covers a cancel that
arrived during a step whose reply proposed an action, and a run of the worker's that comes to
a proposal after a cancel (FR-022a).

**A subagent's proposal is different, and bounded.** The boundary is one of Freyja's own
graph; a subagent is compiled apart and runs inside one tools step. After its proposal is
rejected, the subagent's model is asked again within that step, may work on up to its step
limit, and may propose again; the run then pauses again, and is rejected again. FR-021 holds
as written: no action of a rejected proposal runs, and the run stops at the first boundary
of Freyja's own. What the subagent does in between is what any step under way does when a
stop is asked.

**It is tried a bounded number of times.** A rejecting run can fail without recording
anything: no usable model is active, the run's context cannot be taken. Its own release must
not start the next attempt at once. The task's row counts the attempts and when the next may
be made (`reject_attempts`, `reject_after`): at most five for a task, at least 30 seconds
apart. After that the proposal stays, a person can reject it, and nobody can approve it
(below).

**A decision a person already took** is found as the resume route finds it
(`decisions_taken`). Where there is one, nothing is rejected and nothing is resumed: the
proposal stays, and the console says that the requesting agent canceled. A person who sends
those same decisions again continues the thread, as today.

**Nobody approves in between** (FR-022a): the resume function, once it holds the lease,
refuses a request for a caller's thread whose conversation has no task that is still
working, with `409` and the code `requester_gone`, unless every decision of the request is a
reject or the request repeats decisions already taken. Only that guard is needed for safety;
rejecting by rule is what takes the proposal out of the list.

**A cancel and a decision together** (FR-022) are ordered by the thread's lease, which every
resume takes first, with the guard checked under it. The decision that takes the lease first
runs, as a console run, and stops at its first boundary because the task is canceled; or the
rejection takes it, and the decision is refused as one for a proposal that is no longer
pending.

**A cancel acts only on the canceled task's own thread**, which the task's row names and the
service made. No operation of a caller carries a thread's id.

**Alternatives**: cancelling the run's task (cuts an action off; principle I, 007);
rejecting and letting the model reply (asks a model for words nobody reads).

## R12. Refusals, each in one named way

| Refusal | Answer |
|---|---|
| A credential that is missing or wrong | HTTP `401`, `WWW-Authenticate: Bearer`, before the protocol (R5) |
| A page of another origin | HTTP `403`, code `other_origin`, as the thread routes |
| A request larger than 256 KiB | HTTP `413`, before the body is read whole (R5) |
| A task that is not the caller's, or does not exist | `TaskNotFoundError` (-32001) |
| A cancel of a task that has ended | `TaskNotCancelableError` (-32002) |
| A subscription to a task that has ended; listing tasks | `UnsupportedOperationError` (-32004) |
| Push notifications, in any method or in a send's configuration | `PushNotificationNotSupportedError` (-32003) |
| The extended card | `ExtendedAgentCardNotConfiguredError` (-32007) |
| A message that holds something other than text, or accepts no text | `ContentTypeNotSupportedError` (-32005) |
| What is wrong with the message itself: no text, too long, a task named, a conversation id too long, a hand-over count that is no whole number or has reached the limit | `InvalidParamsError` (-32602), with a fixed sentence |
| Not now: the conversation takes no message, a limit on tasks in progress, too many streams of one task | `UnsupportedOperationError` (-32004), with a fixed sentence and a reason code |

**This settles the second point the checklist left**, and changes the spec: FR-026a asked for
one error for every other refusal of a message, and the plan uses two, so that a caller can
tell a message that will never be taken from one to send again later (spec change, plan).
The protocol has no error for "not now"; -32004 is the nearest it has, and the reason code
travels in the error's data, where the SDK puts it in an `ErrorInfo`.

**What an answer's time may say.** A task that is another caller's and one that does not
exist are answered with the same bytes; the time each takes is not made equal. A task's id
is 32 random hexadecimal characters that only its caller was ever given, so there is no id
to try the difference on.

**Alternatives**: a task in the protocol's rejected state (a task that was never made and
cannot be read back); an error of Freyja's own (the SDK maps an unknown subclass to the
internal error).

## R13. What a caller is sent, and from which events

**Decision**: a follower of a received task is one loop. While a console run of the thread
is in progress on this instance, it reads the run's events (R10) and translates them;
otherwise it waits to be woken by a run's end on this instance (R9), or for
`FOLLOW_SECONDS`, then looks at the thread (R7) and sends what changed. It ends when the task
has ended, when its client goes, or when the service stops.

| Run event | Sent to the caller |
|---|---|
| `update` of `model` whose reply asks for actions | a working status naming them |
| `update` of `tools` | a working status naming what completed |
| `message` marked as a piece of Freyja's own reply | an artifact chunk, appended to the one artifact `reply` |
| `interrupt` | a working status: waiting for a person's approval, with the actions' names |
| `end`, `error` | the thread is looked at (R7): the last status, and the whole reply as the artifact when the task completed |

- **Names only.** A status names tools as the catalog names them, an agent's tool as
  "agent" and the agent's name, and a subagent as a subagent. Arguments, results, and the
  text of a tool's or an agent's message are not read into a status at all (FR-018).
- **Freyja's own reply.** The stream's `message` events carry more than Freyja's reply: a
  subagent's pieces, the messages a tools step returns, the arguments of a call as they are
  written, and the model call that summarizes a long conversation. The run loop can tell: a
  chunk comes with its namespace, its node, its type, and the library's mark of an internal
  call. On a run of a caller's thread, which is a run on the strict build (R6), a `message`
  event gains `metadata.reply`, true only for text of an AI chunk from Freyja's own `model`
  node that is not an internal call. Only the text of those is sent to a caller (FR-017,
  FR-018). Runs of every other thread send their events as before, so nothing changes while
  the feature is off.
- **The whole reply at the end** replaces the artifact, so a caller that joined late or
  missed chunks ends with the right text.
- **Cleaning.** Events of a console run are cleaned of agents' addresses already. The result
  read from the thread is cleaned again where it is sent, of addresses and of stored keys,
  and of callers' tokens (R15).
- **Through another instance**, or for a run of the worker's, there are no events: the loop
  sends each change of state and of the status message, and the result (FR-017).

A task has at most four followers at once on one instance; a fifth there is refused as "not now" (R12). The spec
does not ask for the number; it bounds what a caller can hold open.

## R14. A send that does not ask for a stream

**Decision**: the run is started, and the answer waits for the task to end or for
`FREYJA_A2A_SERVER_WAIT_SECONDS`, 30 by default, whichever comes first; with
`configuration.return_immediately`, not at all. The answer is the task as it stands.

**Rationale**: FR-011, confirmed in clarification. The wait is a follower (R13) that sends
nothing and stops at the first of the two.

## R15. Tokens: where they are, and where they must not be

**Decision**: callers are `FREYJA_A2A_SERVER_CALLERS`, a JSON object of names, each with a
`token`, held as a secret value that neither prints nor serializes. Validation is at startup
and names the setting and the caller, never a value.

Freyja's own code never puts a token into a log line or an answer. Two texts can still
carry one, and both are cleaned of every named caller's token, by its exact text:

- a caller's own message, before it is used, so that a caller that pastes its token does not
  put it into a thread;
- every log record of the process, through the record factory that already cleans records
  of agents' addresses (`install_log_scrubber`). The web server's access log writes a
  request's path with its query, and a caller that wrongly sends its token there would
  otherwise be logged, refused or not (FR-028). `tests/test_a2a_server_nowhere.py` searches every answer, stream,
thread, and log line for two recognizable tokens, with the tools of `tests/nowhere.py`.

The two tokens are values `.gitleaks.toml` already lets through, of the shapes
`sk-recognizable-key-0001` and `sk-recognizable-other-0002`; each is a valid token by FR-004.
That file is not changed.

## R16. A named caller's file area

**Decision**: the strict build (R6) gets a backend of Freyja's own, `CallerFiles`, in place
of the one `FilesystemBackend`. For each call it reads `configurable["freyja_caller_files"]`
from the run's config (`langgraph.config.get_config()`, the pattern deepagents' own
`StateBackend` uses) and delegates:

- the empty string (the local caller): the shared workspace's backend, the same object the
  other build has;
- no key at all: an error, and nothing is read or written. The strict build never falls
  back to the shared workspace by omission;
- a caller's name: a `CompositeBackend` whose default is a `FilesystemBackend` rooted at
  `<files dir>/<name>` in virtual mode, and whose route `/skills/` is the shared skills
  directory behind a wrapper that reads and refuses every write, edit, and delete with an
  error result.

`SegmentedGraph` puts the key into the config of a caller's thread, in the place where it
looked the caller up (R6), so no run of such a thread lacks it.

The files directory is `FREYJA_A2A_SERVER_FILES_DIR`, `caller-files` by default, beside the
workspace and not inside it: a thread on the shared workspace, the local caller's included,
does not see a named caller's files through Freyja either. A caller's name is already a safe
directory name (FR-004). Startup stops when the files directory is the workspace, is inside
it, or contains it.

**What else could reach outside a thread, and does not.** The file tools include `execute`,
a shell; it works only with a backend that offers one, and neither the workspace's backend
nor `CallerFiles` does, so it answers with an error on every thread. The graph is given
LangGraph's store, and no tool or middleware of the orchestrator reads or writes it today:
`build_agent` configures no memory, and the file backend is not a store backend. A tool that
came to use the store would have to be scoped by caller on the strict build; a test of the
strict build's tool list makes sure such a tool is noticed.

**Verified**: deepagents 0.7.32 takes a backend instance only; factories were removed. Every
file tool and the skills middleware use the one backend object. `FilesystemBackend` in
virtual mode refuses `..`, re-roots an absolute path, and resolves a path before it checks
that it is inside the root.

**Read again by the plan's reviewer**: the middleware reaches a backend only through its
methods; its two `isinstance` checks only choose where large results are kept; evicted
results and history go through the backend; and both `asyncio.to_thread` and the pool the
sync `glob` uses copy the context, so `get_config()` gives the run's config there.

**To verify with a test, in the phase that builds it**: each of those, on a run; that the
skills are loaded for a caller's thread through the route; and what a path that leaves the
root by a link gives the model.

**Alternatives**: one build per named caller (as many graphs as callers at each rebuild);
deepagents' permission rules for the skills (fixed per build, and checked in the tools, not
in the backend).

**The image**: `Dockerfile` makes `workspace/cases` writable for the service's user. The
files directory needs the same, in the phase that builds this.

## R17. The hand-over count

**Decision**: the count is `metadata["freyja.hops"]` of a message, a whole number of zero or
more; a message without it has passed through none. The task's row keeps it. For a caller's
thread, `SegmentedGraph` puts the count of the conversation's last message into the run's
config beside the files key, and the agent tools add `count + 1` to the metadata of every
message they send. Messages from every other thread are sent as today.

A protobuf `Struct` holds numbers as doubles: a value that is not a whole number, is
negative, or is no number is refused (R12); one at or above `FREYJA_A2A_SERVER_MAX_HOPS`,
3 by default, is refused.

## R18. Settings

`FREYJA_A2A_SERVER` (off), `FREYJA_A2A_SERVER_CALLERS` (none),
`FREYJA_A2A_SERVER_HOST` and `FREYJA_A2A_SERVER_PORT` (no default; the outside listener is on
when both are set), `FREYJA_A2A_SERVER_URL` (the address for callers; required with the
listener), `FREYJA_A2A_SERVER_WAIT_SECONDS` (30), `FREYJA_A2A_SERVER_CALLER_TASKS` (5),
`FREYJA_A2A_SERVER_TASKS` (20), `FREYJA_A2A_SERVER_MAX_HOPS` (3),
`FREYJA_A2A_SERVER_FILES_DIR` (`caller-files`). Each is in
[contracts/settings.md](contracts/settings.md) with its rules, and goes into `.env.example`
with the phase that reads it.

The prefix keeps them apart from `FREYJA_A2A_AGENTS` and its limits, which are about the
agents Freyja calls.

## R19. One dependency declared, nothing installed

The SDK's server modules need its `http-server` extra, which is `starlette` and
`sse-starlette`. Both are installed already, as dependencies of the service itself, and the
suite imports the server modules today through the development group, which names the extra.
The service would import them from its own code, so `pyproject.toml` should name
`a2a-sdk[http-server]` among the runtime dependencies. That changes `pyproject.toml` and the
metadata in `uv.lock`, and installs no package that is not installed today.

**This needs the owner's word** before the first phase that imports the server modules.
Nothing in this plan's pull request changes either file.

## R20. The console

The list of threads and a thread's row gain `caller`: null, or the caller's name and whether
the caller canceled its last task ([contracts/openapi.yaml](contracts/openapi.yaml)). The
console words `local` as "an agent on this machine". The list, a task's page, and a
proposal's card say who started the task; the page has no box for another message and says
why; it says when the requesting agent canceled, and that a proposal on such a task was
rejected by that cancel and not by a person. The thread itself holds the rejection as the
approval library words every rejection, with the fixed reason; the console's sentence is
what tells the two apart (FR-021). The service refuses a message to a caller's
thread through its own routes with `409` and `caller_thread`, in the two routes, and not in
the function the A2A service calls (R10).

## R21. Tests

- The caller is the SDK's own client over the in-process transport, wrapped in
  `tests/a2a_caller.py`, which also runs as a small command for the quickstart.
- `make_a2a_server_app` in `tests/conftest.py` builds an application the production way with
  the feature on, callers, the stand-in agent, and the scripted model, and gives the outside
  application beside it.
- The two tables have opt-in tests under `tests/integration/`.
- The console's part is Vitest and Playwright, with `TaskService` answering rows that have a
  caller.

## What was not looked into

- Which HTTP status a client of the SDK gives a `401`: the SDK's client is used in the tests
  of the outside listener, which will show it.
- Rate limits for wrong tokens, which the spec leaves to the deployment.
