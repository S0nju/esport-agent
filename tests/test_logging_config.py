import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from esport_agent.logging_config import (
    LOG_FILE_BACKUPS,
    LOG_FILE_MAX_BYTES,
    NOISY_LOGGERS,
    setup_logging,
)


def own_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if getattr(h, "_esport_agent_handler", False)]


def test_logs_go_to_standard_error_at_the_given_level() -> None:
    setup_logging("INFO")

    assert logging.getLogger().level == logging.INFO
    assert [type(h) for h in own_handlers()] == [logging.StreamHandler]


@pytest.mark.parametrize(("level", "noisy_level"), [("INFO", "WARNING"), ("DEBUG", "DEBUG")])
def test_http_loggers_are_quiet_unless_debugging(level: str, noisy_level: str) -> None:
    setup_logging(level)

    for name in NOISY_LOGGERS:
        assert logging.getLevelName(logging.getLogger(name).level) == noisy_level


def test_log_file_is_rotated(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "esport_agent.log"

    setup_logging("INFO", log_file)
    logging.getLogger("esport_agent.test").info("Synced 3 teams")

    file_handlers = [h for h in own_handlers() if isinstance(h, RotatingFileHandler)]
    assert len(file_handlers) == 1
    assert file_handlers[0].maxBytes == LOG_FILE_MAX_BYTES
    assert file_handlers[0].backupCount == LOG_FILE_BACKUPS
    file_handlers[0].flush()
    line = log_file.read_text(encoding="utf-8").strip()
    assert line.endswith("INFO esport_agent.test Synced 3 teams")


def test_calling_twice_does_not_duplicate_handlers(tmp_path: Path) -> None:
    setup_logging("INFO", tmp_path / "a.log")
    setup_logging("WARNING")

    assert [type(h) for h in own_handlers()] == [logging.StreamHandler]
    assert logging.getLogger().level == logging.WARNING


def test_other_handlers_are_kept() -> None:
    foreign = logging.NullHandler()
    logging.getLogger().addHandler(foreign)

    setup_logging("INFO")

    assert foreign in logging.getLogger().handlers
