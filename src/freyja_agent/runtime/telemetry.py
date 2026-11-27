"""Traces and metrics of what the orchestrator does, sent to a collector over OTLP.

Off unless ``FREYJA_OTEL_ENDPOINT`` is set. Off, nothing of OpenTelemetry's SDK is made: the
tracer and the meter are the API's own that do nothing, no thread is started, and nothing is
connected to. On, one span is made per run, per model call, and per tool call, and a few
counts and durations are kept, and both are sent to the endpoint's ``/v1/traces`` and
``/v1/metrics``.

This module is the one place that imports OpenTelemetry. Every span's name, every metric's
name, and every attribute of either is written here, so that what leaves the service can be
read in one file:

- **Names only, never content.** An attribute is an identifier the service made (a run's and
  a thread's id), a name an operator configured (a tool, a tool server, an agent, a provider,
  a model, an LLM profile's id), one of the service's own words (a mode, an outcome, an error
  code, a classification), or an exception's class name. No message, no argument or result
  of a tool, no reply of a model, no exception's text, no address, and no key or token is
  ever one. A tool's name is taken from the tools the orchestrator was built with, never
  from what a model wrote: a call of a tool that is not among them is named ``unknown``.
  ``tests/test_telemetry_nowhere.py`` searches every exported span and metric; a new span,
  metric, or attribute is added to it.
- **Its own providers, never the process's.** The tracer and the meter come from providers
  kept here and are not registered as OpenTelemetry's global ones. The libraries the service
  uses that trace themselves through the global provider (FastAPI, the MCP and A2A SDKs, a
  model provider's client) therefore stay as they were, and none of what they would record,
  which is theirs to choose, is sent with Freyja's.
- **Its own current span, never OpenTelemetry's.** Which span a model call or a tool call
  belongs under is kept in a context variable of this module. OpenTelemetry's own current
  span is never set, so a library that writes the current trace's id into what it sends (the
  MCP SDK into a request's ``_meta``, a provider's client into a header) finds none: turning
  telemetry on changes nothing a model, a tool server, or an agent receives.
- **Never in a run's way.** Recording is done inside ``_safely``: a span or a count that
  cannot be made is left out, with one warning, and the run, the model call, or the tool
  call it was for goes on as if telemetry were off. A tool that ran is never reported as
  failed because its span could not be ended, so no step is run again for telemetry's sake.
- **Only what is passed.** The endpoint, the service's name, the time limits, the sampler,
  the limits of a span, and the batching are given to the SDK here and not left to its
  ``OTEL_*`` variables. What the SDK and its exporter still read from the environment by
  themselves is listed in ``docs/observability.md``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from opentelemetry import metrics as metrics_api
from opentelemetry import trace as trace_api
from opentelemetry.context import Context
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

logger = logging.getLogger(__name__)

# The instrumentation scope of every span and metric.
SCOPE = "freyja_agent"

# The three spans.
RUN_SPAN = "freyja.run"
MODEL_SPAN = "freyja.model_call"
TOOL_SPAN = "freyja.tool_call"

# The attributes, of spans and of metrics alike.
RUN_ID = "freyja.run.id"
THREAD_ID = "freyja.thread.id"
RUN_MODE = "freyja.run.mode"
RUN_CONSOLE = "freyja.run.console"
RUN_STRICT = "freyja.run.strict"
RUN_OUTCOME = "freyja.run.outcome"
ERROR_CODE = "freyja.error.code"
PROFILE_ID = "freyja.profile.id"
PROFILE_SOURCE = "freyja.profile.source"
MODEL_PROVIDER = "freyja.model.provider"
MODEL_NAME = "freyja.model.name"
MODEL_CALLER = "freyja.model.caller"
TOOL_NAME = "freyja.tool.name"
TOOL_CLASSIFICATION = "freyja.tool.classification"
TOOL_SERVER = "freyja.tool.server"
TOOL_AGENT = "freyja.tool.agent"
CALL_OUTCOME = "freyja.call.outcome"
# The class name of an exception, never its text.
ERROR_TYPE = "error.type"

# The metrics.
RUNS_STARTED = "freyja.runs.started"
RUNS_ENDED = "freyja.runs.ended"
RUN_DURATION = "freyja.run.duration"
MODEL_CALLS = "freyja.model.calls"
MODEL_ERRORS = "freyja.model.errors"
TOOL_CALLS = "freyja.tool.calls"
WORKER_ROUND_DURATION = "freyja.worker.round.duration"
WORKER_THREADS_WAITING = "freyja.worker.threads_waiting"

# How a run ended, beside the stream's own three (``end``, ``interrupt``, ``error``): a
# console run that a stop ended, and a run whose reader left or that the service cancelled.
STOPPED = "stopped"
CANCELLED = "cancelled"
# How a call ended.
OK = "ok"
ERROR = "error"
# A tool call that paused the run: a subagent's proposal waits for a person.
INTERRUPTED = "interrupted"
# What stands for a tool, and its classification, that the orchestrator was not built with.
UNKNOWN = "unknown"
# Who made a call of the model: the orchestrator or a subagent, through the middleware; a
# library by itself within a run, such as the one that summarizes a long conversation; or a
# profile's test.
AGENT = "agent"
LIBRARY = "library"
TEST = "test"

# The error codes of a run that are the model's (``api/streaming.py``).
MODEL_ERROR_CODES = frozenset({"model_error", "no_usable_model"})

# Seconds: a run takes from under a second to many minutes, a worker's round less.
_RUN_BUCKETS = (0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1800, 3600)
_ROUND_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)

# What the SDK is given, so that none of it is left to an ``OTEL_*`` variable.
EXPORT_TIMEOUT_SECONDS = 10.0
SPAN_BATCH_DELAY_MILLIS = 5000.0
SPAN_QUEUE_SIZE = 2048
SPAN_BATCH_SIZE = 512
METRIC_INTERVAL_MILLIS = 60000.0
# A span of Freyja's has a dozen short attributes and no event or link.
SPAN_ATTRIBUTES = 32
SPAN_ATTRIBUTE_LENGTH = 256

# The most characters a value that is text has: a name longer than that is cut (the SDK holds
# a span's values to it; a metric's are cut in ``_present``).
VALUE_LENGTH = SPAN_ATTRIBUTE_LENGTH

RunMode = Literal["run", "resume", "background"]
ModelCaller = Literal["agent", "library", "test"]
ScopeKind = Literal["request", "run", "call"]
Attribute = str | bool | int | float
T = TypeVar("T")


@dataclass(frozen=True)
class ToolLabels:
    """What a tool call's span and count say of its tool: names from the build's catalog."""

    name: str
    classification: str
    # The tool server that provides it, or the agent it sends to, by its configured name.
    server: str | None = None
    agent: str | None = None


UNKNOWN_TOOL = ToolLabels(name=UNKNOWN, classification=UNKNOWN)


@dataclass(frozen=True)
class _Scope:
    """The span a span started now belongs under, and the model of the run it is part of.
    ``span`` is None where the span could not be started."""

    span: Span | None
    provider: str | None = None
    model: str | None = None
    # What the span is of: a request, a run, or a call of the model or of a tool.
    kind: ScopeKind = "call"
    # Whether this is a run or something under one.
    in_run: bool = False
    # Whether this is a call of the model that is being recorded, so that a request to the
    # model made inside it is not recorded a second time.
    model_call: bool = False


# Kept apart from OpenTelemetry's own current span on purpose (see the module's docstring).
_CURRENT: ContextVar[_Scope | None] = ContextVar("freyja_telemetry_scope", default=None)


def _present(attributes: dict[str, Attribute | None]) -> dict[str, Attribute]:
    """The values that are there, a text among them cut to ``VALUE_LENGTH``."""
    return {
        key: value[:VALUE_LENGTH] if isinstance(value, str) else value
        for key, value in attributes.items()
        if value is not None
    }


def _under(scope: _Scope | None) -> Context:
    """The context a span starts in: under ``scope``'s span, or as the root of a new trace.

    Never the ambient context, so that a span of Freyja's is neither made a child of a
    library's span nor found by one.
    """
    if scope is None or scope.span is None:
        return Context()
    return trace_api.set_span_in_context(scope.span, Context())


class Call:
    """One model call or tool call under way. ``outcome`` is said by whoever makes the call;
    a call left by an exception nobody named is an ``error`` of that exception's class."""

    def __init__(self) -> None:
        self.outcome: str | None = None


def _left(error: BaseException | None) -> tuple[str, str | None]:
    """How a call that ended with ``error`` ended, and the error's class when it is one."""
    if error is None:
        return OK, None
    if isinstance(error, asyncio.CancelledError | GeneratorExit):
        # Given up: by a time limit, by a reader that left, or by the service stopping.
        return CANCELLED, None
    return ERROR, type(error).__name__


class ModelRequest:
    """One request to the model that nothing else records: made within a run, by a library
    that asks the model by itself. Started when made, ended once by ``end``.

    The model's own methods call it, two of which are generators, so it sets the current
    scope only when asked to (``enter``): a value set across a ``yield`` could not be taken
    back from the context of whoever reads next.
    """

    def __init__(self, telemetry: Telemetry, parent: _Scope) -> None:
        self._telemetry = telemetry
        self._parent = parent
        self._attributes = _present(
            {MODEL_CALLER: LIBRARY, MODEL_PROVIDER: parent.provider, MODEL_NAME: parent.model}
        )
        self._span = telemetry.call_started(MODEL_SPAN, SpanKind.CLIENT, self._attributes, parent)
        self._token: Any = None
        self._ended = False

    def enter(self) -> None:
        """Make this the call in progress, for a request that is awaited in one piece, so
        that a model which streams through its own methods is recorded once."""
        parent = self._parent
        self._token = _CURRENT.set(
            _Scope(self._span, parent.provider, parent.model, in_run=True, model_call=True)
        )

    def end(self, error: BaseException | None = None) -> None:
        if self._ended:
            return
        self._ended = True
        if self._token is not None:
            token, self._token = self._token, None
            # Ended in another context than it was entered in: nothing to take back there.
            with suppress(ValueError):
                _CURRENT.reset(token)
        outcome, failed = _left(error)
        self._telemetry.call_ended(
            self._span, self._attributes, self._telemetry.model_calls, outcome, failed
        )


class RunSpan:
    """One run, from its first event to its last. Ended once, by ``end``."""

    def __init__(self, telemetry: Telemetry, scope: _Scope, labels: dict[str, Attribute]) -> None:
        self._telemetry = telemetry
        self._scope = scope
        self._labels = labels
        self._started = time.monotonic()
        self._ended = False

    async def within(self, stream: AsyncIterator[Any]) -> AsyncIterator[Any]:
        """``stream``, with this run as what a call belongs under while each item is made.

        The run's span is the current one only while the stream is asked for its next item,
        which is when the graph starts the steps that call the model and the tools, and
        never across a ``yield``: an async generator runs in the context of whoever reads
        it, and a value set across a ``yield`` could not be taken back from another.
        """
        iterator = stream.__aiter__()
        try:
            while True:
                token = _CURRENT.set(self._scope)
                try:
                    item = await iterator.__anext__()
                except StopAsyncIteration:
                    return
                finally:
                    _CURRENT.reset(token)
                yield item
        finally:
            close = getattr(iterator, "aclose", None)
            if close is not None:
                await close()

    def end(self, outcome: str, code: str | None = None) -> None:
        """End the run's span and count the run: ``outcome`` is one of the stream's three
        ends, ``stopped``, or ``cancelled``, and ``code`` the error's code."""
        if self._ended:
            return
        self._ended = True
        self._telemetry.run_ended(
            self._scope.span, self._labels, outcome, code, time.monotonic() - self._started
        )


class Telemetry:
    """The tracer, the meter, and every span and count the service makes with them."""

    def __init__(
        self,
        tracer: trace_api.Tracer,
        meter: metrics_api.Meter,
        *,
        providers: tuple[Any, ...] = (),
    ) -> None:
        self.tracer = tracer
        self.meter = meter
        # The SDK's providers, to flush and shut down; none when off.
        self._providers = providers
        # Whether a failure to record was already said in the log.
        self._warned = False
        self._runs_started = meter.create_counter(
            RUNS_STARTED, unit="{run}", description="Runs started on this instance."
        )
        self._runs_ended = meter.create_counter(
            RUNS_ENDED, unit="{run}", description="Runs ended on this instance, by how."
        )
        self._run_seconds = meter.create_histogram(
            RUN_DURATION,
            unit="s",
            description="How long a run took, from its first event to its last.",
            explicit_bucket_boundaries_advisory=_RUN_BUCKETS,
        )
        self.model_calls = meter.create_counter(
            MODEL_CALLS,
            unit="{call}",
            description="Calls of the model, by who made each and how it ended.",
        )
        self._model_errors = meter.create_counter(
            MODEL_ERRORS,
            unit="{run}",
            description="Runs the model ended, by the error code the run ended with.",
        )
        self._tool_calls = meter.create_counter(
            TOOL_CALLS, unit="{call}", description="Calls of a tool, by how each ended."
        )
        self._round_seconds = meter.create_histogram(
            WORKER_ROUND_DURATION,
            unit="s",
            description="How long one round of the delegation worker took.",
            explicit_bucket_boundaries_advisory=_ROUND_BUCKETS,
        )
        self._threads_waiting = meter.create_gauge(
            WORKER_THREADS_WAITING,
            unit="{thread}",
            description=(
                "Threads with an agent task's outcome to deliver or a delivery to try again, "
                "as the delegation worker's last round found them."
            ),
        )

    @property
    def on(self) -> bool:
        return bool(self._providers)

    @classmethod
    def off(cls) -> Telemetry:
        """Telemetry that records and sends nothing: the API's own tracer and meter."""
        return cls(
            trace_api.NoOpTracerProvider().get_tracer(SCOPE),
            metrics_api.NoOpMeterProvider().get_meter(SCOPE),
        )

    @classmethod
    def start(
        cls,
        *,
        service_name: str,
        span_exporter: Any,
        metric_reader: Any,
        batch: bool = True,
    ) -> Telemetry:
        """Telemetry that records, with spans handed to ``span_exporter`` and metrics read
        by ``metric_reader``. The seam the tests swap: they pass the SDK's in-memory two,
        and ``batch=False`` to have each span exported as it ends."""
        # Imported here, so that a service with telemetry off loads nothing of the SDK.
        from opentelemetry.sdk.metrics import AlwaysOffExemplarFilter, MeterProvider
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import SpanLimits, TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON

        # Made directly: ``Resource.create`` would add OTEL_RESOURCE_ATTRIBUTES and
        # OTEL_SERVICE_NAME from the environment.
        resource = Resource({"service.name": service_name})
        tracers = TracerProvider(
            sampler=ALWAYS_ON,
            resource=resource,
            # The lifespan shuts it down; nothing is left to an exit handler.
            shutdown_on_exit=False,
            span_limits=SpanLimits(
                max_attributes=SPAN_ATTRIBUTES,
                max_events=0,
                max_links=0,
                max_span_attributes=SPAN_ATTRIBUTES,
                max_event_attributes=0,
                max_link_attributes=0,
                max_attribute_length=SPAN_ATTRIBUTE_LENGTH,
                max_span_attribute_length=SPAN_ATTRIBUTE_LENGTH,
            ),
        )
        tracers.add_span_processor(
            BatchSpanProcessor(
                span_exporter,
                max_queue_size=SPAN_QUEUE_SIZE,
                schedule_delay_millis=SPAN_BATCH_DELAY_MILLIS,
                max_export_batch_size=SPAN_BATCH_SIZE,
                export_timeout_millis=EXPORT_TIMEOUT_SECONDS * 1000,
            )
            if batch
            else SimpleSpanProcessor(span_exporter)
        )
        meters = MeterProvider(
            metric_readers=[metric_reader],
            resource=resource,
            # No exemplar: one would put a trace's id beside a count.
            exemplar_filter=AlwaysOffExemplarFilter(),
            shutdown_on_exit=False,
        )
        return cls(tracers.get_tracer(SCOPE), meters.get_meter(SCOPE), providers=(tracers, meters))

    @classmethod
    def configured(cls, endpoint: str | None, service_name: str) -> Telemetry:
        """Telemetry as the deployment configures it: off without ``FREYJA_OTEL_ENDPOINT``,
        and otherwise sent to that collector over OTLP/HTTP."""
        if endpoint is None:
            return cls.off()
        from opentelemetry.exporter.otlp.proto.http import Compression
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        base = endpoint.rstrip("/")
        telemetry = cls.start(
            service_name=service_name,
            span_exporter=OTLPSpanExporter(
                endpoint=f"{base}/v1/traces",
                timeout=EXPORT_TIMEOUT_SECONDS,
                compression=Compression.NoCompression,
            ),
            metric_reader=PeriodicExportingMetricReader(
                OTLPMetricExporter(
                    endpoint=f"{base}/v1/metrics",
                    timeout=EXPORT_TIMEOUT_SECONDS,
                    compression=Compression.NoCompression,
                ),
                export_interval_millis=METRIC_INTERVAL_MILLIS,
                export_timeout_millis=EXPORT_TIMEOUT_SECONDS * 1000,
            ),
        )
        # The endpoint is the deployment's own configuration and is not repeated here.
        logger.info("Telemetry is on: traces and metrics are sent to FREYJA_OTEL_ENDPOINT")
        return telemetry

    def close(self) -> None:
        """Send what is still held and stop. It waits for the collector, for at most the
        export's time limit a signal, so it is called off the event loop."""
        providers, self._providers = self._providers, ()
        for provider in providers:
            try:
                provider.shutdown()
            except Exception as exc:
                logger.warning("Telemetry: shutting down failed (%s)", type(exc).__name__)

    def _safely(self, record: Callable[[], T]) -> T | None:
        """Do one piece of recording; None when it could not be done.

        Whatever a tracer, a meter, or an exporter raises ends here: the first failure is
        said once in the log, by its kind, and what the recording was for goes on.
        """
        try:
            return record()
        except Exception as exc:
            if not self._warned:
                self._warned = True
                logger.warning(
                    "Telemetry: a span or a count could not be recorded (%s). Runs are not "
                    "affected; further failures are not logged.",
                    type(exc).__name__,
                )
            return None

    # Runs.

    def run(
        self,
        *,
        thread_id: str,
        run_id: str,
        mode: RunMode,
        console: bool,
        strict: bool,
        profile_id: str,
        profile_source: str,
        provider: str | None,
        model: str | None,
    ) -> RunSpan:
        """Start a run's span and count the run as started."""
        labels: dict[str, Attribute] = {
            RUN_MODE: mode,
            RUN_CONSOLE: console,
            RUN_STRICT: strict,
        }
        attributes = _present(
            {
                **labels,
                RUN_ID: run_id,
                THREAD_ID: thread_id,
                PROFILE_ID: profile_id,
                PROFILE_SOURCE: profile_source,
                MODEL_PROVIDER: provider,
                MODEL_NAME: model,
            }
        )

        def started() -> Span:
            span = self.tracer.start_span(
                RUN_SPAN, context=_under(None), kind=SpanKind.INTERNAL, attributes=attributes
            )
            self._runs_started.add(1, labels)
            return span

        scope = _Scope(self._safely(started), provider, model, kind="run", in_run=True)
        return RunSpan(self, scope, labels)

    def run_ended(
        self,
        span: Span | None,
        labels: dict[str, Attribute],
        outcome: str,
        code: str | None,
        seconds: float,
    ) -> None:
        def ended() -> None:
            if span is not None:
                span.set_attributes(_present({RUN_OUTCOME: outcome, ERROR_CODE: code}))
                if outcome == ERROR:
                    # The code, one of the service's own words; never the error's message.
                    span.set_status(Status(StatusCode.ERROR, code))
                span.end()
            self._runs_ended.add(1, _present({**labels, RUN_OUTCOME: outcome, ERROR_CODE: code}))
            self._run_seconds.record(
                seconds, _present({RUN_MODE: labels[RUN_MODE], RUN_OUTCOME: outcome})
            )
            if code in MODEL_ERROR_CODES:
                self._model_errors.add(1, _present({ERROR_CODE: code}))

        self._safely(ended)

    # Calls.

    def call_started(
        self, name: str, kind: SpanKind, attributes: dict[str, Attribute], parent: _Scope | None
    ) -> Span | None:
        """The span of a call that starts now, under ``parent``; None where it cannot be made."""
        return self._safely(
            lambda: self.tracer.start_span(
                name, context=_under(parent), kind=kind, attributes=attributes
            )
        )

    def call_ended(
        self,
        span: Span | None,
        attributes: dict[str, Attribute],
        counter: Any,
        outcome: str,
        failed: str | None,
    ) -> None:
        """End a call's span and count the call. ``failed`` is the class of what it raised."""

        def ended() -> None:
            if span is not None:
                span.set_attributes(_present({CALL_OUTCOME: outcome, ERROR_TYPE: failed}))
                if outcome == ERROR:
                    # The exception's class, never its text.
                    span.set_status(Status(StatusCode.ERROR, failed))
                span.end()
            counter.add(1, {**attributes, CALL_OUTCOME: outcome})

        self._safely(ended)

    @contextmanager
    def _call(
        self,
        name: str,
        kind: SpanKind,
        attributes: dict[str, Attribute],
        counter: Any,
        *,
        model_call: bool = False,
    ) -> Iterator[Call]:
        parent = _CURRENT.get()
        span = self.call_started(name, kind, attributes, parent)
        call = Call()
        token = _CURRENT.set(
            _Scope(
                span,
                parent.provider if parent else None,
                parent.model if parent else None,
                in_run=parent.in_run if parent else False,
                model_call=model_call,
            )
        )
        failed: str | None = None
        try:
            yield call
        except BaseException as exc:
            if call.outcome is None:
                call.outcome, _ = _left(exc)
            if call.outcome == ERROR:
                failed = type(exc).__name__
            raise
        finally:
            _CURRENT.reset(token)
            self.call_ended(span, attributes, counter, call.outcome or OK, failed)

    def model_call(
        self,
        *,
        caller: ModelCaller = AGENT,
        provider: str | None = None,
        model: str | None = None,
    ) -> AbstractContextManager[Call]:
        """A context manager around one call of the model.

        The orchestrator's own call and a subagent's (``agent``) are of the model of the
        run they are part of. A profile's test (``test``) names what it asks itself: the
        provider, and the model when the profile is a listed one.
        """
        scope = _CURRENT.get()
        if caller != TEST and scope is not None:
            provider, model = scope.provider, scope.model
        attributes = _present({MODEL_CALLER: caller, MODEL_PROVIDER: provider, MODEL_NAME: model})
        return self._call(
            MODEL_SPAN, SpanKind.CLIENT, attributes, self.model_calls, model_call=True
        )

    def model_request(self) -> ModelRequest | None:
        """What records one request to the model that nothing else records, or None.

        The guarded model asks this for every request it is given (``llm/guard.py``). A
        request made within a run and outside a call of the model that is already being
        recorded is a library's own, such as the one that summarizes a long conversation:
        no hook of the agent sees it. Any other request is recorded by whoever makes it, or
        belongs to no run, and gets None.
        """
        scope = _CURRENT.get()
        if scope is None or not scope.in_run or scope.model_call:
            return None
        return self._safely(lambda: ModelRequest(self, scope))

    def tool_call(self, tool: ToolLabels) -> AbstractContextManager[Call]:
        """A context manager around one call of a tool, a hand-off to an agent included."""
        attributes = _present(
            {
                TOOL_NAME: tool.name,
                TOOL_CLASSIFICATION: tool.classification,
                TOOL_SERVER: tool.server,
                TOOL_AGENT: tool.agent,
            }
        )
        return self._call(TOOL_SPAN, SpanKind.INTERNAL, attributes, self._tool_calls)

    # The delegation worker.

    def worker_round(self, seconds: float) -> None:
        """One round of the delegation worker took ``seconds``."""
        self._safely(lambda: self._round_seconds.record(seconds))

    def threads_waiting(self, count: int) -> None:
        """How many threads the worker's round found with something to deliver."""
        self._safely(lambda: self._threads_waiting.set(count))
