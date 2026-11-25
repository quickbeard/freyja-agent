# Receive tasks from agents

Freyja hands work to other agents over A2A (see [Agents](agents.md)); with `FREYJA_A2A_SERVER` on, another agent can hand work to Freyja the same way. The caller's message starts the run the console starts, on a thread of the service's own, so every action with an effect outside the workspace waits for a person in the console, as any other does. What the caller is told is read from that thread. The full contract is in `specs/009-a2a-server/contracts/a2a-server.md`.

This version serves callers on the service's own address and needs no credential there. A request that carries an `Authorization` header is refused with `401`, and reading a task's events again (`SubscribeToTask`) and canceling a task (`CancelTask`) are answered as operations that are not offered.

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

The card names Freyja, gives the address it was asked under followed by `/a2a`, says that streaming is offered and push notifications are not, takes and gives `text/plain`, and declares no security. It names no agent, tool server, tool, model, profile, or caller, and is the same for every caller.

The address is held to the same rules as the rest of the API: a page of another origin is refused with `403` `other_origin`, and a host name the service does not answer for with `403` `unknown_host`. A request larger than 256 KiB is refused with `413` before its body is read whole. At this address A2A is exactly as reachable as the thread routes, the console's forwarding included: whoever can open the console can call Freyja as an agent on this machine.

## Try it with the stand-in caller

The suite's stand-in caller is the protocol's own client behind a small command:

```bash
caller() { uv run python -m tests.a2a_caller --url http://localhost:8000 "$@"; }
caller card
caller stream "List the files in your workspace and say how many there are."
caller send "Say hello."
caller get <task id>
```

Each task or event is printed as one JSON object, and a refusal as the JSON-RPC error it was. `--context <id>` continues a conversation, and `--return-immediately` asks a send not to wait.

## What a caller can do

| Operation | |
|---|---|
| `SendMessage` | Starts a task and answers with it once it has ended, or once `FREYJA_A2A_SERVER_WAIT_SECONDS` (30 by default) have passed, whichever comes first; at once with `configuration.returnImmediately`. A task answered as working goes on; read it with `GetTask`. |
| `SendStreamingMessage` | Starts a task and streams it: the task as working, a status for each step, the pieces of Freyja's reply as they are written, the whole reply, and the task's end. |
| `GetTask` | The caller's task as it stands, its reply once it has completed. No history is returned. |
| Everything else | Refused: `ListTasks`, `CancelTask`, and `SubscribeToTask` with `-32004`, the push notification methods with `-32003`, `GetExtendedAgentCard` with `-32007`. |

A task is `TASK_STATE_WORKING` while a run is in progress, a proposal waits for a person, or a delegate agent's task is awaited; then `TASK_STATE_COMPLETED` with one artifact, `reply`, holding Freyja's reply; `TASK_STATE_FAILED` with a reason; or `TASK_STATE_CANCELED` when its run was stopped in the console. A working task's status says what it does, in the service's words only:

| Status | When |
|---|---|
| `Working.` | Accepted, or between steps. |
| `Freyja is using: <names>.` | A step of Freyja's asked for actions. |
| `Completed: <names>.` | The step that ran them completed. |
| `Waiting for a person's approval of: <names>.` | The run paused on a proposal. |
| `Waiting for agent <names>.` | The run ended and a delegate agent's task is awaited. |

Names are tool names, `agent <name>` for a message to a delegate agent, and `a subagent` for work Freyja gave its subagent. No argument, no result, and nothing a tool, a subagent, or an agent wrote is in a status. A failed or canceled task's reason is one of: `No usable model is active.`, `The model could not be used.`, `The run reached its step limit.`, `The run was interrupted.`, `An internal error.`, `The run was stopped before it finished.` A tool the catalog does not have is named `a tool Freyja does not have`. What is read from the thread, the reply above all, is cleaned before a caller is sent it of every agent address the service knows and of the keys of the active LLM profile and the environment's; a reply is kept cleaned once its task has completed. The stream sends only the text of Freyja's own reply, never a subagent's, a tool's, or a call's arguments, each piece up to its last space or line break, so that an address or a key written over several pieces is cleaned whole; the rest follows with the next piece, or in the whole reply at the end.

**Waiting for a person.** A caller's task that comes to an action needing approval waits for a person in the console, and its status says so with the actions' names. On a caller's thread every message to a delegate agent waits for a person too, whatever the agent's approval choice. The task goes on once the proposal is decided in the console.

## A conversation

`contextId` names a conversation: the caller's own id, at most 128 characters, or, when the message has none, one the service makes and returns on the task. A message in a conversation the caller has used before continues it, on the same thread, with the earlier messages in mind. It is refused while anything of the conversation is in progress: its last task still works, a run a person's decision started is in progress, a proposal waits, or a delegate agent's task is awaited. The refusal is `-32004`, with `reason` `conversation_busy` in the error's data.

Only the caller sends messages on its conversation. The service's own routes refuse a message to a caller's thread with `409` `caller_thread`, and the console offers no box for one; a person still decides its proposals and stops its run there.

## The message

- One or more text parts, joined by a line break: at least one character that is not blank, at most 32,000 characters.
- No `taskId`: a task takes one message, and a conversation is continued by its `contextId`.
- `configuration.acceptedOutputModes`, when given, holds `text/plain`, `text/*`, or `*/*`.
- No push notification configuration.

What is wrong with the message itself is refused with `-32602` (`InvalidParamsError`) and one sentence, such as `The message holds no text.`; a part that is not text, or output modes without text, with `-32005`. Every refusal comes before a thread is made or a model is asked.

## The limits

At most `FREYJA_A2A_SERVER_CALLER_TASKS` (5) received tasks of one caller, and `FREYJA_A2A_SERVER_TASKS` (20) of all callers, are in progress at once. A task waiting for a person counts. Past either, a message is refused with `-32004`, the sentence `A limit on tasks in progress was reached. Send again when one has ended.`, and `reason` `task_limit` in the error's data, and the log names the caller and the limit. A task that ends frees its place.

**Next:** [Web console](web-console.md): the same features through the console.
