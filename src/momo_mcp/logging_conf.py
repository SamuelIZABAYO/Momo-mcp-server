"""Structured JSON logging with redaction of secrets and MSISDNs (spec §4.2, §4.4).

No raw subscription keys, API keys, bearer tokens, or full MSISDNs ever reach a
log line. MSISDNs are masked to last-4; anything that looks like a key/token is
scrubbed by a regex filter applied to every record before it is emitted.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime

# Patterns that must never appear verbatim in logs. Conservative on purpose:
# better to over-redact a log line than to leak a credential (Hard Rule #2).
_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Bearer tokens
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE), "Bearer <redacted>"),
    # Basic auth headers
    (re.compile(r"Basic\s+[A-Za-z0-9+/=]+", re.IGNORECASE), "Basic <redacted>"),
    # Subscription-key style 32-hex strings
    (re.compile(r"\b[0-9a-fA-F]{32}\b"), "<redacted-key>"),
    # uuid4-shaped api users / reference ids are NOT secret on their own, so we
    # leave them; the api *key* is a uuid too but is only ever passed via auth
    # headers which the Basic/Bearer rules above already cover.
)


def mask_msisdn(msisdn: str | None) -> str:
    """Return an MSISDN masked to its last 4 digits, e.g. ``******3450``."""
    if not msisdn:
        return "<none>"
    digits = re.sub(r"\D", "", msisdn)
    if len(digits) <= 4:
        return "*" * len(digits)
    return "*" * (len(digits) - 4) + digits[-4:]


def redact(text: str) -> str:
    """Scrub any secret-shaped substrings from an arbitrary string."""
    for pattern, replacement in _REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class _RedactingJsonFormatter(logging.Formatter):
    """Render each record as a single JSON line, with secrets scrubbed."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        # Promote structured extras (anything attached via `extra=`).
        for key, value in record.__dict__.items():
            if key in _STD_RECORD_KEYS or key.startswith("_"):
                continue
            payload[key] = redact(value) if isinstance(value, str) else value
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


# logging.LogRecord built-in attributes we should not echo as "extra" fields.
_STD_RECORD_KEYS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName",
    }
)


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Install the redacting JSON handler on the package logger.

    Logs go to stderr so they never corrupt the MCP stdio transport on stdout.
    """
    logger = logging.getLogger("momo_mcp")
    logger.setLevel(level)
    logger.handlers.clear()
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(_RedactingJsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child of the package logger (configure once at startup)."""
    base = logging.getLogger("momo_mcp")
    return base if name is None else base.getChild(name)
