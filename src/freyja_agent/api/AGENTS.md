# `src/freyja_agent/api/`

The FastAPI application, module by module. The commands, the conventions, and the map of the
other directories are in the `AGENTS.md` at the root of the repository.

- `app.py` is `create_app(open_runtime)`, `/healthz`, `/readyz`,
  which asks the database one statement under a time limit (`Runtime.ping`, set by
  `Runtime.open`) and answers 200 or 503 with nothing of the connection, and the
  `freyja-api` entry point;

- `hosts.py` is `AllowedHosts`, the middleware `create_app` puts
  before every route, which refuses a request made for a host name the service does not
  answer for (403 `unknown_host`);

- `runs.py` the bodies of the two thread routes that run a
  thread (`start_message`, `start_resume`), what the console's routes do around them
  (`open_first`, `go_on`, `start_console_run`), and the stop (`ask_stop`), so that the routes
  and the A2A service start runs through one piece of code; with feature 009 off it refuses
  a message or a decision on a caller's thread with 409 `a2a_server_off`;

- `received.py` the feature's service, `ReceivedTasks`: accepting a caller's message as a console run, where a
  caller's thread stands (`standing`, research R7's table and its three fences), writing a
  task's end once (`look`), and the function every release calls while the feature is on;
  a task as its caller is told it (`view`: the fixed status sentences and reasons, every text
  read from the thread cleaned of agents' addresses and models' keys), the follower that
  turns a console run's events into what a caller is sent, names only and the text of
  Freyja's own reply (`follow`), and the wait of a send without a stream (`settle`);

- `a2a_server.py` the routes of feature 009, added by the lifespan while the feature is on:
  the card (`GET /.well-known/agent-card.json`) and `POST /a2a`, the SDK's JSON-RPC
  dispatcher behind the checks of other origins, a credential, and the size, with
  `FreyjaRequestHandler`, which checks a message's form, calls `ReceivedTasks`, turns its
  tasks into the protocol's types, and refuses what the contract does not offer; it is the
  one module that imports the SDK's server modules, and builds the card and the routes for
  both addresses;

- `a2a_listener.py` the outside listener of feature 009: the application with the card and
  the operations and nothing else (`application`), for named callers' tokens only, and the
  `uvicorn.Server` the lifespan starts on a socket it binds itself and stops before the
  runtime closes (`Listener`);

- `streaming.py` marks each `message`
  event of a run on the strict build with `metadata.reply`, whether it is text of Freyja's
  own reply (`is_reply`); the two message routes refuse a caller's thread with 409
  `caller_thread` (`runs.refuse_caller_thread`), and a thread's row has `caller`;

- `threads.py` the three thread routes (runs, state, resume),
  each of which takes a context, the two that run under the thread's lease, and validates
  a decision on the orchestrator that then carries it out;

- `tools.py` the catalog route `GET /tools`, which takes a context, so it reads two rows to
  learn whether the agents or the active profile changed, contacts nothing, and lists the
  agents;

- `thread_index.py` the two routes that list threads (feature 007), `GET /threads` and
  `GET /threads/{thread_id}`, in a router of their own under the same prefix, which settle
  the rows whose run ended since they were last read, show no row a status nobody read for
  it, leave out a row that cannot be read instead of failing the list, contact nothing, and
  change no thread, and the three routes of the console's runs:
  `POST /threads/{thread_id}/runs/unattended` and `POST /threads/{thread_id}/resume/unattended`,
  which call the handlers of `threads.py`, take the run's events out of the response
  (`body_iterator`), and hand them to `UnattendedRuns` (the first opens a thread that has
  a state and no row before the run starts, so that its row keeps the thread's own first
  message), answering with
  `Cache-Control: no-store, no-transform` and a ping every `PING_SECONDS`, and
  `POST /threads/{thread_id}/stop`, which marks the row and answers 202, with the run
  the stop is for, 404, or 409 (`no_run`, `not_stoppable`),
  with their shapes in `thread_index_schemas.py`;

- `agents.py` the routes of the Agents section under `/agents`: `GET /agents`, the list,
  which answers 500 when the saved agents cannot be read, `POST /agents`, which reads an
  agent's card and saves the agent when it can be used, requiring approval unless the request
  chose otherwise, `PATCH /agents/{id}`, which changes a saved agent's approval choice, its address, or
  both at the version the page read, reading the card at a new address first and giving up
  the agent's pending tasks with the move, `DELETE /agents/{id}`, which removes a saved
  agent and gives up its pending tasks, and
  `POST /agents/{id}/check`, which reads an agent's card again through its link and says
  whether the agent can be used; the first three are the only answers of the service that hold
  a saved agent's address; and `PUT /agents/mode`, which sets the agents' approval mode
  (feature 010), returned with the list as `mode`;

- `llm.py` the profile routes under `/llm`: `GET /llm/profiles`, `POST /llm/profiles`,
  `PATCH` and `DELETE /llm/profiles/{id}`, and `POST /llm/profiles/{id}/activate`, none of which
  asks a model anything, and `POST /llm/profiles/{id}/test` and `POST /llm/test`, which hold a
  profile's test with its endpoint; an activation is applied on this instance at once;

- `guards.py` what several routers share: the LLM routes and the Agents routes answer a
  refused request without repeating the value (`NoInputRoute`), and those two and the
  thread routes turn away a request that changes something when a browser marks it as made
  by a page of another origin (`refuse_other_origins`, 403 `other_origin`);

- `streaming.py` the SSE run loop with the lease heartbeat, also driven by the worker for background runs,
  which turns a run away with `no_usable_model` before the graph when no usable model is active,
  ends a resume whose paused step came to hold a call that needs a decision with
  `approval_now_required` and the agents it is about (it knows that refusal by the approval
  middleware's sentence, which `tests/test_a2a_gate.py` pins),
  and takes the profile's key out of a model error before it is logged or sent;

- `outcomes.py` records pending tasks' outcomes in their thread exactly once and answers messages to agents
  that a stop left in flight;

- `decisions.py` resume validation, the decisions a subagent's gate already took
  when a resume's run stopped after it (`decisions_taken`, which the route refuses others
  against with 409 `already_decided`), and what a resume tells the step after the gate
  (`decided_calls`: the gate's namespace and the calls the person approved or edited),
  including edits checked against
  a server tool's JSON Schema and pinned arguments;

- `schemas.py` and `serialize.py` the request,
  response, and event shapes.
