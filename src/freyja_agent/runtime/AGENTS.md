# `src/freyja_agent/runtime/`

What an instance holds while it runs, module by module. The commands, the conventions, and the
map of the other directories are in the `AGENTS.md` at the root of the repository.

- `persistence.py` opens the shared Postgres pool, checkpointer,
  and store, brings Freyja's tables to the build's schema version before any store is
  given the pool, and holds what every Postgres store takes from it: `Pool`, the pool's
  type, whose connections return a row as a dict, and `one_row`, the row of a statement
  that always returns one;

- `migrations.py` is the schema: `MIGRATIONS`, the ordered list
  every table and column of Freyja's comes from, and `migrate`, which applies what a
  database lacks at startup, one transaction each under one advisory lock and a lock
  timeout, records it in `freyja_schema_migrations`, and stops startup for a database a
  newer build migrated, one whose applied migration differs from the build's (`pending`, by
  checksum), or a table it cannot get in time; no store makes or changes a table;

- `locks.py` is the per-thread run lease (`PostgresRunLocks`, `InMemoryRunLocks`);

- `agent_tasks.py` is the `AgentTaskStore` for agent conversations, the send ledger, agent
  tasks, and the calls of agents' tools that a model proposed (`PostgresAgentTaskStore` with
  its four `freyja_agent_*` tables, and `InMemoryAgentTaskStore`); a task keeps the progress
  its agent last reported and when it was learned (`progress`, `progress_at`, feature 008),
  columns that migration 1 adds to a table of an earlier deployment, as it adds `agent_ref`;

- `saved_agents.py` is the `SavedAgentStore` for the agents an operator
  saved (`PostgresSavedAgentStore` with `freyja_agents` and `freyja_agents_state`, and
  `InMemorySavedAgentStore`), and for the agents' approval mode of feature 010 (`mode`,
  `set_mode`), a column of `freyja_agents_state` that migration 2 adds, whose change moves
  the saved agents' revision;

- `received_tasks.py` is the `ReceivedTaskStore` of feature 009,
  the conversations and received tasks of other agents (`PostgresReceivedTaskStore` with
  `freyja_a2a_conversations` and `freyja_a2a_tasks`, and `InMemoryReceivedTaskStore`), whose
  conversation row is what makes a thread a caller's and which accepts a message under one
  advisory lock;

- `callers.py` is `Callers`, which turns an `Authorization` header into a
  caller's name, comparing a token with every named caller's by `hmac.compare_digest`,
  registers every caller's token with the record factory's `TOKENS`
  (`delegates/addresses.py`), and warns of refused credentials once and then at most once in
  ten seconds for each of the two addresses;

- `runtime.py` is the `Runtime` dataclass with the catalog, the tool
  servers, the agent registry, the task store, the saved-agent store, and the delegation
  worker. `Runtime.assemble` opens the tool servers, starts the registry, builds the agent, and
  runs the worker; `Runtime.open` adds
  Postgres and is what the FastAPI lifespan enters. Both take `connect`, the seam the tests swap
  to reach stub servers, and `agent_transport`, the httpx transport each agent link uses, which
  the tests swap to reach stand-in agents. `assemble` also takes `model_factory`, which turns
  an LLM profile into a chat model and is the seam the profile tests swap. Every run, and every route and round of the worker that reads the graph or the catalog, first
  takes a `RunContext` from `Runtime.begin_run()`: the graph, the catalog, the active model,
  and the agent set of one build, in step with the active profile and the saved agents, held
  until it is released. `Runtime.agent` and `Runtime.catalog` are what the instance last
  built. While feature 009 is on the model manager builds a second, strict orchestrator
  beside the first (`ModelManager.builds`), kept apart as `Runtime.strict_agent` and
  `Runtime.strict_catalog`; `begin_run(thread_id)` and `begin_read(thread_id)` ask the
  received-task store who started the thread, on or off (`Runtime.caller_of`), and give a
  caller's thread the strict build. `Runtime.environment` is the environment's profile as the list shows it, described
  once at startup. `Runtime.model_to_check` builds the model a profile's test asks, and
  `Runtime.checks` and `Runtime.environment_test` hold the tests in progress and the
  environment's last result, in memory.

- `thread_index.py` is the index of threads
  (feature 007): the `ThreadIndex` store (`PostgresThreadIndex` with `freyja_threads`, and
  `InMemoryThreadIndex`), one row per thread that has had a run; `IndexedRunLocks`, which
  `Runtime.assemble` wraps the run lease in, so that every run that takes the lease and gives
  it back is told to the index, whoever starts it, and whose own writes never fail the lease
  and hold it up for `INDEX_WRITE_SECONDS` at most, and which calls `on_release`, when one is
  set, after every release (feature 009); and `settle_thread`, `open_thread`, and
  `status_of`, which read a thread's state once, keep what a row needs of it, and turn a row
  into its one status. `Runtime.threads` is the index. A write that is for one run names
  that run's lease owner, and one made from a reading of a thread's state names the number
  of runs the row had; either changes nothing once the row has moved on. Whether a run is in
  progress is the lease's to say, never the row's. Text is cleaned of what a text column
  does not take before it is kept.

- `unattended.py` is the console's runs (feature
  007): `UnattendedRuns` drives a run that goes on without a reader in a task of its own,
  hands each event to its reader and disconnects one that falls `READER_BACKLOG` events
  behind, takes an agent's address out of every text of every event before the event is
  noted or handed on (`_scrubbed`), notes in the index how the run ended (`failed` with
  its error, or `stopped`), and
  at shutdown (`close`) asks each run to stop at its next boundary and waits for it;
  `SegmentedGraph`, which `Runtime.begin_run` puts in every context in place of the graph,
  runs an armed thread in segments that end before each `model` and `tools` step, gives each
  segment what is left of the step limit, and asks between two segments, and nowhere else,
  whether to stop. Every other thread is passed straight to the graph. Since feature 009 it
  first asks, for every run, who started the thread: it refuses a caller's thread on the
  ordinary build and any thread whose lookup fails, puts `freyja_caller_files` into the
  config of a caller's thread on the strict build, and refuses every way to run a graph but
  `astream`; `UnattendedRuns.join` gives one more reader of a console run in progress, and
  `UnattendedRuns.on_start` is told of each run before its first event, which is how a
  caller's follower joins the run a person's decision starts (`ReceivedTasks.on_run_started`).
  `Runtime.unattended` is the console's runs of this instance.
