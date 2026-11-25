# Contract: Freyja as an A2A Server

What an agent that calls Freyja can rely on. The protocol is A2A 1.0 over its JSON-RPC
binding, as the SDK (a2a-sdk 1.3) serves it; this file says which parts Freyja offers, at
which addresses, and what each answers. Field names are the protocol's JSON names.

With the feature off, neither path below exists: each is answered as any path the service
does not have.

## Addresses

| | The service's own address | The outside listener |
|---|---|---|
| On when | `FREYJA_A2A_SERVER` is on | also `FREYJA_A2A_SERVER_HOST` and `_PORT` are set |
| Card | `GET /.well-known/agent-card.json` | the same path |
| Operations | `POST /a2a` | the same path |
| Credential | none needed; a named caller's token is honored | a named caller's token, on every operation |
| Anything else | the rest of Freyja's API, as before | answers as a path that does not exist |

Every request to `POST /a2a` carries `A2A-Version: 1.0`; without it the SDK refuses with its
version error.

## The card

```json
{
  "name": "Freyja",
  "description": "A general-purpose orchestrator. It takes a request in plain language, works on it with its tools and the agents it can hand work to, and asks a person before any action with effects outside its workspace.",
  "version": "<the service's version>",
  "supportedInterfaces": [
    {"url": "<address>/a2a", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
  ],
  "capabilities": {"streaming": true, "pushNotifications": false, "extendedAgentCard": false},
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["text/plain"],
  "skills": [
    {"id": "request", "name": "Take a request", "description": "Send a request in plain language; the result is Freyja's reply.", "tags": ["general"]}
  ]
}
```

- At the service's own address, `<address>` is the scheme and host the card was asked under,
  and the card declares no security.
- At the outside listener, `<address>` is `FREYJA_A2A_SERVER_URL`, and the card declares one
  security scheme, HTTP `bearer`, and requires it.
- The card names no delegate agent, tool server, tool, model, profile, or caller, and is the
  same for every caller. It is given without a token at both addresses.

## The credential

`Authorization: Bearer <token>`. At the outside listener, an operation without the header,
with another scheme, or with a token no caller has is answered, before its body is read:

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer
Content-Type: application/json

{"detail": {"code": "unauthorized", "message": "This address takes a request only with a caller's bearer token in the Authorization header."}}
```

The answer is the same in the three cases. At the service's own address, a request with no
`Authorization` header is the local caller's; one with a header is held to the rule above.

A token sent anywhere else, in the body, a query, or another header, is not a credential:
the request is treated as one without. Who the caller is comes from the credential alone;
the protocol's `tenant` field is ignored in every request.

A request that a browser marks as made by a page of another origin is answered `403` with
the code `other_origin`, at both addresses, before the credential is looked at.

A request larger than 256 KiB is answered `413`, at both addresses, before its body is read
whole.

The checks are made in this order, each before the next is looked at: the origin, the
credential, the size, the protocol's version and operation, the message's form, the
conversation and the task it names, the limits.

## Operations

| Method | Offered | Notes |
|---|---|---|
| `SendMessage` | yes | Below. |
| `SendStreamingMessage` | yes | Below. |
| `GetTask` | yes | The caller's own task, as it stands. No history is returned, whatever `historyLength` asks. |
| `SubscribeToTask` | yes | A task that has not ended. |
| `CancelTask` | yes | A task that has not ended. |
| `ListTasks` | no | `UnsupportedOperationError`. |
| The four push notification methods | no | `PushNotificationNotSupportedError`. |
| `GetExtendedAgentCard` | no | `ExtendedAgentCardNotConfiguredError`. |

### The message

- `parts`: one or more, each a `text` part. Their texts, joined by a line break, are the
  message: at least one character that is not blank, at most 32,000 characters.
- `contextId`: absent, or at most 128 characters. Absent: Freyja makes one and returns it on
  the task. One the caller has not used starts a conversation. One it has used continues
  that conversation, when nothing of it is in progress.
- `taskId`: must be absent. A received task takes one message.
- `metadata["freyja.hops"]`: absent, or a whole number of zero or more: how many hand-overs
  between agents the message has passed through.
- `configuration.returnImmediately`: for `SendMessage`, answer at once.
- `configuration.acceptedOutputModes`: absent, or a list that holds `text/plain` or `text/*`
  or `*/*`.
- `configuration.taskPushNotificationConfig`: must be absent.

### The task

```json
{
  "id": "<32 hexadecimal characters>",
  "contextId": "<the conversation's id>",
  "status": {
    "state": "TASK_STATE_WORKING",
    "message": {"role": "ROLE_AGENT", "parts": [{"text": "Waiting for a person's approval of: send_notification."}]},
    "timestamp": "2026-11-25T10:02:11Z"
  },
  "artifacts": []
}
```

| State | When | `status.message` | `artifacts` |
|---|---|---|---|
| `TASK_STATE_WORKING` | From acceptance, while a run is in progress, a proposal is pending, or an agent task is awaited | One of the sentences below | none |
| `TASK_STATE_COMPLETED` | The last run ended with a reply and nothing waits | none | one, `reply`, the whole reply as text |
| `TASK_STATE_FAILED` | The last run ended with an error or was cut off | a reason of the fixed set | none |
| `TASK_STATE_CANCELED` | The caller canceled, or a run was stopped between two steps | a reason of the fixed set | none |

No other state is used. A task that has ended never changes.

**Status sentences of a working task**, each worded by the service:

| Sentence | When |
|---|---|
| `Working.` | Accepted, or between steps with nothing to name. |
| `Freyja is using: <names>.` | A step of Freyja's own asked for actions. |
| `Completed: <names>.` | A step that ran them completed. |
| `Waiting for a person's approval of: <names>.` | The run paused on a proposal. |
| `Waiting for agent <names>.` | The run ended and an agent task is awaited. |

`<names>` are tool names as Freyja's catalog has them, `agent <name>` for a message to a
delegate agent, and `a subagent` for work Freyja gave its subagent. No argument, no result,
and nothing a tool, a subagent, or an agent wrote is in a sentence.

**Reasons** of a failed or canceled task: `No usable model is active.`, `The model could not
be used.`, `The run reached its step limit.`, `The run was interrupted.`, `An internal
error.`, `The run was stopped before it finished.`, `The caller canceled the task.`

### `SendMessage`

Answers with the task: at once with `returnImmediately`; otherwise when the task has ended
or `FREYJA_A2A_SERVER_WAIT_SECONDS` have passed, whichever comes first. A task returned as
working goes on; read it with `GetTask` or follow it with `SubscribeToTask`.

### `SendStreamingMessage` and `SubscribeToTask`

A stream of the protocol's events, in this order:

1. the task as it stands;
2. while it works: `statusUpdate` events with the sentences above, and `artifactUpdate`
   events for the artifact `reply` with `append: true`, each a piece of the text of Freyja's
   own reply as it is written, never a subagent's, a tool's, or a call's arguments;
3. when it ends: for a completed task, one `artifactUpdate` for `reply` with the whole reply,
   `append: false` and `lastChunk: true`; then the last `statusUpdate`, with the end state.

The stream then closes. Pieces are sent only for a run the answering instance drives; the
whole reply at the end is always sent, and replaces the pieces. A follower that reads too
slowly is disconnected and subscribes again. At most four streams follow one task on one instance.

### `CancelTask`

Answers with the task as canceled. A run in progress is stopped after the step under way,
never inside one. A proposal that waits for a person is rejected, with the reason that the
requesting agent canceled. A delegate agent's task that the message started goes on.

## Refusals

Protocol errors are JSON-RPC errors in an answer with HTTP status 200, as the SDK sends them.

| When | Error | Code |
|---|---|---|
| A task id that is not the caller's, or does not exist | `TaskNotFoundError` | -32001 |
| `CancelTask` on a task that has ended | `TaskNotCancelableError` | -32002 |
| A push notification method, or a push configuration in a send | `PushNotificationNotSupportedError` | -32003 |
| `SubscribeToTask` on a task that has ended; `ListTasks` | `UnsupportedOperationError` | -32004 |
| A part that is not text; accepted output modes without text | `ContentTypeNotSupportedError` | -32005 |
| `GetExtendedAgentCard` | `ExtendedAgentCardNotConfiguredError` | -32007 |
| No `A2A-Version: 1.0` | `VersionNotSupportedError` | -32009 |
| The message itself: see the first table below | `InvalidParamsError` | -32602 |
| Not now: see the second table below | `UnsupportedOperationError` | -32004 |
| Anything unexpected | `InternalError`, with the message `An internal error.` | -32603 |

**The message itself** (`InvalidParamsError`), each with one sentence:

| Reason | Sentence |
|---|---|
| no text | `The message holds no text.` |
| too long | `The message is longer than 32,000 characters.` |
| a task named | `A message names no task: a task takes one message. Continue a conversation by its contextId.` |
| conversation id | `The contextId is longer than 128 characters.` |
| hop count's form | `The hand-over count is not a whole number of zero or more.` |
| hop count's limit | `The message has passed through too many agents.` |

**Not now** (`UnsupportedOperationError`), each with one sentence and, in the error's data,
`reason`:

| `reason` | Sentence |
|---|---|
| `conversation_busy` | `This conversation takes no message now: its last task has not ended, or work it started is still going on.` |
| `task_limit` | `A limit on tasks in progress was reached. Send again when one has ended.` |
| `stream_limit` | `This task has as many streams as it takes. Read it with GetTask.` |

An answer about a task that is another caller's equals, in every byte but the request's id,
the answer about a task id that was never used. `task_limit` is the same sentence for the
caller's own limit and for the total.

## What a caller is never given

A stored API key beyond its last four characters, a delegate agent's address, any caller's
token, a thread's id, a proposal's id, another caller's name, and who decided a proposal: in
nothing, Freyja's reply included. On a caller's thread Freyja itself is told an alias in
place of the thread's id. In addition, nothing the service itself words (a status
sentence, a reason, an error) holds an argument or a result of a tool or of a message to an
agent, or anything a subagent or an agent wrote.

Freyja's reply is Freyja's wording of what the caller's own task did and found.

## The count of hand-overs, on messages Freyja sends

Every message Freyja sends to a delegate agent from a caller's thread has
`metadata["freyja.hops"]`: the count of the conversation's last message from its caller,
plus one. Messages from every other thread carry none, as before.
