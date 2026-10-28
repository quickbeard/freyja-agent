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


settings = Settings()
