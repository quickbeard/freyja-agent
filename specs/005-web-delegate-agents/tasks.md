# Tasks: Agents Section of the Web Console

**Input**: Design documents from `/specs/005-web-delegate-agents/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/openapi.yaml,
contracts/sse-events.md, quickstart.md

**Tests**: Included. FR-032 requires automated coverage of the service and of the web app with
no network and no agent installed or running, and an end-to-end test in a browser for the
console's part of every user story; principle V requires new behavior to ship with tests. In
each user story phase the test tasks come first: write them, watch them fail, then implement.
Two kinds of test task come after the code they cover: the tests of Phase 3 that need the new
fixture, which is built on the code, and each story's end-to-end task, which drives the built
console.

**Organization**: Tasks are grouped by user story so each story is an independently testable
increment. The foundation is split over two phases (2 and 3): first what is kept and the pieces
that touch an address or a card, with nothing wired, then the agent set and the one
orchestrator a run uses. Research references (R1 to R22) point at `research.md`. "The
stand-in" is the agent in `tests/a2a_stub.py`; "the fixture" is `make_a2a_app` in
`tests/conftest.py`; "a context" is the `RunContext` of R3.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1 to US6)
- Every task names the file it touches

## Path Conventions

Two projects in one repo: the service in the src layout (`src/freyja_agent/`, `tests/`,
repo-root files) and the web app under `web/` (`web/app/`, `web/components/`, `web/lib/`). Web
unit tests are `*.test.ts` and `*.test.tsx` files under `web/__tests__/`, which follows the
layout of the code it covers. End-to-end tests are `*.spec.ts` files under `web/e2e/`.

## Standing rules for every task

- **Approval.** At the end of every phase, no message reaches an agent that requires approval
  without an approve or edit decision for it. The gate stays generated from the catalog inside
  `build_agent`. A route is merged in the same phase as the rule that keeps it safe: adding
  with the rule that a proposal stays with the agent it was made for, and with approval
  always required (Phase 5); changing an approval choice, and adding without approval, with
  the check before a send (Phase 7); moving and removing with the rules for tasks and
  addresses (Phase 8).
- **The address.** At the end of every phase, a saved agent's address is in no response
  outside the Agents routes, no stream, no thread, and no log line. The scrubber and the log
  record factory are in place in Phase 3, before Phase 5 adds the first route that takes an
  address, and that route is created with validation errors that carry no `input` (R12, R16).
  The proof, `tests/test_agents_address_nowhere.py`, starts in Phase 5 and grows with every
  phase that adds a route.
- **Other origins.** The Agents router is created with `refuse_other_origins`, so every route
  that changes something refuses a page of another origin from the phase that adds it (R16).
- **The stand-in only.** Every agent in a test, an example, or a manual check is
  `tests/a2a_stub.py`. No task hands work to any other agent. A manual check runs with
  `FREYJA_A2A_AGENTS` unset or naming stand-ins only.
- Every resume request in a test or an example sends the proposal's `interrupt_id`
  (feature 001, FR-013).
- No new Ruff suppression; no blocking I/O in async code; SQL only with bound parameters
  (principle V, Ruff `ASYNC` and `S608`).
- The service suite opens no socket. A stand-in runs in the test process, reached through
  `StreamingASGITransport`; only `tests/test_a2a_transport.py` serves one on loopback.
- Before writing any code under `web/`, read `web/AGENTS.md` and the guide it points to in
  `web/node_modules/next/dist/docs/`. No Server Action, no Route Handler, no `use cache`, no
  `export const dynamic`, and no clock read (`Date.now()`, `new Date()`) during render.
- What an agent's card says, and every reason, is rendered as a text node. Nothing builds
  markup or a link from it (FR-002).
- Files under `web/components/ui/` are not edited by hand. Web unit tests replace `fetch` with
  a stub; no web test reaches the service. An end-to-end test hands the page the service's
  answers through `web/e2e/api.ts`.
- A manual check stops any service it started by its port (`ss -ltnp`), never by a process
  pattern.
- Each phase is one pull request and one merge request, and its description has a Tests section
  (AGENTS.md). Before a phase's request is opened, its diff is read by a reviewer that did not
  write it, and each finding is checked against the code before it is acted on.
- A task that says "record in plan.md" writes under a heading `Implementation Notes` in
  `specs/005-web-delegate-agents/plan.md`, creating it the first time.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Three small changes the later phases build on. None changes what the service does.

- [X] T001 Create `src/freyja_agent/api/guards.py` and move `NoInputRoute`, `READS`,
      `OWN_SITE`, and `refuse_other_origins` into it from `src/freyja_agent/api/llm.py`. The
      last sentence of the refusal names what is being changed, so make it
      `refuse_other_origins(what: str)`, which returns the dependency: `"Profiles"` gives
      today's message word for word. `api/llm.py` imports both names and builds its router with
      `refuse_other_origins("Profiles")`; `NoInputRoute` stays importable from `api/llm.py`.
      `uv run pytest tests/test_llm_profiles_api.py` passes with no test changed (R16)
- [X] T002 [P] Give `CheckSlots` in `src/freyja_agent/llm/check.py` the words of its two
      refusals when it is made: `CheckSlots(busy=..., full=..., at_once=TESTS_AT_ONCE)`, with
      today's sentences as the defaults so `Runtime.checks` is unchanged. Add one test to
      `tests/test_llm_check.py`: slots made with other words refuse in those words, and the
      existing tests pass unchanged (R15)
- [X] T003 [P] Extend `tests/a2a_stub.py` with what the card tests need, leaving every existing
      behavior and default as it is: `stub_card(..., skill_count=..., tag_count=...,
      text_length=...)` for a card with many skills, many tags, or long texts; `make_stub(...,
      card_body=bytes, card_status=int)` to serve any bytes with any status in place of the
      card, so a test can serve a document larger than one megabyte, a body that is not a card,
      and a 404; `make_stub(..., card_encoding="gzip")` to serve the card compressed; and
      `make_stub(..., card_chunked=True)` to send the body with no length announced. `uv run
      pytest tests/test_a2a_*.py` passes unchanged (plan.md, tests tree)

**Checkpoint**: `uv run pytest` passes with every earlier test unchanged.

---

## Phase 2: Foundational, part 1 - What is kept, and the pieces that touch an address or a card

**Purpose**: The address rule, the scrubber and the log record factory, the card read, the
saved-agent store, and what the task store gains. Nothing is wired: after this phase the
service reads no card through the new code, saves no agent, and behaves exactly as before. The
two agent tables and the one new column are in this phase.

**CRITICAL**: No user story work can begin until Phases 2 and 3 are complete.

- [X] T004 [P] Write `tests/test_addresses.py`, part 1, for `canonical_address` and
      `destination` in `src/freyja_agent/config.py`. The kept form: `HTTP://LocalHost:9998/`
      gives `http://localhost:9998`; a port left out is written, 80 for `http` and 443 for
      `https`; `http://127.0.0.1:9998/a/` gives the path without its trailing slash;
      `http://[0:0:0:0:0:0:0:1]:9998` gives `http://[::1]:9998`; `127.0.0.2` is accepted as a
      loopback address. Each of these is refused with a sentence of the function's own that
      names the rule and never repeats what was entered: a scheme that is not `http` or
      `https`; a host that is not `localhost` or a loopback IP address (`example.com`,
      `192.168.1.5`, `localhost.example.com`); a user name; a password; a query; a fragment; a
      path with an empty segment, a `.` segment, a `..` segment, a `%`, or a backslash; more
      than 2,000 characters; and anything the parser itself refuses, such as `http://[::1` and
      `http://localhost:abc`, whose own error is caught. `destination` is the port and the
      path: `http://localhost:9998/a`, `http://127.0.0.1:9998/a`, and `https://[::1]:9998/a`
      give one value, and `/a` and `/b` on one port give two (FR-006, R11)
- [X] T005 Write `tests/test_addresses.py`, part 2, for the scrubber and the log record factory
      in `src/freyja_agent/delegates/addresses.py`. The scrubber removes a registered address
      as kept, as its host and port, as its port after `localhost`, `127.0.0.1`, and `[::1]`,
      and as the pairs `('127.0.0.1', 9998)`, `('localhost', 9998)`, and `('::1', 9998)`,
      longest form first; text with no registered address comes back unchanged; an address
      registered and never marked as saved is forgotten only after 200 later ones; one marked
      as saved is kept until the process ends. The factory, on real lines: httpx's `HTTP
      Request: GET http://127.0.0.1:9998/.well-known/agent-card.json "HTTP/1.1 200 OK"`;
      httpcore's `connect_tcp.started host='127.0.0.1' port=9998`, where the port stands apart;
      a traceback whose cause chain holds `Connect call failed ('127.0.0.1', 9998)`. A record
      keeps the number and kind of its arguments: uvicorn's access line, made with five
      arguments, still formats through `uvicorn.logging.AccessFormatter`. An argument that is
      not text is replaced by its cleaned text only when cleaning changes it. A traceback is
      cleaned into the record's own traceback text, and the record's reference to the exception
      is dropped when cleaning changed it, so a handler that formats again finds no address.
      The bare port number is removed in records of the loggers `httpx`, `httpcore`, and `a2a`
      and their children, and left alone in a record of any other logger. Installing the
      factory twice installs it once, and the factory that was there before is still called
      (FR-028, R12)
- [X] T006 [P] Write `tests/test_agent_cards.py` for `read_card` and the card details in
      `src/freyja_agent/delegates/agent_cards.py`, reaching stand-ins through
      `StreamingASGITransport`. A usable card is returned after exactly one request: a `GET` of
      `<address>/.well-known/agent-card.json` with `Accept-Encoding: identity`. Each problem
      raises `CardProblem` with its code and its sentence, word for word as data-model.md gives
      them: `no_answer` "Nothing answered at the address."; `timeout` "The agent did not answer
      within N seconds."; `no_card` "Something answered at the address, but it served no agent
      card."; `unreadable` "The agent's card could not be read."; `redirect` "The address
      answered with a redirect, which is not followed.", with only the configured host
      requested; `too_large` "The agent's card is larger than one megabyte.", with the read
      stopped once one megabyte of the body has arrived, also when no length was announced;
      `auth_required` "The agent's card requires authentication, which is out of scope.";
      `no_text` "The agent's card does not accept text messages."; `no_interface` "The agent's
      card offers no JSON-RPC interface.". A card that names no input modes is usable. A card
      served compressed although the request asked for it unencoded is `unreadable`, and its
      body is not decoded. Anything else the read meets is `unreadable`. No sentence holds text
      from the agent, from a library, or an address. The card details: each text is cut to its
      first 2,000 characters, `skills` to the first 50 and a skill's `tags` to the first 50,
      `cut` is true when anything was cut and false otherwise, and every text has every address
      the scrubber knows removed, another agent's included (FR-002, FR-007, FR-016, R5)
- [X] T007 [P] Write `tests/test_saved_agent_store.py` as contract tests any store can be
      given, exported in `__all__`. An added agent has an `id` of 32 hex characters, `version`
      1, `address_version` 1, `read_only` false unless given, its card details and
      `card_read_at`, and is listed by name and read back by id. A second agent under the same
      name is refused, and so is one whose address leads where another's does
      (`http://localhost:9998` after `http://127.0.0.1:9998`), and the 21st agent; each refusal
      is the store's own error, holds nothing of the address or of the database's text, and
      leaves the revision where it was. The revision starts at 0 and moves by one for an add, a
      change of the approval choice, a move, and a removal. A change of the approval choice
      adds one to `version` and leaves `address_version`; a move adds one to both and replaces
      the card details; one call can do both. A change or a removal that names a version that
      is no longer current changes nothing and reports that the agent changed, or that it is
      not found when it is gone. An agent removed and added again under its name has another
      `id`. A move and a removal give up every pending task whose `agent_ref` is one of this
      agent's, at any `address_version`, and return how many: each is `given_up` with its
      outcome ready and the text it was given; a task that had ended keeps its outcome; a task
      of another agent, and a task with a null `agent_ref` under the same name, are left alone.
      `replace_card(id, ref, card)` replaces the details and moves the revision when they
      differ, leaves both when they are equal, never moves `version`, and reports that nothing
      was recorded when `ref` is no longer the agent's. The pending count is reported by saved
      agent id (FR-005, FR-006, FR-012, FR-015, FR-024, R1, R2, R10, R14, data-model.md)
- [X] T008 [P] Extend the contract tests in `tests/test_agent_task_store.py`: a task is
      recorded and read back with `agent_ref`, null by default; `give_up_pending(refs, text)`
      gives up exactly the pending tasks whose `agent_ref` is in `refs`, marks each outcome
      ready, and returns the count; `pending_counts()` counts pending tasks by agent name and
      `agent_ref` (R2, R10, data-model.md)
- [X] T009 Implement `canonical_address(url)` and `destination(url)` in
      `src/freyja_agent/config.py`, beside `is_local_host`, with an error type of their own
      whose message is the service's sentence. `A2aAgentConfig` is not changed: environment
      entries keep their rule and their spelling (FR-006, R11)
- [X] T010 Implement `src/freyja_agent/delegates/addresses.py`: the scrubber, with `register`,
      `mark_saved`, and `scrub(text)`; the forms of T005; the bound of 200 on addresses that
      were never saved; and `install_log_scrubber()`, which sets the log record factory once
      per process, wrapping whichever factory was there. Nothing imports it yet (FR-028, R12)
- [X] T011 [P] Implement `src/freyja_agent/delegates/agent_cards.py`: `CardProblem` with the
      nine codes and sentences of data-model.md; `read_card(http, address, *, limit)`, which
      makes the one request of T006 with the client it is given, reads at most one megabyte,
      parses with the SDK's `parse_agent_card`, and applies today's three checks; and
      `card_details(card, scrub)`, which gives the cut and cleaned `AgentCardSummary`. Move
      `SkillSummary`, `AgentCardSummary`, and `card_problem` here from
      `src/freyja_agent/delegates/a2a_agents.py`, which imports them back so every existing
      import keeps working; `AgentCardSummary` gains `cut: bool = False`. The path constant
      stays the SDK's (FR-002, FR-007, FR-016, R5)
- [X] T012 Extend `src/freyja_agent/runtime/agent_tasks.py`: `AgentTaskRecord.agent_ref: str |
      None = None`; the two methods of T008 on the protocol, on `InMemoryAgentTaskStore`, and
      on `PostgresAgentTaskStore`; and the column: `setup()` looks in
      `information_schema.columns` and, only when `freyja_agent_tasks` has no `agent_ref`, runs
      `ALTER TABLE freyja_agent_tasks ADD COLUMN IF NOT EXISTS agent_ref text` under `SET LOCAL
      lock_timeout = '5s'`, raising a startup error that names the table when the lock cannot
      be had. Put the three give-up texts here as templates that take the task id and the
      agent's name, moved from `delegation_worker.py`, which uses them from here: today's two,
      word for word, and the one for a move, "Freyja stopped waiting for task {task_id} of
      agent '{agent}', which was moved to another address; the agent may still be working on it
      where it was." (FR-024, R2, R10)
- [X] T013 Implement `SavedAgentRecord` with its `ref` (`<id>:<address_version>`), the
      `SavedAgentStore` protocol, its errors, and `InMemorySavedAgentStore` in
      `src/freyja_agent/runtime/saved_agents.py`, with the rules of data-model.md: `id` "32 hex
      characters, new for every add"; `name` "`^[a-z][a-z0-9_-]{0,31}$`", unique; `url` the
      canonical form, "at most 2,000 characters"; `destination` unique, from
      `destination(url)`; `read_only` "boolean, default false"; `address_version` "integer,
      default 1"; `version` "integer, default 1"; "At most 20 rows". The in-memory store is
      given the in-memory task store and gives tasks up through `give_up_pending`, the one
      place the two meet (R1, R10)
- [X] T014 Implement `PostgresSavedAgentStore` in `src/freyja_agent/runtime/saved_agents.py` on
      the shared pool, creating `freyja_agents` and the one-row `freyja_agents_state` with
      `CREATE TABLE IF NOT EXISTS`: `name` and `destination` `UNIQUE`; `CHECK (id = 1)` on the
      state; the insert counted under a transaction-level advisory lock of its own; a unique
      violation turned into the store's error with nothing of the database's text; a change and
      a removal made `WHERE id = %(id)s AND version = %(version)s`; a move and a removal that
      change the row, give up the agent's pending tasks in `freyja_agent_tasks`, and move the
      revision in one transaction; times from the database's `now()`; every statement with
      bound parameters (FR-005, FR-006, FR-024, R1, R10, R14)
- [X] T015 Write `tests/integration/test_saved_agents_postgres.py`: the contract tests of T007
      collected against Postgres, and the task store's of T008 added by name to the imports and
      `__all__` of `tests/integration/test_a2a_tasks_postgres.py`; two adds sent together at 19
      agents leave 20; two adds of one name, and two of one destination, sent together on two
      pools leave one; when the last statement of a move or of a removal fails, the agent's row
      and its tasks are as they were; `setup()` run twice changes nothing; on a database whose
      `freyja_agent_tasks` has no `agent_ref`, `setup()` adds it and earlier rows read back
      with null; with the column present, `setup()` completes while another connection holds
      the table in an open transaction, which shows it asked for no lock; without the column
      and with that transaction open, `setup()` fails within the timeout naming the table.
      Record in plan.md that the column check and the lock timeout were seen on the Postgres of
      `docker-compose.yml` (FR-006, FR-024, R2, research "Not verified")

**Checkpoint**: `uv run pytest` passes with every earlier test unchanged, and
`FREYJA_TEST_DATABASE_URI=... uv run pytest tests/integration` passes. The service starts and
behaves as before; nothing it runs imports `addresses.py`, `agent_cards.py`'s `read_card`, or
`saved_agents.py`.

---

## Phase 3: Foundational, part 2 - The agent set, and one orchestrator from validation to the end of a run

**Purpose**: The service obtains its agents through the registry. A saved agent written to the
store appears in the next run, with no route yet. Every reader of the graph or the catalog
takes a context, and a decision is validated on the orchestrator that carries it out.

**The one change of behavior in the foundation**: a link is now made without contacting
anything, so an agent whose card cannot be used cannot stop startup any more. The service's
half of User Story 3's first five scenarios therefore lands here, for environment agents, with
the seven existing cases that expected startup to fail (FR-020, FR-021, FR-031, R4, R6, R22).
Saved agents written to the store are covered here as well. Phase 6 adds the check and the
console's warnings.

**One rule of the web app is here too**: no page of another origin may show the console in a
frame (FR-029). It protects the confirmations the later phases add, and the ones the LLM section
already has, so it is in place before the first of them and not with User Story 6.

- [X] T016 Change the seven cases in `tests/test_a2a_startup.py` that expect startup to fail
      for a card problem (the tests at lines 89, 96, 107, 118 with its three cases, and 163).
      Each now expects: the service starts and `/healthz` answers; the log has one warning per
      agent that names the agent and gives the card problem's sentence; `/tools` lists the
      agent as not reachable with that sentence, and both of its tools with the classification
      its entry gives; no text holds `127.0.0.1:9999`. The case that never answers still ends
      within 5 seconds with a startup limit of 0.3 seconds; the redirected card is still not
      followed. The two entry tests at lines 136 and 147 are not touched. These fail until T021
      and T024 (FR-020, FR-031, R22)
- [X] T017 Change `AgentLink` in `src/freyja_agent/delegates/a2a_agents.py`. Its constructor
      keeps its shape, `AgentLink(name, config, *, transport, call_timeout)`, and its
      `read_only`, so `tests/test_factory.py`, which builds links by hand, passes unchanged; it
      gains the keywords `source` (`environment` by default, or `saved`), `agent_id`, and
      `address_version`, and with them `ref`, `connected`, and `tried`. A saved agent's link is
      made with `read_only` false whatever its approval choice, which the set carries: a caller
      that forgets the set gates. Making a link contacts nothing: it registers its address with
      the scrubber and builds its one `httpx.AsyncClient` with an explicit transport and no
      redirects. `connect(card=None, *, limit)` reads the card with `read_card` unless it is
      handed one, builds the SDK client with the address pinned as today, and marks the link
      connected and reachable; one lock per link lets one caller read while the others wait for
      its result; a read that fails leaves the link not connected, with the card problem's
      sentence as its error, and raises the `CardProblem`. `get_task`, `cancel`, and a send on
      a link that is not connected send nothing and raise. `describe()` cleans through the
      scrubber. For an environment agent `card` is what this instance last read; for a saved
      agent it is the kept details it was made with. `start()`, `open_agents`, and
      `_start_failure` go; the line "Agent <name>: read-only|gated, N skills" stays a function
      of this module, on this module's logger, which the registry calls for each agent it
      connected, so the existing test that reads it is unchanged (FR-021, FR-025, FR-027,
      FR-031, R6, R12, R13, data-model.md "Agent link")
- [X] T018 Implement `src/freyja_agent/delegates/agent_registry.py`: `AgentSet` (the revision
      it was read at; the links in use, environment agents first and then saved agents by name;
      each agent's approval choice; the saved agents not in use with the reason for each) and
      `AgentRegistry`. `start()` refuses with `AgentStartupError`, as today and in today's
      words, an entry whose name is a tool server's or whose tool name is taken; makes a link
      for every environment agent and every saved agent in use; connects them all at once
      within `a2a_startup_timeout_seconds`; and logs one warning per agent it could not
      connect, with the sentence, on its own logger, and for each agent it could, today's line
      through `a2a_agents.py`. A saved agent whose name is an environment agent's or a tool
      server's, whose tool name is taken, or whose destination is an environment agent's gets
      no link, is listed as not in use, and is logged as a warning with the reason. `refresh()`
      reads the revision and, when it moved, reads the saved agents and makes a new set: a link
      whose `ref` is unchanged is kept, a link is made for each agent that is new or was moved,
      and a warning is logged for each saved agent the new set finds not in use. `hold(set)`
      and `release(set)` count holders; a set that is no longer current and that nothing holds
      closes the links only it had. `adopt(record, card)` takes up a change this instance made,
      with the card it just read. `mark_changed()` makes the next context rebuild on this
      instance without the revision having moved. `close()` closes every link (FR-017, FR-018,
      FR-019, FR-020, FR-030, R3, R4)
- [X] T019 Change `src/freyja_agent/delegates/a2a_tools.py`: `agent_tools(link, store, *,
      gated=None)` takes the classification from the set, and from `link.read_only` when it is
      given none, as today; the tool fixes `gated` when it is built. Inside every agent tool,
      an environment agent's included, in this order: the send ledger, as today; for an answer
      or a cancel, the task's `agent_ref` against `link.ref`, and when they differ the error
      result "Agent '<name>' task <id> was given up when the agent was moved or removed;
      nothing was sent."; then, on a link that is not connected, `connect()` inside the call's
      own time limit, and whatever that raises becomes the error result "Agent '<name>' cannot
      be used: <the card problem's sentence>" and never escapes the tool, and is logged as a
      warning with the agent's name and the sentence; then the send. Each message kept back is
      logged with the agent's name and the rule that kept it. A task the tool records carries
      `agent_ref=link.ref`. `handoff_description` reads `link.card`, and gives the name alone
      when there is none (FR-021, FR-025, R6, R7 "Order inside a tool", R10)
- [X] T020 Change `ModelManager` in `src/freyja_agent/llm/active.py`: `current(agents)` and
      `load(agents)` are given the `AgentSet`, `build` becomes `build(model, agents)`, and the
      orchestrator is rebuilt when the LLM revision moved or the set is not the one it was
      built for. Two runs that start together still rebuild once. With no change of agents the
      manager does what it did (R3)
- [X] T021 Change `src/freyja_agent/runtime/runtime.py`: `Runtime` holds `saved_agents`,
      `registry`, and `card_reads`, a `CheckSlots` made with words about card reads and agents;
      `RunContext` holds the graph, the catalog, the active model, and the set, and `release()`
      gives it back; `begin_run()` refreshes the set, asks the manager, holds the set, and
      returns the context. `assemble` takes `saved_agents`, starts the registry where it opened
      the agents, and builds through the manager with `agent_tools` for the set's links and
      `agent_tasks` given when the set has a link; `open` creates `PostgresSavedAgentStore` and
      runs its `setup()`. `Runtime.agents` stays, as the links of the current set;
      `Runtime.agent` and `Runtime.catalog` stay, as what this instance last built, which is
      what a read falls back to; `model_for_run()` stays for its callers and goes through the
      same refresh. A runtime put together by hand, with no registry, gets a context from what
      it was given (FR-019, R3, R4)
- [X] T022 Change `src/freyja_agent/api/streaming.py`: `run_stream` is given the context, runs
      on its graph, words a pause with its catalog, no longer calls `model_for_run()` or reads
      `runtime.agent`, and releases the context in its `finally`, beside the lease. Add the
      stream a route returns when no context could be taken: the `start` event and the
      `internal` error of today, the failure logged, and the lease released (FR-019, R3)
- [X] T023 Change `src/freyja_agent/api/threads.py`: the run and resume routes take a context
      right after the lease and use it to read the thread's state, to word the pending
      proposal, to validate the decisions, and for the run; every refusal releases the context
      with the lease. The state route takes a context before it answers and releases it after,
      and answers from what the instance last built when none can be taken (R3)
- [X] T024 [P] Change `src/freyja_agent/api/tools.py`: `GET /tools` takes a context before it
      answers and lists that context's links and catalog, and falls back to the last set when
      none can be taken. Its shape is unchanged. An agent this instance has not tried is listed
      as not reachable with the reason "This instance has not tried the agent yet."; card texts
      are the cut and cleaned ones (FR-003, R22)
- [X] T025 [P] Change `src/freyja_agent/delegates/delegation_worker.py`. A round of watching
      takes one context before it claims and releases it when the round's checks are done. A
      claimed task is given up when its `agent_ref` is not the `ref` of the link in use under
      its name, which keeps today's rule for an agent that left the configuration, both being
      null for an environment agent. For a link that is not connected and has a claimed task,
      the round calls `connect()` once; when that fails it checks none of that link's tasks
      this round, they stay pending, and one line is logged for the link, not one per task. A
      delivery takes a context for its look at the thread before the lease and releases it;
      right after it holds the lease it takes the one the run uses, reads the thread's state on
      it, releases it itself on each early return, and otherwise hands it to `run_stream`,
      which releases it. `start_deliveries` asks whether a usable model is active as it does
      today (FR-017, FR-026, R3, R6, R10)
- [X] T026 [P] Call `install_log_scrubber()` from `create_app` in
      `src/freyja_agent/api/app.py`, before the runtime opens (FR-028, R12)
- [X] T027 Extend the fixture in `tests/conftest.py`: `make_a2a_app` takes `saved_agents`, a
      store shared between apps as the task store is, and `saved`, agents to seed it with
      before the app starts, each with the card details of its stand-in; a stand-in given
      through `saved` or through the new `A2aApp.add_stub(url, stub)` is found by the
      destination of the address asked for, and any other by name, as today, since every
      environment agent of the fixture has one address; `A2aApp` gains `saved_agents`. Every
      existing use of the fixture works unchanged (R20)
- [X] T028 Write `tests/test_agents_startup.py`, part 1, environment agents. Three agents that
      never answer delay startup by one startup limit, not three (SC-006). A hand-off to an
      agent the instance holds no card from gives an error result with the agent's name and the
      sentence, the run continues, nothing is tried again, the step did not fail, and a warning
      names the agent and gives the sentence. Once the agent answers, a hand-off connects
      inside its own time limit and succeeds with no restart, and `/tools` shows it reachable.
      Two hand-offs at once read the card once. An environment agent whose card was not read
      has its tool described by its name alone; after the first read, the next run describes it
      from the card. With a pending task seeded for an agent that is down, the task stays
      pending, each round requests the card once and logs one line, and once the agent answers
      the next round checks the task. Starting a run contacts no agent. An environment entry
      whose name is a tool server's, or whose tool name is taken, still stops startup naming
      the agent, also with a saved agent in the store. With `HTTP_PROXY` and `ALL_PROXY` set, a
      link's client holds no proxy and its card read reaches the stand-in (FR-018 to FR-021,
      FR-026, FR-027, SC-006, R4, R6, R13)
- [X] T029 Extend `tests/test_agents_startup.py`, part 2, saved agents seeded in the store.
      With a saved agent that does not answer, the service starts and serves requests; the log
      has a warning naming the agent and the sentence and no address; `/tools` lists it as not
      reachable with the reason and describes its tools from the card details kept for it;
      every other agent works; a hand-off to it gives the error result and the run continues;
      once it answers, a hand-off succeeds with no restart. A saved agent whose card can no
      longer be used, because it now requires authentication, is listed with that reason and
      stays saved. A card that reads differently when the service connects changes nothing the
      tools are described from. An app that learns of a saved agent from another lists it as
      not tried until it is used. A saved agent with a pending task that is down at startup
      keeps the task pending, and it is checked once the agent answers. A saved agent whose
      name is an environment agent's, whose name is a tool server's, whose tool name is taken,
      or whose address leads where an environment agent's does: the service starts, the agent
      has no tools, a warning gives the reason, its pending tasks are given up as tasks of an
      agent that is no longer configured, and the environment agent's own tasks under that name
      are left alone. An app restarted without the entry that overrode it has the saved agent
      in use again, with the same id, and the tasks given up meanwhile stay given up. An
      answer, and a cancel, for a task whose `agent_ref` is not the link's sends nothing and
      gives the error result of T019: a saved agent's task under a name the environment now
      has, and an environment agent's task under a name a saved agent now has; a task given up
      for its age can still be canceled; each answer or cancel kept back is in the log with the
      agent's name and the rule (FR-015, FR-017, FR-020, FR-021, FR-025, FR-026, FR-030, R4,
      R10)
- [X] T030 Write `tests/test_agents_apply.py`, part 1, with agents written to the store, no
      route. An agent added to the store: the next run has both of its tools on the
      orchestrator and on its subagent, gated or not as its approval choice says, the catalog
      lists them, and the service was not restarted; the first agent of a service with none
      also brings `check_agent_tasks` and the list of pending tasks. A run in progress keeps
      the tools it started with and the next run has the new set; a paused run continued
      afterwards, and a background run that starts afterwards, have the new set. With the
      revision unchanged a run rebuilds nothing; two runs that start together after a change
      rebuild once. Two apps on one set of stores: a change is in the next run, in `/tools`,
      and in the next round of watching of the other, whose worker does not give up the task of
      an agent it had not yet seen. A decision is validated and carried out on one
      orchestrator: on an app that had not yet seen an agent stop requiring approval, a reject
      of its paused hand-off is refused as it is today for a call that no longer needs a
      decision, and nothing is sent; where one agent stopped requiring approval and another
      started while a step holding a call to each was paused, one approve is not applied to the
      other call, the run ends with an error, and neither stand-in received anything. When the
      set cannot be read, a run streams `start` and the `internal` error and nothing runs, the
      lease is free again, and `/tools` and the thread state answer from the last set. After an
      agent's row was moved in the store, the earlier link is closed once the run that held it
      ends, and not before. An app that learns from the store that a saved agent is not in use
      logs a warning with the reason (FR-019, FR-030, SC-002, SC-003, R3)
- [X] T031 [P] Extend `tests/test_a2a_transport.py`, which serves a stand-in on loopback: with
      every logger at DEBUG, httpx's, httpcore's, and the SDK's included, nothing logged during
      a card read and a hand-off holds the port (FR-028, R12)
- [X] T032 [P] Correct what this phase makes false, and nothing more: in `README.md`, that an
      agent the service cannot use stops startup, which now holds only for an entry that is
      invalid in itself, that the catalog answers from memory, since it now reads one row
      first, and the list of tables created at startup; the comment on `FREYJA_A2A_AGENTS` in
      `.env.example`; and `specs/003-a2a-agent-delegation/contracts/agent-config.md`, whose
      cases of a card that cannot be read or used now start the service with a warning (FR-020,
      AGENTS.md "The README documents only what exists")
- [X] T033 [P] Extend `web/__tests__/next.config.test.ts`: `headers()` sets
      `Content-Security-Policy: frame-ancestors 'none'` and `X-Frame-Options: DENY` on the
      source `/:path*` (FR-029, R19)
- [X] T034 Add `headers()` to `web/next.config.ts` with the two headers of T033, on every path
      (FR-029, R19)
- [X] T035 Extend `web/e2e/console.spec.ts`: the built app answers `/llm` and `/agents` with
      both headers, and a page of another origin (the test server reached as `127.0.0.1`, where
      the console runs on `localhost`) that puts the console in a frame is shown nothing of it,
      in Chromium and in Firefox. Record in plan.md that `frame-ancestors` was seen in both
      browsers and that `headers()` applies beside the `/api` rewrite (FR-029, FR-032, R19,
      R21, research "Not verified")
- [X] T036 Check by hand what the plan could not: start the service through uvicorn and see
      that its access line and a traceback are logged as before with the factory installed; run
      `uv run pytest -x` and see that pytest's log capture works beside it; and time a rebuild
      with twenty saved agents, forty agent tools. Record all three in plan.md (research "Not
      verified")

**Checkpoint**: `uv run pytest` passes, with no existing test changed but the seven cases of
T016 (FR-031, SC-014). A service configured through the environment, with every agent
answering, behaves as before. With an environment agent down, the service starts, warns, and
hands work to it once it answers. In `web/`, `pnpm test` and `pnpm test:e2e` pass, and the
console cannot be shown in a frame.

---

## Phase 4: User Story 1 - See which agents Freyja can hand work to (Priority: P1) 🎯 MVP

**Goal**: The Agents section shows every agent as a card: its name, its approval choice, where
it comes from, its state with the time and the reason, and what its card says of it.

**Independent Test**: start the service with two environment stand-ins, one requiring approval
and one not, and open the Agents section: two cards, each marked as coming from the
environment, with no address on the page or in what the page received, and nothing to change
or remove. Runs that hand work to each behave as before this feature.

### Tests for User Story 1

- [X] T037 [P] [US1] Write `tests/test_agents_api.py`, part 1, `GET /agents`: environment
      agents first, in configuration order, then saved agents by name; `limit` is 20 and
      `unusable` counts the agents that are `unreachable` or `not_in_use`; an environment agent
      has the id `env.<name>`, `source` `environment`, `approval` from its entry (`none` for
      `read_only`), and null `url`, `version`, `created_at`, and `updated_at`; a seeded saved
      agent has its id, its address in the kept form, its `version`, and its kept card details
      with `cut`; `state` is `reachable`, `unreachable` with `checked_at` and the reason,
      `not_tried` with null `checked_at`, or `not_in_use` with the reason; `card` is null for
      an environment agent whose card this instance has not read; `pending_tasks` counts each
      agent's pending tasks on every thread; no field of an environment agent, and no reason,
      holds an address; the list and `/tools`, read twice, sent no request to any stand-in; a
      response carries no cross-origin header; when the store cannot be read the list answers
      500 `internal` and shows no earlier data as current (FR-001, FR-003, FR-004, FR-017,
      FR-028, contracts/openapi.yaml)
- [X] T038 [P] [US1] Write `web/__tests__/lib/agents.test.ts`, part 1: `listAgents()` requests
      `/api/agents` and returns the list; `downAgents(list)` gives the agents whose state is
      `unreachable` or `not_in_use`, and none whose state is `not_tried`; a refusal and an
      unreachable service surface as `ApiError` (FR-001a, FR-004)
- [X] T039 [P] [US1] Write `web/__tests__/components/agents/agent-card.test.tsx`, part 1: a
      card shows the name, the approval choice in words ("requires approval", "runs without
      approval"), and its state with the time; an environment agent is marked as coming from
      the environment and shows no address; a saved agent shows its address; an agent that is
      `unreachable` or `not_in_use` carries a warning that says so in words, with the reason
      and the time; a `not_tried` agent says this instance has not tried it yet and carries no
      warning; the card's name, description, version, and skills are shown as the agent's own
      claim, with a note when `cut` is true; card text that holds markup or a link, such as
      `<img src=x onerror=...>` and `[x](http://example.com)`, is on the page as that text,
      with no element and no link made from it; an environment agent whose `card` is null says
      its card has not been read (FR-001, FR-001a, FR-002)
- [X] T040 [P] [US1] Write `web/__tests__/components/agents/agent-list.test.tsx`, part 1: one
      card per agent in the order of the list; with no agent the page says that no agent is
      configured; while the list loads there is a placeholder and no card; when the service
      cannot be reached the page says so with the service's reason, offers to try again, shows
      no card, and trying again reads the list (FR-001, FR-004)
- [X] T041 [P] [US1] Replace `web/__tests__/app/agents/page.test.tsx`: the page renders the
      list of agents, and the placeholder's sentence is gone (FR-031)

### Implementation for User Story 1

- [X] T042 [US1] Add `Agent`, `AgentCard`, and `AgentList` to `src/freyja_agent/api/schemas.py`
      as contracts/openapi.yaml gives them: `approval` one of `required` and `none`; `state`
      one of `reachable`, `unreachable`, `not_tried`, `not_in_use`; `reason` at most 500
      characters; `limit` 20
- [X] T043 [US1] Create `src/freyja_agent/api/agents.py` with the router under `/agents`, made
      with `NoInputRoute` and `refuse_other_origins("Agents")`, and `GET /agents`, which takes
      a context, reads the saved agents and the pending counts, two queries, and builds the
      list from them and from the set's links, answering 500 `internal` when the store cannot
      be read; include the router in `src/freyja_agent/api/app.py` (FR-001, FR-003, FR-017,
      FR-029, R16)
- [X] T044 [P] [US1] Create `web/lib/agents.ts`: the types `Agent`, `AgentCard`, and
      `AgentList`; `AGENTS_KEY`, the one SWR key, `/agents`; `listAgents()`; and `downAgents()`
      (FR-004, R17)
- [X] T045 [US1] Create `web/components/agents/agent-card.tsx`, after
      `web/components/llm/profile-card.tsx`, with what T039 asks; times are formatted as the
      profile card formats them, with no clock read during render (FR-001, FR-001a, FR-002)
- [X] T046 [US1] Create `web/components/agents/agent-list.tsx`, a Client Component that reads
      the list with SWR under `AGENTS_KEY`, after `web/components/llm/profile-list.tsx`, with
      the empty state, the placeholder, and the unreachable state of T040 (FR-001, FR-004)
- [X] T047 [US1] Replace the placeholder in `web/app/agents/page.tsx` with the list
- [X] T048 [US1] Write the end-to-end tests of this story. `web/e2e/api.ts` gains
      `environmentAgent()`, `savedAgent()`, `agentList()`, and `answerAgents(page,
      ...answers)`, after `answerProfiles`. `web/e2e/agents.spec.ts`: two environment agents
      show as two cards marked as coming from the environment, each with its approval choice,
      its state, and its card's details, and with no control to change or remove it; an
      unreachable agent's card carries the warning with the reason; with no agent the page says
      so; with no answer the page says the service cannot be reached, and Try again reads the
      list once the service answers; card text with markup is shown as text and the page made
      no request for it; in a narrow window every card is reachable. Change the test in
      `web/e2e/console.spec.ts` that expects the placeholder: the sidebar's Agents entry leads
      to the section, which lists the agents (FR-032, R21, R22)
- [X] T049 [P] [US1] Correct the sentence of `README.md` that calls the Agents section a
      placeholder: it lists the agents, and nothing is managed from it yet

**Checkpoint**: quickstart section 1 works, without the Add button it mentions, and section
6's first point, without Check, with a stand-in named in `FREYJA_A2A_AGENTS`. User Story 1 is
complete and can be merged alone.

---

## Phase 5: User Story 2 - Add an agent and hand work to it without a restart (Priority: P2)

**Goal**: An operator adds an agent by name and address. Its card is read first; when it can be
used it is saved and the next run can hand work to it, with no restart. In this phase a saved
agent always requires approval: adding one that runs without approval, scenario 10 of the
story, arrives in Phase 7 with the routes that can take that choice back.

**Independent Test**: with the service running, start the stand-in, add it through the console,
and send a message that leads to a hand-off: the run pauses, and once approved the stand-in
received exactly that message. Restart: the agent is still there. An address where nothing
listens, and a name that is taken, are each refused with the reason and leave no card.

### Tests for User Story 2

- [X] T050 [P] [US2] Extend `tests/test_agents_api.py`, part 2, `POST /agents`. A free name and
      a stand-in's address give 201 with the agent: the address in the kept form, `approval`
      `required`, sent or left out, `state` `reachable`, the card's details, `version` 1; a log
      line names the agent and the approval choice and holds no address. The stand-in's
      transport saw one `GET` for the card and nothing else, for an add and for every refusal
      that read a card (SC-011). Refused with 409 and nothing saved: `agent_unusable` with the
      card problem's code and sentence, for each of the nine; `name_taken` for a saved agent's
      name, an environment agent's, a tool server's, and a name whose `handoff_to_<name>` or
      `cancel_<name>_task` is another tool's; `address_taken` for an address that leads where a
      saved agent's or an environment agent's does, in any spelling; `agent_limit` at twenty;
      `check_busy` while four card reads are under way. A name, a limit, or an address that is
      taken is refused before any card is read. Refused with 422, as a list of field errors
      with no `input`, in the service's words, and without the address: each rule of T004, a
      name not in the form, an `approval` that is not `required`, `none` included until Phase
      7, a field the shape does not have, and a body that is not sent as JSON. Refused with 403
      `other_origin` when the request carries `Sec-Fetch-Site: cross-site` or an `Origin` of
      another host. After every refusal the list, `/tools`, and the next run's tools are what
      they were (SC-007). A stand-in that never answers is refused as `timeout` within the
      lesser of the startup limit and twenty seconds (SC-012). No refusal and no log line holds
      the address (FR-005 to FR-008, FR-014, FR-016, FR-027 to FR-030)
- [X] T051 [P] [US2] Extend `tests/test_agents_apply.py`, part 2, through the route. After an
      add, with no restart: a hand-off to the agent pauses, and once approved the stand-in
      received exactly the proposed message, from the orchestrator, from its subagent, and in a
      background run; a run in progress finishes without the new agent and the next run on its
      thread has it; `/tools` lists the agent's two tools with the classification its choice
      implies and no address. An app restarted on the same stores lists the agent with the same
      name, address, approval choice, and card details, and hands work to it (SC-013). On two
      apps sharing the stores, a run that starts on the second after the add was answered on
      the first has the agent. A service with no agent at all has, after its first add,
      `check_agent_tasks` and the list of pending tasks in the next run (FR-019, SC-001,
      SC-002, SC-013)
- [X] T052 [P] [US2] Extend the contract tests in `tests/test_agent_task_store.py` with the
      proposed calls, and add them by name to `tests/integration/test_a2a_tasks_postgres.py`:
      `replace_calls(thread_id, namespace, calls)` replaces the rows of that thread and
      namespace and no other; `get_call(thread_id, namespace, tool_call_id)` reads one;
      `list_calls(thread_id)` lists a thread's; `delete_call` removes one;
      `clear_calls(thread_id)` removes a thread's; two calls with one `tool_call_id` in two
      namespaces of a thread are two rows (R8, data-model.md "Proposed call")
- [X] T053 [P] [US2] Write `tests/test_agents_approval.py`, part 1, a call belongs to the agent
      it was proposed for. A proposed call has a row from the model's reply until its send is
      finished, on the orchestrator and on its subagent, written before the run pauses; a reply
      in the same namespace replaces the namespace's rows; a run that starts with input clears
      the thread's rows; a background run's retry clears none; a row that cannot be written
      fails the step; a step that runs again after its send was recorded returns the recorded
      result. A proposal paused for an environment agent, then an app restarted on the same
      stores without that agent, then a saved agent added under its name: whatever is decided,
      nothing is sent, and the call gives "Agent '<name>': the agent this message was proposed
      for was removed; nothing was sent.". A proposal paused for a saved agent, then an app
      restarted with an environment agent under its name: the same. A proposal for an
      environment agent that is still configured after a restart follows the name, as today,
      and so does a call with no row when the tool is an environment agent's. A call with no
      row that reaches a saved agent's tool, as one proposed for an environment agent before
      this feature was installed would, sends nothing and gives the same result. Each message
      kept back is in the log with the agent's name and the rule (FR-023, FR-030, R8)
- [X] T054 [P] [US2] Write `tests/test_agents_address_nowhere.py`, part 1, built like
      `tests/test_llm_secrets_nowhere.py` and grown in every later phase. One saved agent on a
      port chosen to be recognizable and one environment agent on another. Drive what exists:
      `GET` and `POST /agents` with each refusal, a run that completes, one left paused, and
      one whose hand-off fails with the stand-in down. Then search for each port, in each form
      of FR-028: every response outside the Agents routes, `/tools` and the thread state
      included; every stream, with `debug`; everything the checkpointer holds; every refusal's
      reason, the Agents routes' included; and every log record at DEBUG, with its formatted
      traceback. The saved address is found only in `url` of an Agents answer; the environment
      agent's is found nowhere (FR-028, SC-008, R20)
- [X] T055 [P] [US2] Extend `web/__tests__/lib/agents.test.ts`, part 2: `addAgent({name, url})`
      posts the two values as JSON to `/api/agents` and returns the agent; the address is in
      the body and in no path or query
- [X] T056 [P] [US2] Write `web/__tests__/components/agents/agent-form.test.tsx`, part 1,
      adding. The form opens with an empty name and an empty address, says that messages to the
      new agent will require approval, and has nothing left from an earlier use. Saving sends
      one request, and no confirmation is asked; a second press while it is under way sends
      nothing. A field error is shown at its field and what was typed is kept; `name_taken` is
      shown at the name and `address_taken` at the address; `agent_unusable` shows the
      service's sentence. When the request ends with the code `unreachable` the form says that
      the outcome is not known, never that it failed, and the list is read again. Once sent
      successfully or closed, the form keeps nothing (FR-004, FR-008)
- [X] T057 [P] [US2] Extend `web/__tests__/components/agents/agent-list.test.tsx`, part 2: the
      Add button is there with agents and with none, opens the form, and after a successful add
      the list is read again and the new card is shown (FR-001)

### Implementation for User Story 2

- [X] T058 [US2] Add `AgentCreate` to `src/freyja_agent/api/schemas.py`: `name` in the form
      `^[a-z][a-z0-9_-]{0,31}$`; `url` at most 2,000 characters, validated with
      `canonical_address` and kept in its form, every message the function's own; `approval`
      `required` only, which is also the default, until Phase 7 opens `none`; no other field
      (FR-006)
- [X] T059 [US2] Implement `POST /agents` in `src/freyja_agent/api/agents.py`, in this order:
      the name against the environment's agents, the tool servers, and the tool names that are
      taken; the destination against the environment's agents; the limit, the saved names, and
      the saved destinations; a slot of the four, from `Runtime.card_reads`; the address
      registered with the scrubber; the card read with a client made for it, within the lesser
      of `a2a_startup_timeout_seconds` and twenty seconds; the agent saved, the store deciding
      again on name, address, and limit; the address marked as saved; the change taken up on
      this instance with `registry.adopt(record, card)`, a step whose failure is logged and
      does not undo the add; the log line; 201 with the agent (FR-005 to FR-008, FR-014,
      FR-016, FR-030, R15, R16)
- [X] T060 [US2] Add the proposed calls to `src/freyja_agent/runtime/agent_tasks.py`:
      `ProposedCall` with the fields of data-model.md (`thread_id`, `namespace`,
      `tool_call_id`, `agent`, `agent_id` "the saved agent's id when the call was proposed;
      null for an environment agent", `gated`, `proposed_at`); the methods of T052 on the
      protocol and on both stores; and the table `freyja_agent_calls` with the primary key
      `(thread_id, namespace, tool_call_id)`, created with `CREATE TABLE IF NOT EXISTS` (R8)
- [X] T061 [US2] Record proposed calls in `src/freyja_agent/agent/agent_tasks.py`:
      `AgentTasksMiddleware` is given, per agent tool name, the agent's name, the saved agent's
      id or None, and whether the tool is gated, and after the model answered, in
      `awrap_model_call`, replaces the rows of this thread and namespace with one per call of
      the reply to an agent's tool. The namespace is the run's `checkpoint_ns` with its last
      segment taken off. `DelegateTool` in `src/freyja_agent/delegates/mcp_tools.py` gains
      `agent_id`, which `_delegate` in `src/freyja_agent/delegates/a2a_tools.py` sets from the
      link, and `build_agent` in `src/freyja_agent/agent/factory.py` builds the middleware's
      table from the delegate tools it is given, for the orchestrator and for its subagent
      (FR-023, R8)
- [X] T062 [US2] In every agent tool of `src/freyja_agent/delegates/a2a_tools.py`, an
      environment agent's included, after `connect()` and before the send: read the call's row,
      and when it names another agent than the link's, by `agent_id`, a null on either side
      included, send nothing and give the error result of T053, logged with the agent's name. A
      call with no row follows the name when the tool is an environment agent's; a saved
      agent's tool that finds no row sends nothing and gives the same result. The tool deletes
      its row when its send is finished. In `src/freyja_agent/api/streaming.py`, a run that
      starts with input clears the thread's proposed calls before the graph runs. Record in
      plan.md what the build showed of the rows as a whole (FR-023, FR-030, R8, research "Not
      verified")
- [X] T063 [P] [US2] Add `addAgent` to `web/lib/agents.ts`
- [X] T064 [US2] Create `web/components/agents/agent-form.tsx`, after
      `web/components/llm/profile-form.tsx`, for adding: the two fields, the sentence about
      approval, the errors at their fields, the outcome that is not known, and nothing kept
      once sent or closed, as T056 asks (FR-004, R17)
- [X] T065 [US2] Add the Add button and the form to `web/components/agents/agent-list.tsx`; a
      successful add has the list read again
- [X] T066 [US2] Write the end-to-end tests of this story. `web/e2e/api.ts` gains
      `AgentService`, after `ProfileService`: a stand-in for the Agents routes that keeps what
      the page saved and records what it was asked. `web/e2e/agents-manage.spec.ts`, part 1:
      adding an agent shows its card with the address in the kept form and requiring approval;
      the form opens empty the second time; an address where nothing answers is refused with
      the sentence and what was typed stays; a taken name is refused at the name; a request
      that gets no answer says the outcome is not known and the list is read again; the address
      was sent in a request body and in no address the page asked for (FR-032, R21)
- [X] T067 [P] [US2] Update `README.md` with what now exists and matters before it is used: an
      agent can be added from the Agents section and always requires approval for now; the
      table `freyja_agent_calls`; and that every instance has to run this version before the
      first agent is saved (plan.md "Rollout")

**Checkpoint**: quickstart section 2 works with the stand-in on loopback, the form saying that
approval is required where the quickstart speaks of the approval choice. An agent can be added
and used with no restart, and every message to it requires approval. Nothing can be changed or
removed yet, a saved agent that is not in use included: both arrive in Phases 7 and 8.

---

## Phase 6: User Story 3 - An agent that is down does not stop the service (Priority: P3)

**Goal**: An operator can check any agent, and the console warns about every agent that cannot
be used, on its card, above the cards, and on the sidebar from every section. That the service
starts with an agent down, and uses it again once it answers, is Phase 3's.

**Independent Test**: add the stand-in, stop it, and restart the service: it starts, and the
card says the agent could not be reached and why. Check says it cannot be used. Start the
stand-in and check again: it can be used, the warnings are gone, and the next hand-off
succeeds. The service was never restarted after the first time.

### Tests for User Story 3

- [X] T068 [P] [US3] Write `tests/test_agents_check.py`, `POST /agents/{agent_id}/check`. For a
      saved agent and for `env.<name>`: a usable card gives `usable` true, null `reason` and
      `message`, `checked_at`, and `recorded` true, and the list then shows the agent reachable
      at that time with the details just read; a card that cannot be used gives `usable` false
      with the code and the sentence, the list shows it unreachable with that reason, the link
      is not connected any more so a hand-off fails with the sentence until a later read
      succeeds, and the agent stays configured as it was. A saved agent's changed details are
      kept and move the revision, so the next run on every app describes its tools from them,
      and the answer has `card_changed` true; unchanged details move nothing and give
      `card_changed` false, as do a card that cannot be used, a result that was not recorded,
      and the first read of an environment agent's card; an environment agent's details change
      on this app only, from its next run. The stand-in saw one `GET` and nothing else; no
      thread, pending task, address, approval choice, or `version` changed. A second check of
      an agent that is being checked, and a fifth card read while four are under way, are
      refused with `check_busy` and contact nothing. A stand-in that never answers ends the
      check as `timeout` within the lesser of the startup limit and twenty seconds. When the
      agent's row is moved or removed while the check runs, the answer has `recorded` false and
      nothing was kept. An unknown id is `agent_not_found`; a saved agent that is not in use is
      `agent_read_only`; an id in neither form is 422; a page of another origin is refused with
      403; the log names the agent, the result, and whether the card details were replaced, and
      holds no address (FR-013 to FR-016, FR-026, FR-027, FR-029, FR-030, SC-011, SC-012, R15)
- [X] T069 [P] [US3] Extend `tests/test_agents_address_nowhere.py`, part 2: a check that
      succeeds, one that fails for each card problem, one refused as busy, and a card that
      mentions its own address and another agent's; the search of part 1 again (FR-028, SC-008)
- [X] T070 [P] [US3] Extend `web/__tests__/lib/agents.test.ts`, part 3: `checkAgent(id)` posts
      to `/api/agents/<id>/check` with no body and returns the result
- [X] T071 [P] [US3] Write `web/__tests__/components/agents/check-result.test.tsx` and extend
      `agent-card.test.tsx`, part 2: every card of an agent that is in use has Check, saved or
      from the environment, and a card of an agent that is not in use has none; while a check
      runs the button says so and cannot be pressed again; a usable result says the agent can
      be used; one that is not says why, in the service's sentence as text; a result with
      `recorded` false says it was not recorded; a result with `card_changed` true says that
      the agent's card changed and that its tools are described from the new details from the
      next run; `check_busy` and `agent_not_found` are shown as the service words them, and the
      second has the list read again; after any result the list is read again (FR-013, FR-014)
- [X] T072 [P] [US3] Write `web/__tests__/components/agents/agents-warning.test.tsx` and extend
      `agent-list.test.tsx`, part 3: with one or more agents `unreachable` or `not_in_use`, a
      warning above the cards names each of them and says in words that they cannot be used;
      with every agent reachable, and with only `not_tried` agents, there is none; while the
      service cannot be reached there is none (FR-001a)
- [X] T073 [P] [US3] Extend `web/__tests__/components/app-sidebar.test.tsx`: the Agents entry
      carries a badge with the number of agents that cannot be used and a text for assistive
      technology that says what the number means; with none there is no badge; when the read
      fails there is no badge; the badge shows on the LLM section too. The file's existing
      tests are given an answer for `/api/agents` through `stubFetch`, since the sidebar now
      asks for it (FR-001a, SC-017, R18)

### Implementation for User Story 3

- [X] T074 [US3] Add `AgentCheck` to `src/freyja_agent/api/schemas.py` and implement `POST
      /agents/{agent_id}/check` in `src/freyja_agent/api/agents.py`: the id's two forms; a
      context held for as long as the link is used; the agent's slot and one of the four; the
      card read through `link.connect` within the lesser of the startup limit and twenty
      seconds; for a saved agent `replace_card(id, ref, details)`, with `recorded` and
      `card_changed` from its answer; for an environment agent the link's details replaced and
      `registry.mark_changed()`; a read that fails leaves the link not connected; the log line,
      which says whether the card details were replaced (FR-013 to FR-015, FR-030, R6, R15)
- [X] T075 [P] [US3] Add `checkAgent` and the `AgentCheck` type to `web/lib/agents.ts`
- [X] T076 [US3] Create `web/components/agents/check-result.tsx`, after
      `web/components/llm/test-result.tsx`, and add Check to
      `web/components/agents/agent-card.tsx` as T071 asks
- [X] T077 [US3] Create `web/components/agents/agents-warning.tsx` and show it above the cards
      in `web/components/agents/agent-list.tsx` (FR-001a, R18)
- [X] T078 [US3] Add the badge to the Agents entry in `web/components/app-sidebar.tsx` with
      `SidebarMenuBadge`, reading the list with SWR under `AGENTS_KEY`, the key the list uses
      (FR-001a, R18)
- [X] T079 [US3] Write the end-to-end tests of this story. Extend `web/e2e/agents.spec.ts`:
      with an agent that cannot be used, the sidebar's Agents entry carries the number on the
      LLM section, without the Agents section having been opened; in the Agents section the
      warning above the cards names the agent and its card carries the warning; after a reading
      that finds the agent reachable all three are gone; with no answer for the list the
      sidebar shows no badge; opening the Agents section asks for the list once, which shows
      the sidebar and the list share one reading. Extend `web/e2e/agents-manage.spec.ts` and
      `AgentService`, part 2: Check on a saved and on an environment card reports that the
      agent can be used, then that it cannot and why, and the card follows; a check that finds
      other card details says that the agent's card changed. Give the shared setup of
      `web/e2e/llm.spec.ts` and `web/e2e/profiles.spec.ts` an answer for `/api/agents` wherever
      a spec counts requests. Record in plan.md what the test showed of the shared reading
      (FR-032, R18, R21, research "Not verified")

**Checkpoint**: quickstart section 3 works, and the last paragraph of section 6.

---

## Phase 7: User Story 4 - Decide whether messages to an agent need a person's approval (Priority: P4)

**Goal**: An operator changes a saved agent's approval choice behind a confirmation. Running
without approval holds from the next run; requiring approval holds at once, also for a run in
progress.

**Independent Test**: add the stand-in requiring approval: a hand-off pauses. Switch it to run
without approval, cancel the confirmation: a hand-off still pauses. Switch and confirm: the
next run's hand-off reaches the stand-in with no pause. Start a run that hands off twice with
a wait between, switch back during the wait: the second hand-off is not sent.

### Tests for User Story 4

- [X] T080 [P] [US4] Extend `tests/test_agents_api.py`, part 3. `POST /agents` now accepts
      `approval` `none`, and the test of T050 that refused it is changed to say so. `PATCH
      /agents/{agent_id}` with `approval`: a change gives 200 with the agent, `version` one
      higher, and `given_up` 0; the stand-in saw no request, also when it is down, in both
      directions; a log line names the agent and says its approval choice changed. A request
      that names the choice the agent already has gives 200 with the agent as it is, the same
      `version`, and no log line. A request at a version that is no longer current is refused
      with `agent_changed` and nothing changes (SC-016). `env.<name>` and a saved agent that is
      not in use are refused with `agent_read_only`; an unknown id with `agent_not_found`. A
      body with `name`, with `url`, with no `version`, or with an `approval` that is neither
      value is refused with 422; a body that is not JSON is refused; a page of another origin
      is refused with 403. Pending tasks are as they were. `/tools` then lists the agent's
      tools with the classification the new choice implies (FR-009, FR-012, FR-017, FR-026,
      FR-029, FR-030)
- [X] T081 [P] [US4] Extend `tests/test_agents_approval.py`, part 2. For a saved agent that
      requires approval, a hand-off, a follow-up, an answer, and a cancel each pause before
      anything is sent, on the orchestrator, on its subagent, and in a background run (SC-004).
      After a switch to running without approval, the next run sends with no pause in all
      three, and a run in progress keeps pausing until it ends. After a switch to requiring
      approval, the next run pauses, on every app. A run in progress that started while the
      agent ran without approval and then hands off to it sends nothing: the call gives the
      error result "Agent '<name>' now requires approval, so this message was not sent.", the
      run continues, the log names the agent and the reason, and by the stand-in's record
      nothing reached it after the change was answered; a send that had begun before the change
      is not recalled. The same run, when the agent's row is gone from the store, also when
      another agent was saved under its name with another id, sends nothing: "Agent '<name>'
      was removed; nothing was sent.", and the log says so. A step that runs again after its
      send was recorded returns the recorded result and is not refused. An environment agent's
      tools read no saved agent before they send. A paused proposal decided after a switch to
      running without approval accepts approve only and is sent as proposed. A paused step that
      holds a hand-off that needed no decision when it was proposed, to an agent that now
      requires approval: the resume ends with the `error` code `approval_now_required` and the
      message of contracts/sse-events.md naming the agent, nothing from the step ran, the
      refusal is in the log with the thread and the agent, and the proposal is still pending;
      the same for a subagent's step; once the agent is switched back the resume goes through.
      A card that calls its agent read-only changes nothing: the agent still pauses (FR-019,
      FR-022, FR-023, FR-030, SC-004, R7, R9)
- [X] T082 [P] [US4] Change the test at line 394 of `tests/test_a2a_gate.py`
      (`test_a_hand_off_that_became_gated_blocks_the_resume_and_the_proposal_stays`): the
      resume now ends with `approval_now_required` and a message naming `hello`. Add beside it
      a test that fails the day the approval middleware words its error differently: the
      sentence `run_stream` recognizes is the one the installed
      `langchain.agents.middleware.human_in_the_loop` raises. The matching test for a tool
      server's tool in `tests/test_mcp_gate.py` keeps `internal` and is not touched (FR-023,
      FR-031, R9, R22)
- [X] T083 [P] [US4] Extend `tests/test_agents_apply.py`, part 3, two apps on one set of
      stores, the change made through the route on the first and each decision sent to the
      second: after a switch to running without approval, approve sends the message as
      proposed, and reject and edit are refused with the proposal left in place; after a switch
      to requiring approval of an agent whose call in the paused step needed no decision, each
      of approve, edit, and reject ends with `approval_now_required` and nothing is sent to any
      agent (FR-019, FR-023, R3, R20)
- [X] T084 [P] [US4] Extend `web/__tests__/lib/agents.test.ts`, part 4, and
      `web/__tests__/lib/api.test.ts`: `changeAgent(id, {version, approval})` sends a `PATCH`
      with exactly the values it was given; `isGone` is true for `agent_not_found` as for
      `profile_not_found`
- [X] T085 [P] [US4] Extend `web/__tests__/components/agents/agent-form.test.tsx`, part 2, the
      approval choice. Adding: the form now offers the choice, set to requiring approval; with
      "runs without approval" chosen, Save sends nothing and shows a confirmation that names
      the agent and says that nothing sent to it will be seen by a person first; Cancel sends
      nothing; adding with approval required still asks nothing. Changing: the form opens with
      the agent's values and its name not editable. Save with nothing changed sends nothing.
      Switching to running without approval shows a confirmation that names the agent and says
      that every message to it will be sent without a person seeing it first, from the next
      run, and that a proposal already waiting for a decision can then only be approved.
      Switching to requiring approval shows one that says it holds at once, also for runs in
      progress, and that a paused step holding a message to this agent that needed no approval
      cannot be continued until the choice is changed back or the agent is removed. Nothing is
      sent before Confirm; Confirm is a button of its own, Enter in a field does not confirm,
      and the second press of a double press is ignored. The request carries the version the
      form opened with and only the value that changed. The confirmation shows the agent as the
      list has it at that moment. When the list shows a later version while the form is open,
      the form takes its confirmation back and says the agent was changed elsewhere.
      `agent_changed` and `agent_not_found` say so and have the list read again. No answer says
      the outcome is not known (FR-004, FR-009, FR-011, FR-012, SC-005, R14, R17)
- [X] T086 [P] [US4] Extend `web/__tests__/components/agents/agent-card.test.tsx`, part 3: a
      saved agent that is in use has Edit; an environment agent has none; a saved agent that is
      not in use has none (FR-017)
- [X] T087 [P] [US4] Extend `tests/test_agents_address_nowhere.py`, part 3: an add with
      `approval` `none`, a change of the approval choice with each refusal, a run refused by
      the check before a send, and a resume that ends with `approval_now_required`; the search
      of part 1 again (FR-028, SC-008)

### Implementation for User Story 4

- [X] T088 [US4] Add the check before a send to `src/freyja_agent/delegates/a2a_tools.py`, for
      the tools of a saved agent only. `agent_tools` is given the saved-agent store, which
      `Runtime.assemble` in `src/freyja_agent/runtime/runtime.py` passes. The order inside a
      tool is R7's: the send ledger; the task's ref; `connect()`; the proposed call; then the
      saved agent read from the store by id, with everything else the send needs, the time and
      the thread's conversation among them, read before it, so that nothing is awaited between
      that read and the send's first write but the write itself. When the read finds no row,
      also when another agent now has the name, nothing is sent: "Agent '<name>' was removed;
      nothing was sent.". When the tool needed no decision when it was built and the agent now
      requires approval, nothing is sent: the error result of T081. Each is logged with the
      agent's name and the reason (FR-022, FR-030, R7)
- [X] T089 [US4] Change `src/freyja_agent/api/streaming.py`: a resume that fails with the
      approval middleware's sentence about the number of decisions ends with
      `approval_now_required` and the message of contracts/sse-events.md, naming each agent of
      the thread's proposed calls that needed no decision when proposed and whose tool is gated
      in the run's catalog, and with `internal`, as today, when it finds none. The refusal is
      logged with the thread and the agents it named. The code is not one a background run is
      tried again for (FR-023, R9)
- [X] T090 [US4] In `src/freyja_agent/api/schemas.py`, let `AgentCreate` accept `approval`
      `none`, and add `AgentUpdate`, with `version` and `approval` and no other field, and
      `AgentChanged`. Implement `PATCH /agents/{agent_id}` for the approval choice in
      `src/freyja_agent/api/agents.py`: no card is read and no agent contacted; a request that
      names the choice the agent has changes nothing and answers with the agent; the change is
      taken up on this instance at once; the log line (FR-009, FR-012, FR-017, FR-030, R14,
      R16)
- [X] T091 [P] [US4] Add `changeAgent` to `web/lib/agents.ts` and teach `isGone` in
      `web/lib/api.ts` the code `agent_not_found`
- [X] T092 [US4] Extend `web/components/agents/agent-form.tsx` with the approval choice when
      adding, behind its confirmation, and to change a saved agent's approval choice, with the
      two confirmations, the version the form opened with, and the confirmation taken back when
      the list shows a later one, as T085 asks (FR-011, FR-012, R14, R17)
- [X] T093 [US4] Add Edit to `web/components/agents/agent-card.tsx`, for a saved agent in use
- [X] T094 [US4] Write the end-to-end tests of this story: extend
      `web/e2e/agents-manage.spec.ts` and `AgentService`, part 3. Adding with "runs without
      approval" asks first, sends no request before Confirm, and Cancel leaves no card; an
      agent added without touching the choice requires approval. Switching to running without
      approval asks first and says what is given up; Cancel changes nothing and sends no
      request; Confirm changes the card. Switching back asks and says it holds at once. A
      change made elsewhere while the form is open takes the confirmation back. A change
      refused as `agent_changed` shows the agent as it is now. An environment agent's card
      offers no way to change its approval choice (FR-032, R21)
- [X] T095 [US4] Record in plan.md that the middleware's sentence is still the one matched
      (research "Not verified")

**Checkpoint**: quickstart section 4 works.

---

## Phase 8: User Story 5 - Move or remove an agent (Priority: P5)

**Goal**: An operator changes a saved agent's address, with the card at the new address read
first, or removes the agent. Both are confirmed, both give up the agent's pending tasks in
the same transaction, and both say how many.

**Independent Test**: add the stand-in and start a task that stays pending. The removal's
confirmation says one task is pending; once confirmed the card is gone, the thread lists the
task as given up, a background run tells the thread, and the stand-in received no cancel. Add
it again and move it to a port where nothing listens: refused, and hand-offs still reach the
first address. Move it to a second stand-in: the next run's hand-off reaches that one.

### Tests for User Story 5

- [X] T096 [P] [US5] Extend `tests/test_agents_api.py`, part 4. `PATCH` with `url`, which the
      test of T080 that refused it is changed to accept: a move to a stand-in that can be used
      gives the agent with the new address in the kept form, the details read there, `version`
      one higher, and `given_up`; the new stand-in saw one `GET` and the earlier one nothing;
      an address where the agent cannot be used is refused with `agent_unusable` and the
      earlier address stays saved and in use; an address that comes to the saved form, in
      another spelling, is not a move: no card is read and no task given up; `address_taken`,
      `check_busy`, and each rule of T004 are refused as for an add; `url` and `approval` in
      one request change both; a move of an agent that is being checked goes ahead. `DELETE
      /agents/{agent_id}?version=`: 200 with `given_up`; the agent is gone from the list and
      from `/tools`, and is still gone on an app restarted on the same stores; a missing or
      stale `version` is refused with 422 or `agent_changed`; `env.<name>` is refused with
      `agent_read_only`; a saved agent that is not in use can be removed, and the environment
      agent's tasks under its name are left alone; an agent removed and added again is refused
      at the earlier id. Both routes refuse a page of another origin, and each logs the agent,
      what changed, and how many tasks were given up, with no address (FR-009, FR-010, FR-012,
      FR-017, FR-024, FR-029, FR-030, SC-007, SC-012, SC-016)
- [X] T097 [P] [US5] Write `tests/test_agents_tasks.py`. With three tasks pending for an agent,
      the list reports 3; a move, and a removal, answers with `given_up` 3, each task is listed
      in its thread as `given_up` at once, a background run tells each thread exactly once, and
      by the stand-ins' records no cancel was sent and nothing about those tasks reached the
      new address (SC-010). The report for a move says the agent was moved and may still be
      working where it was, and offers no cancel; for a removal it is today's text for an agent
      that is no longer configured. A task that had ended keeps its outcome. A task that
      becomes pending between the list and the change is given up and counted. A task a run in
      progress starts at the earlier address after a move is given up at the worker's next
      round. An answer, and a cancel, for a task whose `agent_ref` is not the link's gives the
      error result "Agent '<name>' task <id> was given up when the agent was moved or removed;
      nothing was sent." before anything is sent; a task given up for its age can still be
      canceled. An answer under way during a move does not write over the row the move gave up,
      and a check under way at the earlier address does not turn the task back into an outcome:
      the thread is told once. A reply that names a task id the name already has under another
      ref is treated as malformed. A cookie the earlier stand-in set is not sent to the new
      one. While no usable LLM profile is active a given-up task is listed at once and its
      thread is told once one is active. A removed agent's earlier tasks stay listed in their
      threads; a thread's conversation follows the name across a move and across a removal and
      a new add (FR-024 to FR-026, SC-010, R10, R13)
- [X] T098 [P] [US5] Extend `tests/test_agents_approval.py`, part 3, through the routes. After
      a removal: the next run has neither tool; a run in progress that started while the agent
      ran without approval and then hands off to it sends nothing, with the error result "Agent
      '<name>' was removed; nothing was sent."; a continuation that began before the removal
      and still holds an approved hand-off sends nothing either; by the stand-in's record
      nothing of any kind reached it after the removal was answered (SC-004). A paused proposal
      decided after its agent was removed is never sent, whatever the decision; after the agent
      was removed and another added under its name, the proposal accepts what the tool under
      that name now allows, nothing is sent whatever is decided, and the call gives "Agent
      '<name>': the agent this message was proposed for was removed; nothing was sent."; a call
      with no row follows the name only when the tool is an environment agent's. A paused
      proposal approved after its agent was moved is sent to the current address. A run in
      progress finishes at the address it started with; when another agent was meanwhile added
      at that address, it sends nothing: "Agent '<name>': the address this run started with is
      another agent's now; nothing was sent." (FR-019, FR-022, FR-023, SC-003, SC-004, R7, R8)
- [X] T099 [P] [US5] Extend `web/__tests__/lib/agents.test.ts`, part 5: `changeAgent` with
      `url` sends it in the body; `removeAgent(id, version)` sends a `DELETE` with the version
      as a query and returns how many tasks were given up
- [X] T100 [P] [US5] Extend `web/__tests__/components/agents/agent-form.test.tsx`, part 3,
      changing the address: the confirmation names the agent and says that the change applies
      from the next run, that a run in progress finishes at the earlier address, that a
      proposal already waiting for a decision will go to the new address once approved, how
      many of the agent's tasks are pending, as the list has it at that moment, and that they
      will be given up; an address and an approval choice changed together show both meanings;
      a refusal keeps what was typed and says why; after the change the form says how many
      tasks were given up (FR-011, FR-024)
- [X] T101 [P] [US5] Write `web/__tests__/components/agents/remove-dialog.test.tsx`, after the
      profile deletion's tests: the dialog names the agent, says the removal holds at once,
      also for runs in progress, says how many tasks are pending as the list has it at that
      moment, and that they will be given up; nothing is sent before Confirm, and the second
      press of a double press is ignored; the request carries the version the dialog opened
      with; afterwards the console says how many tasks were given up; `agent_changed` and
      `agent_not_found` have the list read again; no answer says the outcome is not known; the
      dialog follows the live copy and closes when the agent is gone from the list (FR-004,
      FR-010 to FR-012, FR-024)
- [X] T102 [P] [US5] Extend `web/__tests__/components/agents/agent-card.test.tsx`, part 4: a
      saved agent has Remove; a saved agent that is not in use has Remove and nothing else; an
      environment agent has none; a saved agent's card shows its number of pending tasks when
      it is not zero (FR-017, FR-024)
- [X] T103 [P] [US5] Extend `tests/test_agents_address_nowhere.py`, part 4: a move with each
      refusal, a removal, a task given up and reported in its thread, and a run refused because
      the address it started with is another agent's; the search of part 1 again (FR-028,
      SC-008)

### Implementation for User Story 5

- [X] T104 [US5] Extend `src/freyja_agent/delegates/a2a_tools.py`. One more rule in the check
      before a send, after the two of T088: the tool's link is of an earlier address and
      another agent now has that destination, so nothing is sent, with the error result of
      T098, logged. A hand-off updates a task's row only while the task is still pending, where
      today an answer updates it whatever its state. A reply naming a task id the name already
      has under another ref is malformed (FR-022, FR-025, FR-030, R7, R10)
- [X] T105 [US5] Change `src/freyja_agent/delegates/delegation_worker.py` and `record_outcome`
      in `a2a_tools.py`: `record_outcome` takes the states it may write over, the worker
      passing the pending ones so a check never writes over a given-up task, and the cancel
      path keeping today's; a task the worker gives up because its ref is of an earlier address
      of an agent that still exists gets the text for a move (FR-024, FR-025, R10)
- [X] T106 [US5] Add `url` to `AgentUpdate` and `AgentRemoved` to
      `src/freyja_agent/api/schemas.py`, and implement the move and `DELETE /agents/{agent_id}`
      in `src/freyja_agent/api/agents.py`: a `url` that is the saved form is no change;
      otherwise the checks and the card read of an add, taking one of the four slots only, then
      the store's move, then `registry.adopt` with the card, which makes a new link with a new
      client; a removal through the store; each taken up on this instance at once; the two log
      lines (FR-009, FR-010, FR-012, FR-014, FR-024, FR-030, R10, R15, R16)
- [X] T107 [P] [US5] Add `removeAgent` to `web/lib/agents.ts` and `url` to what `changeAgent`
      sends
- [X] T108 [US5] Extend `web/components/agents/agent-form.tsx` with the address of a saved
      agent and its confirmation, as T100 asks
- [X] T109 [US5] Create `web/components/agents/remove-dialog.tsx`, after
      `web/components/llm/delete-dialog.tsx`; add Remove and the pending count to
      `web/components/agents/agent-card.tsx`; say how many tasks were given up from
      `web/components/agents/agent-list.tsx`
- [X] T110 [US5] Write the end-to-end tests of this story: extend
      `web/e2e/agents-manage.spec.ts` and `AgentService`, part 4. A move shows the confirmation
      with the number of pending tasks, and once confirmed the card shows the new address and
      the console says how many were given up; a move to an address where nothing answers is
      refused and the card keeps its address. A removal names the agent and its pending tasks,
      and once confirmed the card is gone. An agent removed elsewhere is reported as gone and
      the list is read again. A change from values that are no longer current is refused and
      the card shows the agent as it is now. There is no way to change a name, and none to move
      or remove an environment agent (FR-032, R21)

**Checkpoint**: quickstart section 5 works.

---

## Phase 9: User Story 6 - An agent's address stays inside the Agents section (Priority: P6)

**Goal**: Proof that a saved agent's address is returned by the Agents routes and by nothing
else, and that a page of another origin can neither read nor change an agent. The rule that the
console cannot be shown inside another origin's page is in place since Phase 3; this phase
checks it once more with an address on the page.

**Independent Test**: add an agent on a recognizable port, drive every route of the Agents
section with it, refusals included, with runs that complete, pause, and fail, and search
everything else the service returned, streamed, kept, and logged for the port: nothing. From
another origin, nothing can be read or changed, and the console is not shown in a frame.

### Tests for User Story 6

- [X] T111 [P] [US6] Complete `tests/test_agents_address_nowhere.py`, part 5. The saved agent
      is saved under `localhost`, removed, and saved again under `127.0.0.1`, so both spellings
      are searched for; the parts of Phases 5 to 8 are run as one journey; and a check at the
      end of the file fails when a route of `api/agents.py` or a refusal code of
      contracts/openapi.yaml is driven by no part of it (FR-028, SC-008, R20)
- [X] T112 [P] [US6] Extend `tests/test_agents_api.py`, part 5, as one sweep: each of the four
      routes that change something is refused with 403 `other_origin` for `Sec-Fetch-Site:
      cross-site` and `same-site`, and for an `Origin` whose host is not the one asked for,
      also behind `X-Forwarded-Host`, before anything else is looked at, and nothing changed; a
      request with none of the three headers is served; each route that takes a body refuses
      `text/plain` and a form post with 422; no response of the five routes, and no answer to a
      preflight, carries a cross-origin header (FR-029, SC-009)

### Implementation for User Story 6

- [X] T113 [US6] Close each gap T111 and T112 find, in the module that words, returns, or logs
      the text, and add the route or the line to the proof. When they find none, say so in the
      pull request (FR-028, FR-029)
- [X] T114 [US6] Write the end-to-end tests of this story: extend `web/e2e/agents.spec.ts`. A
      saved agent's card shows its address and an environment agent's shows none; the only
      requests of the page that hold a saved agent's address hold it in a body; with a saved
      agent on the page, the Agents section still cannot be shown in a frame of another origin
      (FR-029, FR-032, R21)

**Checkpoint**: quickstart section 7 works; User Story 6 is complete.

---

## Phase 10: Polish & Cross-Cutting Concerns

- [X] T115 [P] Complete `README.md`, which Phases 3 to 5 kept true: the Agents section and how
      an agent is added, checked, changed, and removed; saved agents beside
      `FREYJA_A2A_AGENTS`; the console's warnings; that requiring approval and a removal hold
      at once; the `approval_now_required` code; and what anyone who can reach the port can now
      do. Only what now exists
- [X] T116 [P] Update `AGENTS.md`: Layout entries for `runtime/saved_agents.py`,
      `delegates/agent_registry.py`, `delegates/agent_cards.py`, `delegates/addresses.py`,
      `api/agents.py`, and `api/guards.py`; what changed in `a2a_agents.py`, `a2a_tools.py`,
      `delegation_worker.py`, `runtime.py`, `threads.py`, and `tools.py`, a context in place of
      `model_for_run()`; the web app's Agents page, components, `lib/agents.ts`, and the two
      new end-to-end files; and under Conventions, that an agent's address appears nowhere
      outside the Agents routes, that text about an agent goes through the scrubber, and that a
      new route or log line about agents is added to `tests/test_agents_address_nowhere.py`
- [X] T117 [P] Update `specs/001-thread-runs-api/contracts/sse-events.md` so the stream
      contract names the `approval_now_required` code where a reader looks first, pointing at
      `specs/005-web-delegate-agents/contracts/sse-events.md`
- [X] T118 [P] Update `specs/005-web-delegate-agents/plan.md`: under Implementation Notes,
      anything the implementation settled differently from the plan, beside what T015, T035,
      T036, T062, T079, and T095 recorded, and its file tree where the implementation touched a
      file it does not list
- [X] T119 Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest`,
      `FREYJA_TEST_DATABASE_URI=... uv run pytest tests/integration`, in `web/` `pnpm lint`,
      `pnpm test`, `pnpm build`, and `pnpm test:e2e`, and `uv run pre-commit run --all-files`;
      confirm that no Ruff suppression was added, that both default suites pass with the
      network disconnected, and that no test that existed before this feature changed beyond:
      the seven cases of T016; the one of T082; the page test of T041; the one in
      `web/e2e/console.spec.ts`; and the files that gained setup or names and lost nothing,
      `tests/integration/test_a2a_tasks_postgres.py` (T015, T052),
      `web/__tests__/components/app-sidebar.test.tsx` (T073), and the shared setup of
      `web/e2e/llm.spec.ts` and `web/e2e/profiles.spec.ts` (T079) (FR-031, FR-032, SC-014,
      principle V)
- [X] T120 Run `specs/005-web-delegate-agents/quickstart.md`, all eight sections, against the
      real service, stand-ins on loopback, and the web app in a browser. Then save twenty
      agents through the API and time the Agents section from navigation to the last card:
      within two seconds (SC-015). Time a start with three stand-ins that never answer against
      a start with all answering: no later than the startup limit (SC-006)
- [X] T121 Re-run the Constitution Check of `specs/005-web-delegate-agents/plan.md` against the
      implemented code and note the result in the pull request description (principles I to VI)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies. T002 and T003 run beside T001.
- **Foundational, part 1 (Phase 2)**: depends on Phase 1 for T003 only.
- **Foundational, part 2 (Phase 3)**: depends on Phases 1 and 2. Blocks every user story.
- **User Story 1 (Phase 4)**: depends on Phase 3. The MVP.
- **User Story 2 (Phase 5)**: depends on Phase 4: it adds to the router, the list, and the
  library module that Phase 4 creates.
- **User Story 3 (Phase 6)**: depends on Phase 5: the check of a saved agent needs an agent
  that was added.
- **User Story 4 (Phase 7)**: depends on Phase 5: it reads the proposed calls Phase 5 records.
  It does not depend on Phase 6.
- **User Story 5 (Phase 8)**: depends on Phase 7: it extends the check before a send, the
  form, and `PATCH`, which Phase 7 creates.
- **User Story 6 (Phase 9)**: depends on Phases 4 to 8: it drives every route.
- **Polish (Phase 10)**: depends on all stories.

### Within Phase 2

- T004, T006, T007, T008 have no dependency inside the phase; T005 follows T004 (same file).
- T009 before T010 (the forms of an address) and before T013. T010 before T011.
- T012 before T013; T013 before T014 (same file); T014 before T015.

### Within Phase 3

- T016 first: it fails until T021 and T024.
- T017 before T018; T018 before T019 and T020; T020 before T021; T021 before T022; T022
  before T023 and T025.
- T024, T025, and T026 touch different files and follow T022.
- T033, T034, and T035 depend on nothing else in the phase and run in that order.
- T027 follows T021 to T026; T028, T029, and T030 follow T027, since the fixture is built on
  the code; T029 follows T028 (same file).
- T031 follows T026. T032 can be written at any time and is merged with the phase.

### Within Each User Story

- Tests first, and they must fail before implementation starts.
- Tasks that share a file run in the order listed. `tests/test_agents_api.py` grows by parts in
  T037, T050, T080, T096, T112; `tests/test_agents_apply.py` in T030, T051, T083;
  `tests/test_agents_approval.py` in T053, T081, T098;
  `tests/test_agents_address_nowhere.py` in T054, T069, T087, T103, T111;
  `tests/test_agent_task_store.py` in T008, T052; `src/freyja_agent/api/agents.py` in T043,
  T059, T074, T090, T106; `src/freyja_agent/api/schemas.py` in T042, T058, T074, T090, T106;
  `src/freyja_agent/delegates/a2a_tools.py` in T019, T061, T062, T088, T104, T105;
  `src/freyja_agent/api/streaming.py` in T022, T062, T089;
  `src/freyja_agent/runtime/agent_tasks.py` in T012, T060; `web/lib/agents.ts` in T044, T063,
  T075, T091, T107, and its test file in T038, T055, T070, T084, T099; the card in T045, T076,
  T093, T109, and its test file in T039, T071, T086, T102; the form in T064, T092, T108, and
  its test file in T056, T085, T100; the list in T046, T065, T077, T109, and its test file in
  T040, T057, T072; `README.md` in T032, T049, T067, T115.
- `web/e2e/api.ts` grows in T048, T066, T079, T094, and T110, `web/e2e/agents-manage.spec.ts`
  in T066, T079, T094, and T110, `web/e2e/agents.spec.ts` in T048, T079, and T114, and
  `web/e2e/console.spec.ts` in T035 and T048.
- A story's end-to-end task (T048, T066, T079, T094, T110, T114) comes after its
  implementation: it drives the built console. It is not one of the tests that must fail first.

### Parallel Opportunities

- Phase 1: T001, T002, T003 together.
- Phase 2: T004, T006, T007, T008 together; T011 beside T012.
- Phase 3: T024, T025, T026 together; T031 and T032 beside the tests; T033 to T035 beside all
  of it.
- Phase 4: T037 to T041 together; then T044 beside T042 and T043.
- Phase 5: T050 to T057 together; T063 beside T058 to T062.
- Phase 6: T068 to T073 together; T075 beside T074.
- Phase 7: T080 to T087 together; T091 beside T088 to T090.
- Phase 8: T096 to T103 together; T107 beside T104 to T106.
- Phase 9: T111 and T112 together.
- Phase 10: T115 to T118 together.
- Phase 6 can be built at the same time as Phase 7, since both depend on Phase 5 only. They
  share `src/freyja_agent/api/agents.py` and `schemas.py`, `web/lib/agents.ts`, the card
  component, their test files, `tests/test_agents_address_nowhere.py`, `web/e2e/api.ts`, and
  `web/e2e/agents-manage.spec.ts`, so the second one to merge takes the other's changes
  first.

---

## Parallel Example: User Story 2

```bash
# Write the tests, then watch them fail:
Task: "POST /agents in tests/test_agents_api.py part 2 (T050)"
Task: "An add, then runs, a restart, and two apps in tests/test_agents_apply.py part 2 (T051)"
Task: "The proposed calls in tests/test_agent_task_store.py (T052)"
Task: "A call belongs to its agent, in tests/test_agents_approval.py part 1 (T053)"
Task: "The address proof, part 1, in tests/test_agents_address_nowhere.py (T054)"
Task: "addAgent in web/__tests__/lib/agents.test.ts part 2 (T055)"
Task: "The Add form in web/__tests__/components/agents/agent-form.test.tsx part 1 (T056)"
Task: "The Add button in web/__tests__/components/agents/agent-list.test.tsx part 2 (T057)"

# Then implement:
Task: "AgentCreate, then POST /agents, in src/freyja_agent/api/ (T058, T059)"
Task: "The proposed calls: the table, the middleware, the tools (T060, T061, T062)"
Task: "addAgent in web/lib/agents.ts (T063)"
Task: "The form in web/components/agents/agent-form.tsx (T064), then the list (T065)"

# Then drive the built console:
Task: "web/e2e/agents-manage.spec.ts part 1 (T066)"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup.
2. Complete Phase 2: what is kept and the pieces, with nothing wired.
3. Complete Phase 3: the agent set and the context. The service gets its agents through the
   registry and starts when one is down.
4. Complete Phase 4: User Story 1.
5. **STOP and VALIDATE**: `uv run pytest`, `pnpm test` and `pnpm test:e2e` in `web/`, then
   quickstart section 1 in a browser.

### Incremental Delivery

1. Setup and the foundation: every earlier test passes, changed only as FR-031 lists, and the
   console can no longer be framed.
2. User Story 1: the console shows which agents Freyja can hand work to. First increment
   worth showing.
3. User Story 2: an agent can be added from the console and used with no restart, always
   under approval. This is the feature's purpose.
4. User Story 3: the check, and the console's warnings about agents that are down.
5. User Story 4: the approval choice can be set and changed, with the checks that make a
   tightening hold at once.
6. User Story 5: agents can be moved and removed, and their tasks are given up with them.
7. User Story 6: the proof completed and the sweep of other origins.
8. Polish: documentation, the full quickstart, the gates.

Each phase is one pull request and one merge request, as in features 001 to 004. Phase 1 is
three small tasks; it can be opened together with Phase 2 as one request if that is preferred.

Between Phase 5 and Phase 8, `main` holds a console that can add an agent and cannot yet
remove one. That is safe, since every saved agent requires approval until Phase 7, and it is
not a state to deploy from.

### Parallel Team Strategy

With more than one developer: after Phase 3, one takes the service tasks of a story and
another its web tasks; the contract in `contracts/openapi.yaml` is what they share. Phases 6
and 7 can proceed at the same time once Phase 5 is merged.

---

## Notes

- [P] tasks touch different files and depend on no incomplete task.
- [Story] labels map a task to its user story for traceability.
- Verify that tests fail before implementing.
- Commit after each task or logical group, in the `type: summary` form.
- Stop at any checkpoint to validate a story on its own.
- Six tasks check something the plan could not verify: T015, T035, T036, T062, T079, and T095.
  Each records what it found in plan.md.
- Choices this task list makes beyond the plan, each open to change before its phase starts:
  - The foundation is two phases, and the second carries the change to startup (FR-020,
    FR-021), with the seven existing cases. A link that is made without contacting anything
    cannot stop startup, so keeping today's stop until Phase 6 would mean code written to be
    removed and the seven cases changed twice.
  - Adding accepts only `approval` `required` until Phase 7. An agent that runs without
    approval could otherwise be saved two phases before anything can put it back under
    approval, and three before it can be removed. Scenario 10 of User Story 2 is therefore
    delivered in Phase 7.
  - `PATCH` accepts the approval choice in Phase 7 and the address from Phase 8; until then a
    body with `url` is refused with 422.
  - A rule lands with the first route, or the first restart, that can reach it. In Phase 3:
    an answer or a cancel is refused when its task is not the link's. In Phase 5: a call
    proposed for one agent is never sent to another under its name. In Phase 7: nothing is
    sent to a saved agent that is gone or now requires approval. In Phase 8: nothing is sent
    to an address another agent took over.
  - The rule that a proposal stays with its agent is applied in an environment agent's tools
    too, where research R8 spoke of a saved agent's. Without it, a proposal made for a saved
    agent would be sent to an environment agent that took its name at a restart. R8 is
    corrected with this task list.
  - `GET /agents` answers 500 when its store cannot be read, where R3 has reads fall back to
    the last set. The list is made of what the store holds, addresses and versions included,
    and FR-004 asks that earlier data is never shown as current. `/tools` and the thread
    state fall back as R3 says.
  - The proposed-call table is created in Phase 5 with its first user, not with the other
    tables in Phase 2: a table created before anything used it could not be corrected by
    `CREATE TABLE IF NOT EXISTS` once deployed.
  - The proof that an address stays in the Agents section starts in Phase 5 and grows with
    each phase, where feature 004 wrote its proof last and it then found two leaks.
  - Documentation is corrected in the phase that makes a sentence false (T032, T049, T067),
    and completed in Phase 10.
  - The frame rule (FR-029) is built in Phase 3, not with User Story 6: the confirmations it
    protects arrive in Phases 5 to 8, and the LLM section has some already.
  - The Add button arrives with User Story 2, not User Story 1, so the console never offers
    something the service cannot do yet.
  - Values the plan left open: five seconds for the lock timeout of the column (T012); 200
    addresses that were never saved kept by the scrubber (T005, T010).
  - Wording the plan left open: the text of a task given up at a move (T012); the error
    results a call gives without sending (T019, T053, T081, T098), whose content
    data-model.md fixes.
  - `DelegateTool` gains `agent_id` (T061), so the middleware knows which saved agent a tool
    is for; the catalog's shape in `/tools` does not change.

## Phase 11: Convergence

- [X] T122 CRITICAL: Record a person's approve or edit decision on each proposed call of an
      agent's tool when a resume is accepted (`decided` on the call's row, a column of
      `freyja_agent_calls`), and have `_still_allowed` in `src/freyja_agent/delegates/a2a_tools.py`
      treat a call as decided only when its row records that decision, in place of inferring it
      from the row's `gated` and the tool; test the sequence that sends without a decision today
      (proposed while the agent requires approval, the checkpoint after it fails, retried while
      it runs without approval, the tool's step fails, retried after it requires approval again)
      per Constitution I, FR-022 (contradicts)
- [X] T123 Remove an environment agent's whole address from what the process logs: the query,
      the fragment, the parameters, and the user info that follow or precede the host and port the
      scrubber finds, in `src/freyja_agent/delegates/addresses.py`, with a test of httpx's request
      line for an entry with a query per FR-028, FR-017 (partial)
- [X] T124 Compare every environment entry's port and path with saved addresses, an entry the
      saved form refuses included (a query, a user name, a fragment, an encoded or doubled path),
      in `src/freyja_agent/delegates/agent_registry.py`, so that no saved agent is added or moved
      where such an entry leads per FR-006, FR-017 (contradicts)
- [X] T125 Say in the sidebar's visible text what its number is about, not only for assistive
      technology, in `web/components/app-sidebar.tsx`, with its unit and end-to-end tests per
      FR-001a, SC-017 (partial)
- [X] T126 Say that the outcome is not known when a check gets no answer, and read the list
      again, in `web/components/agents/agent-card.tsx` per FR-004 (partial)
- [X] T127 Show the agent as the list has it now (address, approval choice, state, pending tasks)
      in the removal confirmation, in `web/components/agents/remove-dialog.tsx` per FR-011
      (partial)
- [X] T128 Correct the documents: record in `plan.md` the renamed table test of
      `tests/integration/test_a2a_tasks_postgres.py`, point the FR-027 row of the requirement to
      test map at `tests/test_agents_startup.py` for the proxy, add the 422 for an id in neither
      form and the 500 `internal` to the Agents routes in `README.md`, and say in `.env.example`
      and `specs/003-a2a-agent-delegation/contracts/agent-config.md` that
      `FREYJA_A2A_STARTUP_TIMEOUT_SECONDS` also bounds the card read of an add, a move, and a
      check per T119, T115, plan: requirement to test map (partial)
