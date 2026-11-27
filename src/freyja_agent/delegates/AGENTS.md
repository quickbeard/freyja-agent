# `src/freyja_agent/delegates/`

The links to tool servers and to other agents, module by module. The commands, the conventions,
and the map of the other directories are in the `AGENTS.md` at the root of the repository.

- `mcp_servers.py` connects to the configured MCP servers (`open_tool_servers`, `ToolServer`, `default_connect`),
  applies the call time limit, records each server's status, and redacts `pass_env` values from
  everything a server link emits.

- `mcp_tools.py` turns each listed tool into a `DelegateTool`,
  classifies it from the `read_only` list (`classify`), and turns a result into text.

- `a2a_agents.py` is the link to one agent (`AgentLink`): making one contacts nothing, and
  `connect` reads the agent's card and builds the client, so nothing is sent over a link that
  holds no usable card. It pins every request to the configured address, sends messages and
  reads and cancels tasks under the call time limit, records each agent's status, and removes
  every known address from everything it emits (`describe`). A reply that names a task
  with an empty id or conversation id, or gives an id that is too long or holds a control
  character or a line break, is malformed (`malformed`), and nothing of it is kept, since
  an id is written into what the model reads line by line.
  `progress_text` says what of a
  status is a task's progress (feature 008): the text of a working status's message, cleaned
  and cut to `PROGRESS_LIMIT` characters, used by the hand-off and the worker alike.

- `agent_cards.py` reads an agent's card under one time limit and one size limit and says
  in the service's own
  sentence why a card cannot be used (`read_card`, `CardError`, the nine `CardProblem`
  codes), and cuts and cleans what a card says of its agent into the details that are kept
  and shown (`card_details`, `cleaned`).

- `addresses.py` is the process's one address
  scrubber (`SCRUBBER`): it is told every agent address the process is given, takes each
  out of text in every form it can be written in, its path included, and cleans every log
  record through a record factory installed at startup.

- `agent_registry.py` is the
  `AgentRegistry`, which holds the instance's `AgentSet`: the links in use, of environment
  agents and of saved ones, each agent's approval choice, the agents' approval mode (feature
  010: an agent's tools run without approval only when the mode is `auto` and its choice
  allows it, `AgentSet.gated`), and the saved agents that are not in use. It refuses an entry that is invalid in itself at startup, tries every agent at once,
  warns about one it cannot use, makes a new set when the saved agents' revision moved, and
  closes a link once no set that is held has it.

- `a2a_tools.py` builds each agent's
  `handoff_to_<agent>` and `cancel_<agent>_task` tools over the send ledger, so a step that
  runs again never sends twice, checks that a task belongs to the agent the tool was built
  for, that the link is connected, that the call was proposed for the agent the tool
  sends to (`_proposed_for`), that the agents' approval mode is not `manual` now when the
  tool was built to run without approval and no person decided the call (feature 010), and,
  for a saved agent, that the agent as the store has it
  now still exists, still runs without approval when no person decided the call (a person
  decided it only when its row records an approve or an edit), and,
  when it was moved since the run's tools were built, that no other saved agent has the
  address the run started with
  (`_still_allowed`, the last thing awaited before the send's first write), and turns a
  reply into text. A hand-off writes a task's row only while the task is pending, and a
  reply that names a task another agent of the name has is treated as malformed, so a task
  stays with the agent it was given to.

- `delegation_worker.py` is the `DelegationWorker`: each poll it
  takes a context, claims pending tasks, checks or gives them up, and starts a delivery for
  each ready outcome: a background run of `run_stream`, retried when it fails. The loop never waits for a delivery;
  `tick()` does, for the tests. While no usable model is active it starts no delivery, and what
  is ready waits.
