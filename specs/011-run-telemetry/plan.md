# Implementation Plan: Telemetry of Runs

**Branch**: `011-run-telemetry` | **Date**: 2026-11-27 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/011-run-telemetry/spec.md`

## Summary

An operator has only the log to learn how the service is doing. This feature sends traces
and metrics of what the service does to a collector the deployment names, and does nothing
when none is named: one span for every request, run, model call, and tool call, each under
the one that caused it; counts and durations of the same; and two values of the work that
waits. What is sent holds names and never content, and recording never changes what a run
does.

Part of it was written before the spec, on the local branch `telemetry-full`: the module
that records (`runtime/telemetry.py`), the middleware that records a run's model calls and
tool calls (`agent/telemetry.py`), the run's span in the run loop, the worker's round, the
two settings, and their tests. The plan keeps that design, because the research confirms
each of its choices against the spec ([research.md](research.md) R1 to R5), and adds what
the spec asks beyond it:

- a span and a duration for every request, on both listeners, by one ASGI middleware, with
  the run's span under the request's (R6, R7);
- the model calls a library makes by itself, recorded where every request to a model
  passes, the guarded model (R8);
- the model calls of a profile's test, recorded in the test (R9);
- who made a model call, as one more named value (R8, R9);
- the two waiting values, read on a timer from the stores every instance shares (R10).

No table, migration, or route is added, and nothing in `web/`. Three packages that were
already installed through `fastapi[standard]` become the service's own dependencies.

Design details are in [research.md](research.md); what a span and a metric are made of in
[data-model.md](data-model.md); every span, metric, and named value a collector receives in
[contracts/telemetry.md](contracts/telemetry.md); the two settings in
[contracts/settings.md](contracts/settings.md); the manual walk-through in
[quickstart.md](quickstart.md).

## Technical Context

**Language/Version**: Python 3.12 (`.python-version`), managed by uv with the committed
`uv.lock`, src layout. The web app is not touched.

**Primary Dependencies**: opentelemetry-api 1.45.2, opentelemetry-sdk 1.45.2, and
opentelemetry-exporter-otlp-proto-http 1.45.1, which `uv.lock` already installs for
`fastapi[standard]` and which `pyproject.toml` now names as the service's own
(`>=1.45,<2`); the lock file installs nothing new. Unchanged: fastapi 0.162.0 on starlette
1.10.0, deepagents 0.7.32 on langchain 1.4.7 and langgraph 1.2.18, psycopg 3.3.6.

**Storage**: none added. The two waiting values are read from tables that exist:
`freyja_threads` (feature 007) through `ThreadIndex.counts`, and `freyja_agent_tasks`
(feature 003) through one new read of the store, with no migration (R10).

**Testing**: pytest 9 with pytest-asyncio, the scripted chat model, in-memory persistence,
and the stand-ins. Telemetry is read in the test process through the SDK's in-memory
exporter and reader, built through the same seam the service uses (`Telemetry.start`), and
once through a stand-in collector on the loopback interface to prove the two paths. The new
read of the agent tasks has an opt-in test under `tests/integration/`. No network.

**Target Platform**: the FastAPI service on Linux, as the one runtime of every environment.

**Project Type**: web service. The console under `web/` shows nothing of this feature.

**Performance Goals**: recording adds no await to a run's path and no request to anything;
an export is made off the event loop by the SDK's own threads. A run with a collector that
never answers produces the same events in the same order as with telemetry off (SC-005).

**Constraints**: off by default with nothing of the SDK made (FR-001); names and never
content (FR-030 to FR-033); never fails or repeats a step (FR-034, FR-035); nothing a
model, a tool server, an agent, or a client receives changes (FR-038, FR-039); the
orchestrator is built the same way on and off (FR-040); no global provider and no current
span of OpenTelemetry's (FR-041).

**Scale/Scope**: a run has one span per model call and tool call, tens at most with the
default step limit; a span has at most 32 short values and no event or link. Spans wait in a
queue of 2,048 and are dropped past it; metrics are sent once a minute. Every value that
tells a metric's counts apart comes from a set the deployment bounds (FR-018).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Touched | How the plan holds it | Before research | After design |
|---|---|---|---|---|
| I. Human approval | Yes | The middleware wraps a tool call and so stands around a gated action that runs. It reads only which tool the call is for, never decides whether the call runs, and sits inside the approval gate: a call it sees was approved, and a rejected call never reaches it (FR-012). Recording is done inside `_safely`, so a span that cannot be started or ended raises nothing into the step, and no step is run again for telemetry's sake (FR-035, R4). `tests/test_telemetry.py` drives recording that fails at each point around an approved action and counts that the action ran once. | PASS | PASS |
| II. Protocol-based delegation | Yes | Nothing is added to what goes over a protocol: no trace's id reaches a tool server, an agent, or a model (FR-038, owner's answer). The service keeps which span is current in a context variable of its own and never sets OpenTelemetry's, because the MCP SDK and a provider's client add the current trace's id to what they send by themselves (R3). No agent framework is imported. | PASS | PASS |
| III. One runtime | Yes | One code path on and off: the middleware is on every build and the request middleware on every application, with a tracer and a meter that do nothing when telemetry is off (FR-040, R2). Telemetry is built in `Runtime.assemble`, which every environment and the tests enter. The console is not touched. | PASS | PASS |
| IV. Configuration through settings | Yes | Two settings on `Settings`, `FREYJA_OTEL_ENDPOINT` and `FREYJA_OTEL_SERVICE_NAME`, documented in `.env.example`, with the safe default: unset, which is off. No credential is taken or stored (FR-004). What the SDK is given is given in code, so that its own `OTEL_*` variables do not configure the service behind `Settings`; the variables its exporter still reads by itself are listed in the documentation (R5). | PASS | PASS |
| V. Quality gates | Yes | Ruff, pyright over `src/` and `tests/`, pytest with the coverage floor, gitleaks, Trivy, and Semgrep hold as they are. New behavior ships with tests; the proof that no content is sent (`tests/test_telemetry_nowhere.py`) is extended to every new span, metric, and value. The three packages are within what Trivy already scans in `uv.lock`. | PASS | PASS |
| VI. Deterministic guardrails | Yes | What may be sent is a fixed list written in one module and held by a test that fails on a value it does not know (FR-033). A tool's name comes from the build's catalog and a route's from the application's routes, never from what a model or a client wrote (FR-009, FR-024). No model judgment is on any path of this feature. | PASS | PASS |
| Technology constraints | Yes | Python 3.12, uv, FastAPI, Deep Agents over LangGraph, Postgres, Ruff, pyright: unchanged. OpenTelemetry is a library the process uses, not a second runtime. | PASS | PASS |

No deviation to justify.

## Project Structure

### Documentation (this feature)

```text
specs/011-run-telemetry/
├── plan.md              # This file
├── research.md          # Phase 0: R1 to R11
├── data-model.md        # Phase 1: what a span, a metric, and a waiting value hold
├── quickstart.md        # Phase 1: the walk-through against a collector stand-in
├── contracts/
│   ├── telemetry.md     # every span, metric, and named value a collector receives
│   └── settings.md      # the two settings
├── checklists/
│   └── requirements.md  # from specify
└── tasks.md             # from /speckit-tasks
```

### Source Code (repository root)

```text
src/freyja_agent/
├── config.py                     # FREYJA_OTEL_ENDPOINT, FREYJA_OTEL_SERVICE_NAME (existing work)
├── runtime/
│   ├── telemetry.py              # existing work; adds the request's span and duration, who made a
│   │                             #   model call, a model call outside the middleware, the two
│   │                             #   waiting values, and the run's span under a request's
│   ├── waiting.py                # new: reads the two waiting values on a timer (R10)
│   ├── agent_tasks.py            # one new read: the age of the oldest outcome not delivered
│   ├── runtime.py                # builds telemetry, hands it on, starts and stops the reading
│   └── unattended.py             # `stopped()`, asked by the run as it ends (existing work)
├── agent/
│   ├── telemetry.py              # TelemetryMiddleware (existing work)
│   └── factory.py                # the middleware on both builds and the subagent (existing work)
├── llm/
│   ├── guard.py                  # the guarded model also tells telemetry of a request to the
│   │                             #   model that nothing else recorded (R8)
│   ├── active.py                 # names the provider and the model of a run (existing work);
│   │                             #   hands the recording to the guard
│   └── check.py                  # a profile's test records its two calls (R9)
├── api/
│   ├── telemetry.py              # new: RequestTelemetry, the ASGI middleware (R6)
│   ├── app.py                    # adds it, outside AllowedHosts
│   ├── a2a_listener.py           # adds it to the outside listener's application
│   ├── streaming.py              # the run's span (existing work)
│   └── llm.py                    # hands a test what it is recorded with
└── delegates/
    └── delegation_worker.py      # a round's duration and what it found (existing work)

tests/
├── telemetry_capture.py          # telemetry kept in memory, built through the seam (existing work)
├── test_telemetry.py             # existing work; adds the model calls of a library and of a test
├── test_telemetry_requests.py    # new: User Story 4
├── test_telemetry_waiting.py     # new: User Story 3
├── test_telemetry_nowhere.py     # existing work; extended to every new span, metric, and value
└── integration/
    └── test_agent_tasks_postgres.py  # the new read against a real Postgres

docs/observability.md             # every span, metric, and value; what is never sent
.env.example, README.md, AGENTS.md and the nested AGENTS.md files
```

**Structure Decision**: single project in the existing src layout. `runtime/telemetry.py`
stays the one module that imports OpenTelemetry and the one place a span's or a metric's
name or value is written, so that what leaves the service can be read in one file; every
other module calls it. Two modules are new, each where its neighbors are: the request
middleware beside `api/hosts.py`, and the reading of the waiting values beside the stores
it reads.

## Complexity Tracking

None. The Constitution Check passed without deviations before research and after design.

## Implementation Notes

- **The work that exists.** The commit on `telemetry-full` is brought to this branch first,
  without `/readyz`, which is a request of its own (PR 163). Its tests pass before anything
  is added. What it lacks against the spec is listed in the Summary; one thing it has that
  the spec changes: a run's span always started a trace of its own, and now stands under
  its request's (FR-013).
- **`/readyz`.** The spec names the routes that say whether the service is up or ready
  (FR-027). `/readyz` is added by PR 163. The request middleware leaves out both paths by
  name, so it is right whichever of the two is merged first, and the test of `/readyz` is
  added once that route is on `main`.
- **One pull request** holds the spec, the plan, the tasks, and the code, as the owner chose
  for feature 010.
