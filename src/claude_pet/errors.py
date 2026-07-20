"""Structured error log for silent-fail paths.

The pet's design deliberately swallows almost every exception so a bad
frame or a network blip never crashes the desktop mascot. That's good for
uptime, terrible for debuggability — bugs like the zombie BreakOverlay
hid for weeks because 69 `except Exception: pass` blocks left no trace.

This module gives those handlers a single destination:

    from .errors import log_exception
    try:
        risky_thing()
    except Exception:
        log_exception("context-label")   # writes to ~/.claude/claude-pet/errors.log

Silent to the user, visible to the next audit. The log is a rotating file
capped at ~256 KB × 3 backups so it can never bloat the disk.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path


_LOG_MAX_BYTES = 256 * 1024
_LOG_BACKUP_COUNT = 3
_LOGGER_NAME = "claude_pet"
_configured = False


def _log_path() -> Path:
    root = Path.home() / ".claude" / "claude-pet"
    root.mkdir(parents=True, exist_ok=True)
    return root / "errors.log"


def _ensure_configured() -> logging.Logger:
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return logger
    # Level from env for on-demand verbose debugging.
    level_name = os.environ.get("CLAUDE_PET_LOG_LEVEL", "WARNING").upper()
    logger.setLevel(getattr(logging, level_name, logging.WARNING))
    # Never propagate to root — some hosts (Claude Code hooks) log to
    # stdout which would pollute the JSON payload the hook returns.
    logger.propagate = False
    try:
        handler = logging.handlers.RotatingFileHandler(
            _log_path(),
            maxBytes=_LOG_MAX_BYTES,
            backupCount=_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))
        logger.addHandler(handler)
    except Exception:
        # If we can't even open the log file (TCC block, out of disk),
        # fall back to a null handler. Never raise from the logger.
        logger.addHandler(logging.NullHandler())
    _configured = True
    return logger


def log_exception(label: str, extra: dict | None = None) -> None:
    """Record an exception + traceback with a callsite label.

    Silent from the caller's POV — the calling except-block still
    swallows control flow. Use in every `except Exception: pass` site.
    """
    try:
        logger = _ensure_configured()
        detail = f" extra={extra!r}" if extra else ""
        logger.exception(f"[{label}]{detail}")
    except Exception:
        pass       # never let the logger itself crash the pet


def log_warning(label: str, message: str) -> None:
    """Non-exception warning path (e.g. 'GitHub rate limited, backing off')."""
    try:
        logger = _ensure_configured()
        logger.warning(f"[{label}] {message}")
    except Exception:
        pass


def log_path() -> Path:
    """For `claude-pet doctor` to point users at the log file."""
    return _log_path()
