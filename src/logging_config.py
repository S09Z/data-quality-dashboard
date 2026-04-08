"""Logging configuration — plain text or structured JSON based on settings."""

from __future__ import annotations

import logging
import sys


def configure_logging(log_level: str = "INFO", log_json: bool = False) -> None:
    """Configure the root logger.

    Parameters
    ----------
    log_level:
        Standard Python log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    log_json:
        When *True*, emit each log record as a single JSON line using
        ``python-json-logger``. Useful for log aggregators (Datadog, CloudWatch).
        When *False*, use a human-readable format.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    if log_json:
        try:
            from pythonjsonlogger.json import (
                JsonFormatter,  # type: ignore[import-untyped]
            )
        except ImportError as exc:
            raise ImportError(
                "Structured JSON logging requires 'python-json-logger'. "
                "Install it with: uv add python-json-logger"
            ) from exc

        handler = logging.StreamHandler(sys.stdout)
        formatter = JsonFormatter(
            fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
        handler.setFormatter(formatter)
    else:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    # Replace any existing handlers to avoid duplicate output
    root.handlers.clear()
    root.addHandler(handler)
