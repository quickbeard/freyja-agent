# Tasks: MCP Tool Delegation

**Input**: Design documents from `/specs/002-mcp-tool-delegation/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/openapi.yaml,
contracts/server-config.md, quickstart.md

**Tests**: Included. FR-017 requires automated coverage for every behavior in this feature, and
SC-007 requires the suite to run with no network and no external server installed. In each user
story phase the test tasks come first: write them, watch them fail, then implement. Phase 2 is
the exception: its fixtures are built on the code, so its tests close the phase.

**Organization**: Tasks are grouped by user story so each story is an independently testable
increment. Research references (R1 to R14) point at `research.md`. "The stub" is the stand-in
MCP server in `tests/mcp_stub.py`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1 to US5)
- Every task names the file it touches

## Path Conventions

Single project, src layout: `src/freyja_agent/`, `tests/`, repo-root files.

## Standing rules for every task

- Every resume request in a test or an example sends the proposal's `interrupt_id`
  (feature 001, FR-013).
- A gated tool is never reachable without the approval gate at the end of any phase: Phase 2
  builds classification and the gate before any later phase adds behavior to a call.
- No new Ruff suppression; no blocking I/O in async code (principle V).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: The two dependencies, the three settings, and the package every later task builds
on.

- [X] T001 Add `mcp>=2.3,<3` and `jsonschema>=4.20` to `[project].dependencies` in
      `pyproject.toml` and run `uv lock`; confirm the lock gains eight packages and no other
      version moves (R1, R9)
- [X] T002 [P] Add tests for the three new settings to `tests/test_config.py`: defaults
      `mcp_servers == {}`, `mcp_call_timeout_seconds == 30`, `mcp_startup_timeout_seconds == 20`;
      `FREYJA_MCP_SERVERS` parses from one JSON value; each timeout refuses zero and a negative
      number (FR-015, R2)
- [X] T003 [P] Write `tests/test_mcp_config.py` for one server entry: a `command` entry with
      `args`, `pass_env`, and `read_only` is accepted; a `url` entry is accepted for
      `http://localhost:8765/mcp`, `http://127.0.0.1:8765/mcp`, `http://[::1]:8765/mcp`, and
      `http://[::ffff:127.0.0.1]:8765/mcp`; refused, with an error whose location is
      `mcp_servers.<name>`: both `command` and `url`, neither, `args` or `pass_env` together
      with `url`, a scheme other than `http` or `https`, the hosts `10.0.0.5`, `0.0.0.0`,
      `::ffff:10.0.0.5`, and `example.com`, an unknown field, and a server name that does not
      match `^[a-z][a-z0-9_-]{0,31}$`; user information in a URL does not change the host that is
      checked; no refusal echoes the entry's values (FR-001, FR-004, R2)
- [X] T004 Implement the settings in `src/freyja_agent/config.py`: `McpServerConfig` with
      `extra="forbid"` and fields `command: str | None`, `args: list[str]` (default empty, only
      with `command`), `pass_env: list[str]` (default empty, only with `command`),
      `url: str | None`, `read_only: list[str]` (default empty); a validator requiring exactly
      one of `command` and `url`; a URL validator requiring scheme `http` or `https` and a host
      that is `localhost` or an IP literal whose `is_loopback` is true, read from
      `urlsplit(url).hostname` with no name lookup, where an IPv4-mapped IPv6 literal is
      unwrapped with `ipv4_mapped` and judged by the IPv4 address it carries; on `Settings`,
      `mcp_servers: dict[str, McpServerConfig] = {}` with keys matching
      `^[a-z][a-z0-9_-]{0,31}$`, `mcp_call_timeout_seconds: float = 30` and
      `mcp_startup_timeout_seconds: float = 20`, both greater than zero, and
      `hide_input_in_errors=True` in `model_config` (FR-001, FR-015, R2; depends on T002, T003)
- [X] T005 [P] Document `FREYJA_MCP_SERVERS`, `FREYJA_MCP_CALL_TIMEOUT_SECONDS=30`, and
      `FREYJA_MCP_STARTUP_TIMEOUT_SECONDS=20` in `.env.example`, with the server list commented
      out and one single-quoted example line taken from `contracts/server-config.md` (FR-015)
- [X] T006 [P] Create the empty package `src/freyja_agent/delegates/__init__.py`

**Checkpoint**: `uv sync` and `uv run pytest` succeed; `test_config` and `test_mcp_config` pass.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Connect to servers at startup, list and convert their tools, classify every tool,
gate the effectful ones, and assemble the orchestrator with them. This is the startup half of
User Story 4, which every other story needs.

**CRITICAL**: No user story work can begin until this phase is complete.

- [X] T007 [P] Write the stub in `tests/mcp_stub.py`: a factory `make_stub(...)` returning an
      MCP server object that records every call it receives, with these tools: `lookup(key)`
      returning text, labelled read-only by the server; `record_note(note, priority?)`, which
      has an effect on the stub's record and which the server also labels read-only; `ping()`
      with no arguments; `file_report(title, opts)` where `opts` has a required list `tags` and
      an optional boolean; `fail(reason)` returning a tool error; `slow(seconds)`; `picture()`
      returning text and an image; `sound()` returning audio; `attachment()` returning a binary
      resource; `stats()` returning structured data; `whoami()` returning the value of the
      environment variable `STUB_TOKEN` and printing it to its error output. Factory options:
      extra or renamed tools (for collisions and a bad tool name), no tools at all, and a tool
      description that contains `STUB_TOKEN`'s value. Run as `python -m tests.mcp_stub` it
      serves stdio; with `--http <port>` it serves Streamable HTTP at
      `http://127.0.0.1:<port>/mcp` (R13)
- [X] T008 [P] Implement classification in `src/freyja_agent/agent/catalog.py`:
      `Classification` with the values `read_only`, `workspace`, `gated`; `CatalogTool` with
      `name`, `server` (null for Freyja's own), `description`, `input_schema`, `classification`,
      `allowed_decisions`, `server_hints`; the fixed table for Freyja's own tools, in this
      order: `ls`, `read_file`, `glob`, `grep` are `read_only`; `write_file`, `edit_file`,
      `delete`, `task` are `workspace`; `send_notification` is `gated`; `execute` is known and
      not offered, so it has no catalog entry; `UnclassifiedToolError` naming the tool;
      `has_declared_input(schema)`, false when the schema has no `properties` or an empty one;
      a `Catalog` holding the entries in order with lookup by name; and a function that
      generates the `interrupt_on` mapping from the gated entries: `allowed_decisions` of
      approve, edit, reject, or approve and reject for a tool with no declared input, and a
      description of `<server>: <first line of the tool's description>` for a server tool, the
      existing sentence for `send_notification` (FR-006, FR-009, FR-011, R8)
- [X] T009 [P] Implement tool conversion in `src/freyja_agent/delegates/mcp_tools.py`: a
      `DelegateTool` record with `name`, `server`, `description`, `input_schema`,
      `server_hints`, `classification`, and the LangChain tool; hints mapped from the server's
      annotations to the keys `title`, `read_only`, `destructive`, `idempotent`, `open_world`,
      only those the server set, null when it set none; classification `read_only` if and only
      if the name is in the server's `read_only` list, otherwise `gated`; and a
      `StructuredTool` built with `name`, `description or ""`, `args_schema` set to the
      server's JSON Schema as a dict, `handle_tool_error=True`, and a coroutine that awaits the
      server link's call, returns the joined text of the result, and raises `ToolException` for
      any failure so that no failure ends a run (FR-006, R5, data-model.md "Delegate tool")
- [X] T010 Implement the server link in `src/freyja_agent/delegates/mcp_servers.py`:
      `default_connect(name, config)` returning the target for `mcp.Client`, which is the URL
      for an address entry, or for a command entry a stdio transport built from
      `StdioServerParameters` with only the entry's `pass_env` variables added to the SDK's
      default environment and with `errlog` set to the write end of a pipe; a redactor that
      replaces each exact `pass_env` value with `***`; and `ToolServer` with `name`,
      `connection` (`command` or `address`), the status fields `reachable`, `checked_at`, and
      `error` (redacted, at most 500 characters, null when reachable), and the listed tools. A
      command server holds one `Client` for the life of the service, entered and exited by one
      dedicated owner task that waits on a stop event and also reads the error-output pipe line
      by line, logging each redacted line at info level under the server's name. An address
      server opens one `Client` per use. `call(tool, arguments)` runs inside
      `asyncio.timeout(call_timeout)`, including connecting. Descriptions, the string values
      inside input schemas, and hints are redacted when the tools are listed (FR-002, FR-005,
      R3, R7, R12)
- [X] T011 Implement startup in `src/freyja_agent/delegates/mcp_servers.py`: the async context
      manager `open_tool_servers(configs, *, connect, call_timeout, startup_timeout)` and
      `ToolServerError`. In order: resolve `pass_env` for every command entry, failing with the
      server and the variable named when one is unset or empty; connect to all servers
      concurrently, each under the startup limit, and list its tools following pagination
      cursors; if any failed, close the ones that opened and raise naming every failed server
      in configuration order, with the innermost exception's type and redacted message; check
      that every name in `read_only` is a tool its server provides, naming server and tool on a
      miss; check that every tool name matches `^[A-Za-z0-9_-]{1,64}$` and is unique across all
      servers and Freyja's own tool names, naming the server or servers and the tool; log one
      line per server with its name, connection form, number of tools, and number read-only. A
      server with no tools is accepted. On exit, stop every owner task, which stops the
      launched processes; an address server is left running (FR-003, FR-004, FR-005, R4;
      depends on T009, T010)
- [X] T012 Change the factory in `src/freyja_agent/agent/factory.py` and
      `src/freyja_agent/agent/tools.py`: `build_agent(model, *, checkpointer, store,
      workspace_dir, max_steps, delegate_tools=(), system_prompt=DEFAULT_PROMPT)` returns
      `Orchestrator(agent, catalog)`; the delegate tools join `send_notification` in the tool
      list of the orchestrator and of the general-purpose subagent; `interrupt_on` for both
      comes from the catalog (T008); after compiling, read the tool names from the
      orchestrator's tool node and the subagent's and raise `UnclassifiedToolError` for any
      name that is neither a delegate tool nor a row of the fixed table; assert that the
      `interrupt_on` keys equal the gated catalog entries; build each of Freyja's own catalog
      entries from the compiled tool, with the JSON Schema of the arguments the model supplies.
      Stop using `GATED_TOOLS` and `SEND_NOTIFICATION_INTERRUPT` from `tools.py` here; T014
      removes them once the routes no longer import them (FR-003, FR-006, FR-007, FR-011, R8,
      R11; depends on T008, T009)
- [X] T013 Change the runtime in `src/freyja_agent/runtime/runtime.py`: `Runtime` gains
      `catalog` and `servers`; one shared async context manager assembles a runtime from a
      checkpointer, a store, a lock store, and a `connect` function by entering
      `open_tool_servers`, building the agent with the resulting tools, yielding, and closing
      the servers; `Runtime.open(settings, *, model=None, instance_id=None,
      connect=default_connect)` opens persistence, enters that manager, and closes the pool
      last, so a server that fails to connect closes the pool on the way out (FR-003, FR-005,
      R4, R11; depends on T011, T012)
- [X] T014 Switch the routes to the catalog without changing behavior, in
      `src/freyja_agent/api/threads.py`, `src/freyja_agent/api/decisions.py`, and
      `src/freyja_agent/agent/tools.py`:
      `build_resume_command` receives the gated catalog entries from `runtime.catalog` in place
      of the `GATED_TOOLS` mapping, and an edit for `send_notification` is validated as today;
      then remove `GATED_TOOLS` and `SEND_NOTIFICATION_INTERRUPT` from `tools.py`, which keeps
      only `send_notification` (R9, R11; depends on T012, T013)
- [X] T015 Extend `tests/conftest.py`: `make_runtime` unpacks `Orchestrator` and fills
      `catalog` and `servers`; a new fixture builds an app through `create_app(open_runtime=...)`
      using the shared assembly from T013 with in-memory persistence and a `connect` that
      returns stub server objects, so `mcp.Client` connects inside the test process; it takes
      the server list as configuration, returns the stubs so a test can read what each one
      received, and offers a wrapper target that fails on demand and can be told to recover
      (R13; depends on T007, T013)
- [X] T016 [P] Extend `tests/test_factory.py`: every tool in the compiled orchestrator and
      subagent has a catalog entry or is `execute`; an own tool missing from the fixed table
      raises `UnclassifiedToolError` naming it; with stub tools given as delegate tools, the
      `interrupt_on` keys equal the gated entries for the orchestrator and for the subagent; a
      gated tool with no declared input gets approve and reject only; the description of a
      gated server tool is `<server>: <first line>`; with no delegate tools the catalog holds
      Freyja's nine own tools with the classifications of the fixed table (FR-006, FR-007,
      FR-011; depends on T015)
- [X] T017 [P] Write `tests/test_mcp_startup.py`: with a stub configured, the tools are bound
      to the model on the first run and one log line names the server, its connection form,
      and its tool counts; a server with no tools starts and its line says zero; startup fails,
      naming what the spec requires, for an unreachable server, for two unreachable servers
      (both named, in configuration order), for a `read_only` name the server does not provide
      (server and tool), for two servers sharing a tool name, for a server tool named like one
      of Freyja's own, for a tool name outside `^[A-Za-z0-9_-]{1,64}$`, and for a `pass_env`
      variable that is unset and one that is empty (server and variable); a server that never
      answers fails at the startup limit; after each failure every server that did open has
      been closed; leaving the app's lifespan closes the connections (FR-003, FR-004, FR-005,
      SC-004; depends on T015)
- [X] T018 [P] Adjust `tests/test_gate.py`, `tests/test_state.py`, `tests/test_subagent.py`,
      and `tests/test_restart.py` for the `Orchestrator` return type and the moved gate
      configuration, with no change to what they assert (depends on T015)

**Checkpoint**: `uv run pytest` passes, including `test_factory` and `test_mcp_startup`. With
no server configured the service behaves exactly as before this feature.

---

## Phase 3: User Story 1 - Use a delegate's read-only tools without friction (Priority: P1) 🎯 MVP

**Goal**: A tool the operator listed as read-only is called in a run like one of Freyja's own
file tools: the call and its result stream, nothing pauses, and a tool error is a result the
run continues from.

**Independent Test**: With the stub configured and `lookup` listed as read-only, start a run
whose scripted model calls `lookup`, read the stream until it closes, and assert the result is
in the transcript, no `interrupt` event appeared, and the stub recorded exactly one call.
Manually: quickstart.md Scenario 2.

### Tests for User Story 1

- [X] T019 [US1] Write `tests/test_mcp_calls.py` with the read-only path: a run that calls
      `lookup` shows the call and its result in `update` events, has no `interrupt` event, ends
      with `end`, and the stub recorded one call with the proposed arguments; the same through
      the subagent, where the parent calls `task` and the subagent calls `lookup`; a call to
      `fail` produces a tool message with status `error` carrying the server's text, the run
      ends with `end`, the lease is released, and one warning names the server, the tool, and
      `tool_error` (US1 scenarios 2 to 4, FR-010, FR-012, SC-002)
- [X] T020 [US1] Add result cases to `tests/test_mcp_calls.py`: `picture` delivers its text
      and `[image omitted: <mime type>]`; `sound` delivers `[audio omitted: <mime type>]`;
      `attachment` delivers `[file omitted: <mime type>]`; a result made only of such parts is
      delivered as those notes with status `success`; no binary data is in the thread state;
      `stats` delivers its structured data once, not twice; a result with structured content
      and no text block delivers the structured content as JSON; ten concurrent `lookup` calls
      on different threads each receive their own value (FR-013, FR-018, R6)

### Implementation for User Story 1

- [X] T021 [US1] Implement result conversion in `src/freyja_agent/delegates/mcp_tools.py`: one
      string built from the content blocks in order and joined with blank lines; a text block
      and an embedded resource with text contents become their text; an image becomes
      `[image omitted: <mime type>]`, audio `[audio omitted: <mime type>]`, an embedded
      resource with binary contents `[file omitted: <mime type>]`, a resource link
      `[resource link omitted: <uri>]`, anything else `[<type> content omitted]`; structured
      content is appended as JSON only when the result has no text block; a result whose
      `is_error` is true is converted the same way and raised as `ToolException` (FR-018, R6)
- [X] T022 [US1] Complete the successful and tool-error outcomes of `ToolServer.call` in
      `src/freyja_agent/delegates/mcp_servers.py`: result text and error text pass through the
      server's redactor; a result marks the server reachable; a tool error also marks it
      reachable and logs one warning with the server, the tool, and `tool_error`; no log line
      or error includes the arguments (FR-002, FR-012, R7)

**Checkpoint**: User Story 1 works on its own; `test_mcp_calls` passes.

---

## Phase 4: User Story 2 - Every effectful delegate tool waits for a person (Priority: P2)

**Goal**: A server tool that is not on the read-only list pauses before the call with the
server, the tool, and the arguments; approve, edit, and reject behave as they do for
`send_notification`; an edit is checked against the tool's declared input; and a decision is
applied under the configuration in force.

**Independent Test**: With the stub configured and only `lookup` listed as read-only, script a
proposal for `record_note`, observe the pause, then on separate threads approve, edit, and
reject, and assert from the stub's record that it received exactly the expected calls.
Manually: quickstart.md Scenario 3.

### Tests for User Story 2

- [X] T023 [US2] Write `tests/test_mcp_gate.py` with the decisions: a proposal for
      `record_note` ends the stream with `interrupt` whose action request has `name`, `server`,
      the proposed `args`, a description of `<server>: <first line>`, and approve, edit, reject;
      the stub received nothing; thread state shows the same proposal; approve makes exactly
      one call with the proposed arguments; an edit makes one call with the edited arguments;
      a reject with a reason makes no call and the reason reaches the model; `record_note` is
      gated although the server labels it read-only; a tool the configuration does not mention
      is gated; a proposal made by the subagent pauses the same way and its decision applies to
      that call; a step proposing `record_note` together with `lookup` pauses before either is
      called, and both run after the decision; a step proposing two gated tools lists both with
      their servers and applies each decision to its own call; a proposal for
      `send_notification` has `server: null` (US2 scenarios 1 to 8, FR-006, FR-007, FR-008,
      SC-001)
- [X] T024 [US2] Add edit validation cases to `tests/test_mcp_gate.py`: for `file_report`, a
      wrong type is refused with 422 and a location ending in `args` and the field; a wrong
      element in the nested list is refused with its full path; a missing required property is
      refused with the property appended to the location; an argument the schema's `properties`
      do not name is refused; after each refusal the proposal is still pending, the stub
      received nothing, and a valid edit then goes through; the pause for `ping` offers approve
      and reject only, and an edit for it is refused with 422 (US2 scenario 3, FR-009, R9)
- [X] T025 [US2] Add the changed-configuration cases to `tests/test_mcp_gate.py`, each pausing
      under one server list and resuming on a second runtime that shares the persistence and
      has another list: the tool still gated is decided as usual and an edit is checked against
      the current schema; the tool removed shows `server: null` with approve and reject, both
      are accepted, the stub receives nothing, and the model is told the tool is unavailable;
      the tool now read-only shows approve only, approve runs it as proposed, and an edit or a
      reject is refused with 422 saying the tool no longer needs a decision; a `lookup` call in
      the paused step that has become gated makes the resume end with an `error` event whose
      code is `internal`, the stub receives nothing, the log holds the gate's message, the
      thread keeps the same `interrupt_id`, a new message on it is refused with
      `decision_pending`, and restoring the first list lets the proposal be decided; on a thread with nothing pending, a new call to a tool
      that is no longer configured comes back as an error result and the run continues
      (FR-019, R9, spec edge case "a server is removed from the configuration")
- [X] T026 [P] [US2] Update the expected proposal shape in `tests/test_gate.py`,
      `tests/test_state.py`, and `tests/test_subagent.py`: every action request carries
      `server`, which is `null` for `send_notification` (FR-008)

### Implementation for User Story 2

- [X] T027 [P] [US2] Add `server: str | None` to `ActionRequest` in
      `src/freyja_agent/api/schemas.py`, and add `server` to `ActionRequest` in
      `specs/001-thread-runs-api/contracts/openapi.yaml` so the two contract files agree
      (FR-008, plan Implementation Notes)
- [X] T028 [US2] Change `interrupt_to_pending` in `src/freyja_agent/api/serialize.py` to take
      the catalog: `server` is filled by tool name; `allowed_decisions` for each action is
      computed against the current catalog, as the catalog says for a tool still gated, approve
      and reject for a tool not in the catalog, and approve only for a tool that is now
      read-only; a tool the catalog does not know gets `server: null`.
      Pass `runtime.catalog` at every call site in `src/freyja_agent/api/streaming.py` and
      `src/freyja_agent/api/threads.py` (FR-008, FR-019, R9; depends on T027)
- [X] T029 [US2] Validate edits for server tools in `src/freyja_agent/api/decisions.py`: for a
      tool whose input is a JSON Schema dict, run `jsonschema`'s validator for that schema over
      the edited arguments and turn each error into an entry with `type`, `loc` of
      `["body", "decisions", <index>, "args", *path]`, and the validator's message, appending
      the property name to `loc` for a missing required property; refuse a top-level argument
      that the schema's `properties` do not name, unless the schema itself allows additional
      properties, in the entry shape feature 001 uses for the same refusal; keep the tool name
      pinned; leave the pydantic path for `send_notification` as it is (FR-009, R9, feature 001
      FR-013)
- [X] T030 [US2] Apply a decision under the configuration in force in
      `src/freyja_agent/api/decisions.py`: check each decision against its action's
      `allowed_decisions` from T028, so an edit or a reject for a tool that no longer needs a
      decision is refused with 422 and that explanation; build the resume payload from the
      decisions for the still-gated actions only, in order, because those are the ones the
      gate asks for when it runs again (FR-019, R9; depends on T028, T029)

**Checkpoint**: User Stories 1 and 2 both work; `test_mcp_gate` passes, and `test_gate`,
`test_state`, and `test_subagent` pass with the extended proposal shape.

---

## Phase 5: User Story 3 - See what Freyja can call (Priority: P3)

**Goal**: `GET /tools` lists every tool the orchestrator was given, with its origin,
description, declared input, classification, and the server's own hints, plus each server's
last known status, and never contacts a server.

**Independent Test**: Start the app with one stub configured, read the catalog, and assert it
lists Freyja's own tools and the stub's tools with the classification the configuration
implies. Manually: quickstart.md Scenario 1.

### Tests for User Story 3

- [X] T031 [US3] Write `tests/test_catalog.py`: the tool names equal the names the model was
      offered; Freyja's own tools come first in the fixed order, then each server's in
      configuration and listing order; every entry has `name`, `server`, `description`,
      `input_schema`, `classification`, `allowed_decisions`, and `server_hints`;
      `allowed_decisions` is empty unless the tool is gated, and lacks edit for `ping`; with no
      server configured only Freyja's own tools appear and `send_notification` is gated;
      `record_note` is gated while its `server_hints` say `read_only: true`; a tool whose
      server publishes no hints has `server_hints: null`, as do Freyja's own; `servers` has one
      entry per configured server with `name`, `connection`, `reachable`, `checked_at`,
      `error`, and `tool_count`, including a server with no tools; the response contains no
      `command`, `args`, `url`, or `pass_env` value; reading the catalog makes no connection
      and no call on the stub; the response arrives within 1 second (US3 scenarios 1, 3, and 4,
      FR-014, SC-003)

### Implementation for User Story 3

- [X] T032 [P] [US3] Add `ToolCatalog`, `ToolServerStatus`, `CatalogTool`, and `ServerHints` to
      `src/freyja_agent/api/schemas.py` per `contracts/openapi.yaml`: `ToolServerStatus.name`
      with pattern `^[a-z][a-z0-9_-]{0,31}$`, `connection` one of `command` and `address`,
      `error` nullable with a maximum length of 500, `tool_count` at least 0;
      `CatalogTool.classification` one of `read_only`, `workspace`, `gated`; `ServerHints`
      with only the optional keys `title`, `read_only`, `destructive`, `idempotent`,
      `open_world`, serialized without the keys the server did not set
- [X] T033 [US3] Implement `GET /tools` in `src/freyja_agent/api/tools.py`: return the tools
      from `runtime.catalog` and one status per entry of `runtime.servers`, read from memory
      with no call to any server (FR-014, R10; depends on T032)
- [X] T034 [US3] Register the tools router in `create_app` in `src/freyja_agent/api/app.py`

**Checkpoint**: User Stories 1 to 3 work; `test_catalog` passes.

---

## Phase 6: User Story 4 - Find out quickly when a server misbehaves (Priority: P4)

**Goal**: A server that is slow, gone, or back again produces a clear error result, a log line
naming it, and the right status in the catalog, and a credential it was given never shows up
anywhere. The startup half of this story was built in Phase 2.

**Independent Test**: With the stub behind the failing wrapper, make a call while it is down,
assert the error result, the warning, and `reachable: false` in the catalog, then let it
recover and assert the next call succeeds without a restart. Manually: quickstart.md
Scenarios 4 and 5.

### Tests for User Story 4

- [ ] T035 [US4] Add failure cases to `tests/test_mcp_calls.py`: with a short call limit,
      `slow` ends in an error result that names the limit and says its effect may or may not
      have happened, the stub recorded one call and no second attempt, the run ends with `end`,
      the lease is released, and a following run on the thread works; a server that cannot be
      reached gives an error result saying so, one warning naming the server, the tool, and
      `unreachable`, and a timed-out call one naming `timeout`; after either, the catalog shows
      the server with `reachable: false`, a reason of at most 500 characters, a later
      `checked_at`, and its tools still listed; once the wrapper recovers, the next call
      succeeds with no restart and the catalog shows `reachable: true` and `error: null`; an
      approved gated call that times out is not repeated (US4 scenarios 4 and 6, US3
      scenario 2, FR-012, SC-005)
- [ ] T036 [US4] Add the credential cases to `tests/test_mcp_calls.py`, with `STUB_TOKEN` set
      and named in `pass_env`: its value is absent, and `***` present where the text would have
      been, in the result of `whoami`, in an error result, in every stream event of the run, in
      the thread state, in the captured log, in the catalog's `error`, and in a tool
      description, an input schema string, and a hint that the stub was built to contain it; a
      startup failure of that server does not contain the value either (US4 scenario 5,
      FR-002, SC-008, R12)
- [ ] T037 [P] [US4] Write `tests/test_mcp_transports.py`, which uses only the loopback
      interface and the test interpreter. Over pipes, launching `python -m tests.mcp_stub`
      through `default_connect`: a call succeeds; the launched process sees a `pass_env`
      variable and does not see another variable set in the service's environment; what the
      stub prints to its error output arrives in the log, redacted, under the server's name,
      and not on the service's own error output; after the process is killed every call gives
      an error result and the catalog shows it unreachable, and nothing launches it again;
      closing the runtime ends a process that is still alive. Over HTTP on `127.0.0.1` with an
      ephemeral port: a call succeeds; with the server stopped a call fails and marks it
      unreachable, and the catalog's `error` for it does not contain the address; after it
      restarts on the same port the next call succeeds and marks it reachable; an endpoint
      that answers with a redirect to a second loopback listener is not followed, the call
      fails, and the second listener receives no request; closing the runtime leaves the
      server running (FR-001, FR-002, FR-005, FR-012, FR-014, FR-017, R2, R3, R13)

### Implementation for User Story 4

- [ ] T038 [US4] Complete the failure outcomes of `ToolServer.call` in
      `src/freyja_agent/delegates/mcp_servers.py`: at the time limit, mark the server
      unreachable and raise a `ToolException` that names the server, the tool, and the limit
      and says the outcome is unknown; for any other exception, take the innermost one out of
      an `ExceptionGroup`, mark the server unreachable with its type and redacted message cut
      to 500 characters, and raise a `ToolException` saying the server could not be reached;
      log one warning per failure with the server, the tool, and `timeout` or `unreachable`;
      set `checked_at` on every outcome; never retry, never relaunch a process, and start no
      background check (FR-012, R7)
- [ ] T039 [US4] Make every text that leaves a server link pass through its redactor in
      `src/freyja_agent/delegates/mcp_servers.py`: startup errors, the status `error`, log
      lines, and the lines read from a launched server's error output; replace the server's
      configured URL with `<address>` in the status `error`, which the catalog shows; confirm
      no log line or error includes arguments, the environment, or a URL's query string
      (FR-002, FR-014, R12)

**Checkpoint**: All of User Stories 1 to 4 work; `test_mcp_calls` and `test_mcp_transports`
pass with the network disconnected.

---

## Phase 7: User Story 5 - Bring a real server up locally (Priority: P5)

**Goal**: A developer following only the README configures the reference git server, reads the
catalog, completes a read-only run, and approves an effectful one.

**Independent Test**: quickstart.md, all six scenarios, against the real service with a model
provider, local Postgres, and `uvx mcp-server-git`.

### Implementation for User Story 5

- [ ] T040 [US5] Document the feature in `README.md` as it now exists: how to describe a tool
      server in `FREYJA_MCP_SERVERS` in both forms, what the read-only list means and that
      silence means gated, how credentials are passed with `pass_env`, the two time limits,
      how to read `GET /tools`, that a proposal names its server, what happens when a server
      fails at startup and during a run, the reference git server as the one to try, and that
      the suite needs no network and no installed server (FR-016)
- [ ] T041 [US5] Run every scenario of `specs/002-mcp-tool-delegation/quickstart.md` against
      the real service and record the outcome of each in the pull request description; correct
      `quickstart.md` or `README.md` where an instruction or an expected result is wrong, and
      add a fix task to this phase for anything that needs a code change (US5 scenarios 1 and
      2, SC-006)

**Checkpoint**: a developer reaches a read-only run and an approved effectful run with a real
local server in under 20 minutes, following the README alone.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T042 [P] Update `AGENTS.md`: the Layout entries for `src/freyja_agent/delegates/`,
      `agent/catalog.py`, `api/tools.py`, and `tests/mcp_stub.py`; the changed return type of
      `build_agent`; the three settings; and the Commands note that the default suite needs no
      installed MCP server
- [ ] T043 [P] Update the documentation tree in
      `specs/002-mcp-tool-delegation/plan.md` to list `checklists/security.md` and `tasks.md`
      as they exist, and record under Implementation Notes anything the implementation settled
      differently from the plan
- [ ] T044 Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest`, and
      `uv run pre-commit run --all-files`; confirm no Ruff suppression was added and that the
      suite passes with the network disconnected (SC-007, principle V)
- [ ] T045 Re-run the plan's Constitution Check against the implemented code and note the
      result in the pull request description (principles I, II, III, IV, V, VI)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies. T002, T003, T005, T006 run in parallel after T001;
  T004 follows T002 and T003.
- **Foundational (Phase 2)**: depends on Phase 1. Blocks every user story.
- **User Story 1 (Phase 3)**: depends on Phase 2. The MVP.
- **User Story 2 (Phase 4)**: depends on Phase 3, because approve and edit end in a call.
- **User Story 3 (Phase 5)**: depends on Phase 2 only.
- **User Story 4 (Phase 6)**: depends on Phases 3 and 5: its tests make calls and read the
  status from the catalog. One case in T035 needs Phase 4 for the approved call.
- **User Story 5 (Phase 7)**: depends on Phases 3 to 6.
- **Polish (Phase 8)**: depends on all stories.

### Within Phase 2

- T007, T008, T009 have no dependency inside the phase.
- T010 before T011 (same file); T009 before T011.
- T008 and T009 before T012; T011 and T012 before T013; T013 before T014.
- T007 and T013 before T015; T015 before T016, T017, T018.

### Within Each User Story

- Tests first, and they must fail before implementation starts.
- Tasks that share a file run in the order listed: T019 then T020; T023, T024, T025; T029 then
  T030; T038 then T039.
- T027 before T028; T028 before T030; T032 before T033; T033 before T034.

### Parallel Opportunities

- Phase 1: T002, T003, T005, T006 together.
- Phase 2: T007, T008, T009 together; later T016, T017, T018 together.
- Phase 4: T026 alongside T023 to T025; T027 alongside the tests.
- Phase 5: T032 alongside T031.
- Phase 6: T037 alongside T035 and T036.
- Phase 8: T042 and T043 together.
- Phase 5 can be built at the same time as Phases 3 and 4, since it depends on Phase 2 only.

---

## Parallel Example: User Story 2

```bash
# Write the tests, then watch them fail:
Task: "Decisions on a gated server tool in tests/test_mcp_gate.py (T023, T024, T025)"
Task: "Extended proposal shape in tests/test_gate.py, test_state.py, test_subagent.py (T026)"

# Then implement in order:
Task: "ActionRequest.server in src/freyja_agent/api/schemas.py and the 001 contract (T027)"
Task: "interrupt_to_pending with the catalog in src/freyja_agent/api/serialize.py (T028)"
Task: "JSON Schema validation and the changed-configuration rules in src/freyja_agent/api/decisions.py (T029, T030)"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup.
2. Complete Phase 2: Foundational. This is the bulk of the new code, and it already gates every
   effectful tool.
3. Complete Phase 3: User Story 1.
4. **STOP and VALIDATE**: `uv run pytest`, then quickstart.md Scenario 2 with the git server.
5. Open a pull request if the increment should merge on its own.

### Incremental Delivery

1. Setup + Foundational: the service starts with tool servers configured, refuses a bad
   configuration, and every tool is classified and gated.
2. User Story 1: read-only server tools in a run (MVP).
3. User Story 2: decisions on effectful server tools, the constitution's core requirement.
4. User Story 3: the catalog.
5. User Story 4: failures during a run, recovery, and the credential checks.
6. User Story 5: README and the manual quickstart pass.
7. Polish.

### Notes

- [P] tasks touch different files and have no dependency on an incomplete task.
- Every test task names the FRs it covers; the FR coverage map in research.md R13 is the
  reference for completeness.
- Between Phase 2 and Phase 6 a call already runs under the time limit and never ends a run;
  Phase 6 adds the distinct error texts, the status record, and the recovery behavior.
- T029 refuses an edit argument the tool's schema does not name, as FR-009 and research R9
  state since the analysis of 2026-11-06; feature 001's FR-013 applies the same rule to every
  action.
- Commit after each phase at least; commits stay manual and use the `type: summary` form.
