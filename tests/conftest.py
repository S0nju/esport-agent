import logging
import sqlite3
from collections.abc import Iterator

import pytest

from esport_agent.config import Settings
from esport_agent.db import connect, init_schema
from esport_agent.logging_config import NOISY_LOGGERS


@pytest.fixture
def settings() -> Settings:
    return Settings(
        anthropic_api_key="test-key", lolesports_api_key="test-lolesports-key", _env_file=None
    )


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = connect(":memory:")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Undo any logging setup done by a test (entry points call `setup_logging`)."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    noisy_levels = {name: logging.getLogger(name).level for name in NOISY_LOGGERS}
    yield
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)
    for name, noisy_level in noisy_levels.items():
        logging.getLogger(name).setLevel(noisy_level)
