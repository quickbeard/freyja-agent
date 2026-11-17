# Tasks: Web Console Shell and LLM Profiles

**Input**: Design documents from `/specs/004-web-llm-profiles/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/openapi.yaml,
contracts/sse-events.md, contracts/settings.md, quickstart.md

**Tests**: Included. FR-025 requires automated coverage of the service and of the web app with
no model provider and no network, and principle V requires new behavior to ship with tests. In
each user story phase the test tasks come first: write them, watch them fail, then implement.
Phase 2 is the exception for its runtime wiring: its fixture is built on the code, so that part
of its tests closes the phase.

**Organization**: Tasks are grouped by user story so each story is an independently testable
increment. User Story 5 is split over two phases (7 and 8): running without a usable model
comes first, because the proof that keys stay secret covers a service whose active profile
cannot be read. Research references (R1 to R20) point at `research.md`. "The stand-in" is the
model endpoint in `tests/llm_stub.py`; "the model factory" is the `model_factory` seam of
`Runtime.assemble` (R18).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1 to US5)
- Every task names the file it touches

## Path Conventions

Two projects in one repo: the service in the src layout (`src/freyja_agent/`, `tests/`,
repo-root files) and the web app under `web/` (`web/app/`, `web/components/`, `web/lib/`). Web
tests sit next to the code as `*.test.ts` and `*.test.tsx`.

## Standing rules for every task

- A key is never stored unencrypted and never returned or logged, at the end of any phase.
  Phase 2 builds encryption and the store before Phase 4 adds the first route that takes a key,
  and that route is created with validation errors that carry no `input` (R9).
- Every resume request in a test or an example sends the proposal's `interrupt_id`
  (feature 001, FR-013).
- No new Ruff suppression; no blocking I/O in async code; SQL only with bound parameters
  (principle V, Ruff `ASYNC` and `S608`).
- The service suite opens no socket. The stand-in runs in the test process, reached through
  `ChatOpenAI`'s `http_async_client`; only its `--http` mode, used by hand, listens on loopback.
- Before writing any code under `web/`, read `web/AGENTS.md` and the guide it points to in
  `web/node_modules/next/dist/docs/`. No Server Action, no Route Handler, no `use cache`, no
  `export const dynamic`, and no clock read (`Date.now()`, `new Date()`) during render (R14).
- shadcn components are added with its CLI and formatted with `pnpm format` afterwards. Files
  under `web/components/ui/` are not edited by hand beyond that.
- Web tests replace `fetch` with a stub. No web test reaches the service.
- A manual check stops any service it started by its port (`ss -ltnp`), never by a process
  pattern.
- Each phase is one pull request and one merge request, and its description has a Tests section
  (AGENTS.md).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: The dependency, the two settings, the web app's test tooling, its components, and
the path from the browser to the service.

- [X] T001 Add `cryptography>=50,<51` to `[project].dependencies` in `pyproject.toml`, then run
      `uv lock`; confirm the lock gains no package and that `cryptography` stays at 50.0.2 (R4)
- [X] T002 [P] Add tests for the two settings to `tests/test_config.py`: `secret_key` is unset
      by default; a key from `Fernet.generate_key()` is accepted; a value that is not a Fernet
      key, such as `not-a-key`, is refused with an error that names `secret_key` and does not
      repeat the value; `llm_test_timeout_seconds` defaults to 60 and refuses 0, a negative
      number, and 61 (FR-019, FR-022, contracts/settings.md)
- [X] T003 Implement the settings in `src/freyja_agent/config.py`: `secret_key: SecretStr | None
      = None`, validated as a Fernet key when set, and `llm_test_timeout_seconds:
      Annotated[float, Field(gt=0, le=60)] = 60` (FR-019, FR-022, R4, R7)
- [X] T004 [P] Document `FREYJA_SECRET_KEY`, with the command that generates one, and
      `FREYJA_LLM_TEST_TIMEOUT_SECONDS` in `.env.example`, with the text of
      contracts/settings.md; leave `FREYJA_SECRET_KEY` commented out (principle IV)
- [X] T005 [P] In `web/`, add `swr` to the dependencies and `jsdom`, `@vitejs/plugin-react`,
      `@testing-library/react`, `@testing-library/dom`, and `vite-tsconfig-paths` to the dev
      dependencies in `web/package.json`, updating `web/pnpm-lock.yaml`. Before keeping `swr`,
      check that its peer dependencies accept React 19.3; if they do not, stop and record it in
      plan.md under Implementation Notes (R14, R17, research "Not verified")
- [X] T006 Create `web/vitest.config.mts` with the `tsconfigPaths()` and `react()` plugins and
      `test.environment: "jsdom"`, as Next.js's Vitest guide gives it; keep the `test` script
      as `vitest run`; confirm `web/lib/utils.test.ts` still passes. Then write
      `web/next.config.test.ts`: `rewrites()` forwards `/api/:path*` to `:path*` on
      `http://127.0.0.1:8000` by default and on `FREYJA_API_URL` when it is set; it fails until
      T009 (FR-003, R13, R17)
- [X] T007 Add the shadcn components `sidebar`, `card`, `dialog`, `alert-dialog`, `input`,
      `label`, `select`, `switch`, `badge`, `skeleton`, and `sonner` under `web/components/ui/`
      with the shadcn CLI in the `base-vega` style, then run `pnpm format`; `pnpm lint`,
      `pnpm test`, and `pnpm build` pass. If the CLI has no such component for the style, stop
      and record it in plan.md (R16, research "Not verified")
- [X] T008 [P] Create `web/.env.example` documenting `FREYJA_API_URL` with its default
      `http://127.0.0.1:8000`, add `!.env.example` to `web/.gitignore` so it is committed, and
      name the variable in the root `.env.example` too, pointing at `web/.env.example`
      (contracts/settings.md, principle IV)
- [X] T009 Add `rewrites()` to `web/next.config.ts`: `/api/:path*` goes to
      `${FREYJA_API_URL}/:path*`, with `FREYJA_API_URL` read from the environment and defaulting
      to `http://127.0.0.1:8000`. Check by hand whether a changed `FREYJA_API_URL` takes effect
      at `next start` without a new `next build`, and record the answer in plan.md under
      Implementation Notes and in `web/.env.example`. The rewrite is the only thing between the
      browser and the service: no code in `web/` reads, keeps, or logs a request's body (FR-003,
      R13, research "Not verified")

**Checkpoint**: `uv run pytest tests/test_config.py` passes; in `web/`, `pnpm lint`, `pnpm test`,
and `pnpm build` pass; `curl localhost:3000/api/healthz` reaches a running service through the
web app's dev server.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Encryption, the provider table, the profile store, the model manager, and the
runtime and run loop built on them, with no route yet. After this phase the service obtains its
model through the manager and behaves as before.

**CRITICAL**: No user story work can begin until this phase is complete.

- [X] T010 [P] Write `tests/test_llm_secrets.py`: a key encrypted and decrypted under one secret
      comes back unchanged; decrypting under another secret raises `KeyUnreadableError`;
      encrypting with no secret raises `SecretNotConfiguredError`; the stored token does not
      contain the key; `key_hint` gives the last four characters of a key of eight or more and
      an empty string for a shorter one; `redact(text, secrets)` replaces every occurrence of
      the exact text of each secret of eight characters or more and leaves a shorter one and an
      encoded form alone (FR-020, FR-021, R4, R9)
- [X] T011 [P] Write `tests/test_llm_providers.py`: the table has exactly `openai`, `anthropic`,
      and `openrouter`; `build_model(spec)` for each builds its client from `api_key` and
      `base_url` with the provider's key variables unset, and no client shows the key in its
      `repr`; for `openai`, `responses_api=False` gives `use_responses_api is False` and true
      keeps the provider profile's value; `environment_profile(settings)` splits `FREYJA_MODEL`
      at its first colon, reports the base URL and the key hint from the provider's own
      variables, reports `responses_api` from `FREYJA_OPENAI_RESPONSES_API` for `openai` only,
      is usable only when the model builds and the provider's key is set, and shows a prefix
      outside the table as written with no key hint (FR-004, FR-008, FR-011a, R3)
- [X] T012 [P] Write `tests/test_llm_profile_store.py` as contract tests any store can be given,
      exported in `__all__`: a created profile is listed and read back; names are unique
      without regard to case; the 51st profile is refused; an update of `provider`, `model`,
      `base_url`, the key, or `responses_api` increments `config_version` and clears the four
      `last_test_*` values, and an update of `name` alone does neither; a test result is
      recorded only when `config_version` still equals the one given; the state starts with no
      active profile and `revision` 0; activating a saved profile sets it and adds one to
      `revision`; activating the active profile changes nothing; activating an unknown id
      reports not found; activating none makes the environment active; an update of the active
      profile adds one to `revision` and an update of another does not; the active profile
      cannot be deleted and another can; a deleted profile is gone (FR-005, FR-006, FR-011,
      FR-015, FR-019a, data-model.md)
- [X] T013 [P] Implement `src/freyja_agent/llm/secrets.py`, with an empty
      `src/freyja_agent/llm/__init__.py`: `encrypt`, `decrypt`, `key_hint`, `redact`, and the
      two errors, using Fernet under `Settings.secret_key` (FR-020 to FR-022, R4, R9)
- [X] T014 [P] Implement `src/freyja_agent/llm/providers.py`: the provider table of
      data-model.md; `ModelSpec` (provider, model, base URL, key, API choice); `build_model`
      through `init_chat_model` with Deep Agents' provider profile, where the API choice only
      changes what the profile set; `environment_profile(settings)`; and the placeholder chat
      model that raises `NoUsableModelError` when called (R3, R8)
- [X] T015 Implement the `LlmProfileStore` protocol, `LlmProfileRecord`, `LlmState`, and
      `InMemoryLlmProfileStore` in `src/freyja_agent/llm/store.py`, with the rules of
      data-model.md: `id` "32 hex characters, generated by the service; never `environment`";
      `name` "1 to 64 characters after trimming; unique without regard to case"; `provider`
      "`openai`, `anthropic`, or `openrouter`"; `model` "1 to 200 characters after trimming";
      `base_url` "at most 2,000 characters; `http` or `https` with a host"; "At most 50 rows";
      `last_test_reason` "at most 500 characters" (FR-004 to FR-006, FR-019a, R5)
- [X] T016 Implement `PostgresLlmProfileStore` in `src/freyja_agent/llm/store.py` on the shared
      pool, creating `freyja_llm_profiles` and the one-row `freyja_llm_state` with
      `CREATE TABLE IF NOT EXISTS`: the unique index on `lower(name)`; `CHECK (id = 1)`;
      `active_profile_id` referencing `freyja_llm_profiles(id)` `ON DELETE RESTRICT`; the insert
      counted under a transaction-level advisory lock; a test result recorded with
      `WHERE id = %(id)s AND config_version = %(version)s`; times from the database's `now()`;
      every statement with bound parameters (R5, data-model.md)
- [X] T017 [P] Write `tests/integration/test_llm_profiles_postgres.py`: the contract tests of
      T012 collected against Postgres; an activation and a deletion of the same profile sent
      together leave exactly one active profile that exists; two adds sent together at 49
      profiles leave 50; `setup()` run twice changes nothing (SC-009, FR-006, R5)
- [X] T018 Implement `src/freyja_agent/llm/active.py`: `ActiveModel` (revision, the profile's
      id, name, and source, usable, reason, secrets) and the manager that loads the active
      profile at startup, compares `revision` before a run, and, under an `asyncio.Lock`,
      rebuilds the orchestrator with the model factory when it moved. In this phase a model
      that cannot be built still raises, as startup does today (FR-011, FR-013, FR-014, R1, R6)
- [X] T019 Wire it into `src/freyja_agent/runtime/runtime.py`: `Runtime` holds the profile
      store and the manager; `assemble` takes `llm_profiles` and
      `model_factory=build_model`, builds the orchestrator through the manager, and keeps what
      a rebuild needs; `open` creates `PostgresLlmProfileStore` and runs its `setup`; a `model`
      instance passed to `assemble` is treated as the environment's (R1, R18, R19)
- [X] T020 [P] Add to `src/freyja_agent/api/schemas.py` the shapes of
      contracts/openapi.yaml: `LlmProfile`, `LlmLastTest`, `LlmTestResult`, `LlmProfileList`,
      `LlmProfileCreate`, `LlmProfileUpdate`, `LlmProfileDraft`, and `RunProfile`; the request
      models with `extra="forbid"` and the limits "1 to 64", "1 to 200", "at most 2,000", and
      "1 to 4,000" characters; no response model has a key field; and add to `ErrorDetail`
      the eight codes of the contract: `profile_not_found`, `profile_read_only`,
      `profile_active`, `profile_unusable`, `profile_limit`, `name_taken`,
      `secret_not_configured`, `test_busy` (FR-006, FR-020, R10)
- [X] T021 Change `src/freyja_agent/api/streaming.py` so that `run_stream` asks the manager for
      the current orchestrator before emitting `start`, uses that one reference for the whole
      run, adds `profile` (`id`, `name`, `source`) to `start`, and logs one INFO line per run
      with the same three values (FR-013, FR-024a, R2, R11, contracts/sse-events.md)
- [X] T022 Add to `tests/fakes.py` a scripted model that carries a label and can hold one call
      open until the test releases it, and a model factory that returns one per profile and
      records what it built; add to `tests/conftest.py` a `make_llm_app` fixture that assembles
      an app the production way with an in-memory profile store, a secret, and that factory,
      and let `make_a2a_app` take the same store and factory so a test can have stand-in agents
      and profiles together; with neither given, existing fixtures behave as before (R18)
- [X] T023 Write `tests/test_llm_switch.py` part 1: with no saved profile, a run's `start` names
      the environment's profile with `source` `environment` and the log has one line for the
      run; a second runtime on the same store reads the same state; the model factory was
      called once across two runs (FR-011, FR-024a, SC-010, R6)

**Checkpoint**: `uv run pytest` passes with every earlier test unchanged (FR-023, SC-006); the
service starts and runs as before, and each run's first event names the environment's profile.

---

## Phase 3: User Story 1 - Open the console and see which model Freyja runs on (Priority: P1) 🎯 MVP

**Goal**: The web app has a sidebar with LLM and Agents. The LLM section shows the environment's
model as a read-only card marked active; the Agents section is a placeholder.

**Independent Test**: with a service configured through the environment only, open the web app:
it lands on `/llm`, the sidebar marks LLM, one card shows the environment's provider and model
with four key characters, and it has no Edit or Delete. Runs through the API behave as before.

### Tests for User Story 1

- [ ] T024 [P] [US1] Write `tests/test_llm_profiles_api.py` part 1: `GET /llm/profiles` returns
      the environment's profile first with `id` `environment`, `source` `environment`,
      `active` true, its provider, model, base URL, and `key_hint`; `active_id` is
      `environment`; `usable_model` is true; `secret_configured` follows the setting; `limit` is
      50; no model endpoint is contacted; the response has no key (FR-007, FR-008, FR-011, US1
      scenarios 2 and 3)
- [ ] T025 [P] [US1] Write `web/lib/api.test.ts`: a request goes to `/api` plus its path; a 2xx
      body is returned parsed; a refusal raises an error carrying the service's `code` and
      `message`; a 422 raises one carrying each field's message; a network failure raises an
      error that says the service cannot be reached. Write `web/lib/llm.test.ts`: `listProfiles`
      asks for `/llm/profiles` and returns the list (FR-003, FR-007)
- [ ] T026 [P] [US1] Write `web/components/app-sidebar.test.tsx`: it lists LLM and Agents as
      links to `/llm` and `/agents` and marks the one matching the current path. Write
      `web/app/agents/page.test.tsx`: the page says agents are not managed from the console yet
      and has no button, link, or field of its own. Extend `web/next.config.test.ts`:
      `redirects()` sends `/` to `/llm` and is not permanent (FR-001, FR-002, US1 scenarios 1, 4,
      5)
- [ ] T027 [P] [US1] Write `web/components/llm/profile-card.test.tsx`: a card shows the name,
      provider label, model id, base URL when there is one, the API choice for `openai` only,
      the four key characters, and the marks for active, from the environment, and not usable;
      the environment's card has no Edit and no Delete control (FR-007, FR-008, US1 scenarios 2
      and 3)
- [ ] T028 [P] [US1] Write `web/components/llm/profile-list.test.tsx`: it shows a loading state,
      then one card per profile; when the service cannot be reached it says so, offers to try
      again, and shows no card; a refusal shows the service's reason; when `usable_model` is
      false it says that no usable model is active (FR-003, FR-016, US1 scenario 6)

### Implementation for User Story 1

- [ ] T029 [US1] Create `src/freyja_agent/api/llm.py` with a router under `/llm` whose routes
      return validation errors without `input`, and `GET /llm/profiles` built from the store,
      the environment's profile, and the manager, with `usable` and `unusable_reason` worked out
      for every profile from this instance's secret and environment; include the router in
      `src/freyja_agent/api/app.py` (FR-007, FR-008, FR-011a, R9, R10)
- [ ] T030 [P] [US1] Implement `web/lib/api.ts`: one function that calls `/api` plus a path with
      JSON, and one error type with the service's code, message, and field messages (FR-003,
      R13)
- [ ] T031 [US1] Implement `web/lib/llm.ts`: the TypeScript types of contracts/openapi.yaml
      (`LlmProfile`, `LlmProfileList`, `LlmLastTest`), with no key field, and `listProfiles`
      (FR-007, R14)
- [ ] T032 [P] [US1] Implement `web/components/app-sidebar.tsx` as a Client Component on the
      shadcn sidebar, with the two links and the active mark from `usePathname()`; it collapses
      on a narrow screen (FR-001, R15)
- [ ] T033 [US1] Make `web/app/layout.tsx` the shell: the sidebar and the page, with the app's
      title and description in its metadata; it stays a Server Component (FR-001, R15)
- [ ] T034 [P] [US1] Implement `web/components/llm/profile-card.tsx` (FR-007)
- [ ] T035 [US1] Implement `web/components/llm/profile-list.tsx` as a Client Component that
      reads the list with SWR and renders the states of T028 (FR-003, FR-007, R14)
- [ ] T036 [US1] Create `web/app/llm/page.tsx` and `web/app/agents/page.tsx`, the second saying
      that agents are not managed from the console yet and offering no control; delete
      `web/app/page.tsx`; add `redirects()` from `/` to `/llm` with `permanent: false` in
      `web/next.config.ts` (FR-002, R15)
- [ ] T037 [US1] Run quickstart section 1 by hand in a browser, including a narrow window and
      the service stopped. Watch the browser console for a hydration warning from
      `usePathname()`; if there is one, apply the documented fix in
      `web/components/app-sidebar.tsx` and record it in plan.md (R15, research "Not verified")

**Checkpoint**: quickstart section 1 works; User Story 1 is complete and can be merged alone.

---

## Phase 4: User Story 2 - Add a profile and switch Freyja to it without a restart (Priority: P2)

**Goal**: An operator adds a profile and activates it. The next run uses it, with no restart; a
run in progress keeps its model; the choice survives a restart and reaches every instance.

**Independent Test**: add a profile for the stand-in through the console, activate it, and
start a run: its first event names the new profile. Restart and run again: the same. Activate
the environment's card: the next run names the environment.

### Tests for User Story 2

- [ ] T038 [P] [US2] Write `tests/test_llm_profiles_api.py` part 2: `POST /llm/profiles` answers
      201 with the saved profile, not active, with the key's four characters and no key; a
      missing value, a name of 65 characters, a model of 201, a base URL that is not a web
      address, and a key of 4,001 characters are each refused with 422 naming the field and
      never repeating a value; a used name in another case and the name `Environment` are
      refused with `name_taken`; the 51st is refused with `profile_limit`; with no secret it is
      refused with `secret_not_configured`; a body that is not JSON is refused; `POST
      .../activate` answers the whole list with one active profile; an unknown id is
      `profile_not_found`; activating the active one changes nothing; `environment` can be
      activated; each add and each activation is logged with the profile's name and no key;
      with 50 profiles saved, one request returns all 51 entries (FR-004 to FR-006, FR-011,
      FR-012, FR-024, SC-008, US2 scenarios 1, 2, 7, 9)
- [ ] T039 [P] [US2] Write `tests/test_llm_switch.py` part 2: after an activation the next run
      on that instance uses the new profile, named in `start`; a run held open while another
      profile is activated ends normally on the model it started with, and the next run on the
      same thread uses the new one with the thread's messages intact; a run paused on a
      proposal and resumed after an activation continues on the new profile; a background run
      started after an activation uses it; a new runtime on the same store starts on the saved
      active profile whatever the environment names; a second runtime sharing the store uses
      the new profile at its next run; activating `environment` returns to the environment's
      model (FR-013, FR-014, FR-015, FR-024a, SC-002, SC-003, SC-007, SC-010, US2 scenarios 3
      to 7, 10, 11)
- [ ] T040 [P] [US2] Write `web/components/llm/profile-form.test.tsx` for adding: the form has
      name, provider, model id, base URL, and key; the API choice is offered for an
      OpenAI-compatible endpoint only and is on by default; a missing value, a name that is
      taken, and a base URL that is not a web address each show which field is wrong and why
      and send nothing more; a save sends the values once; after the form is sent or closed its
      key field is empty when opened again, and nothing was written to `localStorage` or
      `sessionStorage` (FR-003, FR-004, FR-006, US2 scenarios 8 and 9, US5 scenario 8)
- [ ] T041 [P] [US2] Write `web/components/llm/activate-dialog.test.tsx`: Set active opens a
      confirmation that names the profile and says existing conversations will be sent to its
      endpoint from their next run; confirming sends one activation and the list then shows
      exactly one active card; cancelling sends nothing. Extend `web/lib/llm.test.ts`:
      `createProfile` and `activateProfile` send what the contract asks (FR-012, US2 scenarios 2
      and 12)

### Implementation for User Story 2

- [ ] T042 [US2] Add `POST /llm/profiles` to `src/freyja_agent/api/llm.py`: validate, refuse
      the reserved name, encrypt the key, store its hint, save, and log the addition without
      the key (FR-004 to FR-006, FR-020, FR-024, R10)
- [ ] T043 [US2] Add `POST /llm/profiles/{profile_id}/activate` to
      `src/freyja_agent/api/llm.py`: refuse an unusable profile, change the state in one
      statement, rebuild on this instance at once, log it, and answer with the list (FR-012,
      FR-013, FR-024, R6)
- [ ] T044 [US2] Add `createProfile` and `activateProfile` to `web/lib/llm.ts` (R14)
- [ ] T045 [P] [US2] Implement `web/components/llm/profile-form.tsx` as a dialog for adding,
      which resets its state, the key included, whenever it opens or closes (FR-003, FR-004,
      R9, R14)
- [ ] T046 [P] [US2] Implement `web/components/llm/activate-dialog.tsx` (FR-012)
- [ ] T047 [US2] Add the Add button to `web/components/llm/profile-list.tsx` and Set active to
      `web/components/llm/profile-card.tsx`; after either, the list is read again (FR-007)

**Checkpoint**: quickstart section 2 works against a model factory or the stand-in; a switch
needs no restart and interrupts no run.

---

## Phase 5: User Story 3 - Test a profile before relying on it (Priority: P3)

**Goal**: A test holds a two-step conversation with a profile's endpoint and reports which step
failed. A card keeps its last result, and activation warns when the profile has not passed.

**Independent Test**: test a profile whose endpoint refuses a conversation: the failure names
the second step and the card shows it; Set active warns. Test one that works: it passes and the
warning is gone. Nothing else changed.

### Tests for User Story 3

- [ ] T048 [P] [US3] Write `tests/llm_stub.py`: an OpenAI-compatible stand-in serving chat
      completions and the Responses API in the test process, recording every request. It
      behaves by model id: `refuse-conversation` answers a first message and refuses a request
      carrying earlier turns with a 422; `repeat-key` fails with an error that repeats the key
      it was sent; `reject-key` answers 401 to every request; `never-answer` does not answer;
      `slow` answers after 20 seconds; any other id answers everything. With `--http <port>` it
      listens on loopback for the quickstart (R18, R20)
- [ ] T049 [P] [US3] Write `tests/test_llm_check.py` against the stand-in, through the real
      `ChatOpenAI` client, over chat completions and over the Responses API: a working endpoint
      passes after exactly two requests, the second carrying the first user message, an
      assistant turn with a tool call, its result, a second user message, and the tool's
      definition; `refuse-conversation` fails on `conversation` with the endpoint's reason; an
      unreachable address and `reject-key` fail on `first_message`; `never-answer` ends at the
      time limit, reporting that the endpoint did not answer in time; `repeat-key` gives a
      reason without the key; a reason is cut at 500 characters; no retry is made (FR-018,
      FR-019, FR-021, SC-005, US3 scenarios 1 to 3, 8)
- [ ] T050 [P] [US3] Write `tests/test_llm_profiles_api.py` part 3: `POST
      /llm/profiles/{id}/test` answers 200 with the result and `recorded` true, and the list
      then shows it; a result for a profile changed or deleted while the test ran is returned
      with `recorded` false and not kept; the environment's result is kept until a new runtime
      starts; `POST /llm/test` tests unsaved values, saves nothing, and keeps no result; with
      `profile_id` and no key it uses the stored key; with neither it is refused with 422; a
      second test of a profile being tested, and a fifth test on the instance, are refused with
      `test_busy` and reach no endpoint; an unreadable key is `profile_unusable`; a test
      changes no profile's values, not the active profile, and no thread (FR-017, FR-019,
      FR-019a, FR-019b, US3 scenarios 4 to 7, 9)
- [ ] T051 [P] [US3] Extend `web/components/llm/profile-card.test.tsx`,
      `web/components/llm/activate-dialog.test.tsx`, and
      `web/components/llm/profile-form.test.tsx`: Test on a card shows the result when it ends,
      with the failed step and reason; a card shows whether its last test passed or failed and
      the time the service gave, or that it has not been tested since its last change; the
      activation confirmation warns and offers to test first when the last test did not pass or
      there is none, and still lets the operator confirm; Test in the form sends the form's
      values, and in the Edit form an empty key sends the profile's id instead. Extend
      `web/lib/llm.test.ts`: `testProfile` and `testDraft` send what the contract asks (FR-007,
      FR-012, FR-017, SC-011, US3 scenarios 4, 5, 9 to 11)

### Implementation for User Story 3

- [ ] T052 [US3] Implement `src/freyja_agent/llm/check.py`: build the model with no retries,
      send the two steps of data-model.md under one `asyncio.timeout` of
      `llm_test_timeout_seconds`, and return the step that failed with a reason cleaned by
      `redact` and cut to 500 characters (FR-018, FR-019, FR-021, R7)
- [ ] T053 [US3] Add `POST /llm/profiles/{profile_id}/test` and `POST /llm/test` to
      `src/freyja_agent/api/llm.py`: count tests in progress per instance, one per listed
      profile and four in all, and refuse beyond either before a model is built; record a
      listed profile's result against the `config_version` read at the start; keep the
      environment's result in memory (FR-017, FR-019a, FR-019b, R7)
- [ ] T054 [US3] Add `testProfile` and `testDraft` to `web/lib/llm.ts` (R14)
- [ ] T055 [US3] Add Test and the last result to `web/components/llm/profile-card.tsx`, the
      warning to `web/components/llm/activate-dialog.tsx`, and Test to
      `web/components/llm/profile-form.tsx`; show the time the service returned, never one
      computed during render (FR-007, FR-012, FR-017, R14)

**Checkpoint**: quickstart section 3 works with the stand-in served on loopback.

---

## Phase 6: User Story 4 - Change and remove profiles (Priority: P4)

**Goal**: An operator edits a saved profile, keeps or replaces its key, and deletes one that is
not active. The environment's profile can be neither edited nor deleted.

**Independent Test**: edit a profile's model id and see the card change; edit the active one,
confirm, and see the next run use the new value; save with the key empty and test that it still
works; delete an inactive profile; see the active one refuse deletion.

### Tests for User Story 4

- [ ] T056 [P] [US4] Write `tests/test_llm_profiles_api.py` part 4: `PATCH` changes only the
      fields sent; a key left out keeps the stored key and its four characters; a key sent
      replaces both; a change of provider, model, base URL, key, or API choice clears the last
      test result and a change of name keeps it; a used name, and the name `Environment`, are
      `name_taken`; a key sent with
      no secret is `secret_not_configured`; `PATCH` and `DELETE` on `environment` are
      `profile_read_only`; `DELETE` of an inactive profile answers 204 and it is gone, also for
      a new runtime; `DELETE` of the active profile is `profile_active` with a message to
      activate another first; an unknown id is `profile_not_found`; each change and deletion is
      logged without a key (FR-005, FR-008 to FR-010, FR-019a, FR-024, US4 scenarios 1, 2, 4 to
      7)
- [ ] T057 [P] [US4] Write `tests/test_llm_switch.py` part 3: an edit of the active profile is
      used by the next run on this instance and on a second runtime sharing the store, with no
      restart; a run in progress during the edit finishes on the values it started with; a run
      in progress on a profile that is then deactivated and deleted finishes normally (FR-013,
      FR-014, US4 scenario 3, spec Edge Cases)
- [ ] T058 [P] [US4] Extend `web/components/llm/profile-form.test.tsx` and write
      `web/components/llm/delete-dialog.test.tsx`: the Edit form opens with the profile's
      values and an empty key; saving with the key empty sends no key; saving a change to the
      active profile first asks for confirmation, saying the change applies from the next run,
      and sends nothing when cancelled; Delete asks for confirmation and removes the card; the
      refusal for the active profile is shown as the service worded it; the environment's card
      offers neither. Extend `web/lib/llm.test.ts`: `updateProfile` and `deleteProfile` send
      what the contract asks (FR-008 to FR-010, US4 scenarios 1 to 6)

### Implementation for User Story 4

- [ ] T059 [US4] Add `PATCH /llm/profiles/{profile_id}` to `src/freyja_agent/api/llm.py`:
      refuse the environment's profile and the reserved name, apply the fields sent, encrypt a
      new key, and, when the
      profile is the active one, rebuild on this instance at once; log it (FR-005, FR-009,
      FR-013, FR-024)
- [ ] T060 [US4] Add `DELETE /llm/profiles/{profile_id}` to `src/freyja_agent/api/llm.py`:
      refuse the environment's profile and the active one, delete, and log it (FR-005, FR-010,
      FR-024)
- [ ] T061 [US4] Add `updateProfile` and `deleteProfile` to `web/lib/llm.ts` (R14)
- [ ] T062 [P] [US4] Implement `web/components/llm/delete-dialog.tsx` (FR-010)
- [ ] T063 [US4] Give `web/components/llm/profile-form.tsx` its editing mode with the
      confirmation for the active profile, and add Edit and Delete to
      `web/components/llm/profile-card.tsx` for saved profiles only (FR-008 to FR-010)

**Checkpoint**: quickstart section 4 works.

---

## Phase 7: User Story 5, part 1 - Running without a usable model (Priority: P5)

**Goal**: The service starts when no usable model is active. A run ends with
`no_usable_model`, nothing falls back to another profile, and no background run starts.

**Independent Test**: start the service with an active profile whose key cannot be read: it
starts, the console says no usable model is active, and a run ends at once with
`no_usable_model`. Activate a usable profile and send the message again: the run completes.

### Tests for User Story 5, part 1

- [ ] T064 [P] [US5] Write `tests/test_llm_no_model.py`: a runtime whose environment names no
      usable model, and one whose active saved profile cannot be read, both start; the list
      reports `usable_model` false and the profile's `unusable_reason`; a run emits `start`
      naming the active profile and then `error` with code `no_usable_model`, invokes no model,
      and adds nothing to the thread; a resume ended that way leaves the proposal pending and
      it can be decided once a usable profile is active; the service does not switch to the
      environment's profile; `GET /healthz` still answers `{"status": "ok"}`; with an agent's
      outcome ready, the worker starts no background run and uses up no attempt, and after a
      usable profile is activated the outcome is delivered; a refused run and, at startup, each
      unusable profile are logged with the reason and no key; activating a usable profile makes
      the next run succeed with no restart (FR-016, FR-016a, FR-024, SC-013,
      contracts/sse-events.md)

### Implementation for User Story 5, part 1

- [ ] T065 [US5] In `src/freyja_agent/llm/active.py` and `src/freyja_agent/runtime/runtime.py`,
      stop raising when the active profile cannot be turned into a model: record why, build the
      orchestrator around the placeholder model, and log each unusable profile at startup
      (FR-016, FR-024, R8)
- [ ] T066 [US5] In `src/freyja_agent/api/streaming.py`, when no usable model is active, emit
      `start` and then `error` with code `no_usable_model` and the message of
      contracts/sse-events.md without invoking the graph, release the lease, and log the
      refusal. The code belongs to the stream's `error` event, like `step_limit` and
      `model_error`; it is not added to `ErrorDetail`, which is the HTTP refusal shape (FR-016,
      FR-024, R8)
- [ ] T067 [US5] In `src/freyja_agent/delegates/delegation_worker.py`, start no delivery while
      no usable model is active, leaving ready outcomes ready and their attempts untouched
      (FR-016a, R8)

**Checkpoint**: quickstart section 6 works; every earlier test still passes.

---

## Phase 8: User Story 5, part 2 - API keys stay secret (Priority: P5)

**Goal**: Proof that a key appears nowhere beyond its last four characters, that a missing,
changed, or invalid secret behaves as specified, and that a page from another origin cannot use
the API.

**Independent Test**: save a profile with a recognizable key and search every response, the
log, every thread, and the stored profile: only its last four characters appear. Start without
the secret: saving is refused and the environment's model still works.

### Tests for User Story 5, part 2

- [ ] T068 [P] [US5] Write `tests/test_llm_secrets_nowhere.py`: with one recognizable key saved
      in a profile, drive every `/llm` route, including each refusal and each 422, a test that
      passes and one that fails, and a run, a resume, and a failed run on that profile; the
      key's text appears in no response body, no event of any stream, no thread state, and no
      log record, and the stored row holds a token that does not contain it; a key of seven
      characters shows no character on its card; an endpoint that repeats the key in an error
      gives a `model_error` message and a log line without it (FR-020, FR-021, SC-004, US5
      scenarios 1 to 3, 6)
- [ ] T069 [US5] Write `tests/test_llm_profiles_api.py` part 5: with no secret, adding a
      profile is `secret_not_configured`, the environment's profile lists and runs as before,
      and saved profiles are listed unusable with `secret_not_configured`; with a secret other
      than the one the keys were stored under, the runtime starts, each such profile is listed
      unusable with `key_unreadable` and cannot be activated or tested, and sending its key
      again makes it usable; an `OPTIONS` preflight from another origin to every `/llm` route
      gets no `access-control-allow-*` header; every route that takes a body refuses one sent
      as `text/plain` (FR-003a, FR-022, SC-012, US5 scenarios 4, 5, 9)
- [ ] T070 [P] [US5] Extend `web/lib/llm.test.ts` and
      `web/components/llm/profile-card.test.tsx`: no type the web app receives has a key field;
      a profile that is not usable shows why, offers no Set active, and for a saved one offers
      Edit so its key can be entered again (FR-007, FR-020, FR-022, US5 scenario 5)

### Implementation for User Story 5, part 2

- [ ] T071 [US5] In `src/freyja_agent/api/streaming.py`, clean the `model_error` message and
      its log line with `redact` and the manager's secrets (FR-021, R9)
- [ ] T072 [US5] Fix in `src/freyja_agent/api/llm.py` and `src/freyja_agent/llm/active.py`
      whatever T068 and T069 still find. The usable state and its reason are reported since
      T029, and an unusable profile is refused by activation since T043 and by the test since
      T053; this task closes what the proof turns up (FR-011a, FR-012, FR-022)
- [ ] T073 [US5] Show the unusable state and its reason on `web/components/llm/profile-card.tsx`
      (FR-007, FR-022)

**Checkpoint**: quickstart section 5 works; User Story 5 is complete.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T074 [P] Update `README.md`: the web console and how to run it, LLM profiles and the
      active profile, `FREYJA_SECRET_KEY` and the test limit in the settings table, the
      service starting without a usable model, `profile` in a run's first event, the
      stand-in's command, and the web app's `FREYJA_API_URL`; only what now exists
- [ ] T075 [P] Update `AGENTS.md`: Layout entries for the `llm/` package, `api/llm.py`,
      `tests/llm_stub.py`, and the web app's pages, components, and `lib/`; the `model_factory`
      seam; the two settings; the Commands entry `uv run python -m tests.llm_stub --http 9997`;
      and that web tests are `*.test.ts` and `*.test.tsx` run with jsdom
- [ ] T076 [P] Update `specs/001-thread-runs-api/contracts/sse-events.md` so the stream
      contract names `start.profile` and the `no_usable_model` code where a reader looks first,
      pointing at `specs/004-web-llm-profiles/contracts/sse-events.md`
- [ ] T077 [P] Update `specs/004-web-llm-profiles/plan.md`: list `tasks.md`,
      `tests/test_llm_providers.py`, and `tests/test_llm_secrets_nowhere.py` in its trees, and
      record under Implementation Notes what T005, T007, T009, and T037 found and anything the
      implementation settled differently from the plan
- [ ] T078 Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest`,
      `FREYJA_TEST_DATABASE_URI=... uv run pytest tests/integration`, in `web/` `pnpm lint`,
      `pnpm test`, and `pnpm build`, and `uv run pre-commit run --all-files`; confirm no Ruff
      suppression was added and that both default suites pass with the network disconnected
      (FR-025, principle V)
- [ ] T079 Run `specs/004-web-llm-profiles/quickstart.md`, all seven sections, against the real
      service, the stand-in on loopback, and the web app in a browser; section 7 repeats the
      001 quickstart with no saved profile. Then save 50 profiles through the API and time the
      LLM section from navigation to the last card: within two seconds (FR-023, SC-001, SC-006,
      SC-008)
- [ ] T080 Re-run the Constitution Check of `specs/004-web-llm-profiles/plan.md` against the
      implemented code and note the result in the pull request description (principles I to VI)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies. T002, T004, T005, T008 run in parallel after T001;
  T003 follows T002; T006 and T007 follow T005, in that order; T009 follows T008.
- **Foundational (Phase 2)**: depends on Phase 1. Blocks every user story.
- **User Story 1 (Phase 3)**: depends on Phase 2. The MVP.
- **User Story 2 (Phase 4)**: depends on Phase 3: it adds to the router, the list, and the card
  that Phase 3 creates.
- **User Story 3 (Phase 5)**: depends on Phase 4: a test needs a saved profile, and the warning
  sits in the activation dialog.
- **User Story 4 (Phase 6)**: depends on Phase 4. It does not depend on Phase 5, apart from the
  test result an edit clears, which Phase 2's store already handles.
- **User Story 5, part 1 (Phase 7)**: depends on Phase 4: an unusable active profile needs
  activation.
- **User Story 5, part 2 (Phase 8)**: depends on Phases 3 to 7: it drives every route and the
  state with no usable model.
- **Polish (Phase 9)**: depends on all stories.

### Within Phase 2

- T010, T011, T012, T013, T014, T020 have no dependency inside the phase.
- T015 before T016 (same file); T012 before T015. T016 before T017.
- T013, T014, T015 before T018. T018 before T019. T019 before T021 and T022.
- T021 and T022 before T023.

### Within Each User Story

- Tests first, and they must fail before implementation starts.
- Tasks that share a file run in the order listed: `tests/test_llm_profiles_api.py` grows by
  parts in T024, T038, T050, T056, T069; `tests/test_llm_switch.py` in T023, T039, T057;
  `src/freyja_agent/api/llm.py` in T029, T042, T043, T053, T059, T060, T072;
  `web/lib/llm.ts` in T031, T044, T054, T061, and its test file in T025, T041, T051, T058;
  `web/next.config.test.ts` in T006 and T026; the card, list, and form components likewise.

### Parallel Opportunities

- Phase 1: T002, T004, T005, T008 together.
- Phase 2: T010 to T014 and T020 together; T017 alongside T018.
- Phase 3: T024 to T028 together; then T030, T032, T034 together, alongside T029.
- Phase 4: T038 to T041 together; T045 and T046 together, alongside T042.
- Phase 5: T048 to T051 together.
- Phase 6: T056 to T058 together; T062 alongside T059.
- Phase 6 can be built at the same time as Phase 5, since both depend on Phase 4 only.
- Phase 8: T068 and T070 together.
- Phase 9: T074 to T077 together.

---

## Parallel Example: User Story 2

```bash
# Write the tests, then watch them fail:
Task: "Add and activate in tests/test_llm_profiles_api.py part 2 (T038)"
Task: "Switching, runs in progress, pauses, restarts, two instances in tests/test_llm_switch.py part 2 (T039)"
Task: "The Add form in web/components/llm/profile-form.test.tsx (T040)"
Task: "The activation confirmation in web/components/llm/activate-dialog.test.tsx (T041)"

# Then implement:
Task: "POST /llm/profiles, then activate, in src/freyja_agent/api/llm.py (T042, T043)"
Task: "The form dialog in web/components/llm/profile-form.tsx (T045)"
Task: "The activation dialog in web/components/llm/activate-dialog.tsx (T046)"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup.
2. Complete Phase 2: Foundational. The service obtains its model through the manager and
   behaves as before; nothing stores a key yet.
3. Complete Phase 3: User Story 1.
4. **STOP and VALIDATE**: `uv run pytest`, `pnpm test` in `web/`, then quickstart section 1 in
   a browser.
5. Open a pull request and a merge request if the increment should merge on its own.

### Incremental Delivery

1. Setup + Foundational: the model comes through the manager; every earlier test passes
   unchanged.
2. User Story 1: the console shows which model Freyja runs on. First increment worth showing.
3. User Story 2: the model can be changed from the console without a restart. This is the
   feature's purpose; keys are encrypted from the first one stored.
4. User Story 3: a profile can be tested before it is relied on.
5. User Story 4: profiles can be changed and removed.
6. User Story 5: the service runs without a usable model, and the proof that keys stay secret.
7. Polish: documentation, the full quickstart, the gates.

Each phase is one pull request and one merge request, as in features 001 to 003. Phases 5 and 6
can be opened in either order.

### Parallel Team Strategy

With more than one developer: after Phase 2, one takes the service tasks of a story and another
its web tasks; the contract in `contracts/openapi.yaml` is what they share. Phases 5 and 6 can
proceed at the same time once Phase 4 is merged.

---

## Notes

- [P] tasks touch different files and depend on no incomplete task.
- [Story] labels map a task to its user story for traceability.
- Verify that tests fail before implementing.
- Commit after each task or logical group, in the `type: summary` form.
- Stop at any checkpoint to validate a story on its own.
- Four tasks check something the plan could not verify without a network or a browser: T005,
  T007, T009, and T037. Each records what it found in plan.md.
- Test files added beyond the plan's tree: `tests/test_llm_providers.py` (T011),
  `tests/test_llm_secrets_nowhere.py` (T068), `web/next.config.test.ts` (T006), and
  `web/app/agents/page.test.tsx` (T026). T077 brings the plan in line.
- The analysis of 2026-11-17 found that constitution principle IV, which names the `Settings`
  class, did not cover the web app's one variable, `FREYJA_API_URL`. Constitution 1.4.0
  resolved it: the web app reads its deployment configuration in `web/next.config.ts` and
  documents it in `web/.env.example`, as T008 and T009 do. T008 still names the variable in
  the root `.env.example`, as a pointer.
