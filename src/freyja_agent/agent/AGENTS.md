# `src/freyja_agent/agent/`

The orchestrator itself, module by module. The commands, the conventions, and the map of the
other directories are in the `AGENTS.md` at the root of the repository.

- `factory.py` is the one place the orchestrator is assembled
  (`build_agent`; the service, the tests, and any future CLI call it). `build_agent` takes the
  tool server tools as `delegate_tools` and returns an `Orchestrator`: the compiled graph as
  `.agent` and the `Catalog` of exactly the tools it was given as `.catalog`.

- `catalog.py` holds the three classifications, the fixed table of Freyja's own tools (`OWN_TOOLS`), the
  catalog, and the interrupt config generated from it; a tool with no classification raises
  `UnclassifiedToolError` and stops startup.

- `prompts.py` is the system prompt and
  `append_to_system_message`, with which a middleware adds text to it for one model call,
  `tools.py` the
  gated placeholder tool `send_notification`.

- `agent_tasks.py` is `AgentTasksMiddleware`, which
  lists the thread's pending agent tasks in the prompt and, after every reply of the model,
  records which agent each call of an agent's tool was proposed for, and, in a resume, right
  after the approval gate, which of those calls a person approved or edited; and the
  `check_agent_tasks` tool; both are added only when an agent is configured
  (`build_agent(agent_tasks=...)`).

- `build_agent` also takes the file backend and whether to
  name the thread by its alias (feature 009): `caller_files.py` is `CallerFiles`, the strict
  build's backend, which gives a named caller `<files dir>/<name>` and the skills read-only,
  by the key `freyja_caller_files` of the run's config, and refuses a call without it; and
  `context.py`'s `thread_alias` is what the strict build tells the model in place of a
  thread's id, and its `HOPS_KEY` the key of a caller's run's config that holds the
  hand-over count of the conversation's last message (`SegmentedGraph` puts it there), which
  the agent tools send on, plus one, as the metadata `freyja.hops` (`HOPS_METADATA`).

- `telemetry.py` is `TelemetryMiddleware`, which `build_agent` puts on the
  orchestrator and its subagent whether telemetry is on or off, and which records each model
  call and each tool call, naming a tool from the tools the build was given and never from
  what a model wrote.
