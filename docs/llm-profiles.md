# LLM profiles

An LLM profile is one way to reach a model: a name, a provider (`openai` for any OpenAI-compatible endpoint, `anthropic`, or `openrouter`), a model, an optional base URL, an API key, and, for `openai`, whether the Responses API or chat completions is used. Profiles are kept in the database and changed while the service runs, through the routes under `/llm` or in the [web console](web-console.md). Exactly one profile is **active**: the one every run uses. The full contracts are in `specs/004-web-llm-profiles/contracts/`.

What the environment configures (`FREYJA_MODEL`, `FREYJA_OPENAI_RESPONSES_API`, and the provider's own variables) is the **environment's profile**. Its id is `environment` and its name `Environment`; it is read-only, and it is active until a saved profile is activated. A service configured only through the environment behaves as it did before profiles existed, also when it reaches its model through the LangSmith gateway (`LANGSMITH_GATEWAY`): the card then shows the gateway's address and the last four characters of the gateway's key.

## The secret

The keys of saved profiles are stored encrypted under `FREYJA_SECRET_KEY`, a Fernet key. Generate one once, add the printed line to `.env`, and restart the service:

```bash
echo "FREYJA_SECRET_KEY=$(uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
```

Without it the service starts and the environment's profile works as before, but saving a profile is refused with `409` and code `secret_not_configured`. A value that is not a Fernet key stops startup with an error that names the setting, `secret_key`, and not its value. Every instance that shares a database needs the same value. After the secret changes, each saved profile is listed as not usable, with `unusable_reason` `key_unreadable`, until its key is entered again.

## List, add, and activate

```bash
curl -s localhost:8000/llm/profiles | jq
```

The answer has `profiles`, the environment's first and then the saved ones by name, `active_id`, `usable_model` (whether the active profile can be used), `secret_configured`, and `limit`. Each profile has its `id`, `name`, `source` (`saved` or `environment`), `provider`, `model`, `base_url`, `responses_api`, `key_hint`, `active`, `usable`, `unusable_reason`, `last_test`, `created_at`, and `updated_at`. Listing reads the database and the environment and contacts no model endpoint.

Add a profile, then make it the active one:

```bash
ID=$(curl -s -X POST localhost:8000/llm/profiles -H 'content-type: application/json' -d '{
  "name": "Local model", "provider": "openai", "model": "<served model name>",
  "base_url": "http://<host>:<port>/v1", "api_key": "<key>", "responses_api": false}' | jq -r .id)
curl -s -X POST localhost:8000/llm/profiles/$ID/activate | jq .active_id
```

A name has 1 to 64 characters and is unique without regard to case; `Environment` is taken. A model has 1 to 200 characters, a base URL is an `http` or `https` address on any host, and at most 50 profiles can be saved. A profile saved with no base URL is sent to its provider's own service (`https://api.openai.com/v1`, `https://api.anthropic.com`, or `https://openrouter.ai/api/v1`), whatever base URL the environment sets for its own model: the key never goes to an endpoint the profile does not name. Nothing else the environment sets for its provider's client reaches a saved profile either, such as `OPENAI_CUSTOM_HEADERS`, `OPENAI_ORG_ID`, or `OPENAI_PROJECT_ID`: a saved profile is its provider, model, base URL, and key. Two kinds of variable are the exception and apply to saved profiles too, because they are the deployment's: a client's proxy (`OPENAI_PROXY`, `ANTHROPIC_PROXY`), like `HTTP_PROXY`, and a telemetry opt-out (`ANTHROPIC_OPEN_TELEMETRY`). A request that breaks a rule is `422`; a name already in use is `409` with code `name_taken`, and a 51st profile `409` with `profile_limit`. A new profile is not active.

An activation applies without a restart, from the next run, on every instance that shares the database. A run in progress finishes on the model it started with. Existing conversations are sent to the new profile's endpoint from their next run, and a thread paused for a decision continues on the profile that is active when it is resumed. A profile that is not usable cannot be activated (`409`, `profile_unusable`). To go back to the environment's model:

```bash
curl -s -X POST localhost:8000/llm/profiles/environment/activate | jq .active_id   # "environment"
```

Each run names its profile in its `start` event and in one log line. Adding, activating, changing, deleting, and testing a profile each write one log line that names the profile and, for a change, the fields that changed.

## Test a profile

```bash
curl -s -X POST localhost:8000/llm/profiles/$ID/test | jq
# {"ok": true, "at": "...", "step": null, "reason": null, "recorded": true}
```

A test sends the profile's endpoint two requests under one time limit, `FREYJA_LLM_TEST_TIMEOUT_SECONDS`: a first message (`first_message`), then a request that carries earlier turns and a tool call (`conversation`), which is what a run sends after its first step. An endpoint that answers a first message and refuses every later one fails the second step. A failed test names the `step` and gives the `reason`, at most 500 characters.

The last result is kept with a saved profile, also across restarts, and is cleared when its provider, model, base URL, key, or API choice changes. The environment's result is kept in memory until the instance restarts. `POST /llm/test` tests values that are not saved and keeps nothing; it takes the fields of a new profile, and `profile_id` in place of `api_key` uses that saved profile's stored key.

One instance runs one test per profile at a time and four in all. A test beyond either limit is `409` with code `test_busy`, and nothing is sent.

## Change and delete

```bash
curl -s -X PATCH localhost:8000/llm/profiles/$ID -H 'content-type: application/json' \
  -d '{"model": "<another model>"}' | jq
curl -s -X DELETE localhost:8000/llm/profiles/$ID -o /dev/null -w '%{http_code}\n'   # 204
```

A change carries only the fields to change: a key left out keeps the stored key. A change to the active profile applies from the next run, like an activation. The active profile cannot be deleted (`409`, `profile_active`): activate another first. The environment's profile can be neither changed nor deleted (`409`, `profile_read_only`).

## Keys stay secret

A key goes in and never comes out. No route returns it: a profile shows `key_hint`, the key's last four characters, or nothing when the key has fewer than eight. The key is in no log line, stream event, or thread, and where a provider's error repeats it, in a test's reason, a run's `model_error` message, a `debug` event, or what is saved of a failed step, `<key>` stands in its place. A key shorter than eight characters is the exception: it is too short to be told from ordinary text and is not searched for. A refused request to an `/llm` route is answered without the value that was sent.

The API gives no other origin permission to call it, and every route that takes a body refuses one that is not sent as JSON. A request that changes something is also refused with `403` and code `other_origin` when the browser marks it as made by a page of another origin, by `Sec-Fetch-Site` or by an `Origin` that is not the host it asked for. That covers the routes without a body, which a plain form could otherwise reach. So a page from another site cannot change a profile through a browser that can reach the service. The console and clients that are not web pages, such as `curl`, are unaffected.

## No usable model

The active profile is not usable when its stored key cannot be read, when the service has no secret, or when the environment names a provider without a key or a model that cannot be built. The service still starts, `/healthz` answers, and profiles can be listed and changed. A run ends at once with `start` and then `error`:

```json
{"code": "no_usable_model", "message": "No usable model is active. Activate a usable LLM profile, then send the message again."}
```

Nothing is added to the thread and a pending proposal stays pending, so the same request can be sent again once a usable profile is active. The service does not switch to another profile by itself, and background runs wait until a usable one is active.

## Try it with the stand-in endpoint

The test suite's stand-in for an OpenAI-compatible endpoint can be served on loopback, so a profile can be added, tested, and run on without a second account or key:

```bash
uv run --env-file .env python -m tests.llm_stub --http 9997   # base URL http://127.0.0.1:9997/v1
```

Save a profile with provider `openai`, that base URL, any key, and `responses_api` false. The stand-in behaves by the model id it is asked for: `refuse-conversation` answers a first message and refuses a request with earlier turns, `reject-key` answers 401, `repeat-key` fails with an error that repeats the key, `never-answer` does not answer, `slow` answers after 20 seconds, `call-tools` asks for tools, and any other id, such as `ok`, answers everything. `call-tools` acts on a keyword at the start of a message: `notify <text>` proposes a notification to `#ops`, `handoff <agent> <text>` hands the text to that agent, and `steps <n>` takes `n` steps a second apart; it answers chat completions only, so its profile has `responses_api` false. With it the Tasks section can be tried from its first step to its last without a provider's key.

**Next:** [Use the API](api.md): threads, runs, and deciding on a proposal.
