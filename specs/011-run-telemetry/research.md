# Research: Telemetry of Runs

Each entry is a decision the plan rests on, why it was taken, and what else was looked at.
R1 to R5 are decisions the work on `telemetry-full` already took; they were read again
against the spec and are kept. R6 to R11 are for what the spec adds.

## R1. One module imports OpenTelemetry, and writes every name

**Decision**: `runtime/telemetry.py` is the only module that imports OpenTelemetry. Every
span's name, every metric's name, and every named value of either is a constant of that
module, set by a method of its `Telemetry` class. Every other module calls a method and
passes it names it already holds.

**Rationale**: FR-030 to FR-033 are a rule about what leaves the service. A rule kept in
one file can be read and tested in one place: `tests/test_telemetry.py` fails when another
module imports OpenTelemetry, and `tests/test_telemetry_nowhere.py` holds every exported
value to a list.

**Alternatives considered**: the libraries' own instrumentation packages (for FastAPI,
httpx, LangChain). Each decides by itself what it records, and several record content: a
request's URL with its query, a prompt, a tool's arguments. None can be held to FR-031.

## R2. Off is the API's own tracer and meter that do nothing

**Decision**: with no `FREYJA_OTEL_ENDPOINT`, `Telemetry.off()` holds the no-op tracer and
meter of the OpenTelemetry API. Nothing of the SDK is imported, no thread is started, and
nothing is connected to (FR-001). The middleware on the orchestrator and the middleware on
the application are there all the same, and call into those no-ops.

**Rationale**: FR-040 and constitution principle III ask for one way to build the
orchestrator. A middleware that is present only when telemetry is on would make two graphs,
and a bug of one would not be found in the other.

**Alternatives considered**: leaving the middleware out when off. Rejected for the reason
above; the cost of the no-op is a function call a step.

## R3. Providers of its own, and a current span of its own

**Decision**: the tracer and the meter come from providers that `Telemetry` keeps and never
registers as the process's (`set_tracer_provider` is not called). Which span a call belongs
under is kept in a context variable of `runtime/telemetry.py`, and OpenTelemetry's own
current span is never set: every span is started with an explicit context, made from the
parent's span or empty.

**Rationale**: FR-038 and FR-041. Two libraries the service uses write the current trace's
id into what they send, by themselves, whenever OpenTelemetry has a current span: the MCP
SDK (`mcp/shared/_otel.py`, which injects into a request's `_meta`) and a model provider's
client, into a header. With a current span set, turning telemetry on would change what a
tool server and a model receive. With a global provider registered, FastAPI, the MCP and
A2A SDKs, and the provider clients would start to trace themselves into Freyja's collector,
with values this spec does not allow.

The owner confirmed that no trace's id is sent to a delegate (spec, Clarifications), and
that one a client sends is not used (FR-028): the request middleware never reads a
`traceparent` header.

**Alternatives considered**: the standard current span, with the propagators set to none.
The MCP SDK calls `inject` from the global propagator, which could be emptied, but a
provider's client is not held by it, and a later version of either may change how it
propagates. Keeping no current span is what cannot be worked around by a library.

## R4. Recording never raises, and never runs a step again

**Decision**: every piece of recording runs inside `Telemetry._safely`, which catches
whatever a tracer, a meter, or an exporter raises, says the first failure once in the log
by its kind, and returns. A call's context manager ends its span in a `finally` through
`_safely`, after the call returned or raised, and never replaces the call's own result or
exception.

**Rationale**: FR-034 and FR-035. LangGraph runs a step again when the step raised. A span
that failed to end after a tool ran, if it raised, would fail the tool's step, and the
approved action would run a second time. So the end of a span may not raise into the step.

**Alternatives considered**: letting the SDK's own error handling do it. The SDK catches
most of its errors, not all (a processor's `on_end`, an attribute of a wrong type), and a
tracer handed in by a test, or a later version, may raise where today's does not.

## R5. The SDK is configured in code

**Decision**: the endpoint, the service's name, the sampler (every trace), the limits of a
span (32 values of 256 characters, no event, no link), the batching (a queue of 2,048,
sent every five seconds), the metrics interval (60 seconds), and the export's time limit
(10 seconds) are given to the SDK's classes directly. The resource is made directly, not
through `Resource.create`, which would read `OTEL_RESOURCE_ATTRIBUTES` and
`OTEL_SERVICE_NAME`. Exemplars are off, since one would put a trace's id beside a count.
`shutdown_on_exit` is off: the lifespan shuts telemetry down, off the event loop, after the
runs and the worker have ended and before the pool is closed (FR-037).

**Rationale**: constitution principle IV: the service is configured through `Settings`.
The exporter still reads some variables by itself (headers, certificates, a credential
plugin, proxies); FR-004 asks the documentation to name them, and `docs/observability.md`
does.

**Alternatives considered**: the SDK's own environment configuration
(`OTEL_EXPORTER_OTLP_ENDPOINT`). It would configure the service behind `Settings`, with no
validation of the address and no default that is off.

## R6. A request's span is made by one ASGI middleware, outside everything

**Decision**: `api/telemetry.py` holds `RequestTelemetry`, a plain ASGI middleware (not
Starlette's `BaseHTTPMiddleware`, which runs the application in another task and buffers a
stream). `create_app` adds it after `AllowedHosts`, so it is the outermost and sees a
request that is refused for its host name; `a2a_listener.application` adds it to the
outside listener's application. For an `http` scope it:

1. passes the request on untouched when its path is `/healthz` or `/readyz` (FR-027);
2. starts the request's span and makes it the scope a run started inside belongs under;
3. wraps `send` to note the status code of `http.response.start`, and changes nothing that
   is sent (FR-039);
4. when the application returns, raises, or is cancelled, ends the span and records the
   duration with the method, the route, and the status code.

The route is the declared path of what the router matched: `scope["route"].path`, which
Starlette 1.10 and FastAPI set on a match (`starlette/routing.py`, `fastapi/routing.py`).
Without one it is `unmatched`: a path nobody routes, a method a route does not take, and a
request `AllowedHosts` refused before routing. The method is the request's when it is one
of the nine standard ones and `_OTHER` otherwise, since a method is a word a client writes.
The path, the query, the headers, the host name, and the client's address are never read
(FR-024, FR-025).

Names follow OpenTelemetry's HTTP conventions, so that a collector's stock dashboards read
them: the span is named `<method> <route>`, of kind server, with `http.request.method`,
`http.route`, and `http.response.status_code`; the metric is `http.server.request.duration`,
a histogram in seconds whose count is the count of requests (FR-026). One value is Freyja's
own, `freyja.http.listener` (`service` or `outside`), which says on which of the two
addresses the request arrived.

**Rationale**: an ASGI middleware sees every request of an application whatever answers it,
which is what FR-024 asks, and is covered for every future route without a line per route,
as `AllowedHosts` is. A stream's span ends when the application returns, which is when the
stream ends or its reader leaves (FR-029).

**Alternatives considered**: `opentelemetry-instrumentation-fastapi`. It records the URL
with its query and the client's address, uses the global provider, sets the current span,
and continues a client's trace: four things the spec forbids. A FastAPI dependency on every
router would not see a request that no route matched or that a middleware refused.

## R7. A run's span stands under the request that started it

**Decision**: the scope kept in the context variable says what kind it is: a request, a
run, or a call. `Telemetry.run` starts the run's span under the current scope when that is
a request's, and as the root of a new trace otherwise (FR-013). Nothing is passed from the
route to the run loop.

**Rationale**: the context variable is set by the request middleware in the task that
serves the request. Every way a request starts a run copies that context: the stream of
`POST /threads/{id}/runs` and `/resume` is read by a task the response starts, and a
console run is driven by a task `UnattendedRuns` creates inside the request, which is also
how a caller's message over A2A and a person's decision start theirs. The delegation
worker's task is created at startup, outside any request, so a background run finds no
request and starts a trace of its own, as the spec asks. A console run outlives its request;
a span that ends after its parent is allowed and shown as such.

**Alternatives considered**: passing the request's span through `start_message` and
`run_stream` as an argument. It would thread a telemetry value through four layers that
have no other use for it, and the A2A service would need the same.

## R8. A library's own model call is recorded where every request to a model passes

**Decision**: the guarded model (`llm/guard.py`) already wraps the four methods through
which every request to a provider passes, for the keys in its errors. `guard_model` is also
given how to record, and each wrapped method tells telemetry of a request when two things
hold: the request is inside a run, and it is not inside a model call the middleware is
already recording. Such a request is recorded as a model call whose `freyja.model.caller`
is `library`. A call the middleware records says `agent`; a profile's test says `test`
(R9).

**Rationale**: the summary of a long conversation is asked by LangChain's summarization
helper with the model object itself (`self._summary_model.ainvoke`, in
`langchain/agents/middleware/summarization.py`), from inside Deep Agents' own
`awrap_model_call`. Deep Agents places a caller's middleware after its core ones
(`deepagents/graph.py`, `_apply_custom_middleware`), so the summarization wraps
`TelemetryMiddleware` and its own request is made outside the span the middleware opens.
No hook of the agent sees it. The model object does, whichever library asks and for
whatever reason, so the rule is not about summarization alone (FR-010).

The two generator methods (`_stream`, `_astream`) record without touching the context
variable, because a value set across a `yield` of an async generator cannot be reset from
the context of whoever reads next; the two others set the scope for the length of the
call, so that a provider's `_agenerate` that streams through its own `_astream` is counted
once.

A model the service did not guard is not recorded this way: the placeholder model of an
unusable profile, which is never asked, and a model a test hands straight to `build_agent`.

**Alternatives considered**: a LangChain callback handler on the model. It is handed the
prompt and the reply, which the spec forbids reading into telemetry, runs in an executor
for an async call, and pairs a start with an end by an id of LangChain's; the wrapped
methods have the start, the end, and the exception in one frame. Recording only the
summarization, by its `lc_source` metadata, would miss the next library that asks the model
by itself.

## R9. A profile's test records its own two calls

**Decision**: `check_model` takes how to record a call, and wraps each of its two requests
in it, marked `test`. The route hands it `Telemetry.model_call` with the provider, and with
the model's name when the test is of a saved profile or of the environment's (FR-010a). The
spans stand under the request's span, which is the current scope.

**Rationale**: a test calls the model outside any run, so neither the middleware nor R8
sees it. A test of values nobody saved names the provider alone: the provider is one of
the service's fixed table, and the model's name is what the request wrote.

## R10. The two waiting values are read on a timer and sent as observable gauges

**Decision**: `runtime/waiting.py` reads two numbers and hands them to
`Telemetry.waiting`:

- **threads awaiting a decision**: `ThreadIndex.counts()`, the number the console's sidebar
  already shows. A row is settled lazily, by the first reading after its run ended (feature
  007), so before counting, the reading settles the rows `ThreadIndex.unsettled()` returns
  whose lease is not held, with `settle_thread`, as the list of threads does.
- **the age of the oldest outcome not delivered**: one new read of the agent-task store,
  `oldest_ready_outcome_age`, in seconds on the database's clock, zero when there is none.

`Runtime.assemble` starts the reading as a task when telemetry is on, at the interval
metrics are sent, and stops it before telemetry is shut down. The two metrics are
observable gauges whose callback returns what the last reading found, and returns nothing
when there is no reading or the last one is older than two intervals, so a reading that
fails leaves both values out, as FR-022 asks. A gauge that is set would go on sending its
last value.

**Rationale**: both numbers are facts of the database every instance shares, so every
instance sends the same and a collector does not add them up (FR-023). Reading on a timer
keeps the two queries off every run's path. Settling a row writes to the index of threads
what a reading of the list writes; it takes no lease, changes no thread, and contacts
nothing. The spec's FR-022 said "change nothing", which the settling is not, so its wording
is made exact in this plan's pull request (spec, "Plan review").

**Alternatives considered**: counting only the rows already settled. A deployment that is
driven through the API alone, with no console open, never reads the list, and its count
would stay too low. Reading in the gauge's callback: the callback runs in the exporter's
thread, where the pool's connections cannot be awaited.

## R11. What is recorded when, and what is dropped

**Decision**: every trace is recorded (FR-006, owner's answer); the sampler is `ALWAYS_ON`
and no setting changes it. Spans are exported in batches by the SDK's own thread; when the
queue of 2,048 is full, new spans are dropped, which bounds the memory (FR-036). A failed
export is logged by the SDK in its own words. At shutdown each provider is given the
export's time limit to send what it holds (FR-037).

A thread's id is a value of the run's span and of nothing else (FR-032, owner's answer). A
request's span does not carry it, since its route is the declared path with the
placeholder, and no metric does.

**Rationale**: an operator who is told of a run has to find it, so every run is there. A
collector that is down for an hour costs the spans of that hour and nothing of the service.
