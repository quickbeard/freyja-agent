# Contract: what a collector receives

Everything the service sends, when `FREYJA_OTEL_ENDPOINT` is set. Traces go to the
collector's `/v1/traces` and metrics to its `/v1/metrics`, over OTLP/HTTP. Nothing that is
not on this page is sent, and `tests/test_telemetry_nowhere.py` holds the service to it.

The resource of both is `service.name`, from `FREYJA_OTEL_SERVICE_NAME`, and nothing else.
The instrumentation scope is `freyja_agent`. No span has an event or a link, and no metric
has an exemplar.
A value that is text is cut to 256 characters.

## Spans

| Span | Kind | Parent | Values |
|---|---|---|---|
| `<method> <route>`, such as `POST /threads/{thread_id}/runs` | server | none: a trace starts here | `http.request.method`, `http.route`, `http.response.status_code`, `freyja.http.listener`; `error.type` when the application raised |
| `freyja.run` | internal | the request that started the run; none for a background run | `freyja.run.id`, `freyja.thread.id`, `freyja.run.mode`, `freyja.run.console`, `freyja.run.strict`, `freyja.profile.id`, `freyja.profile.source`, `freyja.model.provider`, `freyja.model.name`, `freyja.run.outcome`; `freyja.error.code` when the run failed |
| `freyja.model_call` | client | the run, the tool call that started the subagent, or, for a profile's test, the request | `freyja.model.caller`, `freyja.model.provider`, `freyja.model.name`, `freyja.call.outcome`; `error.type` when the call raised |
| `freyja.tool_call` | internal | the run, or the tool call that started the subagent | `freyja.tool.name`, `freyja.tool.classification`, `freyja.tool.server` or `freyja.tool.agent`, `freyja.call.outcome`; `error.type` when the call raised |

A span that ended in an error has the error status, described by the run's error code, the
exception's class name, or, for a request, nothing. A request answered with a status of 500
or above has the error status; one the application failed to answer is recorded with 500
and the error's class name.

## Metrics

| Metric | Kind | Unit | Told apart by |
|---|---|---|---|
| `http.server.request.duration` | histogram | s | `http.request.method`, `http.route`, `http.response.status_code`, `freyja.http.listener` |
| `freyja.runs.started` | counter | run | `freyja.run.mode`, `freyja.run.console`, `freyja.run.strict` |
| `freyja.runs.ended` | counter | run | the same, with `freyja.run.outcome` and `freyja.error.code` |
| `freyja.run.duration` | histogram | s | `freyja.run.mode`, `freyja.run.outcome` |
| `freyja.model.calls` | counter | call | `freyja.model.caller`, `freyja.model.provider`, `freyja.model.name`, `freyja.call.outcome` |
| `freyja.model.errors` | counter | run | `freyja.error.code` |
| `freyja.tool.calls` | counter | call | `freyja.tool.name`, `freyja.tool.classification`, `freyja.tool.server`, `freyja.tool.agent`, `freyja.call.outcome` |
| `freyja.worker.round.duration` | histogram | s | nothing |
| `freyja.worker.threads_waiting` | gauge | thread | nothing |
| `freyja.threads.awaiting_decision` | gauge | thread | nothing |
| `freyja.outcomes.oldest_undelivered.age` | gauge | s | nothing |

The count of a histogram is the count of what it measures: `http.server.request.duration`
counts the requests. The last two are read from the database every instance shares: every
instance sends the same value, and a collector takes one and does not add them up. They are
left out of an export when they could not be read. Every other metric is of one instance.

No metric is told apart by a run's id or a thread's id.

## Values

| Value | What it holds |
|---|---|
| `http.request.method` | `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS`, `CONNECT`, `TRACE`, or `_OTHER` |
| `http.route` | a route's path as the service declares it, with its placeholders, or `unmatched` for a request that reached no route. A request with a method its route does not take is named by that route, with the status 405 |
| `http.response.status_code` | the status code sent, a number |
| `freyja.http.listener` | `service`, or `outside` for the address other agents call (feature 009) |
| `freyja.run.id` | the run's id, which the service made |
| `freyja.thread.id` | the thread's id, on the run's span only |
| `freyja.run.mode` | `run`, `resume`, or `background` |
| `freyja.run.console` | whether the run goes on without a reader |
| `freyja.run.strict` | whether another agent started the run's thread |
| `freyja.run.outcome` | `end`, `interrupt`, `error`, `stopped`, or `cancelled` |
| `freyja.error.code` | `model_error`, `no_usable_model`, `step_limit`, `internal`, or `approval_now_required` |
| `freyja.profile.id` | the LLM profile's id, or `environment` |
| `freyja.profile.source` | `environment` or `saved` |
| `freyja.model.provider`, `freyja.model.name` | the provider and the model the profile names |
| `freyja.model.caller` | `agent` for a call the orchestrator or a subagent makes, `library` for one a library makes by itself within a run, `test` for one of a profile's test |
| `freyja.tool.name` | the name of a tool the orchestrator was built with, or `unknown` |
| `freyja.tool.classification` | `read_only`, `workspace`, `gated`, or `unknown` |
| `freyja.tool.server` | the configured name of the tool server a tool belongs to |
| `freyja.tool.agent` | the configured name of the agent a hand-off or a cancel is for |
| `freyja.call.outcome` | `ok`, `error`, `interrupted`, or `cancelled` |
| `error.type` | an exception's class name |

## Never sent

A message; a tool's arguments or its result; a model's reply; an error's text; an agent's
address; an API key; a caller's name or token; the name an operator gave an LLM profile; a
request's path as it was written, its query, its headers, its body, the host name it asked
for, or its client's address; anything a model wrote.

## Not recorded

- A request to `/healthz` or `/readyz`.
- A proposal a person rejected: the action never ran.
- The model's name in a test of values that are not saved.
- A trace's id a client sent with a request: it is not read.
- The status code of a request whose client left before an answer began: there is none.
