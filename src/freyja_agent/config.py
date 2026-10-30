from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from FREYJA_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="FREYJA_", extra="ignore")

    # Local runs listen only on this machine. Containers set FREYJA_HOST=0.0.0.0.
    host: str = "127.0.0.1"
    port: int = 8000
    # Carries credentials, so it has no default: startup fails until FREYJA_DATABASE_URI is set.
    database_uri: str
    model: str = "anthropic:claude-sonnet-5-5"
    # Root of the agent's file tools; skills/ inside it is committed (FR-017).
    workspace_dir: str = "workspace"
    # LangGraph recursion limit per run; reaching it ends the run with an error (FR-004a).
    max_steps: int = 50
    # Expiry of the per-thread run lock; the heartbeat renews every third of it (FR-006).
    run_lock_ttl_seconds: int = 60


settings = Settings()
