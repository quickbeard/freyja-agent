# `src/freyja_agent/llm/`

The LLM profiles (feature 004), module by module. The commands, the conventions, and the map of
the other directories are in the `AGENTS.md` at the root of the repository.

- `providers.py` holds the
  provider table, `build_model` for a saved profile, and the environment's profile, and is
  the one module that reads a provider's own variables (`environment_key`). A saved profile's
  client is built with its provider's variables hidden (`build_model`), and for the
  provider's own address when it has no base URL, so nothing the environment sets for its own
  model reaches a saved profile's endpoint, apart from a proxy and a telemetry opt-out; the
  environment's profile is described from what its built client holds and will send
  (`environment_access`), a LangSmith gateway and a credential in the SDK's custom headers
  included;

- `secrets.py` encrypts stored API keys under `FREYJA_SECRET_KEY` and removes keys from text (`redact`);

- `guard.py` makes a model raise its errors with its profile's key already taken out
  (`guard_model`), because LangGraph shows a failed step's error in the debug stream and
  saves it with the thread;

- `store.py` is the `LlmProfileStore` for saved profiles
  and the active choice (`PostgresLlmProfileStore` with `freyja_llm_profiles` and
  `freyja_llm_state`, and `InMemoryLlmProfileStore`);

- `active.py` is the `ModelManager`, which
  rebuilds the orchestrator through `build_agent` when the active profile's revision moved,
  or when the agent set it is handed is not the one it built for,
  around a placeholder model when the active profile is not usable, so that the service
  starts and runs end with `no_usable_model`, and which guards every model it builds;

- `check.py` is a profile's test (`check_model`: two requests under one time limit, the
  second carrying earlier turns and a tool call) and the count of tests in progress
  (`CheckSlots`).
