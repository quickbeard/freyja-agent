# Research: MCP Tool Delegation

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Findings about packages already in `.venv` (deepagents 0.7.32, langchain 1.4.7, langchain-core
1.7.0, langgraph 1.2.18, pydantic 2.12.5, pydantic-settings 2.16.1, starlette 1.10.0) were read
from the installed code. The MCP packages are not in `.venv` yet, so they were resolved into a
throwaway overlay with `uv run --with ...`, which changes neither `pyproject.toml` nor `uv.lock`:
`mcp` 2.3.0, and `langchain-mcp-adapters` 0.3.2 with the `mcp` 1.30.0 it pins. Paths are relative
to `site-packages/`. Items marked "probe" were confirmed by running a throwaway script against
that code; the probes used a stub MCP server with nine tools, the reference git server, and the
real `create_app` under `TestClient`. Every unknown from the plan's Technical Context is
resolved below.

## R1. Client library: the `mcp` SDK 2.x directly

**Decision**: add `mcp>=2.3,<3` as a runtime dependency and talk to servers through its
high-level `mcp.Client`. `langchain-mcp-adapters` is not used. Freyja converts each MCP tool to
a LangChain `StructuredTool` itself (R5).

**Rationale**:

- `mcp.Client` (`mcp/client/client.py`) takes one positional target: a URL string (Streamable
  HTTP), a `StdioServerParameters` (launch a command and speak over its stdin and stdout), or a
  server object, which it connects to in-process with no sockets and no subprocess. That one
  parameter is the spec's two forms of connection plus the test stand-in FR-017 asks for, with
  no Freyja-owned transport code.
- Probe: one `Client` serves concurrent calls and returns each caller its own result (five
  0.3 s calls plus five lookups finished in 0.31 s over the in-process transport, and four
  concurrent lookups over stdio each returned their own value), which is FR-013.
- Probe: the default `mode="auto"` negotiated protocol `2026-07-28` with a 2.x server and fell
  back to the initialize handshake (`2025-11-25`) with a server built on `mcp` 1.x, so older
  servers keep working.
- `langchain-mcp-adapters` 0.3.2 requires `mcp<2.0.0`, the previous major. Its four behaviors
  that matter here would each have to be overridden:
  - a tool bound to a connection config opens a new session per call, which for stdio launches
    the command on every call (`langchain_mcp_adapters/tools.py:460-475`); the alternative,
    binding to a caller-owned session, has no per-call mode, so the two forms would need two
    different code paths and neither accepts an injected in-process transport;
  - audio content raises `NotImplementedError` (`tools.py:197-202`), which is not a
    `ToolException`, so it propagates out of the tool node and ends the run;
  - transport and session failures also propagate (`tools.py:111-114` documents this), which
    contradicts FR-012;
  - `structuredContent` goes to the tool message's artifact (`tools.py:276-283`), which the
    model never sees, while FR-018 wants structured data to reach the orchestrator.
  What would remain is the schema hand-off, about ten lines.
- `mcp` 2.3.0 adds eight packages over the current lock: `mcp`, `mcp-types`, `jsonschema`,
  `jsonschema-specifications`, `referencing`, `rpds-py`, `attrs`, `pyjwt`. `httpx2`, `anyio`,
  `starlette`, `sse-starlette`, `uvicorn`, and `python-multipart` are already present.
- Constitution principle II is about protocols, not libraries: the SDK is a protocol client and
  carries no agent loop or model client.

The pin is below the next major because the client surface used here (`Client`,
`StdioServerParameters`, `call_tool`, `list_tools`, the snake_case result fields) changed
between 1.x and 2.x. A bump is a deliberate step verified by the transport tests (R13).

**Alternatives considered**: `langchain-mcp-adapters` with `mcp<2` (above); `MultiServerMCPClient`
from the same package (the per-call session default, same objections); `fastmcp` the third-party
client (a second, larger dependency for the same three transports).

## R2. Server configuration

**Decision**: three settings, all read through `Settings`.

| Variable | Type | Default | Meaning |
|---|---|---|---|
| `FREYJA_MCP_SERVERS` | JSON object, server name to entry | `{}` | The tool servers. Empty means none. |
| `FREYJA_MCP_CALL_TIMEOUT_SECONDS` | number > 0 | `30` | Time limit for one tool call, including connecting (FR-012). |
| `FREYJA_MCP_STARTUP_TIMEOUT_SECONDS` | number > 0 | `20` | Time limit for connecting to each server and listing its tools at startup. |

A server entry (`McpServerConfig` in `config.py`, `extra="forbid"`):

| Field | Type | Rules |
|---|---|---|
| `command` | string or absent | The executable to launch. Exactly one of `command` and `url` is set. |
| `args` | list of strings | Arguments for `command`. Only with `command`. |
| `pass_env` | list of variable names | Variables copied from Freyja's environment into the launched process. Only with `command`. |
| `url` | string or absent | Address of a running server. Scheme `http` or `https`; host `localhost` or a loopback IP literal. |
| `read_only` | list of tool names | The tools exempt from approval. Default empty: every tool is gated. |

Server names match `^[a-z][a-z0-9_-]{0,31}$`. The contract is in
[contracts/server-config.md](contracts/server-config.md).

**Rationale**:

- pydantic-settings parses a `dict[str, Model]` field from one JSON environment value (probe:
  the example in the contract round-trips through `uv run --env-file`, single-quoted in `.env`).
  One variable keeps the whole server list in one place, which is what a read-only list needs
  to be reviewable.
- Every FR-004 configuration failure becomes a settings validation error raised when the
  process imports `config.py`, before the lifespan starts: both forms, neither form, a
  non-local address, an unknown field, a bad name (probe: each is refused, and the error path
  is `mcp_servers.<name>`, which names the server).
- The address rule uses no DNS: `urlsplit(url).hostname` must be `localhost` or parse as an IP
  address whose `is_loopback` is true. A name that happens to resolve to this machine is
  refused, so the check is deterministic and cannot be changed by a resolver. The one name
  accepted, `localhost`, is accepted as written, so the machine's own resolution of that name
  is trusted (spec assumption). Probe, Python 3.12.16: `::ffff:127.0.0.1` counts as loopback,
  `0.0.0.0` and `::ffff:10.0.0.5` do not, and user information in the address does not change
  the host that is checked. The project supports every Python from 3.12.0 on, and
  `is_loopback` does not answer the same for an IPv4-mapped address in every patch release
  (probe: `False` for `::ffff:127.0.0.1` on 3.12.0 and 3.12.3, `True` on 3.12.16), so the
  validator does not rely on it: an IPv4-mapped IPv6 literal is unwrapped with
  `ipv4_mapped` and judged by the IPv4 address it carries.
- A server at a loopback address cannot send the client elsewhere: the SDK follows a redirect
  only when it stays on the endpoint's origin, and fails the request otherwise
  (`mcp/client/streamable_http.py`). Freyja passes no HTTP client of its own, so that rule is
  the one in force. Because the rule is the SDK's and not Freyja's, a transport test asserts
  it, so an SDK upgrade that changed it would fail the suite (R13).
- `pass_env` holds variable names, never values, so a credential cannot be written into the
  server list by following the documented shape (FR-002). The SDK's stdio transport gives a
  launched process only `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, and `USER` by default
  (`mcp/client/stdio.py`, `get_default_environment`); probe: `FREYJA_DATABASE_URI` was not
  visible to the child and a `pass_env` variable was. A name in `pass_env` that is unset or
  empty in Freyja's environment fails startup naming the server and the variable; an empty
  value would otherwise make redaction (R12) rewrite every string.
- `Settings` sets `hide_input_in_errors=True`, so a validation error never echoes an entry's
  contents (probe: with it on the outer model the `input_value` is omitted; on the inner model
  alone it is not).
- The startup limit is 20 s so an unresponsive server fails inside SC-004's 30 s: the SDK
  waits up to 2 s for a launched process to exit when the connection is cancelled (probe: a
  command that never speaks MCP was abandoned 2.0 s after the limit).

**Alternatives considered**: one variable per server (`FREYJA_MCP_SERVER_GIT_COMMAND=...`):
needs a custom settings source and scatters the read-only list. A committed YAML file: a second
configuration channel, against principle IV. `env` as a name-to-value map with `${VAR}`
expansion, as the adapter does: values can then be pasted in literally. A `transport` field:
redundant, since `command` and `url` already say which form an entry is.

## R3. Connection lifetime

**Decision**: the form of the entry decides the lifetime.

- **Launched server (`command`)**: one `Client` for the life of the service, entered by a
  dedicated asyncio task, the owner task. The task enters `async with Client(params)`, hands
  the client to the hub, and waits on a stop event; shutdown sets the event and awaits the
  task, which terminates the process. Every tool call uses that one client.
- **Address server (`url`)**: one `Client` per tool call: connect, call, close. Startup opens
  one to list the tools and closes it.

**Rationale**:

- Spec clarification 3 asks for reconnection on the next call for a server that is answering
  again, and no relaunch of a process that exited. Per-call connections give the first with no
  reconnect logic: probe, over loopback HTTP, a call while the server was down failed in under
  10 ms with `ConnectError`, and the next call after the server restarted succeeded. A
  connect-and-call round trip on loopback took about 10 ms.
- A long-lived HTTP connection does not recover: probe, after the server stopped, every later
  call on the same `Client` raised `MCPError: Connection closed`, including after the server
  came back.
- A long-lived launched connection behaves as the spec wants: probe, after the process exited,
  every call failed immediately with `MCPError: Connection closed`, the client still closed
  cleanly, and nothing relaunched the command.
- The owner task exists because a transport failure cancels the scope of whichever task
  entered the `Client`. Probe: with a long-lived HTTP client entered directly, the server's
  death cancelled the surrounding block; entered by an owner task, only that task ended and
  the caller received an ordinary exception. Holding a connection in the lifespan task would
  let one server's failure cancel the service's startup scope. The owner task also satisfies
  anyio's rule that a transport is entered and exited by the same task, whatever calls close.
- A launched server is "started and stopped with the service" (FR-005): the SDK terminates the
  process tree when the client exits.

**Alternatives considered**: per-call connections for launched servers (starts the command on
every call: 0.5 s for a Python server, and state held by the server is lost); a long-lived
connection with reconnect-on-failure for address servers (more code than per-call for a 10 ms
saving); a background health check (ruled out by clarification 3).

## R4. Startup sequence and failures

**Decision**: `open_tool_servers(configs, ...)` is an async context manager entered by
`Runtime.open` after persistence is ready and before the agent is built. In order:

1. Resolve `pass_env` for every launched entry; an unset or empty variable fails naming server
   and variable.
2. Connect to all servers concurrently, each under the startup limit, and list its tools,
   following pagination cursors. For a launched server this starts the owner task (R3).
3. If any server failed, close the ones that opened and raise `ToolServerError` naming every
   failed server, in configuration order.
4. For each server, check that every name in `read_only` is a tool it provides; a miss fails
   naming server and tool, so a typo cannot gate or ungate anything silently.
5. Check tool names: each must match `^[A-Za-z0-9_-]{1,64}$`, the form model providers accept
   for a function name, and be unique across all servers and Freyja's own tool names (R8). A
   bad name or a collision fails naming the server or servers and the tool.
6. Log one line per server: name, connection form, number of tools, number read-only.

`Runtime.open` then builds the agent with the resulting tools and yields. On exit it closes
the servers before the database pool. The service does not serve requests until the lifespan
has yielded, so every failure above leaves nothing listening (FR-004).

**Rationale**: probes of what each failure looks like, so the error text can be built without
guessing: a missing executable raises `FileNotFoundError` at once; a command that exits raises
an `ExceptionGroup` within 10 ms; a command that runs but never speaks MCP hangs until the
limit; nothing listening at an address raises an `ExceptionGroup` holding `httpx2.ConnectError`
within 20 ms. All four are caught per server and reported by server name with the innermost
exception's type and redacted message (R12). A server with no tools is not an error (spec edge
case); its log line says zero.

The provider name rule is checked at startup because a tool whose name a provider rejects makes
every model request fail, which would look like a broken model rather than a misconfigured
server. Failing loudly matches the spec's treatment of every other startup inconsistency.

**Alternatives considered**: connecting lazily on first use (contradicts FR-003 and FR-004);
prefixing tool names with the server name to avoid collisions (the spec's assumption is that a
collision is an error, and the pause shows the name the server documents); skipping a tool with
an unusable name (a silent gap in the catalog).

## R5. Tool conversion

**Decision**: for each listed tool, build

```python
StructuredTool(
    name=tool.name,
    description=tool.description or "",
    args_schema=tool.input_schema,  # the server's JSON Schema, passed as a dict
    coroutine=call,  # closes over the server link and the tool name
    handle_tool_error=True,
)
```

`call(**arguments)` asks the server link to run the call under the time limit and returns the
result text, or raises `ToolException` with the error text (R6, R7).

**Rationale**:

- Probe: Deep Agents accepts such tools in `tools=`; the model is offered them (the scripted
  model's `bind_tools` saw every name); `convert_to_openai_tool` renders the dict schema as the
  function's parameters, including a tool with no properties.
- With a dict `args_schema` LangChain does not validate arguments itself; the server does.
  Probe: a call with a wrong argument came back from the server as an error result, became a
  tool message with `status="error"`, and the run continued.
- `handle_tool_error=True` turns a `ToolException` into an error tool message instead of
  ending the run, which is the delivery FR-012 requires. Only `ToolException` is handled this
  way, so `call` converts every failure into one (R7).
- Probe: an unknown tool name is answered by the server as an error result, so a tool that
  disappears on the server side after startup degrades to an error result. Probe: a call to a
  name the tool node does not hold, which is what an old thread produces after its server is
  removed from the configuration, comes back as an error tool message listing the valid tools,
  and the run continues (spec edge case).

**Alternatives considered**: generating a pydantic model from the JSON Schema for
`args_schema` (lossy for schemas pydantic cannot express, and the server validates anyway).

## R6. Result conversion (FR-018)

**Decision**: a result becomes one string, built from its content blocks in order and joined
with blank lines:

| Block | Becomes |
|---|---|
| `TextContent` | its text |
| embedded resource with text contents | its text |
| `ImageContent` | `[image omitted: <mime type>]` |
| `AudioContent` | `[audio omitted: <mime type>]` |
| embedded resource with binary contents | `[file omitted: <mime type>]` |
| resource link | `[resource link omitted: <uri>]` |
| anything else | `[<type> content omitted]` |

When the result has `structured_content` and no text block, the structured content is appended
as JSON. A result whose `is_error` is true is converted the same way and raised as
`ToolException`, so it reaches the orchestrator as an error result. The text passes through
redaction (R12).

**Rationale**: probe: a tool returning text and an image produced a `text` block and an `image`
block with its mime type, so the note can name the kind. Probe: a tool with structured output
returned the same data both as a JSON text block and as `structured_content`, which is what the
protocol asks servers to do; appending it again would duplicate it, hence the "no text block"
condition. A string is the content shape every model provider accepts in a tool message, and
it keeps binary data out of the checkpoint.

**Alternatives considered**: passing image blocks through (rejected in clarification 4);
failing a result that has non-text parts (rejected in clarification 4).

## R7. Call failures, the time limit, and reachability

**Decision**: `ToolServer.call(tool, arguments)` runs the whole call inside
`asyncio.timeout(call_timeout)`, including connecting for an address server, and classifies
the outcome:

| Outcome | Server status | Delivered to the orchestrator | Log |
|---|---|---|---|
| result, `is_error` false | reachable | the result text | none |
| result, `is_error` true | reachable | error result with the server's text | warning: server, tool, `tool_error` |
| time limit reached | unreachable | error result naming the limit and saying the outcome is unknown | warning: server, tool, `timeout` |
| any other exception | unreachable | error result saying the server could not be reached | warning: server, tool, `unreachable` |

Status is `reachable`, `checked_at`, and a short redacted `error`, held in memory per server
and updated only here and at startup. Nothing is retried: a failed call is reported once. An
approved effectful call that timed out may or may not have taken effect, the error text says
so, and a second attempt is a new proposal that pauses again.

**Rationale**:

- Probe: `asyncio.timeout` around a call cancelled it at the limit over the in-process, stdio,
  and HTTP transports, and the next call on the same connection or address succeeded.
- "Reconnect on the next call" needs no extra code: an address server is connected per call;
  a launched server whose process is alive answers the next call on the same connection; one
  whose process exited fails every call immediately and stays unreachable until restart (R3),
  as the spec's edge case states.
- A tool error means the server answered, so the server stays reachable; only silence or a
  transport failure marks it unreachable. That is "the outcome of the most recent attempt"
  from clarification 3.
- An error result is an ordinary tool message, so the run continues, the stream stays open,
  and the run lease is untouched (FR-012, SC-005).
- Exceptions from a per-call connection arrive wrapped in an `ExceptionGroup` (probe); the
  reported reason is the innermost exception's type and redacted message.
- The service declares no sampling, elicitation, or roots capability to servers; a tool that
  needs one fails as a tool error.

**Alternatives considered**: the SDK's own `read_timeout_seconds` (covers reading the reply
only, not connecting); one automatic retry for read-only tools (not asked for, and it doubles
the worst-case wait).

## R8. Classification and the gate

**Decision**: `agent/catalog.py` owns one total function from tool to classification.

- **Server tools**: `read_only` if the tool's name is in its server's `read_only` list,
  otherwise `gated`. No other input: the server's annotations, the tool's description, and the
  model are not consulted (FR-006).
- **Freyja's own tools**: a fixed table for the tools Deep Agents 0.7.32 puts in the tool node
  (probe):

  | Tool | Classification |
  |---|---|
  | `ls`, `read_file`, `glob`, `grep` | `read_only` |
  | `write_file`, `edit_file`, `delete`, `task` | `workspace` |
  | `send_notification` | `gated` |
  | `execute` | not offered: no backend supports it, so it never reaches the model (001 research R1) |

- **Anything else**: `build_agent` raises `UnclassifiedToolError` naming the tool. After
  compiling, it reads the tool names from the orchestrator's tool node and from the
  subagent's (`graph.nodes["tools"].bound.tools_by_name`, probe: both expose it) and requires
  every one to be a server tool or a row in the table.

`interrupt_on` is generated from the classification: one entry per `gated` tool, with
`allowed_decisions` of approve, edit, and reject, or approve and reject for a tool with no
declared input (R9), and a description of `<server>: <first line of the tool's description>`.
The same mapping goes to the orchestrator and to the general-purpose subagent. The factory
then asserts that the set of `interrupt_on` keys equals the set of gated catalog entries.

The catalog exposes three values. The spec's rule for server tools yields exactly two of them:
its "read-only" is `read_only` and its "effectful" is `gated`. `workspace` is assigned only by
the table, to Freyja's own tools that change files inside the workspace or delegate to the
subagent; they need no approval under constitution principle I, and calling them `read_only`
would be false.

**Rationale**:

- The rule is a pure function of configuration, evaluated once at startup, so it satisfies
  principle VI: no model call is on the path that decides whether a tool pauses.
- "Silence means gated" extends to Freyja's own tools: a Deep Agents upgrade that adds a tool
  cannot leave it ungated by accident, because startup fails until someone adds a table row.
  The `deepagents<0.8` pin and a factory test keep the table honest.
- Probe: with `interrupt_on` naming a server tool, a proposal for it paused before the server
  was called, an approve ran it once with the proposed arguments, and an edit ran it with the
  edited ones. Probe: the catalog names equal the names the model is offered once `execute` is
  excluded.
- Subagents inherit the gate the same way `send_notification` does today (001 research R1);
  the subagent receives the same tool list and the same `interrupt_on`.
- A step with several proposals is held as a whole (FR-007). Deep Agents builds
  `HumanInTheLoopMiddleware` in its default `batched` mode, which raises one pause after the
  model's message and before the tool node. Probe: with a gated and a read-only tool proposed
  in one step, the pause listed the gated one and neither had been called; both ran after the
  decision.

**Alternatives considered**: gating by default inside a middleware that inspects each call
(a second gate implementation next to the HITL middleware); trusting `read_only_hint` when the
operator says nothing (forbidden by FR-006); two classification values with file-writing tools
reported as read-only (false) or as effectful and ungated (contradicts FR-007).

## R9. The proposal names the server; edits are validated against JSON Schema

**Decision**:

- `ActionRequest` gains `server`: the server's name for a server tool, `null` for one of
  Freyja's own. `interrupt_to_pending(interrupt, catalog)` fills it by tool name, so the
  `interrupt` event and `GET /threads/{id}/state` both carry it (FR-008). A tool the current
  catalog does not know, which can only be a proposal that outlived a configuration change,
  gets `null`.
- `build_resume_command` receives the gated catalog entries instead of the `GATED_TOOLS`
  mapping. An edit for a tool whose input is a JSON Schema dict is validated with
  `jsonschema`'s validator for that schema; each error becomes a FastAPI-shaped entry with
  `loc` of `["body", "decisions", i, "args", *path]` and the validator's message. A missing
  required property is reported with the property appended to `loc`. A top-level argument that
  is not in the schema's `properties` is refused, unless the schema sets
  `additionalProperties` to `true` or to a schema. An edit for a tool with a pydantic schema
  (`send_notification`) is validated as today.
- A tool has **no declared input** when its input schema has no `properties` or an empty one.
  Its `allowed_decisions` are approve and reject, so the pause itself tells the client that
  edit is unavailable, and the existing per-action check refuses an edit with a 422 before
  anything resumes (FR-009).

- **A proposal decided under a changed configuration (FR-019).** A proposal can outlive a
  restart, and the gate re-runs with the new `interrupt_on` when the thread resumes, so
  `interrupt_to_pending` and `build_resume_command` compare every pending action with the
  current catalog:

  | The action's tool is now | `allowed_decisions` | What the resume does |
  |---|---|---|
  | still `gated` | as the catalog says | Unchanged. `server` and the schema an edit is checked against are the current ones. |
  | not in the catalog | approve, reject | The decision is not passed to the gate. The tool node answers the call with its unknown-tool error, so nothing is called. |
  | `read_only` | approve | The decision is not passed to the gate and the call runs as proposed. An edit or a reject gets a 422 saying the tool no longer needs a decision. |

  The resume payload carries the decisions for the still-gated actions only, in order, because
  those are the ones the gate asks for when it re-runs. A call in the paused step that has
  since become gated is not in the stored proposal; the gate then asks for more decisions than
  the payload holds and raises before the tool node runs. The stream reports that as a run
  error with code `internal`, and the thread stays paused on the same proposal until the
  earlier configuration is restored. The gate's own message, with the two counts, is in the
  service log through the stream's existing handler. A new message is refused while the
  proposal is pending, so the thread is blocked for that time. Telling this case apart for
  the client would need the paused step's tool calls, which for a proposal made by a subagent
  are in the subagent's state; that is left for a later increment.

`jsonschema>=4.20` is declared in `pyproject.toml` although `mcp` already installs it, because
Freyja imports it directly.

**Rationale**: probe: `validator_for(schema)(schema).iter_errors(args)` reported
`(['priority'], 'type', "'high' is not of type 'integer'")`, a nested path
`['opts', 'tags', 1]`, and a missing property as `([], 'required', "'note' is a required
property")`, which is why the missing name is appended to `loc`. The per-action
`allowed_decisions` mechanism already exists (001 convergence), so a no-input tool needs no new
validation path. The tool name stays pinned server-side on an edit, as today.

JSON Schema on its own accepts a key it does not describe whenever `additionalProperties` is
absent, which is the usual case for a server's tool. The edited arguments are sent to the
server as given, so nothing would be dropped, but a server that ignores an unknown key would
turn a mistyped name into a call with the proposed or default value, which is not the call
the approver meant. Refusing the key matches what feature 001 requires of every action (its
FR-013), and a schema that really is open says so itself.

Probes for a changed configuration, each pausing a two-tool step under one gate and resuming
under another on the same checkpointer: with all the original decisions the gate raised
`Number of human decisions (2) does not match number of hanging tool calls (1)`, no tool ran,
and the thread kept the same pending proposal; with only the still-gated decision, both calls
ran, the no-longer-gated one with its proposed arguments; with the tool removed, the call came
back as `Error: alpha is not a valid tool` and the run continued; with a read-only call that
had become gated, the gate raised the same error with the counts reversed, and again nothing
ran. A decision the gate no longer asks for is therefore ignored unless the API accounts for
it, which is why a reject for a now read-only tool is refused instead of accepted and dropped.

**Alternatives considered**: validating edits by calling the server with a dry-run flag (no
such thing in the protocol); refusing edits for all server tools (contradicts FR-009);
honouring a reject for a now read-only tool by writing a rejection into the thread's messages
on resume (probe: it works for a proposal made by the orchestrator; it was not verified for
one made by a subagent, whose paused step is in the subagent's own state); refusing
every decision on a proposal the configuration no longer matches (leaves a thread stuck after
a server is simply removed).

## R10. The catalog endpoint

**Decision**: `GET /tools` returns the catalog. It reads memory only and never contacts a
server (FR-014).

- `tools`: one entry per tool the orchestrator is offered, Freyja's own first in a fixed order,
  then each server's in configuration and listing order. Fields: `name`, `server` (`null` for
  Freyja's own), `description`, `input_schema` (the server's JSON Schema, or the JSON Schema of
  the tool's model-facing arguments for Freyja's own), `classification`, `allowed_decisions`
  (empty unless gated), and `server_hints`.
- `server_hints`: the annotations the server publishes, with only the keys it set, under the
  names `title`, `read_only`, `destructive`, `idempotent`, `open_world`; `null` when it
  publishes none and for Freyja's own tools. The field's name and its description in the
  contract mark it as the server's claim.
- `servers`: one entry per configured server, including one with no tools: `name`,
  `connection` (`command` or `address`), `reachable`, `checked_at`, `error`, `tool_count`.

The contract is in [contracts/openapi.yaml](contracts/openapi.yaml).

**Rationale**: the tool list is built once by `build_agent` from the tools it compiled in, so
it cannot drift from what the orchestrator was given (FR-014, SC-003); only the server status
is read live, from the in-memory record R7 maintains. Probe: the reference git server publishes
`read_only_hint`, `destructive_hint`, `idempotent_hint`, and `open_world_hint` for every tool,
and the stub publishes them for some, so both the "hints present" and "none" shapes occur in
practice. Reachability is per service instance: each instance holds its own connections, so
two instances can report different statuses for the same server.

**Alternatives considered**: nesting tools under their server (Freyja's own tools have no
server, and a flat list matches how a proposal names a tool); probing servers on read (ruled
out by clarification 3).

## R11. Factory and runtime changes

**Decision**:

- `build_agent(model, *, checkpointer, store, workspace_dir, max_steps, delegate_tools=(),
  system_prompt=DEFAULT_PROMPT)` returns `Orchestrator(agent, catalog)`. `delegate_tools` is
  the sequence of converted server tools, each carrying its server name, input schema, hints,
  and classification. With none, the result is today's agent plus a catalog of Freyja's own
  tools.
- `Runtime` gains `catalog` and `servers`. `Runtime.open(settings, *, model=None,
  instance_id=None, connect=default_connect)` enters `open_tool_servers(...)` between opening
  persistence and building the agent. `connect(name, config)` returns the target handed to
  `mcp.Client`: in production the URL, or a stdio transport built from `StdioServerParameters`
  with the error-output pipe (R12).
- `api/threads.py` and `api/streaming.py` use `runtime.catalog` where they used `GATED_TOOLS`
  and the bare `interrupt_to_pending`. `api/tools.py` adds the catalog route.
- New package `src/freyja_agent/delegates/`: `mcp_servers.py` (configuration to target,
  `ToolServer`, `open_tool_servers`, status, redaction) and `mcp_tools.py` (tool and result
  conversion). The name follows the constitution's word for what the orchestrator integrates
  over protocols and avoids a package called `mcp` next to the SDK's.

**Rationale**: the factory stays the only place the orchestrator is assembled (principle III);
returning the catalog from it is what guarantees the catalog and the agent agree. `connect` is
the single seam for tests, at the same level as the in-memory checkpointer: it swaps the
transport, not the code that lists, converts, classifies, gates, calls, or reports. Probe: the
real `create_app` with a lifespan that opened an in-process server in an owner task streamed a
run through `TestClient`, paused on the gated tool, resumed, read state, and shut down cleanly.

**Alternatives considered**: keeping `build_agent`'s return type and deriving the catalog
separately (two calls that must be given the same tools); connecting inside `build_agent`
(makes the factory async and ties tests to a transport).

## R12. Credentials never leave the process boundary

**Decision**: a credential reaches a launched server only as an environment variable named in
`pass_env`. Each `ToolServer` keeps the values of its `pass_env` variables and replaces any
occurrence of one with `***` in every string that comes from the server or is written about
it:

- result text and error text, which is everything a call puts in the stream and the thread;
- log lines, and the catalog's `error`;
- what the server lists at startup: each tool's description, the string values inside its
  input schema, and its hints, so the catalog and the model see the redacted form;
- the server's error output (below).

The match is on the exact value. A value the server encodes or cuts before printing it is not
recognized, which FR-002 states. An empty value never reaches redaction, because startup
refuses it (R4). Log lines and errors name the server and tool and never include `args`, the
environment, or the URL's query string. The status `error`, which the catalog shows, also has
the server's configured URL replaced with `<address>`, because FR-014 keeps the address out of
the catalog and an exception's text can quote it. Error text is cut to 500 characters.

A launched server's error output is captured instead of inherited. The stdio transport is
built with `stdio_client(params, errlog=<write end of a pipe>)` and handed to `Client`; the
owner task (R3) also runs a reader that takes the pipe line by line, redacts each line, and
logs it at info level under the server's name.

**Rationale**: FR-002 lists the log, including a launched server's error output, error
messages, the stream, tool results and thread state, and the catalog. Redacting at the one
place text leaves a server link covers all of them, including a server that echoes its own
environment in an error. Authentication to address servers is out of scope, so there are no
headers to protect. Probe: a stub that printed its token to its error output at startup and
again inside a tool call; with the pipe in place nothing reached Freyja's own error output, and
both captured lines carried `***`. `Client` accepts the transport object directly
(`mcp/client/client.py` passes `stdio_client(...)` through the same path itself).

**Alternatives considered**: redacting only in the log formatter (misses the stream and the
catalog); refusing to start when `args` looks like it holds a secret (guesswork); leaving a
launched server's error output on Freyja's own, which is the SDK default (a server that prints
its token there would put it in the service's output unredacted).

## R13. Test strategy

**Decision**:

- `tests/mcp_stub.py`: an `MCPServer` with tools that cover every behavior: a lookup the
  configuration marks read-only; a recording tool that is effectful and whose server labels it
  read-only; a no-argument tool; a tool with nested and optional arguments; a failing tool; a
  slow tool; tools returning an image, audio, a binary resource, and structured data; a tool
  that echoes a named environment variable in its result and on its error output, and whose
  description can be made to contain it. It records every call it receives so tests can assert
  exactly what reached the server. Run as a module it serves stdio, or loopback HTTP with
  `--http <port>`.
- `tests/conftest.py` gains a fixture that builds the app through `create_app(open_runtime=...)`
  where the context manager is the production assembly with in-memory persistence and a
  `connect` that returns stub server objects, so `mcp.Client` connects in-process. A wrapper
  target that fails on demand simulates an unreachable server and its recovery.
- The default suite therefore needs no network and no external server (FR-017, SC-007). Two
  transport tests in `tests/test_mcp_transports.py` exercise the real stdio and HTTP paths
  against the stub: one launches it with the test interpreter over pipes, including the
  process exiting mid-service; one serves it on `127.0.0.1` with an ephemeral port, including
  stop and restart, and an endpoint that answers with a redirect to a different origin, which
  must not be followed. They use only the loopback interface and what `uv sync` installed. The
  stdio test also asserts what the launched process can see: a `pass_env` variable is present,
  another variable set in the service's environment is absent, and the stub's error output
  arrives in the log redacted. That re-checks, on every SDK upgrade, the default environment
  this design relies on (R2).
- Subagent tests reuse the 001 pattern: the parent script calls `task`, the subagent script
  calls the server tool.

FR coverage map (FR → test module): 001, 002 → `test_mcp_config`, `test_mcp_calls`
(redaction in results, errors, the log, and the catalog), `test_mcp_transports` (the launched
process's environment and error output); 003, 004, 005 → `test_mcp_startup`; 006, 007, 008,
009, 019 → `test_mcp_gate` (including a step with several proposals, directly and through a
subagent, and each row of R9's table for a changed configuration), `test_factory` (every tool
classified, `interrupt_on` equals the gated set); 010, 012, 013,
018 → `test_mcp_calls`; 011 → `test_factory`, `test_catalog`; 014 → `test_catalog`; 015 →
`test_config`, `test_mcp_config`; 016 → README review and quickstart; 017 → the suite, plus
`test_mcp_transports` for the two real forms.

**Rationale**: the in-process transport is part of the SDK (`mcp/client/_memory.py`), selected
by passing a server object to `Client`, so the stand-in is the real protocol implementation on
both sides with only the byte transport removed. Everything Freyja wrote is exercised by the
default suite; the two transport tests cover the one function the seam replaces. Probe: stdio
connect to a Python stub took 0.57 s and a loopback HTTP connect 3 ms, so both are cheap.

**Alternatives considered**: mocking `ToolServer` (would not exercise conversion or the SDK);
transport tests behind an opt-in variable like the Postgres tests (they need nothing a clean
clone lacks, so there is no reason to skip them).

## R14. The quickstart server

**Decision**: the README and the quickstart use the reference git server, launched with
`uvx mcp-server-git --repository <path>` against a scratch repository the developer creates.
The address form is demonstrated with the suite's stub served over loopback HTTP.

**Rationale**: probe, with `mcp-server-git` 2026.11.4 through the 2.x client: 14 tools; read
tools such as `git_status`, `git_log`, and `git_branch`; effectful tools such as
`git_create_branch`, `git_add`, and `git_commit`; every tool carries server hints, including
`read_only_hint: true` on the read tools, which makes scenario "the server's label is ignored"
visible with a real server; a path outside the configured repository is refused by the server.
It needs no account and no Node.js, and `uvx` comes with the `uv` the project already requires.
Creating a branch is an effect that is easy to observe (`git branch`) and harmless in a scratch
repository. The first launch downloads the package (probe: 8.0 s, then 0.5 s from cache), so
the quickstart runs `uvx mcp-server-git --help` once before starting the service to stay inside
the startup limit. The reference git server speaks stdio only, which is why the address form
uses the stub.

**Alternatives considered**: the reference filesystem server (needs Node.js, and overlaps with
Freyja's own file tools); the reference time and fetch servers (no effectful tool; fetch needs
the network); the reference "everything" server (needs Node.js).
