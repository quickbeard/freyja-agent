# Receive tasks from agents

Freyja hands work to other agents over A2A (see [Agents](agents.md)); with `FREYJA_A2A_SERVER` on, another agent can hand work to Freyja the same way. The caller's message starts the run the console starts, on a thread of the service's own, so every action with an effect outside the workspace waits for a person in the console, as any other does. What the caller is told is read from that thread. The full contract is in `specs/009-a2a-server/contracts/a2a-server.md`.

Callers on this machine call the service's own address and need no credential there. Agents on other hosts are named by the deployment, each with a token, and call an outside listener that serves A2A and nothing else (see [Named callers](#named-callers) and [The outside listener](#the-outside-listener)).

## Turn it on

Add one line to `.env` and start the service:

```bash
FREYJA_A2A_SERVER=true
```

The log says so at startup:

```text
INFO:     freyja_agent.api.app: Receiving tasks from other agents over A2A is on: ...
```

With it off, which is the default, neither address below exists: each is answered as a path the service does not have.

## The card and the address

| | Path |
|---|---|
| The card | `GET /.well-known/agent-card.json` |
| The operations | `POST /a2a`, A2A 1.0 over JSON-RPC; every request carries `A2A-Version: 1.0` |

The card names Freyja, gives the address it was asked under followed by `/a2a`, says that streaming is offered and push notifications are not, takes and gives `text/plain`, and declares no security. It names no agent, tool server, tool, model, profile, or caller, and is the same for every caller. It is given without a token.

The address is held to the same rules as the rest of the API: a page of another origin is refused with `403` `other_origin`, and a host name the service does not answer for with `403` `unknown_host`. A request larger than 256 KiB is refused with `413` before its body is read whole. At this address A2A is exactly as reachable as the thread routes, the console's forwarding included: whoever can open the console can call Freyja as an agent on this machine.

At this address a request with no `Authorization` header is the local caller's, every agent that calls without a token being one caller, `local`. A request with a named caller's token, `Authorization: Bearer <token>`, is that caller's. One with any other `Authorization` header, another scheme or a token no caller has, is refused with `401`, `WWW-Authenticate: Bearer`, and `{"detail": {"code": "unauthorized", ...}}`, before its body is read.

## Named callers

A deployment names the agents that may call it with a token of their own, in `FREYJA_A2A_SERVER_CALLERS`:

```text
FREYJA_A2A_SERVER_CALLERS='{"artex": {"token": "<16 to 4096 visible ASCII characters>"}}'
```

A name is lowercase letters, digits, hyphens, and underscores, starting with a letter, at most 32 characters, and not `local`; a token is 16 to 4,096 visible ASCII characters, no space and no control character, and each caller's is its own; at most 20 callers. An entry that breaks a rule stops startup with an error that names the setting, and the caller by its name or its place, never a token; a value that is no JSON object of that shape, or that names a caller twice, is refused naming the setting only, since a name may be a token put in the wrong place. A caller may share a name with a delegate agent: the two are unrelated.

A caller sends its token as `Authorization: Bearer <token>`, and only there: a token in the body, a query, or another header is not a credential. The token is compared with every caller's in a time that says nothing of how much of it was right. Who the caller is comes from the token alone; the protocol's `tenant` field is ignored. A caller's tasks and conversations are its own: an operation on a task id that is another caller's, or a console thread's, is answered exactly as one on an id never used, and a conversation id another caller uses starts a conversation of the sender's.

A token appears in no answer, stream, thread, or log line. A caller that pastes its token into a message has it taken out before the message is used, and every log record of the process, the web server's access log included, is cleaned of every named caller's token. Refused credentials are logged as a warning once, and then at most once in ten seconds for each of the two addresses, with how many were refused since. A token is cleaned however a path, a query, or a log line writes it: percent-encoded in any of the usual ways, or escaped.

**A named caller's files.** A named caller's runs, Freyja's and its subagent's, work in a directory of their own, `<FREYJA_A2A_SERVER_FILES_DIR>/<name>` (`caller-files` by default, beside the workspace and never inside it): they list, read, write, and delete there and nowhere else, read the skills from the workspace, and cannot change them. The directory is shared by the caller's conversations and kept across restarts. The local caller's runs work in the workspace, as every thread of the service's own API does.

**Removing or renaming a caller.** A caller the settings no longer name is refused from then on, and when the service starts its working tasks are canceled as the caller's own cancel would (below), with a warning that names the caller; the console says the caller is no longer configured. Named again under the same name, with the same token or a new one, it has its conversations and its files again; an old token is refused. A new name is a new caller with nothing of the old one's, so do not give a removed caller's name to another party. Every instance that shares the database is given the same callers: one that lacks a caller cancels that caller's tasks.

## The outside listener

Agents on other hosts call an address and a port of the deployment's own, which serves the card and the operations and nothing else: every other route of the service, `/healthz` and the console's forwarding included, answers there as a path the service does not have. It is off unless both are set:

```text
FREYJA_A2A_SERVER_HOST=10.0.0.5
FREYJA_A2A_SERVER_PORT=8443
FREYJA_A2A_SERVER_URL=https://freyja.example.com
```

It needs the feature on, a named caller, and `FREYJA_A2A_SERVER_URL`, the address callers reach it under (`http` or `https`, a host, and at most a port), which its card gives followed by `/a2a`, with one HTTP `bearer` security scheme that it requires. Every operation there needs a named caller's token; without one, with another scheme, or with a token no caller has, it is refused with the same `401` as above, before its body is read, and nothing is made. The card is given without a token. A page of another origin is refused there as at the service's own address; the host names of `FREYJA_ALLOWED_HOSTS` are not checked there. The port must not be the service's own on the same address, and one that cannot be bound stops startup with an error that names the setting.

The listener starts and stops with the service, in the same process, however the service is started; an open stream there is closed when the service stops. It writes no access log line, and leaves the service's own as it is. Startup logs that it is on and where, and a page of another origin refused there is warned of sparingly, as a refused credential is.

**The service does not encrypt the connection.** A deployment that opens the listener to other hosts puts what encrypts in front of it, such as a reverse proxy with TLS, and gives that address as `FREYJA_A2A_SERVER_URL`. With an `http` address the service starts and warns that tokens sent there can be read on the network path.

## Try it with the stand-in caller

The suite's stand-in caller is the protocol's own client behind a small command:

```bash
caller() { uv run python -m tests.a2a_caller --url http://localhost:8000 "$@"; }
caller card
caller stream "List the files in your workspace and say how many there are."
caller send "Say hello."
caller get <task id>
caller follow <task id>
caller cancel <task id>
```

Each task or event is printed as one JSON object, and a refusal as the JSON-RPC error it was. `--context <id>` continues a conversation, `--return-immediately` asks a send not to wait, `--hops <n>` sends the message with a hand-over count, and `--token <token>` calls as a named caller; give the outside listener's address as `--url` to call it there.

## What a caller can do

| Operation | |
|---|---|
| `SendMessage` | Starts a task and answers with it once it has ended, or once `FREYJA_A2A_SERVER_WAIT_SECONDS` (30 by default) have passed, whichever comes first; at once with `configuration.returnImmediately`. A task answered as working goes on; read it with `GetTask`. |
| `SendStreamingMessage` | Starts a task and streams it: the task as working, a status for each step, the pieces of Freyja's reply as they are written, the whole reply, and the task's end. |
| `GetTask` | The caller's task as it stands, its reply once it has completed. No history is returned. |
| `SubscribeToTask` | Follows a task that has not ended: the task as it stands, then the same events as a send's stream, to the task's end. |
| `CancelTask` | Cancels a task that has not ended, and answers with it canceled (below). |
| Everything else | Refused: `ListTasks` with `-32004`, the push notification methods with `-32003`, `GetExtendedAgentCard` with `-32007`. |

A task is `TASK_STATE_WORKING` while a run is in progress, a proposal waits for a person, or a delegate agent's task is awaited; then `TASK_STATE_COMPLETED` with one artifact, `reply`, holding Freyja's reply; `TASK_STATE_FAILED` with a reason; or `TASK_STATE_CANCELED` when its caller canceled it or its run was stopped in the console. A working task's status says what it does, in the service's words only:

| Status | When |
|---|---|
| `Working.` | Accepted, or between steps. |
| `Freyja is using: <names>.` | A step of Freyja's asked for actions. |
| `Completed: <names>.` | The step that ran them completed. |
| `Waiting for a person's approval of: <names>.` | The run paused on a proposal. |
| `Waiting for agent <names>.` | The run ended and a delegate agent's task is awaited. |

Names are tool names, `agent <name>` for a message to a delegate agent, and `a subagent` for work Freyja gave its subagent. No argument, no result, and nothing a tool, a subagent, or an agent wrote is in a status. A failed or canceled task's reason is one of: `No usable model is active.`, `The model could not be used.`, `The run reached its step limit.`, `The run was interrupted.`, `An internal error.`, `The run was stopped before it finished.`, `The caller canceled the task.` A tool the catalog does not have is named `a tool Freyja does not have`. What is read from the thread, the reply above all, is cleaned before a caller is sent it of every agent address the service knows and of the keys of the active LLM profile and the environment's; a reply is kept cleaned once its task has completed. The stream sends only the text of Freyja's own reply, never a subagent's, a tool's, or a call's arguments, each piece up to its last space or line break, so that an address or a key written over several pieces is cleaned whole; the rest follows with the next piece, or in the whole reply at the end.

**Waiting for a person.** A caller's task that comes to an action needing approval waits for a person, and stays working for as long as that takes, days included: its status says `Waiting for a person's approval of: <names>.`, with the actions' names and none of their arguments. On a caller's thread a message to a delegate agent follows the rule of every thread (see [Auto mode](agents.md#auto-mode)): it waits for a person unless the agents' approval mode is Auto and the agent is set to Auto. With the mode on Manual, no agent receives anything from a caller's task until a person approves. An agent that hands work back to Freyja can make a loop that turns without a person while both are Auto; the [hand-over count](#the-hand-over-count) ends it when the other agent passes the count on, the limits on tasks in progress bound it when it does not, and setting the mode to Manual stops it at its next message. The task never asks its caller for a decision, and nothing a caller sends decides one: a message in the conversation is refused as `conversation_busy`, and no other operation touches the proposal.

A person decides in the console's Tasks section, where the proposal's card says that an agent started the run and that no person asked for the action, or through the routes the console uses (see [Use the API](api.md)), with every rule a decision has. Approved or edited, the action runs once, with the person's arguments; rejected, it does not run. The task goes on either way. A caller that follows it, by the stream of its send, receives `Working.`, the steps that follow, the pieces of Freyja's reply, and the task's end, when the decision was sent the console's way, through `POST /threads/{thread_id}/resume/unattended`, to the instance that streams to it. Otherwise, as for a decision sent through `POST /threads/{thread_id}/resume` or to another instance, it receives the changes of the task's state and status and the whole reply, from looks at the task when a run on this instance ends and every four seconds. The caller is not told who decided or why: only the steps that follow and what Freyja's reply says. A run a person stops in the console ends the task as canceled, with `The run was stopped before it finished.`

## Follow a task, and come back to it

A task is read from its thread, which every instance of the service shares, so `GetTask` and `SubscribeToTask` give the same task with the same id, state, and reply after a restart and through any instance. A subscription to a task that has ended is refused with `-32004`; one to an id the caller was never given with `-32001`, as for `GetTask`. At most four streams follow one task on one instance, a send's stream included; a fifth is refused with `-32004`, the sentence `This task has as many streams as it takes. Read it with GetTask.`, and `reason` `stream_limit` in the error's data. A stream that reads too slowly to keep up with its run is closed, and the run goes on: subscribe again.

The pieces of Freyja's reply are sent only by the instance that runs the task's run. Through another instance, or for a run the service starts by itself, such as the one that reports a delegate agent's outcome, a stream receives each change of the task's state and status and the whole reply at the end, from looks at the task when a run on that instance ends and every four seconds.

A caller's run that is in progress when the service stops is stopped between two steps, as every console run is, and its task ends as canceled with `The run was stopped before it finished.` Nothing of it runs by itself afterwards; the next message in the conversation continues with what the stopped run did.

## Cancel a task

`CancelTask` answers with the task canceled, `The caller canceled the task.`, and the log names the caller and the task. Then, on the task's thread:

- **A run in progress** that a caller's message or a decision in the console started stops after the step under way, never inside it: a model's reply, a tool, or an action a person approved completes and is recorded, and nothing after it starts. A run that ends by itself first ends as it would have; the task stays canceled.
- **A proposal that waits for a person** is rejected by the service itself, every action with the reason `The requesting agent canceled the task.`, and none of it runs. The run that rejects it stops before Freyja's model is asked again, and the task then no longer counts as one to decide. A subagent's proposal is rejected the same way; the subagent's step goes on until it ends, and anything it proposes in it is rejected too, 30 seconds later at the soonest (below). A proposal a run comes to after the cancel, such as one a delegate agent's report leads to, is rejected as well.
- **A run the service started by itself**, such as the one that reports a delegate agent's outcome, and a decision's run started through `POST /threads/{thread_id}/resume`, go on to their end; what they propose is rejected as above.
- **A delegate agent's task** that the message started goes on, and its outcome is recorded in the thread. The conversation takes no message until it is.

From the cancel on, nobody can approve or edit what the task proposed: the console's routes refuse it with `409` `requester_gone`, and a rejection is taken. A rejection by the service that cannot run, as when no usable model is active, is tried again 30 seconds later at the soonest, five times at most in all; after that the proposal stays until a person rejects it. A proposal a person approved or edited before the cancel, whose run then stopped after the decision was taken, is neither rejected nor resumed. An action that no longer needs approval, as after the agents' approval mode turned Auto, takes no decision but approve, so the service cannot reject it, nobody can approve it, and the log says to set the mode to Manual; a person can then reject it, and the conversation takes messages again. A decision sent through `POST /threads/{thread_id}/resume` is not stopped by a cancel; the task is canceled all the same, and what that run does is kept in the thread.

A cancel of a task that has ended is refused with `-32002`, and of an id the caller was never given with `-32001`.

A canceled task stops counting against the limits once its thread has no run in progress and no delegate agent's task awaited; a proposal that still waits does not keep it counting. Each instance looks every 30 seconds at the tasks that still count, and at the proposals still to be rejected, so that this holds also when a look was lost with a process that stopped.

## A conversation

`contextId` names a conversation: the caller's own id, at most 128 characters, or, when the message has none, one the service makes and returns on the task. A message in a conversation the caller has used before continues it, on the same thread, with the earlier messages in mind. It is refused while anything of the conversation is in progress: its last task still works, a run a person's decision started is in progress, a proposal waits, or a delegate agent's task is awaited. The refusal is `-32004`, with `reason` `conversation_busy` in the error's data.

Only the caller sends messages on its conversation. The service's own routes refuse a message to a caller's thread with `409` `caller_thread`, and the console offers no box for one; a person still decides its proposals and stops its run there.

## The message

- One or more text parts, joined by a line break: at least one character that is not blank, at most 32,000 characters.
- No `taskId`: a task takes one message, and a conversation is continued by its `contextId`.
- `configuration.acceptedOutputModes`, when given, holds `text/plain`, `text/*`, or `*/*`.
- No push notification configuration.
- `metadata["freyja.hops"]`, when given, a whole number of zero or more below `FREYJA_A2A_SERVER_MAX_HOPS` (below).

What is wrong with the message itself is refused with `-32602` (`InvalidParamsError`) and one sentence, such as `The message holds no text.`; a part that is not text, or output modes without text, with `-32005`. Every refusal comes before a thread is made or a model is asked.

## The hand-over count

A message says how many hand-overs between agents it has passed through in its metadata, under `freyja.hops`; a message without it has passed through none. Only the message's own metadata is read: a count in the request's metadata or in a part's is not. A count that is not a whole number of zero or more, such as `-1`, `1.5`, `"1"`, or `true`, is refused with `-32602` and `The hand-over count is not a whole number of zero or more.`; a count of `FREYJA_A2A_SERVER_MAX_HOPS` (3 by default, 1 to 16) or more with `-32602` and `The message has passed through too many agents.` Both are refused before a thread is made or a model is asked. The task keeps the count its message carried.

Every message Freyja sends to a delegate agent from a caller's thread, a hand-off, a follow-up, or an answer to an agent's question, carries `metadata["freyja.hops"]`: the count of the conversation's last message from its caller, plus one. That holds in every run on the thread: the message's own, one a person's decision continues, one that reports a delegate agent's outcome, and its subagent's work. Its message id names the thread by the alias the model is told, never by the thread's id. A cancel carries no count, since it starts no work. Messages from every other thread carry none, as before.

The count is a partial guard. It ends a loop between Freyja and another agent only when the other agent passes the count on: an agent that hands work back to Freyja with a new message that does not carry it starts the count again from nothing. What bounds such a loop then is the limits on tasks in progress (below), and the agents' approval mode set to Manual, which holds every message to an agent for a person.

## The limits

At most `FREYJA_A2A_SERVER_CALLER_TASKS` (5) received tasks of one caller, and `FREYJA_A2A_SERVER_TASKS` (20) of all callers, are in progress at once. A task waiting for a person counts, and so does a canceled one until its thread is at rest (above). Past either, a message is refused with `-32004`, the sentence `A limit on tasks in progress was reached. Send again when one has ended.`, and `reason` `task_limit` in the error's data, and the log names the caller and the limit. A task that ends frees its place.

**Next:** [Web console](web-console.md): the same features through the console.
