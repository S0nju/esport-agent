import sqlite3
from collections.abc import Iterator

import pytest

from esport_agent.config import Settings
from esport_agent.db import connect, init_schema


@pytest.fixture
def settings() -> Settings:
    return Settings(anthropic_api_key="test-key", _env_file=None)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = connect(":memory:")
    init_schema(connection)
    yield connection
    connection.close()
