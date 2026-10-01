"""Application configuration, loaded from the environment and the `.env` file."""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class Settings(BaseSettings):
    """Application settings. Each field maps to an environment variable."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Optional here so each entry point only needs its own key: the CLI needs the
    # Anthropic key, the sync needs the lolesports key. See `require_secret`.
    anthropic_api_key: SecretStr | None = None
    lolesports_api_key: SecretStr | None = None
    claude_model: str = "claude-haiku-4-5-20251001"
    default_team: str = "Karmine Corp"
    leaguepedia_bot_username: str | None = None
    leaguepedia_bot_password: SecretStr | None = None
    sqlite_path: Path = Path("esport_agent.db")
    lolesports_leagues: list[str] = ["lec", "lfl", "worlds", "msi", "first_stand"]
    """Slugs of the lolesports leagues whose schedule is synced."""
    preferred_leagues: list[str] = ["lec"]
    """lolesports league slugs, by priority. When a partial team name matches several teams
    ("Vitality"), the one playing in the first of these leagues is picked."""
    leaguepedia_team_aliases: dict[str, str] = {}
    """lolesports team name -> Leaguepedia page name, for teams no rule can match (e.g.
    {"Team Liquid Alienware": "Team Liquid"}). Checked before any automatic rule."""
    timezone: str = "Europe/Paris"
    """IANA time zone used to show match times and today's date to the agent."""
    log_level: str | None = None
    """DEBUG, INFO, WARNING or ERROR. Unset, each entry point uses its own default: INFO for
    the sync, WARNING for the CLI (so that logs do not mix with the answers)."""
    log_file: Path | None = None
    """Also write logs to this file, rotated at 5 MB."""

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown time zone: {value!r}") from exc
        return value

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, value: str | None) -> str | None:
        if value is None:
            return None
        level = value.strip().upper()
        if level not in LOG_LEVELS:
            raise ValueError(f"Unknown log level: {value!r} (expected one of {LOG_LEVELS})")
        return level

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


class MissingSettingError(RuntimeError):
    """A setting required by the current entry point is not configured."""


def require_secret(value: SecretStr | None, env_var: str) -> str:
    """Return the secret's value, or fail with a message naming the variable to set."""
    if value is None or not value.get_secret_value():
        raise MissingSettingError(f"{env_var} is not set (see .env.example).")
    return value.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    """Return the single settings instance."""
    return Settings()
