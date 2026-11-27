"""What the tests of telemetry share: telemetry that keeps what it records in memory.

``Captured`` builds the production ``Telemetry`` around the SDK's own in-memory span exporter
and metric reader, which is the seam ``runtime/telemetry.py`` offers, and reads back every
span and every metric point as plain values. ``texts`` is everything a collector would be
sent, for the proof that none of it is content (``tests/test_telemetry_nowhere.py``).
"""

from dataclasses import dataclass
from typing import Any

from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from freyja_agent.runtime.telemetry import SCOPE, Telemetry

SERVICE = "freyja-test"


@dataclass(frozen=True)
class Point:
    """One point of a metric: its attributes and its value (a histogram's count and sum)."""

    metric: str
    attributes: dict[str, Any]
    value: float
    total: float | None = None


class Captured:
    """Telemetry that is on, with everything it records kept to be read."""

    def __init__(self, *, batch: bool = False) -> None:
        self.exporter = InMemorySpanExporter()
        self.reader = InMemoryMetricReader()
        self.telemetry = Telemetry.start(
            service_name=SERVICE,
            span_exporter=self.exporter,
            metric_reader=self.reader,
            batch=batch,
        )

    def spans(self, name: str | None = None) -> list[ReadableSpan]:
        """The spans that ended, in the order they ended; those named ``name`` when given."""
        spans = list(self.exporter.get_finished_spans())
        return [span for span in spans if name is None or span.name == name]

    def attributes(self, name: str) -> list[dict[str, Any]]:
        return [dict(span.attributes or {}) for span in self.spans(name)]

    def points(self, metric: str | None = None) -> list[Point]:
        """Every point of every metric as it stands now; those of ``metric`` when given."""
        data = self.reader.get_metrics_data()
        found: list[Point] = []
        for resource in data.resource_metrics if data is not None else ():
            for scope in resource.scope_metrics:
                assert scope.scope.name == SCOPE
                for item in scope.metrics:
                    # A sum's points, a gauge's, and a histogram's are types of their own.
                    points: Any = item.data.data_points
                    for point in points:
                        attributes = dict(point.attributes or {})
                        if hasattr(point, "bucket_counts"):
                            found.append(Point(item.name, attributes, point.count, point.sum))
                        else:
                            found.append(Point(item.name, attributes, point.value))
        return [point for point in found if metric is None or point.metric == metric]

    def count(self, metric: str, **attributes: Any) -> float:
        """The sum of ``metric``'s points whose attributes hold every one of ``attributes``,
        each given by its full name."""
        return sum(
            point.value
            for point in self.points(metric)
            if all(point.attributes.get(key) == value for key, value in attributes.items())
        )

    def texts(self) -> list[str]:
        """Everything a collector would be sent, as text: each span's name, attributes,
        events, links, status, and resource, and each metric's name, unit, description, and
        every attribute of every point."""
        texts: list[str] = []
        for span in self.spans():
            texts.append(span.name)
            texts.append(repr(dict(span.attributes or {})))
            texts.append(repr(span.status.status_code))
            texts.append(str(span.status.description))
            for event in span.events:
                texts.append(event.name)
                texts.append(repr(dict(event.attributes or {})))
            for link in span.links:
                texts.append(repr(dict(link.attributes or {})))
            texts.append(repr(dict(span.resource.attributes)))
            # The whole span as the SDK writes it, whatever else it holds.
            texts.append(span.to_json())
        data = self.reader.get_metrics_data()
        if data is not None:
            texts.append(data.to_json())
        return texts
