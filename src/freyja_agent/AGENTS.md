# `src/freyja_agent/`

The service's package. This file lists the settings of `config.py`; what each directory here
holds is in that directory's own `AGENTS.md`. The commands, the conventions, and the map of the
other directories are in the `AGENTS.md` at the root of the repository.

- `FREYJA_MODEL` names the model, and
  `FREYJA_OPENAI_RESPONSES_API` says whether an `openai:` model is called through the Responses
  API or through chat completions.

- `FREYJA_ALLOWED_HOSTS` lists the host names the service
  answers for beside `localhost` and IP addresses.

- `McpServerConfig` is one entry of `FREYJA_MCP_SERVERS`;
  `FREYJA_MCP_CALL_TIMEOUT_SECONDS` and `FREYJA_MCP_STARTUP_TIMEOUT_SECONDS` are the two time
  limits for tool servers.

- `A2aAgentConfig` is one entry of `FREYJA_A2A_AGENTS` (a loopback
  `url` and `read_only`); agent and server names share one namespace. Seven limits go with it:
  `FREYJA_A2A_CALL_TIMEOUT_SECONDS`, `FREYJA_A2A_STARTUP_TIMEOUT_SECONDS`,
  `FREYJA_A2A_POLL_INTERVAL_SECONDS`, `FREYJA_A2A_TASK_MAX_AGE_SECONDS`,
  `FREYJA_BACKGROUND_RUN_ATTEMPTS`, `FREYJA_BACKGROUND_RETRY_SECONDS`, and
  `FREYJA_BACKGROUND_RUN_CONCURRENCY`.

- Two settings belong to LLM profiles: `FREYJA_SECRET_KEY`,
  a Fernet key or unset, which protects stored API keys and stops startup when it is not a
  Fernet key, and `FREYJA_LLM_TEST_TIMEOUT_SECONDS`, the most a profile's test may take.

- Ten belong to receiving tasks from other agents (feature 009), checked only while it is
  on: `FREYJA_A2A_SERVER`, `FREYJA_A2A_SERVER_WAIT_SECONDS`, `FREYJA_A2A_SERVER_CALLER_TASKS`,
  `FREYJA_A2A_SERVER_TASKS`, `FREYJA_A2A_SERVER_MAX_HOPS`, the hand-over count at which a
  caller's message is refused, `FREYJA_A2A_SERVER_FILES_DIR`, which may not be the workspace,
  be inside it, or contain it, `FREYJA_A2A_SERVER_CALLERS`, named callers with their tokens
  (`A2aCallerConfig`, a token held as `SecretStr`), and the outside listener's
  `FREYJA_A2A_SERVER_HOST`, `FREYJA_A2A_SERVER_PORT`, and `FREYJA_A2A_SERVER_URL`, whose host
  without its port, or the listener set while the feature is off, stops startup also then.
