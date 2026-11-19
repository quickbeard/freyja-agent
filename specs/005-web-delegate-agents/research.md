# Research: Agents Section of the Web Console

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Decisions the plan rests on, each with its reason and what was set aside. Facts marked
*verified* were checked on 2026-11-19 against the code on `main` and the packages in `uv.lock`
(a2a-sdk 1.3.0, httpx 0.28.1, deepagents 0.7.32) and `web/pnpm-lock.yaml` (Next.js 16.4.6, swr
2.5.1), without a network and without contacting any agent. Facts marked *run* were seen in a
run of the real factory with a scripted model, in memory. Facts about Next.js come from the
documentation bundled with the installed version (`web/node_modules/next/dist/docs/`), as
`web/AGENTS.md` requires. What could not be checked, and what is knowingly not covered, is
listed at the end.

The draft of this plan was read by a reviewer that had not written it. Where a decision below
changed because of that reading, it says so.

## R1. Where saved agents are kept

**Decision**: one table, `freyja_agents`, and a one-row table, `freyja_agents_state`, that holds
a revision. A new store, `SavedAgentStore`, in `runtime/saved_agents.py`, with
`PostgresSavedAgentStore` for the service and `InMemorySavedAgentStore` for the suite, as the
profile store has. The columns are in [data-model.md](data-model.md).

The name and the destination are `UNIQUE` columns. The limit of 20 is checked under a
transaction-scoped advisory lock, as the profile limit is (`llm/store.py`, `_LIMIT_LOCK`,
*verified*). Two adds that conflict, on one instance or on two, cannot both be saved (FR-006).
A unique violation is turned into the store's own error with nothing of the database's text in
it, as the profile store does, because that text repeats the value.

**Rationale**: it is feature 004's shape for the same need, operator-managed configuration with
a revision that tells an instance its copy is old. The revision gets a table of its own, not a
column in `freyja_llm_state`, so that a change of agents does not look like a change of model
and each store stays the owner of its tables.

**Alternatives considered**: putting saved agents in the agent task store. Rejected: that store
is 600 lines about conversations, sends, and tasks, and saved agents have different rules. The
two stores meet in one place, R10.

## R2. Telling one agent from another under the same name

**Decision**: a saved agent has an `id`, new on every add, and an `address_version`, which a
move increases. Together they are the agent ref, `<id>:<address_version>`. A task records the
ref of the agent it was started with, in a new nullable column `agent_ref` of
`freyja_agent_tasks`. An environment agent has no ref: its tasks keep a null `agent_ref` and
follow its name, as today. A null ref is an environment agent's: a saved agent's link never
takes a task with a null ref for its own.

The column is added at startup, only when `information_schema.columns` does not show it, with
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` under a short lock timeout, and a startup error
naming the table when the lock cannot be had.

**Rationale**: three clarified rules need more than a name. A task belongs to the agent as it
was when the task started (FR-025): one equality on the ref. A proposal follows its agent
across a move and not across a removal and a new addition (FR-023): the `id`. A run in
progress may send to a removed agent no more (FR-022): the `id` says whether the agent the run
started with still exists. Environment agents get none of it, so their behavior is what it was
(FR-031).

This is the first time a table that exists in deployments gains a column. Adding a nullable
column takes no table rewrite, but it takes a lock that waits behind any long reader of the
table, such as a backup, and every query on the table then waits behind it. Looking first
means a service that already has the column never asks for the lock, and the timeout means
one that needs it fails with a clear error instead of stalling (changed after review).

**Deployment note**: every instance has to run this version before the first agent is saved.
An instance on the earlier version does not know saved agents, and its worker gives up every
task whose agent is not in its configuration, a saved agent's included.

**Alternatives considered**: comparing times, such as "was the task started before the agent
was last changed". Rejected: a run that started before a move can record a task after it.

## R3. The agent set, and one orchestrator from validation to the end of a run

**Decision**: a new `AgentRegistry` in `delegates/agent_registry.py` owns the agent links of one
instance. It holds the current `AgentSet`: the revision it was read at, the links in use with
each agent's approval choice, and the saved agents that are not in use with the reason for
each (FR-017). `refresh()` reads the revision, one single-row read; when it moved, it reads
the saved agents and makes a new set: a link whose ref is unchanged is kept, and a link is
made for each agent that is new or was moved. Making a link contacts nothing (R6). A refresh
logs a warning for each agent it finds not in use (FR-030).

`Runtime.begin_run()` replaces what `run_stream` did at its start. It refreshes the set, asks
the model manager for the orchestrator, which rebuilds through `build_agent` when the LLM
revision or the agent set moved, and returns one `RunContext`: the graph, the catalog, the
active profile, and the set, which it holds until the context is released.

**Every reader of the graph or the catalog between runs takes a context, under the thread's
lease where it has one.** The run and resume routes take it right after the lease and use it
for reading the thread's state, for the pending proposal, for validating the decisions, and
for the run itself; `run_stream` is given the context and no longer reads `runtime.agent`.
The worker takes one per delivery, and one per round of watching. The thread state route, the
catalog, and the agents list take one before they answer. A check takes one for as long as it
uses a link. A set that is no longer current and that nothing holds closes the links only it
had.

When taking a context fails, a run streams the same `start` and `internal` error it does today
when the active profile cannot be read, and nothing else runs (FR-019); a read falls back to
the last set the instance had, as the profile list does. The agents list is the exception: it
is made of what the store holds, addresses and versions included, so it answers with an error
when the store cannot be read and shows no earlier data as current (FR-004; corrected when the
tasks were written).

**Rationale**: found in review, and the most serious finding. Today the resume route validates
decisions against `runtime.catalog` and the run then calls `model_for_run()`
(`api/threads.py:124-141`, `api/streaming.py:129`, *verified*). With feature 004 the catalog
could not differ between the two. Now it can: another instance changes an approval choice,
this instance validates against its old catalog and runs on the new one. *Run*: a paused
hand-off whose agent has meanwhile stopped requiring approval, resumed with a reject that the
old catalog allows, ends with the message sent; and where one agent stopped requiring approval
and another started, a single approve meant for the first is applied to the second. Both are
the approval middleware working as built: it returns early when no call needs a decision, and
otherwise applies decisions by position. With validation and the run on one context, the
first case is refused as today ("no longer needs a decision") and the second fails in the gate
and is reported by R9.

Counting holders per set is a few lines; closing links on a timer would cut a long run's calls
or leak clients. A rebuild takes about 15 ms (004 research R1). The worker refreshes before it
claims tasks, which is required, not only timely: with an old set it would give up the task of
an agent another instance has just added.

**Alternatives considered**: one graph whose tools look the current agents up when called.
Rejected: the gate is configured per tool name when the graph is built, so a new agent's tools
would have no gate entry.

## R4. Starting when an agent cannot be used

**Decision**: `open_agents` gives way to the registry's start. It still refuses, with
`AgentStartupError`, what is wrong with an entry: a name that is also a tool server's, and a
tool name already taken (FR-018); the settings class already refuses a bad name, a missing or
non-local address, and an unknown field before that. Card problems no longer raise. The
registry makes a link for every environment agent and every saved agent in use, reads all
their cards at once within `FREYJA_A2A_STARTUP_TIMEOUT_SECONDS`, and for each that fails logs
a warning naming the agent and the reason and leaves the link not connected (FR-020).

A saved agent that conflicts with the environment gets no link and no tools, is listed as
`not_in_use`, and a warning says why: a name that is an environment agent's or a tool
server's, a tool name that is taken, or a destination that is an environment agent's (FR-017).

**Rationale**: confirmed in clarification. The line between the two kinds of failure is whether
anything has to be contacted to see it. Reading all cards at once keeps startup within the one
time limit whatever number of agents are down (SC-006).

## R5. Reading a card

**Decision**: one function, `read_card`, in a new `delegates/agent_cards.py`, used at startup,
by an add, a move, and a check, and when a link connects later. It requests
`<address>/.well-known/agent-card.json` with the link's own client, asks for the body
unencoded (`Accept-Encoding: identity`), follows no redirect, stops after one megabyte of the
body as received, parses it with the SDK's `parse_agent_card`, and applies the checks
`card_problem` makes today. It returns the card or raises a `CardProblem` with one of nine
codes, each with a sentence of the service's own
([data-model.md](data-model.md#card-problem-cardproblem), FR-007, FR-016). Anything else it
meets is a `CardProblem` too: `unreadable`.

**Rationale**: the SDK's `A2ACardResolver` does the same request but reads a body of any size,
logs the address and the whole card at INFO, and puts the address in every error it raises
(`a2a/client/card_resolver.py:186-219`, *verified*). Reading the response ourselves is about
thirty lines and gives a size limit and a failure that is already classified. The path
constant and the parser stay the SDK's. A card that names no input modes passes, as today.
Without the header httpx would decode a compressed body before the limit is counted (changed
after review).

## R6. Connecting a link, and a link that stops being usable

**Decision**: a link is built without contacting anything and starts not connected.
`connect()` reads the card (R5), builds the SDK client from it with the address pinned as
today, and marks the link reachable; one lock per link lets one caller read while the others
wait for its result. It is called at startup (R4); by an add or a move, which hand the
registry the card they just read so that it is not read twice; by a check; before a hand-off,
an answer, or a cancel on a link that is not connected, inside that call's own time limit;
and by the worker, once per round per link, for a link that is not connected and has a pending
task (FR-021, FR-026). When that read fails the worker checks none of the link's tasks that
round and logs one line for the link, not one per task.

A card read that fails, at a check or anywhere else, leaves the link not connected, so
hand-offs fail with the reason until a later read succeeds (changed after review: the draft
had no way back from connected). A link that is not connected sends nothing.

In a tool, whatever `connect()` raises becomes the tool's error result with the card problem's
sentence. An exception left to escape would end the step, and LangGraph keeps a failed step's
error with the thread, where no log rule reaches.

For a saved agent, connecting uses the card for the connection only. The details that are
shown and that describe its tools are the kept ones and change only by an operator's move or
check (FR-015). When an environment agent's card is read for the first time after the tools
were built without it, the registry marks its set as changed on this instance, so the next run
describes the tools from the card (FR-021).

**Rationale**: with agents that can be down at startup, and agents another instance added, a
link has to come up later without an operator. A link that was connected and then fails a
call is unchanged: httpx connects again by itself, as feature 003 relies on. "Not connected"
means only that this instance holds no usable card from this address, which keeps an
environment-only deployment's calls as they were.

## R7. What is checked right before a message is sent

**Decision**: every tool of a saved agent reads the agent from the store, by id, immediately
before it records the send and sends:

- the agent is gone, also when another agent now has its name: an error result saying it was
  removed;
- the tool needed no decision when its run was built, and the agent now requires approval: an
  error result saying so;
- the tool's link is of an earlier address, and another agent now has that destination: an
  error result saying the address the run started with is another agent's now.

Nothing is sent, and the run continues (FR-022). Whether the tool needed a decision is fixed
in the tool when it is built, not read from the link, which a later set may share.

**Rationale**: confirmed in clarification, and widened after review. The draft checked only
tools that run without a pause, on the ground that a gated tool's continuation starts on the
current set. But a continuation that started before a removal still holds the removed agent's
tool, and SC-004 asks for zero messages of any kind after a removal is answered. One
primary-key read before a send that already writes to the send ledger twice is cheap. It reads
the database, so it holds on every instance from the moment the change is committed, and it
asks no model anything (principle VI). An environment agent cannot change while the service
runs, so its tools read no saved agent; they do read the proposed call (R8).

**Order inside a tool**: the send ledger first, so a step that runs again returns the result
it recorded and is never refused after the fact. Then the task's ref, for an answer or a
cancel (R10). Then `connect()` if the link needs it (R6), which may take most of the call's
time limit. Then the proposed call (R8) and this check, with nothing awaited between the check
and the send's first write but the write itself. A change that is committed after the check
and before the request leaves is not recalled, as the spec says.

The third rule closes a path the review found: an agent that runs without approval is moved,
and an agent that requires approval is added at the address it left; a run in progress would
keep sending the first agent's messages there, unseen.

## R8. Proposed calls

**Decision**: a small table, `freyja_agent_calls`, kept by the agent task store beside the send
ledger, with one row per call of an agent's tool, saved or from the environment, that a model
has proposed: the thread, the graph namespace the call belongs to, the tool call id, the
agent's name, the saved agent's id or null, and whether the call needed a decision then.

`AgentTasksMiddleware` writes the rows in `awrap_model_call`, right after the model answered,
on the orchestrator and on its subagent: it deletes the rows of this thread and namespace and
inserts one per call of the reply. A row that cannot be written fails the model call, and with
it the step. The tool deletes its row when it finishes its send. A run that starts with input,
which a thread with a paused step refuses, deletes whatever rows the thread still has.

Two rules read it:

- **A proposal does not follow a re-added name** (FR-023). When an agent's tool runs, a saved
  agent's or an environment agent's, and finds a row whose `agent_id` is not the link's, a
  null on either side included, the call is an error result saying the agent the message was
  proposed for was removed. An environment agent's tool makes the same comparison because a
  restart can give the environment a saved agent's name while a proposal for the saved agent
  waits (corrected when the tasks were written). This covers every call of a
  paused step, the ones that needed a decision and the ones that did not. A call with no row,
  such as one proposed before this feature was installed, follows the name, as today.
- **A blocked resume names its agent** (R9).

**Rationale**: a pause stores the tool's name and arguments and nothing about which agent held
the name. The middleware already runs around every model call on both graphs and already
reads its thread from the run. *Run*: the hook sees the reply's tool calls
(`ModelResponse.result`), on the subagent as on the orchestrator; the id in the reply is the
id the tool receives as `InjectedToolCallId`; and the row is written before the approval
middleware pauses the run.

The namespace is what the model's node and the tool's node share: the run's
`checkpoint_ns` with its last segment taken off. *Run*: on the orchestrator the model runs in
`model:<id>` and its tools in `tools:<id>`, so both give the empty namespace; in a subagent
the model runs in `tools:X|model:<id>` and its tools in `tools:X|tools:<id>`, so both give
`tools:X`. The key is the thread, that namespace, and the tool call id (changed after review:
the draft keyed by the thread and the tool call id alone, and the send ledger's own notes say
a provider does not keep that id unique on a thread; two subagents in one step could collide).

Replacing a namespace's rows at each reply, and clearing at a run with input, is what keeps a
row from outliving its step: a call that is rejected never reaches its tool, and one with
arguments the tool node refuses does not either. A background run's retry starts with no input
and continues a step that is waiting, so it clears nothing. Deleting a row only once the send
is finished means a step that stops between the two finds its row again when it runs again.

**Alternatives considered**: (a) Compare the proposal's time with the agent's. Rejected for
the reason in R2. (b) Keep the id in the message that holds the call and read it back from the
graph's state. Rejected: it rests on fields of a stored message surviving every library in
between. (c) Record it when the run pauses. Rejected: `run_stream` sees the proposal, not the
calls of the step that needed no decision, and not a subagent's step.

**What a re-added agent's old proposal accepts**: what the tool under that name allows now,
since the pause holds no id to say otherwise; whatever is decided, nothing is sent. The spec's
edge case said "approve and reject as for any removed agent" and is corrected with this plan.

## R9. A resume that a tightened choice blocks

**Decision**: when a resume fails because the paused step holds more calls that need a
decision than decisions were given, `run_stream` ends it with the `error` code
`approval_now_required` and a message naming the agents it found: the thread's proposed calls
(R8) that needed no decision when proposed and whose tool is gated in the run's catalog. When
it finds none, it ends with `internal`, as today. Nothing from the step runs and the proposal
stays, as today.

**Rationale**: today this ends with `internal` and no reason
(`tests/test_a2a_gate.py:394-410`, *verified*), which was tolerable when only a restart with
another configuration could cause it. An operator can now cause it from the console (FR-023).
The failure is the approval middleware's: `"Number of human decisions (1) does not match
number of hanging tool calls (2)."` (`langchain/agents/middleware/human_in_the_loop.py:818`).
*Run*: the same sentence reaches the caller for a subagent's step, and the proposal is still
there afterwards. The handler recognizes that sentence; a test fails the day the library words
it differently, and the fallback is today's behavior.

**Alternatives considered**: refuse before the stream, with an HTTP error. Rejected: for a
subagent's step the calls are in a nested checkpoint that nothing reads today. Letting the
gate find it and explaining it afterwards is exact for both.

## R10. Giving tasks up

**Decision**: `SavedAgentStore.move` and `SavedAgentStore.remove` do everything in one
transaction: check the version, change or delete the row, give up every pending task whose
`agent_ref` is one of this agent's, move the revision, and return how many tasks were given up
(FR-024). The count the list shows for a saved agent is taken the same way. A given-up task
gets its outcome ready, so a background run tells the thread, as for a task given up today.
The text for a removal is today's; for a move it says the agent was moved and may still be
working where it was, and offers no cancel.

The Postgres store writes to `freyja_agent_tasks` itself, in the same transaction; the
in-memory store is given the in-memory task store and calls a new method on it,
`give_up_pending`. That is the one place the two stores meet.

Further rules keep a task with the agent it was given to (FR-025):

- The worker gives up a claimed task whose `agent_ref` is not the ref of the link in use under
  its name. This catches a task a run in progress started at an earlier address after the
  move, the tasks of a saved agent that is not in use, and a task of an environment agent
  whose name a saved agent now has.
- A hand-off with a `task_id`, and a cancel, for a task whose `agent_ref` is not the link's is
  an error result before anything is sent. A cancel for a task given up for its age stays
  possible (003 FR-022): that task still has its agent.
- A hand-off updates a task's row only while the task is still pending. Today an answer
  updates it whatever its state (`a2a_tools.py:441-445`, *verified*), so an answer under way
  during a move would write over the row the move just gave up.
- The worker records what a check found only while the task is still pending. Today it also
  records over a given-up task, which the cancel path needs and keeps; for the worker it
  would let a check that was under way at the earlier address turn a given-up task back into
  a reported outcome, and the thread would be told twice (SC-010). `record_outcome` takes the
  states it may write over as an argument.
- A reply that names a task id the name already has under another ref is treated as a
  malformed reply: the row belongs to another agent and another thread's history.

**Rationale**: "together or not at all" needs one transaction. Selecting by ref and not by
name is what leaves an environment agent's tasks alone when a saved agent that it overrides is
removed (changed after review: the draft selected by name). A background run reports a
given-up task only while a usable model is active (004 FR-016a); until then it is listed as
given up and waits.

## R11. One form for an address

**Decision**: `canonical_address(url)` in `config.py`, beside `is_local_host`. It refuses
anything but `http` and `https`; a host that is not `localhost` or a loopback IP address; a
user name or password; a query; a fragment; and a path with an empty segment, a `.` or `..`
segment, a `%`, or a backslash. It returns the scheme and host in lowercase, the port always
written (80 or 443 when left out), an IP address in its usual compressed writing, and the path
without a trailing slash. `destination(url)` is the port and the path: any way of writing this
machine counts as the same host. Every refusal is worded by the function; the parser's own
errors are caught, because they repeat what was entered.

A saved agent's `url` is always the canonical form, so "the address changed" is a comparison
of two strings, and an entry that comes to the saved form is not a move (FR-006, FR-009).

**Rationale**: whether two addresses are the same decides whether a card is read and every
pending task given up. httpx rewrites what it is given: the host in lowercase, a default port
dropped, and dot segments resolved, so that `/x/..` is requested as `/` while its string
differs (found in review, *run*). A query or a fragment is refused because the card's address
is the entered address with a path added to it: a caller could otherwise choose the path the
service requests on any local port.

Environment entries are not changed: they keep their rule and their spelling.

## R12. The address leaves the service nowhere else

**Decision**: one scrubber, in `delegates/addresses.py`, that knows every agent address this
process has been given and removes each in the forms FR-028 lists: as kept, its host and
port, and its port after `localhost`, `127.0.0.1`, and `[::1]`. It also removes the pair in
which the operating system's errors name a connection, `('127.0.0.1', <port>)` and its
`localhost` and `::1` forms. An address is registered before it is first requested, the
address of an add that is then refused included, and is kept until the process ends, with a
bound on the number of addresses that were never saved.

It is applied in three places.

1. **Text the service words or passes on.** `AgentLink.describe` uses it. Today it removes
   the address as configured and its host and port, and nothing written another way
   (`a2a_agents.py:163-175`, *verified*).
2. **Card details.** Every text of a card goes through it before it is kept or returned, for
   saved and environment agents. Because it knows every address, a card that names another
   agent's address is cleaned too.
3. **Log lines.** A log record factory, installed once per process, rewrites every record as
   it is made. The message and each of its arguments are cleaned in place, an argument that is
   not text being replaced by its cleaned text only when cleaning changes it, so a record
   keeps the number and kind of arguments its formatter expects. The traceback is formatted
   and cleaned into the record's own traceback text, and the record's reference to the
   exception is dropped when cleaning changed that text, so a handler that formats it again
   cannot bring the address back. In records from the HTTP and agent libraries (`httpx`,
   `httpcore`, `a2a`) the port number is also removed wherever it stands as a number of its
   own, because `httpcore` logs the host and the port apart.

**Rationale**: httpx logs every request with its address at INFO, and the SDK logs the card's
address; both are hidden today only because the root logger is at WARNING (`api/app.py:23`,
*verified*). FR-028 asks for no line at any level from any library. A filter on a handler does
not see a handler added later; a filter on a logger does not see its children's records. The
record factory sees every record of the process.

Changed after review, each seen in a run: collapsing a record's arguments into its message
breaks uvicorn's access log, whose formatter unpacks them; a refused connection's own error
reads `Connect call failed ('127.0.0.1', 9998)` and sits in the cause chain of a traceback any
logger may write; and a card read made for an add logs before any link exists.

The proof (R20) reaches agents through the suite's in-process transport, where httpcore never
logs. So the scrubber has a unit test on the libraries' real lines, and the one test that
serves a stand-in on loopback (`test_a2a_transport.py`) searches its log at DEBUG too.

The reasons of R5 need no cleaning: they are fixed sentences. What an agent writes in its own
replies and progress updates is not searched, as the spec says.

**Alternatives considered**: raising the level of the three library loggers. Rejected: it
silences their lines for every other use, the model clients' included, and a library that adds
a logger later is not covered.

## R13. Every request goes straight to the configured address

**Decision**: a link keeps one `httpx.AsyncClient` with an explicit transport, no redirects,
and no reading of proxy settings; a move makes a new link with a new client (R3).

**Rationale**: with a transport given, httpx does not read proxy variables
(`httpx/_client.py:685`: `allow_env_proxies = trust_env and transport is None`, *verified*), so
this is already true and only needs a test to stay true (FR-027). A client's cookie jar sends
a cookie back by host and ignores the port, so a client kept across a move would hand an agent
at a new port whatever the old one set; a new client per address cannot (FR-025).

## R14. Changes from a stale page

**Decision**: a saved agent has a `version` that every change of its address or approval choice
increases. `PATCH` and `DELETE` name a version; the store changes the row only
`WHERE id = ... AND version = ...`, and a request that changes no row is answered
`agent_changed` when the agent still exists and `agent_not_found` when it does not (FR-012).
A check that replaces the card details does not move the version.

In the console, the version a form sends is the one its fields were filled from when it
opened, not the one the list shows now, and a form sends only the values the operator changed.
When the list shows a later version while a form is open, the form takes its confirmation
back and says the agent was changed elsewhere.

**Rationale**: with the later save kept, a page that still shows an agent as running without
approval can put it back there under a confirmation about an address. The console half was
found in review: the list is read again whenever the window gets the focus, the form's fields
are filled once, and a form given the live version would send old values under a new version,
which is the case the version exists to stop.

## R15. The check

**Decision**: `POST /agents/{id}/check` reads the card (R5) and connects the link when it can;
when it cannot, the link is left not connected (R6). For a saved agent whose card can be used
and differs from the kept details, it replaces them and moves the revision, so every
instance's next run describes the tools from them. For an environment agent it replaces the
link's details on this instance and marks the set as changed here. The result is not recorded
when the agent's ref changed while the check ran, and the answer says so (FR-013, FR-014).

The limits are FR-014's: one check per agent, and four card reads in all on an instance. A
check takes its agent's slot and one of the four; the card read of an add or a move takes one
of the four only, so a move of an agent that is being checked goes ahead, as the spec's edge
case says. `CheckSlots` in `llm/check.py` counts this way already; its refusals speak of
profiles and tests, so it is given its words when it is made. A card read the console asked
for is limited to the lesser of the startup time limit and twenty seconds, so its answer
arrives before the web app's server stops waiting, which it does after thirty seconds
(`next/dist/server/lib/router-utils/proxy-request.js:37`, *verified*).

## R16. The routes

**Decision**: a new router, `api/agents.py`, under `/agents`: list, add, change, remove, and
check ([contracts/openapi.yaml](contracts/openapi.yaml)). It uses `NoInputRoute` and
`refuse_other_origins`, which move from `api/llm.py` to a new `api/guards.py` that both
routers import. The refusal's message names what is being changed, so the function is given
that word; nothing else about it changes. An address is only ever in a body. A refusal never
repeats it: `NoInputRoute` drops the refused value, and the address rule's messages are the
service's own (R11).

Every change applies on this instance at once, by the same best-effort step as an activation
(004 Phase 4): the change stands whatever that step finds, and every other instance takes it
up at its next context (R3).

An agent that cannot be used is refused with 409 and the code `agent_unusable`, as
`profile_unusable` is, with the card problem's code beside it. Every 422 stays a list of field
errors.

**Rationale**: the convention in `AGENTS.md` asks every router that changes operator-managed
configuration to refuse other origins the same way; sharing the function is how it stays one
rule.

## R17. The console

**Decision**: the Agents section mirrors the LLM section file for file: `web/lib/agents.ts`
with the types and the calls, and `web/components/agents/` with the list, the card, the form
for adding and changing, the removal dialog, and the check's result. The list is read with SWR
under one key, `/agents`.

- **Confirmations** are a step of the form, as the active profile's is: Save shows what the
  change means, and Confirm is a button of its own that ignores the second press of a double
  press (004 T085). Adding with approval required has no such step. The texts are FR-011's.
- **Live copies**: a confirmation shows the agent found by id in the list as it is now, the
  pending count included. The version that is sent is the form's own (R14).
- **Stale and gone**: `agent_changed` and `agent_not_found` both have the list read again;
  `isGone` in `web/lib/api.ts` learns the second code.
- **No answer**: when a request that changes something ends with the code `unreachable`, the
  form says the outcome is not known and has the list read again (FR-004). A read that fails
  keeps saying the service cannot be reached.
- **Card text** and every reason are rendered as text nodes; nothing builds markup or a link
  from them (FR-002).

## R18. Warnings about agents that are down

**Decision**: three places, all from the same reading of the list (FR-001a). The card carries
a warning badge and its reason. `web/components/agents/agents-warning.tsx` renders a warning
above the cards that names each agent. The sidebar's Agents entry carries a `SidebarMenuBadge`
with the number and a text for assistive technology. The sidebar reads the list through the
same SWR key as the Agents section, so on any section the console asks once when it opens, and
whenever the Agents section reads the list the sidebar shows the same answer. When the read
fails the sidebar shows no badge.

**Rationale**: confirmed in clarification. SWR shares one cache entry per key between the
components that use it. `SidebarMenuBadge` is already in the generated
`web/components/ui/sidebar.tsx` (*verified*).

**Consequence for the existing end-to-end tests**: every page now asks for `/api/agents`. The
specs of feature 004 hand a page no answer for it, so the request fails as it does with the
service stopped, and the sidebar shows nothing. They keep passing; the shared setup gains an
answer for the route where a spec counts requests.

## R19. No console inside another origin's page

**Decision**: `web/next.config.ts` gains `headers()`, which sets, on every path,
`Content-Security-Policy: frame-ancestors 'none'` and `X-Frame-Options: DENY` (FR-029).

**Rationale**: `headers()` is the documented way to set a header on every route, with the
source `/:path*` (bundled docs, `05-config/01-next-config-js/headers.md`, *verified*). It is
the one place the web app's configuration lives (principle IV). Both headers are sent because
`frame-ancestors` is the current rule and `X-Frame-Options` is what an older browser reads.

## R20. Service tests

**Decision**: the suite keeps its two seams. `make_a2a_app` gains saved agents: a
`SavedAgentStore` shared between instances as the task store is, and stand-ins found by
address as well as by name, since a saved agent is added at an address while the app runs.
`StreamingASGITransport` already records every request, which is how a test knows that only a
card was read (SC-011). Store contract tests run against the in-memory store and, opt-in,
Postgres, by the re-import pattern of `tests/integration/`. Two instances are two apps on one
set of stores; a change through one followed by a resume through the other is tested for each
decision, because that is where R3's finding lives.

`tests/test_agents_address_nowhere.py` is the proof for SC-008, built like
`test_llm_secrets_nowhere.py`: one agent on a recognizable port, every route of the Agents
section with its refusals, runs that complete, pause, and fail, a check, a move, and a
removal, then a search of every response outside the Agents section, every stream with
`debug`, everything the checkpointer holds, and every log record at DEBUG with its traceback.

## R21. Web tests

**Decision**: Vitest unit tests under `web/__tests__/`, laid out as the code, with `stubFetch`.
End-to-end tests under `web/e2e/`: `api.ts` gains `AgentService`, a stand-in for the agents
routes that keeps what a page saved, as `ProfileService` does, and `answerAgents` for specs
that only read. Each user story's console part has an end-to-end test (FR-032), in Chromium
and Firefox. The frame rule is tested there too: the response headers, and a page of another
origin that tries to show the console in a frame.

## R22. Compatibility

**Decision**: with no saved agent and every environment agent answering, nothing changes but
what FR-031 lists.

- `tests/test_a2a_startup.py`: the tests at lines 89, 96, 107, 118 (three cases), and 163
  expect startup to fail for a card problem. They become tests that the service starts and
  lists the agent as unreachable with the reason. That is five test functions, seven cases.
  The two entry tests, lines 136 and 147, are unchanged.
- `tests/test_a2a_gate.py:394`: expects `internal` when an environment agent lost its
  read-only mark across a restart. The proposed call's row is in the task store the two apps
  share, so the test now expects `approval_now_required` naming the agent. The matching test
  for a tool server's tool keeps `internal`.
- `web/__tests__/app/agents/page.test.tsx` and one test in `web/e2e/console.spec.ts` expect
  the placeholder's sentence and are replaced.
- The text of a startup warning about a card is now one of R5's sentences. No test outside the
  list above reads the old texts.
- `GET /tools` keeps its shape exactly: the card claims gain no field. A card's texts and
  skills are cut (FR-002) and cleaned of addresses (R12) there too, which changes nothing for
  a card within the limits that names no address. For an agent this instance has not tried,
  `/tools` says what it says today for an agent it could not reach, with a reason saying it
  has not been tried yet. `/tools` now reads one row of the database before it answers; when
  that read fails it answers from the last set it had.

## Not verified

- **The proposed-call rows as a whole.** The pieces were seen in a run: the hook on the
  subagent, the ids, the namespaces, the order. The table, its replacement per reply, and its
  clearing were not built.
- **The approval middleware's sentence** is matched as it reads in the installed version. A
  later version may change it; the test of R9 is what notices.
- **Rebuild time with forty agent tools.** 004 measured 14 to 57 ms with the tools of its day.
- **The record factory beside pytest's capture and under uvicorn's own configuration.** It was
  tried with the standard handlers and with uvicorn's access formatter.
- **`frame-ancestors` in both browsers** against the built app, and that `headers()` applies
  to pages served beside the `/api` rewrite. The end-to-end test is what proves it.
- **The column check and the lock timeout** on the Postgres version of `docker-compose.yml`.
  The opt-in Postgres tests cover it.
- **SWR's shared cache between the sidebar and the list** under Cache Components: that the
  sidebar, which is outside the page, and the list read one entry.

## Known and not covered

- **Two saved addresses that end at one endpoint.** A message goes to the path the agent's
  card names for its interface, on the saved host and port. Two agents saved at different
  paths of one port whose cards name the same interface path are two names for one endpoint.
  FR-006 compares the port and the saved path. Catching this would mean comparing what cards
  say, which an agent controls.
- **Tracing.** The agent SDK records errors on OpenTelemetry spans when a deployment turns
  tracing on. Nothing here configures tracing, and nothing here cleans a span. FR-028 lists
  responses, streams, threads, and log lines.
- **A record made without the factory**, by `logging.makeLogRecord`, which no library in the
  lock uses for its own lines.
- **An instance on the earlier version** beside instances on this one (R2).
