"""Logging setup shared by the entry points (sync, CLI, and later the Discord bot)."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

LOG_FILE_MAX_BYTES = 5 * 1024 * 1024
LOG_FILE_BACKUPS = 3
"""The log file rotates at 5 MB and keeps 3 old files, so it never grows without bound."""

NOISY_LOGGERS = ("httpx", "httpx2", "httpcore", "anthropic")
"""Third-party loggers that log every HTTP request: only shown at DEBUG level."""

# Marks the handlers installed here, so that a second call replaces them instead of
# duplicating every line.
_HANDLER_MARK = "_esport_agent_handler"


def setup_logging(level: str, log_file: Path | None = None) -> None:
    """Send logs of `level` and above to standard error, and to `log_file` if given."""
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
        root.removeHandler(handler)
        handler.close()

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_file,
                maxBytes=LOG_FILE_MAX_BYTES,
                backupCount=LOG_FILE_BACKUPS,
                encoding="utf-8",
            )
        )
    formatter = logging.Formatter(LOG_FORMAT)
    for handler in handlers:
        handler.setFormatter(formatter)
        setattr(handler, _HANDLER_MARK, True)
        root.addHandler(handler)

    root.setLevel(level)
    third_party_level = logging.DEBUG if root.level <= logging.DEBUG else logging.WARNING
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(third_party_level)
