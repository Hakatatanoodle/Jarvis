"""
Unified logging (§8: "No print(). Use the unified logger everywhere.").
One instance, every subsystem gets a named child logger, per §7's
infra/logging.py responsibility.
"""
from __future__ import annotations

import logging
import sys

_CONFIGURED = False
_ROOT_NAME = "jarvis"


def _configure_root(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    root = logging.getLogger(_ROOT_NAME)
    root.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root.addHandler(handler)
    root.propagate = False
    _CONFIGURED = True


def get_logger(subsystem: str) -> logging.Logger:
    """Every subsystem calls get_logger(__name__) or get_logger("goals"),
    etc. — never logging.getLogger() directly, and never print()."""
    _configure_root()
    return logging.getLogger(f"{_ROOT_NAME}.{subsystem}")
