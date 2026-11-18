# Contract: Settings added by feature 004

Deployment configuration, read once at startup (constitution principle IV). LLM profiles
themselves are not settings: they are operator-managed configuration, kept in the database and
changed through the API ([openapi.yaml](openapi.yaml)).

## The service

| Variable | Default | Meaning |
|---|---|---|
| `FREYJA_SECRET_KEY` | unset | The secret that protects stored API keys (FR-020, FR-022): a Fernet key, 44 URL-safe base64 characters. |
| `FREYJA_LLM_TEST_TIMEOUT_SECONDS` | `60` | The most a profile test may take, both steps together (FR-019). Greater than 0 and at most 60. |

Both are documented in `.env.example`.

### `FREYJA_SECRET_KEY`

Generate one once and keep it with the deployment's other secrets:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

| State | Effect |
|---|---|
| Unset | The service starts. The environment's profile works as before. Saving a profile, or changing a saved profile's key, is refused with `secret_not_configured`. Saved profiles, if any exist, are listed as unusable with `secret_not_configured`. |
| Set, valid | Profiles can be saved. Keys are stored encrypted under it. |
| Set, not a Fernet key | The service does not start. The error names the setting, `secret_key`, and never repeats its value. |
| Changed since keys were stored | The service starts. Each profile whose key no longer decrypts is listed as unusable with `key_unreadable`, until its key is entered again. If the active profile is one of them, runs end with `no_usable_model`. |

The value is never logged, returned, or shown, and it never leaves the service process.

Every instance that shares a database must be given the same value. The service does not check
this: an instance with another value lists the saved profiles as unusable and, if one of them
is active, ends its runs with `no_usable_model` (spec Assumptions).

### What is unchanged

`FREYJA_MODEL`, `FREYJA_OPENAI_RESPONSES_API`, and the providers' own variables
(`OPENAI_API_KEY`, `OPENAI_API_BASE`, `ANTHROPIC_API_KEY`, `ANTHROPIC_API_URL`,
`OPENROUTER_API_KEY`, `OPENROUTER_API_BASE`) keep their meaning. Together they are the
environment's profile: read-only in the console, and active until a saved profile is activated
(FR-008, FR-011).

So do the LangSmith gateway's variables, `LANGSMITH_GATEWAY`, `LANGSMITH_GATEWAY_API_KEY`, and
`LANGSMITH_API_KEY`, which the provider's client reads itself. An environment may reach an
`openai:` or `anthropic:` model through the gateway with no provider key at all: its profile
is usable, and it shows the gateway's address and the last four characters of the gateway's
key (FR-011a). None of these is a setting of the service, and none applies to a saved
profile: a saved profile's client is built with its provider's variables hidden, apart from a
proxy and a telemetry opt-out, which are the deployment's.

One difference: a model that cannot be built from them no longer stops startup. The service
starts, the environment's profile is listed as unusable, and runs end with `no_usable_model`
(FR-016).

## The web app

| Variable | Default | Meaning |
|---|---|---|
| `FREYJA_API_URL` | `http://127.0.0.1:8000` | Where the web app's server forwards `/api/...` (research R13). Read by `web/next.config.ts` when `pnpm dev` starts and when `pnpm build` runs. A built app keeps the address it was built with: `pnpm start` does not read the variable again. |

It is a server-side variable with no `NEXT_PUBLIC_` prefix: the browser only ever calls the web
app's own origin and never learns the API's address. It is documented in `web/.env.example`.
