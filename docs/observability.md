# Observability

Beside its log, the service tells an operator how it is doing through traces and metrics of what it does, sent to a collector. They are off unless a collector is configured.

## Traces and metrics

The service records what the orchestrator does as [OpenTelemetry](https://opentelemetry.io) traces and metrics and sends them to a collector over OTLP/HTTP. Two settings configure it:

| Variable | Default | What it is |
|---|---|---|
| `FREYJA_OTEL_ENDPOINT` | unset | The collector's address, such as `http://127.0.0.1:4318`. Traces go to its `/v1/traces` and metrics to its `/v1/metrics`. Unset, telemetry is off. |
| `FREYJA_OTEL_SERVICE_NAME` | `freyja` | The service's name in what is sent (`service.name`): letters, digits, dots, hyphens, and underscores, 64 at most. |

Off means off: nothing of OpenTelemetry's SDK is made, no thread is started, and nothing is connected to. The address is `http` or `https`, a host, and at most a port and a path. One with a user name, a password, a query, or a fragment stops startup, with an error that does not repeat it. The service's own startup line says that telemetry is on and not where it is sent.

### What is sent

One trace per run, with a span for the run and, under it, one for each call of the model and each call of a tool. A call a subagent makes is under the `task` call that started the subagent.

| Span | Attributes |
|---|---|
| `freyja.run` | `freyja.run.id`, `freyja.thread.id`, `freyja.run.mode` (`run`, `resume`, or `background`, the run that delivers an agent task's outcome), `freyja.run.console` (a run that goes on without a reader, which is every run the console or another agent starts), `freyja.run.strict` (a run on a thread another agent started), `freyja.profile.id`, `freyja.profile.source`, `freyja.model.provider`, `freyja.model.name`, `freyja.run.outcome` (`end`, `interrupt`, `error`, `stopped`, or `cancelled` when its reader left or the service stopped it), and with `error` `freyja.error.code` (`model_error`, `no_usable_model`, `step_limit`, `internal`, `approval_now_required`) |
| `freyja.model_call` | `freyja.model.provider`, `freyja.model.name`, `freyja.call.outcome` (`ok`, `error`, `cancelled`), and with `error` `error.type`, the exception's class name |
| `freyja.tool_call` | `freyja.tool.name`, `freyja.tool.classification` (`read_only`, `workspace`, `gated`), `freyja.tool.server` for a tool server's tool, `freyja.tool.agent` for a hand-off to an agent or a cancel, `freyja.call.outcome` (`ok`, `error`, `interrupted` when a subagent's proposal paused the run inside the call, `cancelled`), and `error.type` when the call raised |

A span that ended in an error has the error status, described by the run's error code or the exception's class name. No span has an event or a link. A gated call is recorded when it runs, which is after a person approved it; a rejected one never runs and has no span. A model call the libraries make for themselves, such as the one that summarizes a long conversation, has none either.

| Metric | Kind | Attributes |
|---|---|---|
| `freyja.runs.started` | counter | `freyja.run.mode`, `freyja.run.console`, `freyja.run.strict` |
| `freyja.runs.ended` | counter | the same, with `freyja.run.outcome` and `freyja.error.code` |
| `freyja.run.duration` | histogram, seconds | `freyja.run.mode`, `freyja.run.outcome` |
| `freyja.model.calls` | counter | `freyja.model.provider`, `freyja.model.name`, `freyja.call.outcome` |
| `freyja.model.errors` | counter | `freyja.error.code`: the runs that ended with `model_error` or `no_usable_model` |
| `freyja.tool.calls` | counter | `freyja.tool.name`, `freyja.tool.classification`, `freyja.tool.server`, `freyja.tool.agent`, `freyja.call.outcome` |
| `freyja.worker.round.duration` | histogram, seconds | none: one round of the worker that watches pending agent tasks |
| `freyja.worker.threads_waiting` | gauge | none: the threads that round found with an agent task's outcome to deliver or a delivery to try again |

No metric names a run or a thread. Every count is of one instance; a collector adds them up.

### Names, never content

An attribute is an id the service made, a name the deployment configured, one of the service's own words, or an exception's class name. A message, a tool's arguments or its result, a model's reply, an error's text, an agent's address, an API key, and a caller's token are never sent. A tool's name comes from the tools the orchestrator was built with: a call of a tool that a model made up is named `unknown`, not by what the model wrote. An LLM profile is named by its id, not by the name an operator gave it. A thread another agent started is named by its id, as in the log: telemetry goes to the operator, not to the caller.

### What telemetry leaves alone

- **What a model, a tool server, and an agent receive.** Some of the libraries the service uses add the current trace's id to what they send whenever OpenTelemetry has a current span: the MCP SDK to a request, a model provider's client to a header. Freyja keeps which span is current to itself and never sets OpenTelemetry's, so turning telemetry on changes nothing that is sent to a model, a tool server, or an agent. A delegate's own traces are therefore not joined to Freyja's.
- **The libraries' own telemetry.** Freyja's tracer and meter come from providers of its own, which are not registered as the process's global ones. FastAPI, the MCP and A2A SDKs, and a model provider's client can each trace themselves through the global provider; they stay as they were, and nothing they would record is sent with Freyja's. A deployment that turns their telemetry on through their own variables, such as `FASTAPI_OTEL_AUTO_CONFIGURE`, gets a second telemetry that is theirs: what it holds is for those libraries to say, and the rule above does not cover it.

### The collector

Spans are sent in batches, every five seconds at most, and metrics once a minute. An export takes ten seconds at most. A collector that does not answer costs a run nothing: exports are made off the path of a run, and a failed one is logged by the SDK in its own words, which name the collector's address. Recording itself never fails a run either: a span or a count that cannot be made is left out, the log says so once with the kind of failure, and the run, the model call, or the tool call it was for goes on as it would with telemetry off. When the service stops, what it still holds is sent after the last run has ended, within that limit for traces and again for metrics.

Freyja offers no credential for a collector: no setting holds one, and none is stored. Run the collector where the service reaches it without one, such as on the same host or in the same pod.

Freyja gives the SDK the endpoint, the service's name, the time limits, the batching, the sampler (every run is recorded), and the limits of a span, so none of these is taken from an `OTEL_*` variable: `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES`, `OTEL_TRACES_SAMPLER`, `OTEL_EXPORTER_OTLP_TIMEOUT`, `OTEL_EXPORTER_OTLP_COMPRESSION`, the `OTEL_BSP_*` and `OTEL_METRIC_EXPORT_*` variables, and the span limits change nothing. The SDK and its exporter still read these by themselves, and Freyja does not stop them:

- `OTEL_SDK_DISABLED=true` makes the SDK record nothing, also with the endpoint set.
- `OTEL_EXPORTER_OTLP_HEADERS`, and the same for one signal (`OTEL_EXPORTER_OTLP_TRACES_HEADERS`, `OTEL_EXPORTER_OTLP_METRICS_HEADERS`), are added to every export request. A header set there is the exporter's to send; Freyja does not read it, and its tests do not cover it.
- `OTEL_EXPORTER_OTLP_CERTIFICATE`, `OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE`, and `OTEL_EXPORTER_OTLP_CLIENT_KEY`, and the same for one signal, name the files a TLS connection to the collector uses.
- `OTEL_PYTHON_EXPORTER_OTLP_HTTP_CREDENTIAL_PROVIDER`, and the same for one signal, names an installed plugin that supplies the exporter's HTTP session.
- `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` and `OTEL_EXPORTER_OTLP_METRICS_DEFAULT_HISTOGRAM_AGGREGATION` choose how metrics are written for a collector that wants them another way.
- The exporter's HTTP library reads the proxy variables (`HTTPS_PROXY`, `NO_PROXY`), `REQUESTS_CA_BUNDLE`, and a `.netrc` file.

This is the last of the docs; the [README](../README.md#docs) lists them all.
