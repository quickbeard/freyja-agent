# Data Model: Telemetry of Runs

Nothing is stored: no table, column, or migration is added. What this feature makes lives
in the process until it is sent, and is described here by what holds it.

## Scope

What a span started now belongs under, kept in a context variable of
`runtime/telemetry.py`. One of three kinds.

| Field | Meaning |
|---|---|
| `kind` | `request`, `run`, or `call` |
| `span` | the span, or none where it could not be started |
| `provider`, `model` | the model of the run the scope is part of, for the model calls under it |
| `in_run` | whether the scope is a run or under one |
| `model_call` | whether the scope is a model call that is being recorded, so that a request to the model inside it is not recorded twice (research R8) |

Rules:

- A request's scope is set by `RequestTelemetry` for the length of the request.
- A run's span starts under the current scope when that is a request's, and as a trace of
  its own otherwise (FR-013). A run's scope is current only while the graph is asked for
  its next item, never across a `yield`.
- A call's scope is current for the length of the call, so a subagent's calls stand under
  the call that started it (FR-011).
- OpenTelemetry's own current span is never set (research R3).

## Request

Made by `RequestTelemetry` for every `http` request but `/healthz` and `/readyz`.

| Field | From |
|---|---|
| method | the request's, when it is a standard one; `_OTHER` otherwise |
| route | the declared path of what the router matched, read once the application has answered: `scope["route"].path` for a route of FastAPI's, and for any other route the path of the application's route with the scope's endpoint; `unmatched` without one |
| status code | the `http.response.start` the application sent; 500 when it raised before one |
| listener | `service` or `outside`, given when the middleware is added |
| duration | from the request's arrival to the application's return |

## Run, model call, tool call

As [contracts/telemetry.md](contracts/telemetry.md) lists them. Three things are added to
the work that existed:

- a run's parent (above);
- `freyja.model.caller` on a model call's span and count: `agent`, `library`, or `test`;
- a model call that is not made through the middleware: a library's within a run, recorded
  by the guarded model, and a profile's test's, recorded by the test.

## Waiting reading

What `runtime/waiting.py` last read, kept by `Telemetry` for the two gauges.

| Field | Meaning |
|---|---|
| `awaiting_decision` | the threads whose last run paused with a proposal nobody decided: `ThreadIndex.counts()` after the rows whose run ended were settled |
| `oldest_undelivered` | seconds since the oldest outcome that is ready and not delivered became ready; 0 when there is none |
| `read_at` | when the reading was made, on the process's monotonic clock |

Rules:

- A reading replaces the one before it. A reading that fails takes the one before it away,
  so both values are left out at once, and is said in the log once until a reading
  succeeds again.
- A gauge's callback returns the reading's value while the reading is no older than two
  intervals, and nothing otherwise (FR-022).
- Reading takes no thread's lease, changes no thread, and contacts no model, tool server,
  or agent. It settles a thread's row in the index of threads as a reading of the list of
  threads does (feature 007).

## The oldest outcome not delivered

One new read of `AgentTaskStore`, `oldest_ready_outcome_age() -> float`.

- Postgres: over `freyja_agent_tasks`, the rows with `outcome_ready` and no `delivered_at`;
  the age of the earliest of `coalesce(ended_at, last_checked_at)` against the database's
  `now()`; 0 for no row. A task that asks for input has an outcome and no `ended_at`, so
  the time it was last checked stands for when the outcome was learned.
- In memory: the same over the store's records and its clock.

## Settings

`otel_endpoint: str | None` and `otel_service_name: str`, as
[contracts/settings.md](contracts/settings.md) has them.
