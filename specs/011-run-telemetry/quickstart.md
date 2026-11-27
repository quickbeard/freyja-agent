# Quickstart: Telemetry of Runs

A walk-through that shows the feature end to end against a collector on this machine. It
needs the local Postgres (`docker compose up -d postgres`), a `.env` copied from
`.env.example`, and Docker for the collector. No model provider's key is needed: the
stand-in model endpoint answers.

What each span and metric holds is in [contracts/telemetry.md](contracts/telemetry.md), and
the two settings in [contracts/settings.md](contracts/settings.md).

## 1. Off by default

Start the service with nothing set for telemetry:

```bash
uv run --env-file .env freyja-api
```

Expected: no line about telemetry in the startup log, and no connection to port 4318.
`uv run pytest tests/test_telemetry.py -k off` shows the same in the suite: no SDK is
loaded and no thread is started.

## 2. A collector that prints what it receives

```bash
cat > /tmp/otelcol.yaml <<'YAML'
receivers:
  otlp:
    protocols:
      http:
        endpoint: 127.0.0.1:4318
exporters:
  debug:
    verbosity: detailed
service:
  pipelines:
    traces: {receivers: [otlp], exporters: [debug]}
    metrics: {receivers: [otlp], exporters: [debug]}
YAML
docker run --rm --network host -v /tmp/otelcol.yaml:/etc/otelcol/config.yaml \
  otel/opentelemetry-collector:latest
```

## 3. Turn telemetry on and follow one run

In another terminal, serve the stand-in model and start the service with the collector
named:

```bash
uv run python -m tests.llm_stub --http 9997 &
FREYJA_OTEL_ENDPOINT=http://127.0.0.1:4318 uv run --env-file .env freyja-api
```

Add an LLM profile for `http://127.0.0.1:9997/v1` with the model `call-tools`, activate it,
and start a run:

```bash
curl -N -X POST localhost:8000/threads/quick-1/runs \
  -H 'content-type: application/json' -d '{"message": "notify the team"}'
```

Expected, in the collector's output within five seconds of the run's pause:

- one trace with the span `POST /threads/{thread_id}/runs`, and under it `freyja.run` with
  `freyja.thread.id` `quick-1` and `freyja.run.outcome` `interrupt`;
- under the run, one `freyja.model_call` with `freyja.model.caller` `agent`;
- no span for `send_notification`: it waits for a decision;
- nowhere the words `notify the team`.

Approve the proposal (`POST /threads/quick-1/resume`). Expected: a second trace, for the
resume, with one `freyja.tool_call` whose `freyja.tool.name` is `send_notification` and
whose `freyja.tool.classification` is `gated`.

## 4. Requests

```bash
curl -s localhost:8000/threads > /dev/null
curl -s localhost:8000/no/such/path?secret=1 > /dev/null
curl -s localhost:8000/healthz > /dev/null
```

Expected: a span `GET /threads` with status 200; a span `GET unmatched` with status 404,
and `no/such/path` and `secret` nowhere; nothing for `/healthz`.

## 5. A profile's test

Press Test on the profile in the console's Models section, or
`curl -X POST localhost:8000/llm/profiles/<id>/test`. Expected: a span
`POST /llm/profiles/{profile_id}/test` with two `freyja.model_call` spans under it, each
with `freyja.model.caller` `test`.

## 6. Counts and the work that waits

Wait for the next export of metrics, a minute at most. Expected in the collector's output:
`freyja.runs.started` and `freyja.runs.ended` with what the steps above drove,
`http.server.request.duration` by route, and `freyja.threads.awaiting_decision` equal to
the number of threads with an undecided proposal. Start another run that pauses and do not
decide it: the value is one higher at the export after the next.

## 7. A collector that is down

Stop the collector and start a run. Expected: the run's events arrive as before; the
service's log has the SDK's own line that an export failed; nothing else changes. Start the
collector again: the next runs arrive.

## 8. An address that carries a credential

```bash
FREYJA_OTEL_ENDPOINT=http://user:hunter2@127.0.0.1:4318 uv run --env-file .env freyja-api
```

Expected: the service does not start, and the error names `FREYJA_OTEL_ENDPOINT` without
`hunter2` and without the address.

## The suite

```bash
uv run pytest tests/test_telemetry.py tests/test_telemetry_requests.py \
  tests/test_telemetry_waiting.py tests/test_telemetry_nowhere.py
```
