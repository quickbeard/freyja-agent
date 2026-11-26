# Agents

The orchestrator can also hand work to agents that do their own reasoning, over the [A2A protocol](https://a2a-protocol.org). The service gives the orchestrator two tools for every agent, one to hand it a task and one to cancel a task, and reads each agent's card at startup. An agent is listed in `FREYJA_A2A_AGENTS`, or added while the service runs (see [Add an agent while the service runs](#add-an-agent-while-the-service-runs)). The full contract is in `specs/003-a2a-agent-delegation/contracts/agent-config.md`.

## Try it with the reference hello agent

The A2A project's own sample `helloworld` agent runs locally and needs no account or key. Fetch its two files at the pinned commit and run it; it listens on `127.0.0.1:9999`:

```bash
mkdir -p /tmp/freyja-hello && cd /tmp/freyja-hello
base=https://raw.githubusercontent.com/a2aproject/a2a-samples/1bb0871294/samples/python/agents/helloworld
curl -sSfO "$base/__main__.py" && curl -sSfO "$base/agent_executor.py"
uv run --no-project --with 'a2a-sdk[http-server]==1.1.0' --with uvicorn python __main__.py
```

In another terminal, add one line to `.env` and start the service:

```bash
FREYJA_A2A_AGENTS='{"hello": {"url": "http://127.0.0.1:9999", "read_only": true}}'
```

```bash
uv run --env-file .env freyja-api
# ... Agent hello: read-only, 1 skill
```

A hand-off to a read-only agent runs without a pause. The stream shows the agent's progress as `update` events with `node: "agent"`, then the tool result with its answer, and the reply relays it. An agent that streams gives one such event for each status update it streams. For an agent whose card says it does not stream, the hand-off reads the task's status about once a second and gives one event for each change it finds: another state, or the same state with a text other than the one it last saw. A blank text is no change, and neither is one that differs from the last only after its first 2,000 characters:

```bash
curl -N -X POST localhost:8000/threads/hello-1/runs -H 'content-type: application/json' \
  -d '{"message": "Ask the hello agent to say hello to the ops team."}'
```

Remove `"read_only": true` from the entry and restart the service. The same request now ends with an `interrupt` event for `handoff_to_hello` that shows the message Freyja wrote, and nothing reaches the agent until you approve it as described under **Decide on a proposal** in [Use the API](api.md). Then the agent's answer reaches the reply.

Remove the line from `.env` to go back to a service with no agents.

## Describe an agent

`FREYJA_A2A_AGENTS` is one JSON object on one line, in single quotes, mapping an agent name (lowercase letters, digits, hyphen, underscore; starting with a letter; at most 32 characters) to an entry with two fields:

- `url`: where the agent serves its card, `http` or `https`, on `localhost` or a loopback address; any other host is refused without a lookup. Every request goes to that host and port: from the card the service keeps only the path of the agent's JSON-RPC interface, and it follows no redirect.
- `read_only`: `true` sets the agent to Auto: every message to it runs without approval while the agents' approval mode is Auto (see [Auto mode](#auto-mode)). Without it the agent is Manual: every hand-off, answer, and cancel to that agent pauses for a decision, on the orchestrator and on its subagent. **Silence means gated**, and nothing the agent's card says about itself changes that.

```bash
FREYJA_A2A_AGENTS='{"hello": {"url": "http://127.0.0.1:9999", "read_only": true}, "planner": {"url": "http://localhost:9998"}}'
```

Agent names and tool server names share one namespace: an agent may not be named like a configured server.

## Hand-offs, answers, and cancels

Each agent contributes two tools:

- `handoff_to_<agent>` with `message` (1 to 32,000 characters), an optional `context` (up to 32,000), and an optional `task_id`. Only the message and the context reach the agent; nothing else from the thread does. Every hand-off from a thread to the same agent belongs to one conversation with it, which survives a restart; another thread has another conversation.
- `cancel_<agent>_task` with the `task_id` to cancel. It cancels only a task this thread started, and a gated cancel offers approve and reject only.

When the agent needs more input, the hand-off's result carries its question, the orchestrator asks you in its own words, and your answer goes back as a hand-off with that task's `task_id`. An answer is a message to the agent like any other: to a gated agent it pauses for a decision. An edit may change the message and the context, never the `task_id`, so an answer or a cancel stays bound to its task.

The agent's answer comes back as the tool result, in the orchestrator's voice: text and structured data as text, and a note in place of each file or link. A hand-off waits at most `FREYJA_A2A_CALL_TIMEOUT_SECONDS`.

## Pending tasks

A task the agent has acknowledged and is still working on at the call limit is **pending**: the hand-off's result says so, and the run ends normally. The service checks pending tasks every `FREYJA_A2A_POLL_INTERVAL_SECONDS`, each by exactly one service instance, also across restarts; a check only reads the task's status and never sends the agent new work. A task that waits for your answer is checked too, so one the agent ends meanwhile is reported instead of waiting until it is given up.

The thread state lists every agent task the thread has started, pending or ended, under `agent_tasks`, and never drops one:

```bash
curl -s localhost:8000/threads/hello-1/state | jq .agent_tasks
```

Each entry has the `agent`, the `task_id`, the `message` it was started with, its `status` (`working`, `input_required`, `completed`, `failed`, `rejected`, `canceled`, or `given_up`), `started_at`, `last_checked_at`, `ended_at`, `outcome_delivered`, and `progress`: what the agent last reported about its work, as an object with its `text` and `learned_at`, or null when it has reported nothing. The text is what the agent attached to a status that says it is working, in its own words and cut to 2,000 characters; it may hold anything an agent writes, so a client shows it as plain text. `learned_at` is when the service first learned that text, on the database's clock. It stays while the agent repeats the same text, so it says since when the agent has reported nothing new, while `last_checked_at` says when the task was last checked. Progress is learned from the status reads the service already makes, while a hand-off waits for the task and at each check of a pending task, with no request of its own to the agent, and it is kept with the task once the task has ended. The orchestrator is told on every model call which of the thread's tasks are pending, and its own read-only tool `check_agent_tasks` lists them on demand; the tool exists only when at least one agent is configured. For a task an agent is working on, the check's result also gives the progress the agent last reported and when it was learned, in the agent's own words, set apart under the task with each line indented. It is read from the service's database, so asking how a task is going sends the agent nothing.

When a pending task finishes, fails, needs input, or is canceled by the agent, its outcome is recorded in the thread exactly once, as Freyja's `check_agent_tasks` call followed by its result. Your next message gets it at the start of its run, ahead of what you wrote. If no message comes first, a **background run** delivers it as soon as the thread is free: no run in progress and no pending proposal. A background run is a run like any other, with the same gate, step limit, and run lease, so a proposal in it pauses it and you decide it through resume. Background runs do not hold up the checks of other pending tasks, and one service instance runs at most `FREYJA_BACKGROUND_RUN_CONCURRENCY` of them at once; further ones wait their turn. No client is told that a background run happened: read the thread state to see its reply or its pause. A background run that fails with a model or internal error is tried again after `FREYJA_BACKGROUND_RETRY_SECONDS`, up to `FREYJA_BACKGROUND_RUN_ATTEMPTS` attempts in all, without recording the outcome or sending any message twice.

A task still pending after `FREYJA_A2A_TASK_MAX_AGE_SECONDS`, counted from its start, is **given up**: the service stops watching it and reports that the agent may still be working on it. It sends no cancel by itself: ask for one, and the cancel goes through the gate like any message to that agent. A pending task whose agent was removed from the configuration is given up after the restart that applies the change. A saved agent's pending tasks are given up at once when the agent is moved or removed (see [Move or remove an agent](#move-or-remove-an-agent)).

## Add an agent while the service runs

An agent can be added without editing a file or restarting anything, from the Agents section of the [web console](web-console.md) or with a request:

```bash
uv run python -m tests.a2a_stub --http 9998      # the stand-in agent, in another terminal
curl -s -X POST localhost:8000/agents -H 'content-type: application/json' \
  -d '{"name": "stub", "url": "http://127.0.0.1:9998"}'
```

The service reads the agent's card at the address first, within `FREYJA_A2A_STARTUP_TIMEOUT_SECONDS` and at most twenty seconds, and sends the agent nothing else: no message and no task. An agent whose card can be used is saved in the database and answered with 201. The next run on every instance sharing the database has its two tools, and a run in progress finishes with the tools it started with. The agent is still there after a restart.

Before it is used, know that:

- **An added agent requires approval unless the request says otherwise.** Every hand-off, answer, and cancel to it then pauses for a person's decision. `"approval": "none"` adds an agent that runs without approval: nothing sent to it is seen by a person first. The console asks for confirmation before it sends that; a request sent straight to the service is not asked.
- **A saved agent's name never changes.** Its approval choice and its address can be changed, and it can be removed, as the next sections say.
- **Every instance has to run this version before the first agent is saved.** An instance on an earlier version knows no saved agent and gives up the tasks of an agent it does not know.

A name is in the form the environment's agents use, and an address is `http` or `https` on this machine (`localhost` or a loopback IP address), with no user name, password, query, or fragment. It is kept in one form, such as `http://127.0.0.1:9998`. A request is refused, and nothing is saved, with:

- 422 and the field's error when a value breaks a rule, never repeating the value;
- 409 `name_taken` when another agent or a tool server has the name, or when another tool has the name of one of the agent's two tools, and `address_taken` when another agent is at the address, however this machine is written, also an agent named in the environment whose address carries a user name, a query, or a fragment;
- 409 `agent_limit` when twenty agents are saved, and `check_busy` while four card reads are under way on the instance;
- 409 `agent_unusable` when the card cannot be used, with `reason` and the service's own sentence: nothing answered, no answer in time, no card, a card that cannot be read, a redirect, a card larger than one megabyte, or a card that requires authentication, accepts no text, or offers no JSON-RPC interface;
- 403 `other_origin` when a browser marks the request as made by a page of another origin.

Every route of the Agents section answers 500 `internal` when the saved agents cannot be read or the change cannot be written; nothing is then changed, and the reason names no address.

`GET /agents` lists every agent, with the number of its pending tasks. It and the answers to an add and to a change are the only answers of the service that hold a saved agent's address, in `url`. The address is in no other response, no stream, no thread, and no log line, also where a card names it: its host and port are taken out after any scheme and however this machine is written, and its path with them. An agent's own words are the exception: its replies, its progress updates, and the progress kept with its task (`progress` in the thread's state) are kept and returned as the agent wrote them, an address included, though the console's runs take one out of every event they send.

A message is only ever sent to the agent it was proposed for. The service keeps each call of an agent's tool that a model proposes in `freyja_agent_calls`, from the model's reply until the message was sent, and records there when a person approved or edited it in a resume. A tool that waits for a person sends a call only when that record says a person decided it. A proposal that waits for a decision while its agent leaves the configuration, and another agent is added or configured under the same name, is not sent to that other agent whatever is decided: the call's result says that the agent the message was proposed for was removed.

## Auto mode

Every agent has its own mode, **Manual** or **Auto**: a saved agent's `approval` (`required` is Manual, `none` is Auto) and an environment entry's `read_only` (`true` is Auto). Beside them the instance has one switch, the **agents' approval mode**, also Manual or Auto, shown as the **Approvals** dropdown beside the Send buttons of the [web console](web-console.md)'s Tasks section. A message to an agent, whether a hand-off, a follow-up, an answer, or a cancel, runs without approval only when the switch is Auto and the agent is Auto; otherwise it pauses for a decision. That holds on every thread, a task another agent started over A2A included (see [Receive tasks from agents](a2a-server.md)), on the orchestrator and its subagent, and in the runs the service starts by itself. A tool server's tool and `send_notification` are not affected.

The switch is kept in the database, beside the saved agents, and is the same for every instance. Where nobody set it, it is Auto, so a deployment behaves as it did before the switch existed: only agents someone set to Auto run without approval. `GET /agents` answers with it as `mode`, and it is set with:

```bash
curl -s -X PUT localhost:8000/agents/mode -H 'content-type: application/json' \
  -d '{"mode": "manual"}'                       # or "auto"
```

The answer is the mode as kept. No agent is contacted. Setting the mode the switch already has changes nothing. The two directions take effect as an agent's own choice does (next section): **Manual holds at once**, on every instance, also for a run in progress, whose message to an Auto agent that no person decided gets the error result "Agent '<name>' now requires approval, so this message was not sent."; a paused step that holds a message proposed while the switch was Auto ends its resume with `approval_now_required` until the switch is set back. **Auto holds from the next run**, and a proposal for an Auto agent that already waits for a decision can then only be approved. A request is refused with 403 `other_origin` when a browser marks it as made by a page of another origin, and with 422 for a body with any field but `mode`, or a mode that is neither `manual` nor `auto`. Each change is logged as `Agents' approval mode changed: <mode>, was <mode>`.

## Change whether an agent needs approval

A saved agent's approval choice, its own mode, is changed from its card in the Agents section of the [web console](web-console.md), with its Manual/Auto switch or **Edit**, behind a confirmation, or with:

```bash
curl -s -X PATCH localhost:8000/agents/<id> -H 'content-type: application/json' \
  -d '{"version": 1, "approval": "none"}'       # or "required"
```

`version` is the one `GET /agents` shows for the agent. The answer is the agent as it is now, with `version` one higher, and `given_up`, which is 0 for this change. The agent is not contacted, so the change does not depend on whether it answers, and its pending tasks stay as they are. A request that names the choice the agent already has changes nothing.

The two directions do not take effect the same way:

- **Requiring approval holds at once**, on every instance, also for a run in progress and for a failed step that is tried again. Before a tool of a saved agent sends anything, it reads the agent from the database. A message that no person decided, to an agent that requires approval by then, is not sent; a person decided it only when a resume carried their approve or edit for it: the call gets an error result, "Agent '<name>' now requires approval, so this message was not sent.", and the run continues. A message whose sending had begun is not recalled.
- **Running without approval holds from the next run.** A run in progress keeps pausing until it ends. A proposal for the agent that is already waiting for a decision can then only be approved, and is sent as proposed.
- **A paused step can become impossible to continue.** When a step waits for a decision and also holds a message to an agent that needed no approval when it was proposed and requires it now, the resume ends with an `error` event of code `approval_now_required` that names the agent. Nothing from the step ran, and the proposal stays. Switch the agent back, then decide again.

A change is refused, and nothing changes, with 409 `agent_changed` when the agent is no longer at `version`, 409 `agent_read_only` for an agent named in the environment and for a saved agent that is not in use, 404 `agent_not_found`, 422 for an id in neither form and for a body with any field but `version`, `approval`, and `url` (`name` among them), and 403 `other_origin`.

Anyone who can reach the service's port can make an agent run without approval with this request; see [what such a client can do](#saved-agents-beside-the-environments).

## Move or remove an agent

A saved agent is moved to another address, or removed, from its card in the Agents section of the [web console](web-console.md), each behind a confirmation, or with:

```bash
curl -s -X PATCH localhost:8000/agents/<id> -H 'content-type: application/json' \
  -d '{"version": 2, "url": "http://127.0.0.1:9996"}'      # move; "approval" may be sent with it
curl -s -X DELETE 'localhost:8000/agents/<id>?version=3'    # remove
```

Both name the `version` that `GET /agents` shows, and both answer with `given_up`: how many of the agent's pending tasks were given up.

- **A move reads the card at the new address first**, within `FREYJA_A2A_STARTUP_TIMEOUT_SECONDS` and at most twenty seconds, and sends the agent nothing else. An agent that cannot be used there is refused with 409 `agent_unusable` and the reason, and the earlier address stays saved and in use. An address that comes to the saved form is no move: another case, a trailing slash, or a default port written out. The same place under another host name is one, such as `localhost` for an agent saved at `127.0.0.1`.
- **A move applies from the next run.** A run in progress finishes at the address it started with. A proposal for the agent that is waiting for a decision goes to the new address once approved.
- **A removal holds at once**, on every instance, also for a run in progress: a tool of a saved agent reads the agent from the database before it sends, and the result is "Agent '<name>' was removed; nothing was sent." The next run has neither of the agent's tools. An agent added again under the name is another agent, and a proposal made for the removed one is never sent to it.
- **Both give up the agent's pending tasks**, together with the change. Each task is listed in its thread as given up at once, a background run tells the thread once, and no cancel is sent: the agent may still be working. After a move the report says the agent was moved and may still be working where it was.
- **A task stays with the agent it was given to.** It is never checked, answered, or canceled at the agent's new address, nor at another agent that has its name: such a call's result is "Agent '<name>' task <id> was given up when the agent was moved or removed; nothing was sent."
- **A run does not send to an address its agent has left once another agent is there.** The result is "Agent '<name>': the address this run started with is another agent's now; nothing was sent."

A move is refused, and nothing changes, with the refusals of a change above, and with 409 `address_taken`, 409 `check_busy`, 409 `agent_unusable`, and 422 for an address that breaks a rule, as for an add. A removal is refused with 409 `agent_changed`, 404 `agent_not_found`, 409 `agent_read_only` for an agent named in the environment, 422 for an id in neither form or a missing `version`, and 403 `other_origin`. A saved agent that is not in use can be removed, and nothing else can be done with it.

## Saved agents beside the environment's

An instance has two kinds of agent, and `GET /agents` lists both: the ones named in `FREYJA_A2A_AGENTS`, first and in their order, then the saved ones by name.

- **An agent named in the environment is read-only.** It can be checked, and nothing else: not changed, not removed. Its `id` is `env.<name>`, and its address is shown nowhere, the list included.
- **A saved agent** has an `id`, its `url`, a `version` that every change of its address or approval choice raises, and `pending_tasks`. At most twenty are saved.
- **Each agent has a `state`**, as this instance last found it: `reachable`, `unreachable` with the `reason`, `not_tried`, or `not_in_use`.
- **The environment comes first.** A saved agent cannot take the name or the address of an agent named in the environment, nor the name of a tool server, and neither of its two tool names may be another tool's. When a restart gives the environment one of those, the environment's is the one in use: the saved agent is listed as `not_in_use` with the reason, its pending tasks are given up, a proposal that waited for it is never sent to the environment's agent, and it can only be removed. When a later restart takes the conflict away, it is in use again as it was saved.
- **A change reaches every instance sharing the database** at its next run, its next reading of the list or the catalog, and its next round of watching. A run in progress keeps the tools it started with.

**What anyone who can reach the service's port can now do.** The service has no authentication. Beside starting runs and deciding proposals, such a client can add an agent, on this machine only, since an address must be `localhost` or a loopback address; switch a saved agent to run without approval, and set the agents' approval mode to Auto, so that no person sees what is sent to an Auto agent; move a saved agent to another address on this machine; remove one; and have any agent's card read again. It can read every saved agent's address. A web page of another origin can do none of this through a browser: the Agents routes refuse it, and give it no permission to read an answer. Keep the service bound to an address only its operators reach.

## Check an agent

Whether an agent can be used is checked on request, from its card in the Agents section of the [web console](web-console.md) or with:

```bash
curl -s -X POST localhost:8000/agents/env.hello/check        # an agent named in the environment
curl -s -X POST localhost:8000/agents/<id>/check             # a saved agent, by its id from GET /agents
```

A check reads the agent's card again, within `FREYJA_A2A_STARTUP_TIMEOUT_SECONDS` and at most twenty seconds, and sends the agent nothing else. The answer says whether the agent can be used (`usable`), and when it cannot, which check failed (`reason`) and the service's own sentence for it (`message`), the same nine as for an add. It changes no address, no approval choice, no thread, and no task.

- An agent that can be used is listed as reachable from then on, and a hand-off reaches it.
- An agent that cannot be used is listed as not reachable with the reason. It keeps both of its tools, and a hand-off to it becomes an error result with that reason until a later read of its card succeeds, at the next check or the next hand-off.
- `card_changed` is true when the card differs from the details kept or shown before. From the next run the agent's tools are described from the new details: on every instance for a saved agent, whose details are kept in the database, and on the answering instance for an agent named in the environment.
- `recorded` is false when a saved agent was moved or removed while the check ran. Nothing was kept then.

A check is refused, and the agent is not contacted, with 409 `check_busy` while the same agent is being checked or four card reads are under way on the instance, 404 `agent_not_found` for an id no agent has, 422 for an id in neither form, 409 `agent_read_only` for a saved agent that is not in use, and 403 `other_origin` for a request a browser marks as made by a page of another origin.

## When an agent fails

At startup, an entry that is invalid in itself stops the service with an error that names the agent, and nothing listens: an address not on this machine, a name shared with a tool server, or a tool name already taken.

An agent whose card cannot be used does not stop startup: a card that cannot be read within `FREYJA_A2A_STARTUP_TIMEOUT_SECONDS` or is redirected, or one that requires authentication, accepts no text, or offers no JSON-RPC interface. The service starts, logs a warning that names the agent and the reason, and lists the agent as not reachable with that reason. The agent keeps both of its tools, and a hand-off to it becomes an error result naming it. Nothing is sent to an agent the service has read no usable card from. All agents are tried at once, so any number of agents that are down delay startup by one time limit. An agent that answers later is used without a restart: the next hand-off to it reads its card first, inside the hand-off's own time limit, and while it has a pending task the service asks for its card once in each round of watching.

During a run, a hand-off to an agent that cannot be reached, does not acknowledge within the call limit, fails or rejects the task, asks for authentication, or replies with something malformed becomes an error result naming the agent; the run goes on, and a warning names the agent. A reply is malformed when a task it names has an empty id or conversation id, or when any id it gives is longer than 256 characters or holds a control character or a line break; nothing from it is kept, its id included. The agent chooses its task's id, and the id is written into what the orchestrator reads line by line, so a line break in one could start a line of the agent's choosing; for the same reason the check of agent tasks writes the start of a task's message on one line, each line break shown as `⏎`. An unacknowledged hand-off's result says the agent may or may not have received it. Nothing is ever sent twice: an approval authorizes one attempt. If the service stops while a message to an agent is under way, the next run on that thread records that the agent may or may not have received it, and the message is not sent again. An agent that answers again is usable without a restart. No error result, log line, thread message, or catalog entry contains an agent's address.

## Agents in the catalog

`GET /tools` lists each agent in use under `agents`, so not a saved agent the environment overrides: its `name`, `reachable`, `checked_at`, and `error` from the most recent attempt to use it (at startup, in a hand-off or cancel, while checking a task, in a check asked for, or when it was added or moved), and its `card`: the card's name, description, version, and skills. The card is the agent's own claim and never changes the classification. Each agent's two tools are in `tools` with `agent` set to its name; every other tool has `agent: null`. The catalog never contacts an agent and never shows its address. An agent the service has not tried yet is listed as not reachable, with a reason that says so; one whose card it has not read has an empty `card`, and its hand-off tool is described by its name alone until the card is read. Before it answers, the catalog reads two rows of the database, to learn whether the agents or the active LLM profile changed; when a read fails it answers from what it last built. With no agents configured, `agents` is empty and the tools are as before.

A proposal for an agent's tool names its agent: each entry of `action_requests` has `agent` set, and `null` for every other tool.

**Next:** [Receive tasks from agents](a2a-server.md): other agents handing work to Freyja.
