from unittest.mock import MagicMock

import pytest

from esport_agent.config import Settings
from esport_agent.data import leaguepedia


def test_make_client_not_implemented(settings: Settings) -> None:
    with pytest.raises(NotImplementedError):
        leaguepedia.make_client(settings)


def test_fetchers_not_implemented() -> None:
    client = MagicMock()
    with pytest.raises(NotImplementedError):
        leaguepedia.fetch_team_roster(client, "Some Team")
    with pytest.raises(NotImplementedError):
        leaguepedia.fetch_team_matches(client, "Some Team")
