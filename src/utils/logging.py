"""Structured logging setup: file handler + Rich console handler."""

from __future__ import annotations

import logging
from pathlib import Path

from rich.logging import RichHandler

_CONFIGURED = False


def setup_logging(
    level: str = "INFO",
    log_file: Path | None = None,
    *,
    rich_console: bool = True,
) -> None:
    """Configure root logging once. Subsequent calls are no-ops."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    handlers: list[logging.Handler] = []
    if rich_console:
        handlers.append(RichHandler(rich_tracebacks=True, show_path=False))
    else:
        handlers.append(logging.StreamHandler())
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        handlers.append(file_handler)
    logging.basicConfig(level=level.upper(), format="%(message)s", handlers=handlers, force=True)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger."""
    return logging.getLogger(name)
