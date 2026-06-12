"""Audit helper, one append-only row per tool call.

The audit row records *that* a tool ran and how it turned out, never the raw
amount or MSISDN: the input is hashed (so identical inputs are correlatable
without storing them) and outcomes are coarse reason codes. This audit trail
must hold even when a tool raises, so the recording happens in a
``finally``-style context manager regardless of success, rejection, or error.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from .providers.base import GuardrailRejection
from .store import Store


def hash_input(payload: dict[str, object]) -> str:
    """Stable SHA-256 of a tool's inputs (sorted keys), truncated for storage.

    No raw values are persisted, only this digest, so identical calls are
    correlatable in the audit log without leaking amounts/MSISDNs."""
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:32]


@dataclass
class _AuditScope:
    reference_id: str | None = None
    outcome: str | None = None


@contextmanager
def audit_call(
    store: Store, *, tool: str, payload: dict[str, object]
) -> Iterator[_AuditScope]:
    """Record exactly one audit row for a tool call, whatever the outcome.

    Inside the block, set ``scope.reference_id`` once known and optionally
    override ``scope.outcome``. On a GuardrailRejection the outcome is recorded as
    ``rejected:<reason_code>``; on any other exception as ``error:<type>``; on
    success as ``scope.outcome or 'ok'``.
    """
    scope = _AuditScope()
    input_hash = hash_input(payload)
    t0 = time.monotonic()
    try:
        yield scope
    except GuardrailRejection as exc:
        _write(store, tool, input_hash, scope, t0, f"rejected:{exc.reason_code}")
        raise
    except Exception as exc:
        _write(store, tool, input_hash, scope, t0, f"error:{type(exc).__name__}")
        raise
    else:
        _write(store, tool, input_hash, scope, t0, scope.outcome or "ok")


def _write(store, tool, input_hash, scope, t0, outcome) -> None:
    store.record_audit(
        tool=tool,
        input_hash=input_hash,
        outcome=outcome,
        reference_id=scope.reference_id,
        latency_ms=int((time.monotonic() - t0) * 1000),
    )
