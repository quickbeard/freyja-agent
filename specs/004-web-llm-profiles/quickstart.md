# Quickstart: Web Console Shell and LLM Profiles

Validation guide for the feature. Each scenario maps to a user story in [spec.md](spec.md). The
API shapes are in [contracts/openapi.yaml](contracts/openapi.yaml), the stream changes in
[contracts/sse-events.md](contracts/sse-events.md), the settings in
[contracts/settings.md](contracts/settings.md), and the entities in
[data-model.md](data-model.md).

The saved profiles point at `tests/llm_stub.py`, the suite's stand-in for an OpenAI-compatible
endpoint, served on loopback. It behaves by the model id it is asked for:
`refuse-conversation` answers a first message and refuses a request that carries earlier turns,
and any other id, such as `ok`, answers everything ([research R20](research.md#r20-the-quickstarts-second-model)). No second
account or key is needed.

## Prerequisites

- Everything the [001 quickstart](../001-thread-runs-api/quickstart.md) needs: Docker with
  Compose v2, `uv`, and a model provider key in `.env` for the environment's model.
- Node 20.9 or newer and `pnpm`, for the web app.
- `curl` and `jq`.

## Setup

```bash
uv sync
docker compose up -d postgres

# The secret that protects stored keys. Add the printed line to .env.
echo "FREYJA_SECRET_KEY=$(uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"

# Terminal 1: the service
uv run --env-file .env freyja-api

# Terminal 2: the stand-in model endpoint
uv run python -m tests.llm_stub --http 9997      # http://127.0.0.1:9997/v1

# Terminal 3: the web app (add -p <port> if 3000 is taken)
cd web && pnpm install && pnpm dev               # http://localhost:3000
```

`FREYJA_API_URL` defaults to `http://127.0.0.1:8000`; set it before `pnpm dev` if the service
listens elsewhere.

## 1. Open the console (User Story 1)

Open `http://localhost:3000`.

- The address changes to `/llm`. A sidebar on the left lists LLM and Agents, with LLM marked.
- One card, **Environment**, with the provider and model from `.env`, the last four characters
  of its key, and the marks "from the environment" and "active". It has Test, and no Edit or
  Delete.
- Agents opens a page saying agents are not managed from the console yet.

The same through the API, which is what the page shows:

```bash
curl -s localhost:3000/api/llm/profiles | jq '{active_id, usable_model, profiles: [.profiles[] | {id, name, source, key_hint, active}]}'
# active_id "environment"; one profile; key_hint is four characters
```

Stop the service and reload the page: it says the service cannot be reached and offers to try
again. Start the service again.

## 2. Add a profile and switch to it (User Story 2)

In the console: **Add**, name `Stub`, provider OpenAI-compatible, model `ok`, base URL
`http://127.0.0.1:9997/v1`, key `quickstart-key-0001`, Responses API off, **Save**. A second card
appears, not active. **Set active** on it and confirm.

Or through the API:

```bash
ID=$(curl -s -X POST localhost:8000/llm/profiles -H 'content-type: application/json' -d '{
  "name": "Stub", "provider": "openai", "model": "ok",
  "base_url": "http://127.0.0.1:9997/v1", "api_key": "quickstart-key-0001",
  "responses_api": false}' | jq -r .id)
curl -s -X POST localhost:8000/llm/profiles/$ID/activate | jq .active_id
```

Start a run. The service was not restarted:

```bash
curl -N -X POST localhost:8000/threads/llm-1/runs -H 'content-type: application/json' \
  -d '{"message": "Say hello."}' | head -4
# event: start
# data: {..., "profile": {"id": "<ID>", "name": "Stub", "source": "saved"}}
```

The stand-in's terminal shows the request, and the service log has one line naming `Stub` for
the run. Restart the service and run again: `profile` still names `Stub`. Then activate the
environment's profile and run again:

```bash
curl -s -X POST localhost:8000/llm/profiles/environment/activate | jq .active_id   # "environment"
```

**A run in progress keeps its model.** Start a run that takes a while, activate another profile
while it streams, and let it finish: it ends normally, and only the next run names the new
profile.

**Two instances.** Start a second instance on another port against the same database, activate
a profile through the first, and start a run through the second:

```bash
FREYJA_PORT=8001 uv run --env-file .env freyja-api          # terminal 4
curl -s -X POST localhost:8000/llm/profiles/$ID/activate > /dev/null
curl -N -X POST localhost:8001/threads/llm-2/runs -H 'content-type: application/json' \
  -d '{"message": "Say hello."}' | head -4                   # profile: Stub
```

## 3. Test a profile (User Story 3)

Add a second profile like the first with name `Refuses` and model `refuse-conversation`, then
press **Test** on each card.

```bash
curl -s -X POST localhost:8000/llm/profiles/$ID/test | jq        # {"ok": true, ...}
curl -s -X POST localhost:8000/llm/profiles/$REFUSES/test | jq   # {"ok": false, "step": "conversation", "reason": "..."}
```

- `Stub`'s card says its last test passed, and when.
- `Refuses`'s card says its last test failed on the conversation step. **Set active** on it
  warns that its last test failed and offers to test first; it can still be activated.
- Reload the page, and restart the service: both results are still shown.
- In the Add form, fill in the values of `Refuses` without saving and press **Test**: the same
  failure is reported and no card appears.

Test the environment's card the same way. Change `Refuses`'s model to `ok` and save: its card
says it has not been tested since its last change.

## 4. Change and remove profiles (User Story 4)

- Edit `Stub`, leave the key empty, change the name, save: the card shows the new name, the same
  four key characters, and the test result is kept.
- Edit it again with a new key: the card shows the new key's last four characters and no test
  result.
- With `Stub` active, change its model to `ok-2` and start a run: the service was not
  restarted, and the stand-in's terminal shows the request asking for `ok-2`. Change it back.
- Delete `Refuses` and confirm: its card is gone, also after a restart.
- Try to delete the active profile: refused, with a message to activate another first.

```bash
curl -s -X DELETE localhost:8000/llm/profiles/$ID | jq .detail.code                  # "profile_active"
curl -s -X PATCH localhost:8000/llm/profiles/environment -H 'content-type: application/json' \
  -d '{"name": "x"}' | jq .detail.code                                                # "profile_read_only"
```

## 5. Keys stay secret (User Story 5)

```bash
# Nowhere in what the API returns:
curl -s localhost:8000/llm/profiles | grep -c 'quickstart-key'                        # 0

# Not readable in the database:
docker compose exec postgres psql -U freyja -d freyja -c \
  "select name, key_hint, left(api_key_encrypted, 16) from freyja_llm_profiles"
# key_hint 0001; the stored value starts with gAAAA, not with the key
```

Search the service's terminal output for `quickstart-key`: nothing.

Remove `FREYJA_SECRET_KEY` from `.env` and restart. The environment's card works as before; the
saved cards are shown as unusable; saving a profile is refused:

```bash
curl -s -X POST localhost:8000/llm/profiles -H 'content-type: application/json' \
  -d '{"name": "X", "provider": "openai", "model": "ok", "api_key": "k"}' | jq .detail.code   # "secret_not_configured"
```

Put the secret back and restart: the saved profiles are usable again.

## 6. No usable model (FR-016)

With a saved profile active, start the service with a different `FREYJA_SECRET_KEY`. It starts.
The console says no usable model is active and shows the active card as unusable. A run ends at
once:

```bash
curl -N -X POST localhost:8000/threads/llm-3/runs -H 'content-type: application/json' \
  -d '{"message": "Say hello."}'
# event: start
# event: error
# data: {"code": "no_usable_model", ...}
```

The service has not switched to the environment's model. Activate the environment's profile in
the console and send the message again: the run completes. Restore the original secret.

## 7. Nothing changed for an environment-only deployment (FR-023)

Delete every saved profile, or use a fresh database, and repeat the
[001 quickstart](../001-thread-runs-api/quickstart.md): every step behaves as written there.
The one addition is `profile` in each run's first event.

## Automated checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest                              # no network, no model provider, no database
FREYJA_TEST_DATABASE_URI=postgresql://freyja:freyja@localhost:5432/freyja_test uv run pytest tests/integration
cd web && pnpm lint && pnpm test && pnpm build
```
