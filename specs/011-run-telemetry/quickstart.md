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
and start a run (adding a profile needs `FREYJA_SECRET_KEY` set):

```bash
curl -s -X POST localhost:8000/llm/profiles -H 'content-type: application/json' -d '{
  "name": "Stand-in", "provider": "openai", "model": "call-tools",
  "base_url": "http://127.0.0.1:9997/v1", "api_key": "sk-recognizable-key-0001",
  "responses_api": false}'
curl -s -X POST localhost:8000/llm/profiles/<id>/activate
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

## Walked on 2026-11-27

Every step was walked on this branch against `otel/opentelemetry-collector` 0.166.0 with the
configuration of step 2, the stand-in model endpoint, and a database of its own on the
local Postgres. The service, the stand-in, and the collector were on spare ports (8011,
9981, 4328) and not the ones written above; nothing else differed.

| Step | Seen |
|---|---|
| 1 | No line about telemetry in the startup log, `/healthz` answered, and the collector received nothing. |
| 3 | The startup log said `Telemetry is on: traces and metrics are sent to FREYJA_OTEL_ENDPOINT`, without the address. The run was one trace: `POST /threads/{thread_id}/runs`, under it `freyja.run` with `freyja.thread.id` `quick-1`, the profile's id, `openai` and `call-tools`, and the outcome `interrupt`, and under that one `freyja.model_call` with the caller `agent`. No span for `send_notification`. Neither `notify the team` nor the key was anywhere in what the collector printed. After the approval, a second trace: `POST /threads/{thread_id}/resume`, `freyja.run` with the mode `resume` and the outcome `end`, and `freyja.tool_call` with `send_notification`, `gated`, `ok`. |
| 4 | `GET /threads` with 200; `GET unmatched` with 404, and neither `no/such` nor `secret` anywhere; nothing for `/healthz`. |
| 5 | `POST /llm/profiles/{profile_id}/test` with two `freyja.model_call` spans under it, each with the caller `test`, `openai`, and `call-tools`. |
| 6 | At the next export: `freyja.runs.started` 2 for `run` and 1 for `resume`; `freyja.runs.ended` 2 with `interrupt` and 1 with `end`; `freyja.model.calls` 3 for `agent` and 2 for `test`; `freyja.tool.calls` 1; `http.server.request.duration` one series a route, with the count of its requests; `freyja.threads.awaiting_decision` 1, for the one thread left undecided; `freyja.outcomes.oldest_undelivered.age` 0. |
| 7 | With the collector stopped, a run ended in 0.04 seconds with the same events as before, and the log had the exporter's own warning, which names the collector's address. With the collector started again, the next run's trace arrived, and so did the trace of the run made while it was down: the exporter was still trying it again within its time limit. |
| 8 | The service did not start. The error named `otel_endpoint` and said what an address may be; neither `hunter2` nor the address was in the output. |
