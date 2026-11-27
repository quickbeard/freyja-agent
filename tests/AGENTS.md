# `tests/`

The suite's fixtures, fakes, stubs, and stand-ins, and where a feature's tests are. The
commands, the conventions, and the map of the other directories are in the `AGENTS.md` at the
root of the repository.

- `conftest.py` supplies the required environment and the in-memory
  runtime fixtures, so no `.env` is needed; `make_mcp_app` assembles an app the production way
  with stub tool servers, `make_a2a_app` with stand-in agents, `make_llm_app` with LLM
  profiles and a model factory, and `make_a2a_server_app` with feature 009 on, one stand-in
  agent, and a received-task store a second app can share (`again`), with helpers to accept
  a caller's message through `ReceivedTasks` and read its run to the end.

- `fakes.py` holds the scripted chat model, a labelled one that
  can hold a call open, and the test model factory;

- `nowhere.py` what the three proofs
  that no key and no agent address gets out share;

- `llm_stub.py` the stand-in model
  endpoint, OpenAI-compatible, which the suite reaches through the real client, and whose
  model id `call-tools` asks for tools, so that the Tasks section can be tried by hand
  without a provider's key;

- `mcp_stub.py` the stub MCP server, which
  records every call it
  receives;

- `a2a_stub.py` the stand-in A2A agent, built with the SDK's server classes, which
  records every message and cancel and behaves by a keyword at the start of the message, and
  which, for feature 008, sends `working_text` with every task's first working status, has a
  held task send a working status with the text a test reports (`report`, which returns once
  the status is stored), and reports "Step k of n" for `steps <n> <seconds>`, and, for
  feature 009, keeps the hand-over count each message and cancel carried (`hops`,
  `cancel_hops`) and, served by hand, writes it with each message;

- `asgi_stream.py` the httpx transport that runs a stand-in in the test process and streams
  its replies as they are sent;

- `unattended_helpers.py` what the tests of the console's runs
  share (a tool step a test holds open, a stream read in a thread of its own);

- `test_a2a_progress.py` the tests of feature 008, the progress of pending agent tasks, in
  eight parts, from what counts as progress to the stream of an agent that does not stream;

- `test_agents_mode.py` the tests of feature 010, the agents' approval mode, from the set's
  rule to a run in progress that Manual holds at once;

- `a2a_caller.py` the stand-in caller of feature 009, the SDK's own client behind a small
  class and a command, as another agent would call Freyja;

- `a2a_requests.py` the requests
  the feature's tests make to the A2A routes, with the stand-in caller and without it;

- `fakes.py`'s `StreamingChatModel` streams a reply in pieces and can hold between two; the
  feature's tests are
  `test_a2a_server_*.py` and `test_received_tasks.py`, with
  `integration/test_received_tasks_postgres.py` for the store on Postgres;

- `telemetry_capture.py` is telemetry that keeps what it records in memory (`Captured`),
  built through the seam of `runtime/telemetry.py`; the tests of feature 011 are
  `test_telemetry.py`, telemetry off and on, runs, calls, and who made a model call,
  `test_telemetry_requests.py`, the requests and a run under its request,
  `test_telemetry_waiting.py`, the work that waits, and `test_telemetry_nowhere.py`, the
  proof that none of it is content;

- `integration/` holds the Postgres tests, skipped unless
  `FREYJA_TEST_DATABASE_URI` is set.
