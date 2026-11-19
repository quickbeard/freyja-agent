# Data Model: Agents Section of the Web Console

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Research**: [research.md](research.md)

What the service keeps and what it holds in memory for delegate agents after this feature.
Tables are created at startup with `CREATE TABLE IF NOT EXISTS`, like the run lease, the agent
tables of feature 003, and the profile tables of feature 004. One existing table gains a
column, added at startup only when it is missing (R2).

## Entities

### Saved agent (`SavedAgentRecord`, table `freyja_agents`)

One agent an operator added through the console (FR-005).

| Field | Type | Rules |
|---|---|---|
| `id` | text, primary key | 32 hex characters, new for every add. An agent removed and added again under the same name has another id (FR-012). |
| `name` | text, unique | `^[a-z][a-z0-9_-]{0,31}$`, the form environment agents use. Never changes (FR-009). |
| `url` | text | The address in its one kept form (R11): lowercase scheme and host, port always written, no trailing slash; no user name, password, query, or fragment; no empty, `.`, or `..` path segment, no `%`, no backslash; at most 2,000 characters; host `localhost` or a loopback IP address. |
| `destination` | text, unique | Where the address leads, however this machine is written: the port and the path (R11). Two saved agents cannot share it (FR-006). |
| `read_only` | boolean, default false | The approval choice. False: messages require approval. True: they run without approval. Named as the environment's entry names it. |
| `address_version` | integer, default 1 | Counts the addresses this agent has had; a move adds one. With `id` it makes the agent ref (below). |
| `version` | integer, default 1 | Counts the operator's changes: a change of address or of the approval choice adds one. A change or removal names the version its page read and is refused when it is no longer current (FR-012, R14). |
| `card` | jsonb | The card details as kept (below). Set when the agent is added; replaced only by a move or by a check whose card can be used (FR-015). |
| `card_read_at` | timestamptz | When `card` was read, on the database's clock. |
| `created_at`, `updated_at` | timestamptz | The database's clock. |

At most 20 rows (FR-006). The limit, the unique name, and the unique destination are enforced
in the database, under one advisory lock for the count, so two adds at the same moment on two
instances cannot both pass (R1).

### Agents revision (table `freyja_agents_state`)

One row: `id = 1`, `revision bigint`, `updated_at`. The revision moves, in the same transaction,
whenever what a run would be built with changes: an agent added, moved, removed, its approval
choice changed, or its card details replaced by a check. It is how an instance knows the set it
built for is no longer current: one single-row read (R3).

### Environment agent (`A2aAgentConfig`, never stored)

Unchanged from feature 003: an entry of `FREYJA_A2A_AGENTS`, read once at startup. It has no
id of its own in the database. In the API it is addressed as `env.<name>`; a dot can be in
neither a saved agent's id nor a name. Its address is never returned (FR-017).

### Card details (`AgentCardSummary`)

What is kept, shown, and used to describe an agent's tools: `name`, `description`, `version`,
and `skills` (`id`, `name`, `description`, `tags`), as today, plus `cut`, which the Agents
routes return and the catalog does not: the catalog keeps its shape.

- Each text is cut to its first 2,000 characters, `skills` to the first 50, and a skill's
  `tags` to the first 50; `cut` is true when anything was cut (FR-002).
- Every text has every agent address the process knows removed, in the forms of R12, before
  it is kept or returned (FR-028).
- For a saved agent the details are the row's `card`: the same on every instance, whether or
  not the agent answers. For an environment agent they are what this instance last read, or
  none (FR-015, FR-021).

### Agent ref

A string that says which agent, at which address, something belongs to: `<id>:<address_version>`
for a saved agent, and null for an environment agent, whose tasks and proposals follow its name
as today. A task carries the ref of the agent it was started with (FR-025). A null ref is
never a saved agent's.

### Agent link (`AgentLink`, in memory)

The running link to one agent in use on one instance. New or changed fields:

| Field | Meaning |
|---|---|
| `source` | `saved` or `environment`. |
| `agent_id`, `ref` | The saved agent's id and its agent ref; None for an environment agent. |
| `card` | The card details the tools are described from (above). |
| `connected` | True while this instance holds a usable card from the link's address and a client built from it. A card read that fails makes it false again. A link that is not connected sends nothing (FR-021). |
| `tried` | False until this instance has tried the agent: the state `not_tried`. |
| `reachable`, `checked_at`, `error` | The outcome of the most recent attempt to use the agent, as today (FR-003). |

A link belongs to one address and does not carry the approval choice: the set does, and a tool
fixes at its build whether it needed a decision (R7). A move makes a new link, with a new HTTP
client, so nothing an agent at the earlier address gave the service is sent to the later one
(FR-025).

### Agent set (`AgentSet`, in memory)

What one instance builds its orchestrator's agent tools from: the revision it was read at, the
links in use with each agent's approval choice, environment agents first and then saved agents
by name, and the saved agents that are not in use with the reason for each (FR-017).

### Run context (`RunContext`, in memory)

One orchestrator for one piece of work: the graph, the catalog, the active LLM profile, and
the agent set, held until the context is released. A run takes one under its thread's lease
and uses it from the first read of the thread's state to its last event. The worker, the
thread state route, the catalog, the agents list, and a check take one too. A set nothing
holds any more closes the links only it had (R3).

### Agent state, as the list reports it

| State | When |
|---|---|
| `reachable` | The most recent attempt by this instance succeeded. |
| `unreachable` | It failed; `reason` says why. |
| `not_tried` | This instance has not tried the agent yet. |
| `not_in_use` | A saved agent the environment overrides (FR-017); `reason` says which rule. |

### Card problem (`CardProblem`)

Why an agent cannot be used, as a code and one fixed sentence the service words itself
(FR-016). Nothing from the agent or from a library is in the sentence.

| Code | Sentence |
|---|---|
| `no_answer` | Nothing answered at the address. |
| `timeout` | The agent did not answer within N seconds. |
| `no_card` | Something answered at the address, but it served no agent card. |
| `unreadable` | The agent's card could not be read. |
| `redirect` | The address answered with a redirect, which is not followed. |
| `too_large` | The agent's card is larger than one megabyte. |
| `auth_required` | The agent's card requires authentication, which is out of scope. |
| `no_text` | The agent's card does not accept text messages. |
| `no_interface` | The agent's card offers no JSON-RPC interface. |

### Agent task (`AgentTaskRecord`, table `freyja_agent_tasks`), one new column

| Field | Type | Rules |
|---|---|---|
| `agent_ref` | text, null | The agent ref of the agent the task was started with. Null for an environment agent's task and for every row written before this feature. |

A pending task whose `agent_ref` is not the ref of the link in use under its name is given
up: its agent was moved, removed, removed and added again, or is not in use, or the name is
now another kind of agent's (FR-024, FR-025, FR-017). Between an environment agent's task and
an environment agent's link both are null, and the task follows the name, as today.

### Proposed call (table `freyja_agent_calls`)

One row per call of an agent's tool, saved or from the environment, that a model has proposed
(R8).

| Field | Type | Rules |
|---|---|---|
| `thread_id`, `namespace`, `tool_call_id` | text, primary key together | The call, as the tool will know it when it runs. `namespace` is the graph namespace the model's node and the tool's node share: empty on the orchestrator, the subagent's own inside a subagent. |
| `agent` | text | The agent's name. |
| `agent_id` | text, null | The saved agent's id when the call was proposed; null for an environment agent. |
| `gated` | boolean | Whether the call needed a decision when it was proposed. |
| `proposed_at` | timestamptz | The database's clock. |

Written when the model's reply holds the call, on the orchestrator and on its subagent: the
rows of the thread and namespace are replaced by the reply's. A row is deleted when its call
has finished its send. A run that starts with input deletes whatever rows its thread still
has. A row therefore lasts from the reply that proposed its call until the call was sent, the
next reply in the same place, or the thread's next run with input, whichever comes first.

## State transitions

### A saved agent

```text
(none) --add: card read and usable--> saved, version 1, address_version 1
saved --change of approval choice--> saved, version + 1                       (no contact)
saved --move: card at the new address read and usable--> saved, version + 1,
        address_version + 1, card replaced, its pending tasks given up
saved --check: card usable--> saved, card replaced                            (version unchanged)
saved --remove--> (none), its pending tasks given up
```

Every arrow moves the agents revision, except a check that finds the card details unchanged or
finds the card unusable. A refused add or move changes nothing (SC-007). A move, a removal, and
the giving up that goes with each are one transaction (FR-024).

### An agent link on one instance

```text
not tried --card read, usable--> connected, reachable
not tried --card read fails or card unusable--> not connected, unreachable (reason)
not connected --a hand-off, a round of watching, or a check reads a usable card--> connected
connected --a card read fails or finds the card unusable--> not connected, unreachable (reason)
connected --a call fails--> connected, unreachable (reason)                    (as today)
connected --a call succeeds--> connected, reachable                            (as today)
```

A link starts as not tried when an instance learns of a saved agent from another instance, and
at startup until the card reads end. Nothing is ever sent over a link that is not connected.

### A pending task

```text
working | input_required --its agent is removed, moved, or not in use--> given_up
                           (outcome ready; a background run tells the thread)
```

Beside the transitions of feature 003, which are unchanged. A task given up this way can be
neither answered nor canceled (FR-025): the call gives an error result and sends nothing. A
task given up because it reached the maximum age can still be canceled, as today.

### A proposed call

```text
(none) --the model proposes it--> waiting
waiting --its send is finished--> (none)
waiting --the model answers again in the same namespace--> (none)
waiting --a run with input starts on the thread--> (none)
```

## What a call gives without sending

Each is an error result of the tool; the run continues, and nothing reaches an agent.

| When | Result names |
|---|---|
| The call's row names another agent than the link's, an environment agent included, or a saved agent's tool finds no row for the call: the agent it was proposed for was removed, and another was added under its name (FR-023) | the agent, and that the agent the message was proposed for was removed |
| The saved agent the tool was built for is gone (FR-022) | the agent, and that it was removed |
| The tool needed no decision when its run was built, and the agent now requires approval (FR-022) | the agent, and that it now requires approval |
| The tool's link is of an earlier address, and another agent now has that address (R7) | the agent, and that the address the run started with is another agent's now |
| An answer or a cancel for a task whose `agent_ref` is not the link's (FR-025) | the agent and the task, and that the task was given up when the agent was moved or removed |
| The link is not connected and no usable card can be read within the call's time limit (FR-021) | the agent, and the card problem's sentence |

## API shapes

Defined in [contracts/openapi.yaml](contracts/openapi.yaml): `Agent`, `AgentList`,
`AgentCreate`, `AgentUpdate`, `AgentChanged`, `AgentRemoved`, and `AgentCheck`. `Agent.url` is
null for an environment agent. `Agent.version` is what a change or a removal sends back.
`Agent.pending_tasks` is the number the confirmations show. No shape carries an environment
agent's address, and no shape outside these routes carries any address.
