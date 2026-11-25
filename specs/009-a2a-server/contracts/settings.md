# Contract: Settings

Read from the environment at startup, through `Settings`. None can be changed while the
service runs or through any route. Each goes into `.env.example` with the phase that first
reads it.

| Variable | Default | Rule |
|---|---|---|
| `FREYJA_A2A_SERVER` | `false` | Whether Freyja receives work over A2A. Off: every other variable here may be set and is not used, apart from two errors of the outside listener below: `FREYJA_A2A_SERVER_HOST` without `_PORT` or the other way round, and the outside listener set while the feature is off. |
| `FREYJA_A2A_SERVER_CALLERS` | `{}` | A JSON object: a caller's name, then an object with `token` and nothing else. At most 20. |
| `FREYJA_A2A_SERVER_HOST` | unset | The address the outside listener binds. No default: a deployment that opens it says where. |
| `FREYJA_A2A_SERVER_PORT` | unset | Its port, 1 to 65535, and not the service's own on the same address. |
| `FREYJA_A2A_SERVER_URL` | unset | The address callers reach the outside listener under, as the card gives it: `http` or `https`, a host, optionally a port, no path, query, fragment, user name, or password. |
| `FREYJA_A2A_SERVER_WAIT_SECONDS` | `30` | The most a send without a stream waits. 0 to 600. |
| `FREYJA_A2A_SERVER_CALLER_TASKS` | `5` | Tasks in progress for one caller. 1 to 100. |
| `FREYJA_A2A_SERVER_TASKS` | `20` | Tasks in progress for all callers. At least the caller's limit, at most 1,000. |
| `FREYJA_A2A_SERVER_MAX_HOPS` | `3` | The hand-over count at which a message is refused. 1 to 16. |
| `FREYJA_A2A_SERVER_FILES_DIR` | `caller-files` | Where named callers' file areas are kept, one directory per caller. Not inside the workspace. |

```text
FREYJA_A2A_SERVER=true
FREYJA_A2A_SERVER_CALLERS='{"artex": {"token": "<16 to 4096 visible ASCII characters>"}}'
FREYJA_A2A_SERVER_HOST=10.0.0.5
FREYJA_A2A_SERVER_PORT=8443
FREYJA_A2A_SERVER_URL=https://freyja.example.com
```

## A caller

- `name`: lowercase letters, digits, hyphens, and underscores, starting with a letter, at
  most 32 characters; not `local`; different from every other caller's.
- `token`: 16 to 4,096 characters, each a visible ASCII character (no space, no control
  character); different from every other caller's.

A name is what a caller's conversations and file area are kept under. A caller that is
removed has its tasks canceled at the next start; one that is named again under the same
name has its conversations and its files again. Do not give a removed caller's name to
another party. Every instance that shares the database is given the same callers: an
instance that lacks one cancels that caller's tasks.

## What stops startup

Each with an error that names the variable, and the caller where there is one, and never a
token or a value:

- a caller whose name or token breaks its rule; two callers with one token; more than 20;
- `FREYJA_A2A_SERVER_HOST` without `_PORT`, or the other way round;
- the outside listener set while `FREYJA_A2A_SERVER` is off, while no caller is named, or
  while `FREYJA_A2A_SERVER_URL` is unset;
- `FREYJA_A2A_SERVER_URL` that breaks its rule;
- a limit outside its range, or a total limit below the caller's;
- `FREYJA_A2A_SERVER_FILES_DIR` that is the workspace, is inside it, or contains it;
- the outside listener's address that cannot be bound, when the service starts.

## With the feature off

Nothing here is used, and the service is what it was before the feature. On a deployment
that had the feature on, the threads callers started are still there: they can be read, and
are not run until the feature is on again ([openapi.yaml](openapi.yaml), `a2a_server_off`).

## What warns and starts

- `FREYJA_A2A_SERVER_URL` with the scheme `http`: tokens sent there can be read on the
  network path (FR-029).

## Logged at startup, at INFO

That the feature is on, how many callers are named, and whether the outside listener is on.
The listener's address is the deployment's own setting and is logged; no token is.
