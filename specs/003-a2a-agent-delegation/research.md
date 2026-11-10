# Research: A2A Agent Delegation

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Findings about packages already in `.venv` (deepagents 0.7.32, langgraph 1.2.18, langchain-core
1.7.0, httpx 0.28.1, starlette 1.10.0) were read from the installed code. The A2A SDK is not in
`.venv` yet, so it was installed into a throwaway virtual environment in the session scratchpad
(`a2a-sdk[http-server]==1.3.0`), and its effect on the lock was measured on a scratch copy of
`pyproject.toml` and `uv.lock`; neither file in the repository changed. Paths are relative to
`site-packages/`. Items marked "probe" were confirmed by running a throwaway script against that
code: a stand-in agent built with the SDK's own server classes, served in-process, and the
official `helloworld` sample read from `a2aproject/a2a-samples`. Every unknown from the plan's
Technical Context is resolved below.

## R1. Client library: the official `a2a-sdk` 1.x

**Decision**: add `a2a-sdk>=1.3,<2` as a runtime dependency and talk to agents through its
client (`a2a.client`). Add `a2a-sdk[http-server]` to the `dev` group for the stand-in agent
(R16). No agent framework is imported.

**Rationale**:

- `a2a-sdk` is the A2A project's own Python SDK. 1.3.0 (2026-10-27) speaks protocol 1.0 and
  keeps a compatibility transport for 0.3 agents (`a2a/compat/v0_3/`), chosen per agent from
  the card (`a2a/client/client_factory.py`, `_find_best_interface`).
- Its client covers every operation the spec needs and nothing it forbids: `send_message`
  (streaming or not), `get_task`, `cancel_task`, `subscribe`
  (`a2a/client/client.py`). Push notification configuration exists in the API and is never
  called, because push notifications are out of scope.
- The client runs on an `httpx.AsyncClient` the caller can supply (`ClientConfig.httpx_client`),
  which is the one seam the tests need (R16), and which lets Freyja own redirects and time
  limits.
- It adds seven packages to the current lock (measured on a scratch copy): `a2a-sdk`,
  `aiologic`, `culsans`, `google-api-core`, `json-rpc`, `proto-plus`, `wrapt`. `protobuf`,
  `googleapis-common-protos`, `httpx`, `pydantic`, `starlette`, and `sse-starlette` are already
  present. The `http-server` extra adds nothing beyond those.
- Constitution principle II is about protocols, not libraries: the SDK is a protocol client and
  server toolkit with no agent loop and no model client.

The pin stays below the next major because the types are generated from the protocol's
protobuf definition and the 0.3 to 1.0 change renamed most of them. A bump is a deliberate
step verified by the transport test (R16).

**Alternatives considered**:

- `python-a2a` 0.5.10: requires `openai`, `anthropic`, `boto3`, and `langchain` at install
  time, which brings model clients and an agent framework into the orchestrator process
  (principle II).
- `fasta2a` 2.1.1: a server framework for exposing an agent; its client side is thin, and it
  follows its own schema rather than the generated protocol types.
- `a2a-json-rpc` with `a2a-cli`/`a2a-server` (last released 2025-08): protocol 0.2/0.3 only.
- A hand-written JSON-RPC client: version negotiation, the 0.3 compatibility layer, and the
  streaming framing would all be Freyja's to maintain.

## R2. Agent configuration

**Decision**: one setting, `FREYJA_A2A_AGENTS`, a JSON object from agent name to entry, parsed
into `A2aAgentConfig` (`url`, `read_only`). The full shape and the refusal rules are in
[contracts/agent-config.md](contracts/agent-config.md). Six more settings carry the limits:

| Setting | Default | Meaning |
|---|---|---|
| `FREYJA_A2A_AGENTS` | `{}` | The agents; empty means none |
| `FREYJA_A2A_CALL_TIMEOUT_SECONDS` | `30` | How long one hand-off, answer, or cancel waits within a run (FR-009, FR-017) |
| `FREYJA_A2A_STARTUP_TIMEOUT_SECONDS` | `20` | Per agent, for reading its card at startup (SC-005) |
| `FREYJA_A2A_POLL_INTERVAL_SECONDS` | `10` | How often pending tasks are checked and outcomes delivered (FR-019, SC-003) |
| `FREYJA_A2A_TASK_MAX_AGE_SECONDS` | `86400` | When a pending task is given up (FR-021) |
| `FREYJA_BACKGROUND_RUN_ATTEMPTS` | `3` | Attempts for one background run, the first included (FR-027) |
| `FREYJA_BACKGROUND_RETRY_SECONDS` | `60` | Wait between attempts (FR-027) |

**Rationale**:

- The entry has two fields because the spec gives an agent two properties: where it is and
  whether its hand-offs are reviewed. `read_only` is a boolean per agent, not a list, because
  Freyja gates what it sends to an agent, not which of the agent's skills it uses.
- The name follows the tool server pattern (`^[a-z][a-z0-9_-]{0,31}$`), and agent names and
  server names share one namespace, so a proposal's origin is never ambiguous.
- The address rule is the one `config.is_local_host` already implements for tool servers:
  `localhost` as written or a loopback IP literal, no lookup, an IPv4-mapped literal judged by
  the IPv4 address it carries. The URL must be `http` or `https`.
- `extra="forbid"` and `hide_input_in_errors=True`, as for `McpServerConfig`, so a typo fails
  and an error never repeats the entry.
- The poll interval of 10 s leaves room within SC-003's one-minute bound for a tick that waits
  for a free thread or a slow agent. The retry interval of 60 s with three attempts gives a
  provider outage a few minutes to clear without leaving a thread silent for long.

**Alternatives considered**: a read-only list of skill ids per agent (the gate cannot see which
skill an agent uses for a message); reusing the `FREYJA_MCP_*` time limits (an agent's task and
a tool call have different expected durations, and the spec treats them separately).

## R3. Reading the card and holding the connection

**Decision**: each agent gets one `AgentLink` that owns an `httpx.AsyncClient` for the life of
the process: `follow_redirects=False`, the SDK's version header, and the call time limit as
its timeout. At startup the link reads the card from
`<url>/.well-known/agent-card.json` with `A2ACardResolver` on that client, checks it, and builds
the SDK client from a copy of the card whose interface URLs are rewritten onto the configured
address. All links start concurrently, each bounded by the startup time limit.

**Card checks (FR-003)**, each a startup failure naming the agent:

- the card cannot be fetched or parsed, or the fetch is redirected;
- `security_requirements` or `security_schemes` is non-empty, which means the agent requires
  authentication;
- `default_input_modes` is non-empty and names no `text/*` type, which means it does not accept
  text messages;
- no `supported_interfaces` entry uses the `JSONRPC` binding.

**Rewriting the address (FR-002)**: the card says where the agent is reachable
(`AgentInterface.url`). Probe: a card served at `http://localhost:9999` that advertised
`http://elsewhere.example:1234/` was accepted by the SDK, and the SDK would have sent every
request there (`ClientFactory.create` passes `selected_interface.url` to the transport). Freyja
therefore keeps only the path of each interface URL and joins it to the configured scheme, host,
and port before handing the card to `ClientFactory.create`. Host and port from the card are
never used, which also handles the common case of a card advertising `0.0.0.0` or a container
name.

**Rationale**: one client per agent keeps connections pooled, and httpx reconnects by itself
after the agent restarts, so an agent answering again is usable without a restart (FR-009). The
redirect rule matches feature 002 and needs no code: httpx does not follow redirects unless
asked.

**Alternatives considered**: `ClientFactory.create_from_url` (follows the card's address);
calling the JSON-RPC transport directly with the configured URL (bypasses the SDK's 0.3/1.0
negotiation); one client per call (a new connection for each poll of every pending task).

## R4. The hand-off and cancel tools

**Decision**: each agent contributes two tools, both carrying the agent's classification:

| Tool | Arguments | Purpose |
|---|---|---|
| `handoff_to_<agent>` | `message` (required, 1 to 32,000 characters), `context` (optional, up to 32,000), `task_id` (optional) | A new task, a follow-up, or, with `task_id`, the answer that continues a task waiting for input |
| `cancel_<agent>_task` | `task_id` (required) | Cancel a pending or given-up task |

The argument schemas are pydantic models, so the existing edit path (`_validated_by_model` in
`api/decisions.py`) validates edits and refuses unknown keys. `task_id` is pinned: an edit that
changes it is refused, because an answer or a cancel stays bound to its task (FR-013). The
cancel tool has no editable argument, so it allows approve and reject only. The tool
description is built from the card: the agent's name, its description, and one line per skill
(name and description), cut at 2,000 characters.

**Rationale**:

- The approval gate is configured per tool name (`interrupt_on`), so a cancel must have a tool
  of its own per agent to be gated like a hand-off to that agent (FR-011). A single cancel tool
  for all agents would need its classification decided per call, which the gate cannot do.
- Answers go through the hand-off tool with `task_id`, so they are gated exactly like a
  hand-off, as clarified on 2026-11-09.
- Both names are checked for uniqueness by the existing catalog rule, so a collision with a
  tool server tool or one of Freyja's own fails startup naming it (FR-003).

**Alternatives considered**: one tool with an `action` argument (an edit could then turn a
cancel into a hand-off unless the action were pinned too, and the pause would be harder to
read); a cancel that is never gated (contradicts FR-022).

## R5. Making a hand-off

**Decision**: the hand-off tool's coroutine does the following, all bounded by the call time
limit:

1. Look up `(thread_id, step, tool_call_id)` in the send ledger (R8), where `step` is the
   graph namespace the tool call runs in. If the message was already sent, return the recorded
   result and send nothing. This is what makes a retried step safe (FR-010, FR-027). A recorded
   send whose digest of agent, kind, and arguments differs is another call under the same key:
   the tool returns an error result and sends nothing.
2. Build the A2A `Message`: role user; `message_id` derived from the thread, the tool call id,
   and a short digest of the step;
   `context_id` from the thread's conversation with this agent, if there is one; `task_id` when
   continuing a task; parts: the message text, then the context as a second text part with
   metadata `{"freyja": "context"}`. Nothing else from the thread is included (FR-005).
3. Record the send in the ledger, then send: streaming (`message:stream`) when the card says
   the agent streams, otherwise non-streaming with `return_immediately` followed by `get_task`
   every second.
4. The first `Task` or `Message` received is the acknowledgment. Store the conversation's
   `context_id` and, for a task, a task row (R8).
5. Forward each status update with a message, and each artifact update, as a progress event
   (R7).
6. End on the first of: a terminal state, `input_required`, a direct `Message` reply, the time
   limit, or a failure.

| Outcome within the call | Result the orchestrator receives |
|---|---|
| `completed`, or a direct `Message` reply | the reply's content (R6), headed with the agent and task id |
| `input_required` | the agent's question, marked as needing input, with the task id to answer |
| `failed`, `rejected`, `canceled`, `auth_required` | an error result naming the agent and the state, with the agent's message if any |
| time limit after acknowledgment | a note that the task is pending, naming the agent and the task id |
| time limit before acknowledgment | an error result saying the agent may or may not have received the task |
| unreachable, protocol error, malformed reply (including a task or context id that is empty or longer than 256 characters) | an error result naming the agent |

Every failure becomes a `ToolException`, so nothing an agent does can end a run (FR-009). The
link records reachability on every call. The ledger row is updated with the result text.

**Probe**: over the in-process stand-in with the SDK's `DefaultRequestHandler`, a non-streaming
`send_message` with `return_immediately=True` returned the task in `SUBMITTED` at 0.00 s and
`get_task` showed `WORKING` and then `COMPLETED` with one artifact; a second message with the
first reply's `context_id` stayed in the same context. With the streaming transport of R16, the
streaming send yielded the task at 0.02 s, a status update at 0.02 s, and the artifact and the
final status at 1.01 s, so acknowledgment and progress arrive as they happen.

**Alternatives considered**: always non-streaming with polling (progress between polls would
be lost, against FR-007); `return_immediately` followed by `subscribe` (events between the two
calls can be missed).

## R6. Turning a reply into text (FR-006)

**Decision**: `a2a_result_text(task_or_message)` returns one string: the task's artifacts in
order, then the final status message, each part converted as below, joined with blank lines.

| Part | Becomes |
|---|---|
| `text` | the text |
| `data` (structured) | compact JSON |
| `raw` (bytes) | `[file omitted: <media type or "unknown type">]` |
| `url` | `[file link omitted: <media type or "unknown type">]` |

The notes use the wording of feature 002's MCP notes (002 research R6), so the orchestrator sees
one vocabulary. Bytes and URLs never reach the model or the thread. A status message that
repeats the last artifact's text is not added twice.

## R7. Progress in the run's stream (FR-007)

**Decision**: the hand-off tool writes each progress update with LangGraph's stream writer
(`get_stream_writer()`); `run_stream` adds the `custom` stream mode and emits each custom chunk
as an `update` event with `node: "agent"` and a fixed payload
(see [contracts/sse-events.md](contracts/sse-events.md)). `run_stream` also turns on
`subgraphs=True` and filters what that adds.

**Probe**: a custom chunk written inside a graph invoked from a node of the parent, which is how
the `task` tool runs the subagent, did not reach the parent's `astream` without
`subgraphs=True`. With it, the chunk arrived tagged with the child's namespace, and so did the
child's `updates`.

**Filtering**, so the 001 contract holds:

- `updates` and `__interrupt__` are read from the root namespace only, as today; a subagent's
  steps still do not appear as separate `update` events;
- `messages` are forwarded from every namespace, as they are today;
- `custom` chunks are forwarded from every namespace, so a subagent's hand-off reports
  progress too.

The existing tests for the subagent, the stream order, and the interrupt shape guard this
change; a new test checks a hand-off made through the subagent.

**Alternatives considered**: a side channel from the tool to the stream keyed by run id (a
second source to merge into the stream generator, for what LangGraph already carries);
progress only for hand-offs made directly by the orchestrator (contradicts FR-007 for
subagents).

## R8. Where tasks, conversations, and sends are kept

**Decision**: three tables in the service's Postgres database, created by `setup()` like
`freyja_run_locks`, behind one protocol `AgentTaskStore` with a Postgres implementation and an
in-memory one for the suite (`runtime/agent_tasks.py`). Columns are in
[data-model.md](data-model.md).

- `freyja_agent_conversations`: `(thread_id, agent)` to `context_id` (FR-008).
- `freyja_agent_sends`: one row per message sent to an agent, keyed by
  `(thread_id, step, tool_call_id)`, with a digest of the request and the result text once
  known. A step that runs again finds its row and does not send again (R5, FR-010, FR-027).
  The step is the namespace LangGraph gives the tool call: the same when the call's step runs
  again from the last checkpoint, and different for every other call. The tool call id alone
  is not enough, because nothing makes a model provider keep it unique on a thread.
- `freyja_agent_tasks`: one row per agent task a thread started: state, timestamps, the
  outcome waiting to be delivered, the watcher's claim, and the background run's attempts
  (FR-017, FR-019, FR-020, FR-027).

**Rationale**:

- The watcher has to find every pending task across all threads and claim each for one
  instance (FR-019). The checkpoints are per thread and the LangGraph store has no atomic
  compare-and-set; a table with `UPDATE ... RETURNING` and `FOR UPDATE SKIP LOCKED` does both
  in one statement, as the run lease already does.
- The thread state lists every task the thread started, with its final status (clarification
  of 2026-11-09), so rows are never deleted.
- Statements are parameterized by psycopg; no value is formatted into SQL (Ruff `S608`).

**Alternatives considered**: keeping tasks in the thread's checkpointed state (no cross-thread
query, and updating it from the watcher would need the run lease for every status change);
the LangGraph store (no claim semantics).

## R9. Watching pending tasks (FR-019, FR-021, FR-023)

**Decision**: a `DelegationWorker` runs as one asyncio task per service instance, started after
the agent is built and stopped before the links close. Each tick, every poll interval:

1. Claim the due rows: state `working` or `input_required`, not given up, and unclaimed or
   whose claim has expired. The claim is `watcher = <instance id>`,
   `watch_until = now + 3 × interval`.
2. For each claimed `working` task: if its agent is no longer configured, or its age exceeds
   the maximum, give it up. Otherwise call `get_task` and store the new state. A terminal state
   or `input_required` records the outcome text and marks it ready for delivery. An `input_required` task is not
   polled again until it is answered, because nothing changes at the agent until then, but the
   maximum age still applies to it.
3. Deliver ready outcomes (R10).

A failed `get_task` marks the agent unavailable and leaves the task pending; it is checked again
on the next tick. An answer that the task is not found ends it as `failed` with that reason
(spec FR-019). The age is counted from `started_at`, and every time compared across instances
(age, `watch_until`) is the database's `now()`, not the instance's clock. Watching only reads
(FR-019).

**Rationale**: claiming per row, with a lease that expires, gives exactly one watcher per task
across instances and hands a task to another instance when its watcher dies, which is
the run lease's design applied to tasks. The clock and the tick are injectable, so the suite
drives ticks directly instead of sleeping.

**Worst case when a watcher stops** (SC-003): its claim lasts `3 × interval` = 30 s; another
instance claims the task on its next tick, at most 10 s later, and checks it at once; delivery
follows in the same tick if the thread is free. About 40 s plus the run's start, inside the
one-minute bound with the default interval.

**Alternatives considered**: `subscribe` per pending task (one open stream per task, and a
dropped stream needs polling anyway); push notifications (out of scope).

## R10. Delivering outcomes and background runs (FR-018, FR-020, FR-027)

**Decision**: an outcome is delivered by adding two messages to the thread: an `AIMessage`
calling Freyja's own read-only tool `check_agent_tasks` for the task ids, and one `ToolMessage`
per task with the outcome text. Their ids are derived from the task id
(`a2a-outcome-<agent>-<task id>`). The tool also exists for the orchestrator to call on
demand; it lists the thread's agent tasks and their status.

Delivery happens at the start of two kinds of run:

- **A user's run** (`POST /threads/{id}/runs`): under the lease and after the paused-thread
  check, undelivered outcomes are prepended to the run's input, before the user's message.
- **A background run**, started by the worker for a thread with undelivered outcomes once it
  can take the thread's run lease and the thread has no pending proposal. Its input is the
  outcome messages alone. It goes through `run_stream` with mode `background`, the same agent,
  gate, step limit, and lease heartbeat as any run (principle III); the worker consumes the
  events and reads only the terminal one.

A resume's continuation records nothing (FR-018).

**Exactly once**: before recording, the run checks the thread's messages for the outcome
message id. If it is already there, the outcome is marked delivered and skipped; otherwise it
goes into the input and is marked delivered once the run's first checkpoint is written. A crash
between the two is repaired by the next run's check. The messages reducer replaces a message
whose id already exists, so even a repeated write cannot add a second copy.

**Retries (FR-027)**: a background run that ends with `model_error` or `internal` is retried
after the retry interval, up to the configured attempts. A retry first repeats the delivery
check, then continues from the last checkpoint with input `None`. A step that already sent a
message finds its ledger row and does not send again (R5). Retry state is kept on the delivered
task rows (`followup_attempts`, `followup_next_at`). It is cleared when any run on the thread
ends with `end`, `interrupt`, or `step_limit`, which stops retries early once another run has
completed. The last failure is logged with the thread and task ids, and the outcome stays in
the thread for the next run.

**Rationale**: a tool call and its result is how the orchestrator already receives a delegate's
answer, so "as it would have received the hand-off's result" (FR-020) holds literally. A
`HumanMessage` would put words in the user's mouth, and a system message in the middle of a
conversation is not accepted by every provider.

**Alternatives considered**: `aupdate_state` to write the outcome as its own checkpoint before
the run (a write outside a run, which the gate and the lease do not see); delivering outcomes
only through background runs (contradicts the clarification of 2026-11-09).

**Sends left in flight by a stop** (added by the analysis of 2026-11-09): a crash between
sending and recording a result leaves the tool call unanswered in the thread. At the start of
the next user or background run, the run answers it from the send ledger before the input
reaches the agent, because Deep Agents' `PatchToolCallsMiddleware`
(`deepagents/middleware/patch_tool_calls.py`) would otherwise answer it with a generic
"cancelled or interrupted" text that hides the uncertainty (spec FR-017).

## R11. Telling the orchestrator about pending tasks (FR-018)

**Decision**: `AgentTasksMiddleware`, next to `ThreadContextMiddleware` on the orchestrator and
the subagent, appends one line per pending task of the current thread to the system message on
each model call: agent, task id, status, start time. It reads the store; with no pending task it
adds nothing. `check_agent_tasks` gives the full list on demand.

**Rationale**: the middleware already used for the thread id is the established way to add
per-run facts to the prompt, and it runs on every model call, so a status that changes during a
long run is current.

## R12. Classification, the pause, and edits (FR-011 to FR-014)

**Decision**: the hand-off and cancel tools of an agent are `read_only` when the agent's entry
says `read_only: true`, and `gated` otherwise; `check_agent_tasks` is in the fixed table of
Freyja's own tools as `read_only`. The gate configuration is generated from the catalog as
today, so the orchestrator, the subagent, and background runs share it.

- `CatalogTool` and the API's `ActionRequest` gain `agent`, the agent a hand-off or cancel
  goes to; `server` stays null for them. The pause description is
  `<agent>: hand-off` or `<agent>: cancel a task`.
- An edit is validated against the tool's pydantic model, which refuses an empty message and
  unknown keys, and then against the pinned arguments: `task_id` must equal the proposed one.
- A decision under a changed configuration follows `Catalog.decisions_for` as in feature 002:
  the rules are per tool name, and an agent's tools are named after the agent.

**Rationale**: everything the gate needs already exists from feature 002; the agent tools are
two more classified tools with one more field.

## R13. The catalog (FR-024)

**Decision**: `GET /tools` gains `agents`, one entry per configured agent: name, reachable,
`checked_at`, error (address removed), and `card`, the card's name, description, version, and
skills, labelled as the agent's own claim. Each agent tool in `tools` names its `agent`. With no
agents configured, `agents` is an empty list and `tools` is unchanged. The read contacts no
agent. Shapes: [contracts/openapi.yaml](contracts/openapi.yaml).

## R14. Failures, logging, and the address

**Decision**: one function, `AgentLink.describe`, produces every text about an agent that is
logged, raised, or stored as its status, and removes the configured address in its URL, host
and port, and host forms, as `ToolServer.describe` does. The log names the agent on startup
(skill count), on each failed call, on each give-up, and on each background run attempt.

## R15. Factory and runtime changes

**Decision**:

- `DelegateTool` and `CatalogTool` gain `agent: str | None`. `build_agent` takes the agent tools
  through the existing `delegate_tools` and gains `agent_tasks`, the store the middleware and
  `check_agent_tasks` read.
- `Runtime` gains `agents` (the links), `agent_tasks` (the store), and `worker`.
  `Runtime.assemble` gains `agent_transport`, a function from agent name and URL to the httpx
  transport its link uses. Production passes the default transport; the suite passes the
  streaming in-process transport of R16. It is the one new seam (principle III).
- Order in `Runtime.open`: persistence (with the three tables), tool servers, agent links, the
  agent, the worker, yield; stop the worker, close the links, the tool servers, the pool.
- `run_stream` gains mode `background`, the `custom` stream mode, `subgraphs=True` with the
  filter of R7, and a callback for the terminal event that the worker and the retry rule use.

## R16. Test strategy

**Decision**:

- `tests/a2a_stub.py`: a stand-in agent built with the SDK's own server classes
  (`DefaultRequestHandler`, `InMemoryTaskStore`, `create_agent_card_routes`,
  `create_jsonrpc_routes`). It records every message it receives and behaves by keyword in the
  message: by default it answers with the message and the conversation's context id; `ask`
  (input required, then answer), `slow <seconds>`, `fail`, `reject`, `parts` (text, data, and a
  file part), `progress <n>`. It supports cancel. Its card is configurable: streaming or
  not, authentication required, no text input, another advertised address, no skills. It runs
  in-process for the suite, and `python -m tests.a2a_stub --http PORT` serves it on loopback
  for the transport test and the quickstart.
- `tests/asgi_stream.py`: an httpx transport that runs an ASGI app in-process and hands each
  response chunk over as it is sent. **Probe**: httpx's own `ASGITransport` buffers the whole
  response, so the streaming send yielded all four events at once, after the agent finished
  (1.01 s); a task still working at the time limit would then look unacknowledged. With the
  streaming transport the task arrived at 0.02 s.
- Worker ticks are called directly with a fake clock, so long-running, give-up, and retry tests
  take no wall time. Two runtimes sharing one in-memory store prove one watcher and one
  delivery per task; the opt-in Postgres tests repeat that against the real tables.
- One transport test serves the stub over loopback with uvicorn and runs a hand-off, a pending
  task, and a stopped-then-restarted agent through the real httpx transport.
- Test files: `test_a2a_config.py`, `test_a2a_startup.py`, `test_a2a_handoff.py`,
  `test_a2a_gate.py`, `test_a2a_input.py`, `test_a2a_pending.py`, `test_a2a_background.py`,
  `test_a2a_catalog.py`, `test_a2a_transport.py`, `integration/test_a2a_tasks_postgres.py`.
  The FR to test map is in the plan.

## R17. The quickstart agent

**Finding**: no published package meets the spec's bar of a real, published A2A agent that runs
locally with no account or key (FR-025, SC-008). Checked on 2026-11-09:

| Candidate | Why it does not qualify |
|---|---|
| `a2a-server` 0.6.1, `a2a-cli` 0.2 (PyPI, last release 2025-08) | protocol 0.2/0.3 on their own library; the server's quick start needs an OpenAI key |
| `python-a2a` 0.5.10 | a library, not an agent; its examples call model providers |
| `fasta2a` 2.1.1 | a server framework; bring your own agent |
| `a2a-samples` 10.0.2 on PyPI | unrelated to the A2A project (no source link, unrelated README) |
| `a2a-inspector` 0.1.0 | a debugging client, not an agent |

**Decision: the closest substitute**: the `helloworld` agent in the A2A project's official samples
repository (`a2aproject/a2a-samples`, `samples/python/agents/helloworld`), pinned to commit
`1bb0871294` (2026-07-07, "python sample A2A 1.1.x update"). It is the project's own reference
agent, uses `a2a-sdk` 1.1.0 and protocol 1.0, needs no account or key, streams, and listens on
`127.0.0.1:9999`. It is source in a repository, not a package, so the quickstart fetches the two
files at the pinned commit and runs them with `uv run --with`. It answers every message at once,
so the scenarios for questions, pending tasks, and cancels use the stand-in agent served over
loopback. The samples' `number_guessing_game` agents are also key-free and could later show a
multi-turn conversation.

**Accepted at plan review on 2026-11-09.** FR-025 and SC-008 said "published"; they now say "an
official sample of the A2A project, pinned to a commit". The alternative, keeping the wording and
proving the real-agent scenarios with the stand-in only, was not adopted.
