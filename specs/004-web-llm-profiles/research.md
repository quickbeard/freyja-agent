# Research: Web Console Shell and LLM Profiles

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Decisions the plan rests on, each with its reason and what was set aside. Facts marked
*verified* were checked on 2026-11-17 against the code on `main` and the packages in `uv.lock`
and `web/pnpm-lock.yaml`, without a network. Facts about Next.js come from the documentation
bundled with the installed version (`web/node_modules/next/dist/docs/`, Next.js 16.4.6), as
`web/AGENTS.md` requires. What could not be checked is listed at the end.

## R1. Switching the model: rebuild the orchestrator

**Decision**: when the active profile changes, build a new orchestrator with `build_agent` and
the new model, and replace `runtime.agent` and `runtime.catalog`. The tool server links, the
agent links, the checkpointer, the store, and the worker are kept.

**Rationale**: `build_agent` is the one assembly point (constitution III), and Deep Agents
chooses part of its middleware from the model it is given, so the graph and the model belong
together. Rebuilding takes about 15 ms with the current tool set (*verified*: five builds, 14 to
57 ms). A run binds its graph when it calls `runtime.agent.astream(...)`
(`api/streaming.py:109`, *verified*), so a run in progress keeps the orchestrator it started on,
subagent included, which is FR-013 with no extra bookkeeping. Thread history lives in the
checkpointer, which both graphs share.

**Alternatives considered**: one graph built once around a model object that forwards to
whichever model is active. Rejected: pinning a run to the model it started with would need
per-run state inside the model, and provider-specific middleware chosen at build time would no
longer match the model in use.

## R2. Where a run picks up the change

**Decision**: `run_stream` is the single place. Before it emits `start`, it asks the model
manager for the current orchestrator (R6), takes one reference to it for the whole run, and
names its profile in the `start` event and one log line (R11).

**Rationale**: every run goes through `run_stream`: a user's run, a continuation after a pause,
and the worker's background runs (*verified*: `api/threads.py`, `delegation_worker.py:299`).
One place covers FR-013's three cases and FR-024a at once, and keeps principle III's single run
loop. The routes still read state through `runtime.agent` before the run; that only reads
checkpoints and is the same on either graph.

## R3. Providers, and building a model from a profile

**Decision**: a fixed provider table in `llm/providers.py` (data-model.md) and one function,
`build_model(spec)`, that turns a `ModelSpec` (provider, model, base URL, key, API choice) into
a chat model with
`init_chat_model(f"{provider}:{model}", **apply_provider_profile(...), api_key=..., base_url=...)`.
The API choice goes through the existing `resolve_model` rule: it only changes what the
provider profile set.

**Rationale**: all three clients take `api_key` and `base_url` and read nothing from the
environment when both are given (*verified* with the environment's keys unset: `ChatOpenAI`,
`ChatAnthropic`, `ChatOpenRouter` each built, with the base URL in `openai_api_base`,
`anthropic_api_url`, and `openrouter_api_base`). None shows the key in its `repr`
(*verified*). Going through `init_chat_model` with Deep Agents' provider profile keeps a saved
profile resolved exactly as the environment's model is (001 research R3).

**The environment's profile** is `FREYJA_MODEL` split at its first colon, plus the provider's
own key and base URL variables read from the process environment. They are read to describe
the profile and for nothing else: the last four characters of the key and the base URL for the
card, whether a key is set at all, which FR-011a makes part of "usable", and the key's text so
that it can be taken out of an error (FR-021). They are read in one module,
`llm/providers.py`, and never to configure the service: the client libraries keep reading
those variables themselves, as today. A prefix outside the table is shown as written.

**Usable** means: the model can be built, and the provider's key variable is set when the
provider is in the table. The second half is needed because `ChatAnthropic` builds without a
key and only fails when called, while the other two fail when built (*verified*).

**Alternatives considered**: constructing each client class directly. Rejected: it would skip
Deep Agents' provider profile and import three client modules by name.

## R4. Protecting stored keys

**Decision**: encrypt each key with Fernet from the `cryptography` package under
`FREYJA_SECRET_KEY`, a Fernet key the operator generates once. Store the token as text and the
key's last four characters beside it in clear. A missing secret refuses saves; a token that does
not decrypt marks its profile unusable.

**Rationale**: Fernet is authenticated encryption with a fixed, documented format, so a wrong
secret is detected instead of yielding a wrong key, which is what FR-022 needs. `cryptography`
50.0.2 is already in the lock through `a2a-sdk` (*verified*), so declaring it adds no package.
Keeping the hint in clear lets a card be shown when the secret is missing or changed, and four
characters of a key of eight or more are not enough to use it (FR-020). A value in
`FREYJA_SECRET_KEY` that is not a Fernet key is a configuration error and stops startup, like
any invalid setting; an unset one does not (FR-022).

**Alternatives considered**: deriving the key from a passphrase (adds a salt and a cost
parameter to manage for no gain when the operator can generate a key); `MultiFernet` for
rotation (out of scope: after a change of secret the operator types the keys again, FR-022);
the database's own `pgcrypto` (the key would cross to the database in clear and appear in
statement logs).

## R5. Where profiles are kept

**Decision**: two tables in the existing database, created at startup with
`CREATE TABLE IF NOT EXISTS` like the run lease and the agent tables: `freyja_llm_profiles` and
the one-row `freyja_llm_state` (data-model.md). A `LlmProfileStore` protocol has a Postgres
implementation on the shared pool and an in-memory one for the suite, with one set of contract
tests run against both, as `AgentTaskStore` does.

**Rationale**: "exactly one active profile" (SC-009) is cheapest to guarantee in the database:
the active choice is one column of one row, an activation is one `UPDATE`, and a foreign key
with `ON DELETE RESTRICT` refuses to delete the row it points at in whatever order two requests
arrive. The limit of 50 is counted under a transaction-level advisory lock so two concurrent
adds cannot both pass.

**Alternatives considered**: the LangGraph store (no unique index, no foreign key, no atomic
compare); an `is_active` column on each profile (needs a partial unique index and two writes per
activation, and cannot express "the environment is active").

## R6. Reaching every instance

**Decision**: `freyja_llm_state.revision` is incremented by every activation that changes the
active profile and by every edit of the active profile. Each instance remembers the revision it
built for and reads the current one before every run. When they differ it reads the active
profile, rebuilds (R1), and then runs. The instance that handled the request rebuilds at once.

**Rationale**: a run already talks to the database for its lease, so one more single-row read
adds about a millisecond, and it makes the bound for other instances zero for the run that
matters, well inside FR-014's ten seconds. There is no background task to start, stop, or test.
An `asyncio.Lock` around the rebuild keeps two runs that start together from building twice.

**Alternatives considered**: a poller in each instance (a second background task, and a run
could still start on the old model within the interval); `LISTEN`/`NOTIFY` (a dedicated
connection outside the pool to keep alive, with a poll still needed to cover a missed
notification).

## R7. Testing a profile

**Decision**: the test builds a model from the profile's values with no retries and sends two
requests under one time limit (`FREYJA_LLM_TEST_TIMEOUT_SECONDS`, default 60, at most 60):

1. `first_message`: one user message.
2. `conversation`: that message, an assistant turn that calls a tool, the tool's result, and a
   second user message, with the tool's definition bound.

The conversation is written by the test, not produced by the model. It passes only if both
requests are answered. A result for a listed profile is recorded only if the profile's
`config_version` is still the one read when the test began.

**Rationale**: the failure this exists to catch is an endpoint that answers a first message and
refuses a request carrying earlier turns. On 2026-11-16 a vLLM server behind LiteLLM did
exactly that over the Responses API: it rejected assistant messages, tool calls, and tool
results as input (001 research R3). A history written by the test triggers it every time;
asking the model to call a tool first would depend on the model choosing to. Binding a tool
makes the request carry tool definitions, as every orchestrator request does. Retries are off
so that the limit is a limit.

**How many at once** (FR-019b): each instance keeps, in memory, the ids of the listed profiles
being tested and a count of all tests in progress. A second test of the same profile, or a
fifth test, is refused with 409 `test_busy` before a model is built. The limits are per
instance because a test holds no database row; four is a constant, not a setting.

**Alternatives considered**: a single request (misses the failure); running a real orchestrator
turn (needs a thread, and FR-019 says a test changes none).

## R8. Starting without a usable model

**Decision**: startup no longer fails when the model cannot be built. The manager records why,
and the orchestrator is built around a placeholder chat model so that the graph, the catalog,
and the thread routes exist. `run_stream` checks usability before invoking the graph: it emits
`start`, then `error` with code `no_usable_model`, and releases the lease. The placeholder
raises the same error if it is ever called.

**Rationale**: the thread state route and resume validation need a graph, so "no graph" is not
an option. Refusing in `run_stream` is a fixed rule that runs before the model (principle VI),
adds nothing to the thread, and leaves a pending proposal pending. The database is still
required at startup, as today. The service never falls back to another profile (clarified
2026-11-17).

**The worker** starts no background run while no usable model is active (FR-016a). An outcome
that is ready stays ready and is delivered once a usable profile is active, by a background run
or at the start of the user's next run.
Without this rule each waiting outcome would use up its background run attempts (003 FR-027)
on runs that cannot succeed.

**Alternatives considered**: refusing the request with an HTTP status. Rejected: the spec says
the run ends with an error, a background run has no HTTP response to carry it, and one shape
for all three kinds of run is simpler for a client.

## R9. Keys are never returned, logged, or stored in a thread

**Decision**, in four parts:

- No response model has a key field. The only place a decrypted key exists is the `ModelSpec`
  handed to `build_model`.
- Validation errors on the `/llm` routes are returned without `input`. FastAPI's default 422
  repeats the refused value (*verified*: a refused field's value comes back under `input`), and
  on these routes that value may be a key.
- `redact(text, secrets)` replaces every secret of eight characters or more with a marker. It is
  applied to a test's reason, to the `model_error` message and its log line in `run_stream`,
  and to any log line about a profile. It looks for the key's exact text only; an encoded form
  is not searched for (FR-021).
- Request bodies are never logged; uvicorn's access log has the path only.

**Rationale**: the same approach as 002's `pass_env` redaction and 003's `describe`: one
function, applied at every exit. A provider can repeat a key in an error, so cleaning error text
is not optional (FR-021).

**In the web app** (FR-003): the key lives in the form's state only. The form clears it when it
is sent and when it closes, which also covers Cache Components keeping a hidden page mounted
(R14). Nothing is written to `localStorage` or `sessionStorage`, and no response carries a key
to show again.

## R10. The API

**Decision**: a new router `api/llm.py` under `/llm`, with the shapes in
[contracts/openapi.yaml](contracts/openapi.yaml): list, add, change, delete, activate, test a
listed profile, and test unsaved values. Refusals use the existing error shape,
`{"detail": {"code", "message"}}`, with eight new codes, and a ninth, `other_origin`,
since the converge step (plan, Phase 10 as built). The environment's profile has the id
`environment`, so one path shape serves both kinds; a write to it is `profile_read_only`.
Activation answers with the whole list, so the console redraws from one response.

**Rationale**: it follows the thread routes' conventions (*verified*: `_error` in
`api/threads.py`), and 409 for a refusal that depends on state is what those routes already do.

## R11. Which profile a run used

**Decision**: the `start` event gains `profile` with `id`, `name`, and `source`, and
`run_stream` logs one INFO line per run with the same values
([contracts/sse-events.md](contracts/sse-events.md)).

**Rationale**: `start` is already sent by every run before the model is called, and 001's
contract lets fields be added.

**Corrected in Phase 2**: this said no existing test compares the whole `start` event. One
does: `tests/test_runs.py` compares the first event's payload in full, on the line after the
check of its kind that the search had found. That assertion now expects `profile` as well.
It is the only earlier test that changed; FR-023 and SC-006 say so.

## R12. Requests from other origins

**Decision**: no CORS configuration is added, and that is now a requirement with a test
(FR-003a, SC-012): a preflight from another origin gets no permission, and a body that is not
sent as JSON is refused. The browser reaches the API on the web app's own origin (R13).

**Rationale**: with no CORS headers, a page on another origin cannot read a response or send a
JSON request, because that needs a preflight the API does not answer. A cross-origin form post
with `text/plain` and a JSON body is refused with 422 (*verified* on FastAPI 0.162.0). One
exposure is not closed here and is not new: a request that reaches the port directly is
trusted, as it already is for approving proposals (spec Assumptions).

## R13. Web: reaching the API on the same origin

**Decision**: `rewrites()` in `web/next.config.ts` forwards `/api/:path*` to
`${FREYJA_API_URL}/:path*`, with `FREYJA_API_URL` defaulting to `http://127.0.0.1:8000`. The
browser only ever calls `/api/...`.

**Rationale**: Next.js's guide says to use `rewrites` to forward requests to another backend,
and a Route Handler only when requests must be validated or changed
(`01-app/02-guides/backend-for-frontend.md`). The same guide says a Route Handler proxy holds
the whole response in memory and to use rewrites for streaming, which matters once the console
shows a run. The variable is server-side and has no `NEXT_PUBLIC_` prefix, so the API's address
never reaches the browser. It is documented in `web/.env.example`.

**Alternatives considered**: a catch-all Route Handler (buffers, and adds code on the web server
that principle III wants kept out); Proxy, the renamed Middleware (the documentation advises
against relying on it); CORS on the API (opens an unauthenticated API to other origins).

## R14. Web: fetching and changing data

**Decision**: Client Components fetch with SWR and change data with `fetch` calls to `/api`,
followed by `mutate` of the list. No Server Actions, no `use cache`.

**Rationale**: the documentation recommends "a client data-fetching library such as SWR,
TanStack Query, or Apollo Client" for data read in Client Components
(`01-app/02-guides/client-side-data-fetching/index.md`). SWR is the smallest of them and needs
no Suspense boundary under Cache Components; TanStack Query reads the current time during
render and must sit behind one. The documentation's form guide uses Server Actions, which run on
the web app's server. That would make the web server a second place where profile changes are
handled, against principle III, so the console calls the API from the browser instead; a key
typed into a form goes to the API in the request and is never held by the web app's code.

**Rules that follow from Cache Components** (`migrating-to-cache-components.md`): no
`Date.now()` or `new Date()` during render (a build error), so "tested at" shows the time the
API returned; no `export const dynamic`; and because a page stays mounted while hidden, a dialog
or form resets its state when it opens.

## R15. Web: shell and routes

**Decision**: the root `app/layout.tsx` stays a Server Component and renders the sidebar and the
page. Two static routes, `app/llm/page.tsx` and `app/agents/page.tsx`. `/` redirects to `/llm`
with `redirects()` in `next.config.ts` (`permanent: false`), and the stock `app/page.tsx` is
removed. The sidebar's links are one small Client Component that marks the active section with
`usePathname()`.

**Rationale**: a layout keeps its state across navigation, which is what FR-001 asks of the
sidebar, and the documentation advises keeping the layout a Server Component with only the
interactive leaf on the client. Both routes are static, so `usePathname()` needs no Suspense
boundary.

**One risk**: the `usePathname` reference warns of hydration mismatches when an app has
rewrites. The rewrite here only matches `/api/...`, which no page uses. If a mismatch shows in
development, the documented fix applies: render a stable fallback and set the path in an effect.

## R16. Web: components

**Decision**: shadcn/ui components added with its CLI in the `base-vega` style already set up:
`sidebar`, `card`, `dialog`, `alert-dialog`, `input`, `label`, `select`, `switch`, `badge`,
`skeleton`, and `sonner`, plus what the CLI adds as their dependencies. They are formatted with
`pnpm format` after they are added (AGENTS.md).

**Rationale**: the sidebar component already handles collapsing on a narrow screen (FR-001), and
`alert-dialog` gives the confirmations FR-010 and FR-012 need.

## R17. Web: tests

**Decision**: Vitest with jsdom, `@vitejs/plugin-react`, `@testing-library/react`,
`@testing-library/dom`, and `vite-tsconfig-paths`, in `vitest.config.mts`, as Next.js's guide
gives them (`01-app/02-guides/testing/vitest.md`). `fetch` is replaced by a stub in each test.
Tests are `*.test.ts` and `*.test.tsx` files under `web/__tests__/`, which follows the layout
of the code it covers. (Until Phase 3 they sat next to the code; see plan.md, Implementation
Notes.)

**Rationale**: every component with behavior here is a Client Component, which this setup
renders. The guide's limit, that async Server Components are not supported, touches only the
layout and the two page files, which hold no logic.

## R18. Service tests

**Decision**: three seams, all in the test process, no network and no provider (FR-025):

- `model_factory`, a new argument of `Runtime.assemble` with `build_model` as its default. Tests
  pass one that returns scripted chat models labelled by profile, which shows which profile a
  run used, holds a run open while the profile is switched, and fails on a chosen call.
- `tests/llm_stub.py`, a small OpenAI-compatible endpoint (chat completions and the Responses
  API) run in the test process and reached through `ChatOpenAI`'s `http_async_client`
  (*verified* to exist). It answers a first message and can refuse a conversation, repeat the
  key in an error, or never answer. It proves the real client path for R7 and R9.
- The in-memory `LlmProfileStore`, with the contract tests also collected against Postgres in
  `tests/integration/`.

Two runtimes sharing one store stand for two instances, as in 003.

## R19. Compatibility

**Decision**: with no saved profile and a usable model in the environment, nothing changes
except the added `profile` field. `Runtime.assemble(model=<instance>)`, which every existing
test uses, keeps working: an injected model is treated as the environment's.

**Rationale**: FR-023 and SC-006. The existing suite is the check: it passes with one
assertion changed, the one that compares a run's first event in full (R11).

## R20. The quickstart's second model

**Decision**: the quickstart uses the suite's `tests/llm_stub.py`, served on loopback with
`uv run --env-file .env python -m tests.llm_stub --http 9997`, as the endpoint for the saved
profiles, in a mode that works and a mode that refuses a conversation.

**Rationale**: the walk-through needs a second endpoint and one that fails on the second step.
A stand-in gives both with no second account or key, as `tests/a2a_stub.py` does for 003.

## Not verified

These need the network or a browser and are checked when the feature is built:

- Whether a `rewrites()` destination is read when `next build` runs or when `next start` does.
  The bundled documentation does not say. Checked in Phase 1 (T009): at `next build`; see
  plan.md, Implementation Notes.
- That SWR's current release supports React 19.3 and Next.js 16.4, and that the shadcn CLI has
  every component in R16 for the `base-vega` style.
- That `usePathname()` causes no hydration warning here (R15). Checked in Phase 3 (T037):
  none; see plan.md, Implementation Notes.
- That a streamed response passes through a rewrite unbuffered. The documentation implies it;
  this feature streams nothing through the console.
