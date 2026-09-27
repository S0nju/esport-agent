"""Application configuration, loaded from the environment and the `.env` file."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings. Each field maps to an environment variable."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: SecretStr
    claude_model: str = "claude-haiku-4-5-20251001"
    default_team: str = "Karmine Corp"
    leaguepedia_bot_username: str | None = None
    leaguepedia_bot_password: SecretStr | None = None
    sqlite_path: Path = Path("esport_agent.db")


@lru_cache
def get_settings() -> Settings:
    """Return the single settings instance."""
    return Settings()
