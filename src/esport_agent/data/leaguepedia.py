"""Fetching data from Leaguepedia (Cargo API) through mwrogue.

Leaguepedia rate limits are very low: this module is only called by `sync.py`,
never by the tools.
"""

from mwrogue.esports_client import EsportsClient

from esport_agent.config import Settings


def make_client(settings: Settings) -> EsportsClient:
    """Create a Leaguepedia client, authenticated if bot credentials are configured."""
    raise NotImplementedError


def fetch_team_roster(client: EsportsClient, team: str) -> list[dict[str, str]]:
    """Fetch the raw rows of `team`'s current roster (Cargo table to be determined)."""
    raise NotImplementedError


def fetch_team_matches(client: EsportsClient, team: str) -> list[dict[str, str]]:
    """Fetch the raw rows of `team`'s recent matches (Cargo table to be determined)."""
    raise NotImplementedError
