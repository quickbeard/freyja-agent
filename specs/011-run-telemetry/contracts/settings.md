# Contract: settings

Two settings on `Settings`, documented in `.env.example` and in the README's table.

| Variable | Default | What it is |
|---|---|---|
| `FREYJA_OTEL_ENDPOINT` | unset | The collector's address, such as `http://127.0.0.1:4318`. Unset, telemetry is off: nothing is recorded, nothing is started, and nothing is connected to. A line left as `FREYJA_OTEL_ENDPOINT=` is unset. |
| `FREYJA_OTEL_SERVICE_NAME` | `freyja` | The service's name in what is sent, as `service.name`: letters, digits, dots, hyphens, and underscores, 1 to 64. |

## The address

`http` or `https`, a host, and at most a port and a path. Startup stops, with an error that
names the setting and does not repeat its value, when the address has another scheme, no
host, a port that is not one, a user name, a password, a query, or a fragment.

## What no setting holds

- A credential for the collector. The collector runs where the service reaches it without
  one, or the deployment sets what the collector's client reads from the environment by
  itself, such as `OTEL_EXPORTER_OTLP_HEADERS`, which Freyja neither reads nor tests.
- A share of traces to keep. Every trace is recorded.
- Whether a trace's id is sent to a delegate, or taken from a client. Neither happens.

## What the startup log says

One line that telemetry is on, when it is. The line does not hold the address.
