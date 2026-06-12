"""Sandbox test fixtures, the magic MSISDNs and the reason→status mapping.

These were verified directly against the live MTN sandbox on 2026-06-11 (see
docs/GOTCHAS.md). The sandbox keys outcomes off the payer MSISDN, but every
non-success outcome arrives as ``status: "FAILED"`` with a distinguishing
``reason``, so the normalized outcome is derived from ``reason``, not ``status``
alone.
"""

from __future__ import annotations

# Payer MSISDN -> (raw status, raw reason or None, normalized status we expect).
SANDBOX_OUTCOMES: dict[str, tuple[str, str | None, str]] = {
    "46733123450": ("FAILED", "INTERNAL_PROCESSING_ERROR", "FAILED"),
    "46733123451": ("FAILED", "APPROVAL_REJECTED", "REJECTED"),
    "46733123452": ("FAILED", "EXPIRED", "TIMEOUT"),
    "46733123453": ("SUCCESSFUL", None, "SUCCESSFUL"),
}

# Convenience handles for tests that want a specific outcome.
MSISDN_SUCCESS = "46733123453"
MSISDN_FAILED = "46733123450"
MSISDN_REJECTED = "46733123451"
MSISDN_TIMEOUT = "46733123452"

# The sandbox allowlist mirrors these numbers (config MSISDN_ALLOWLIST default).
SANDBOX_ALLOWLIST = tuple(SANDBOX_OUTCOMES.keys())

# A number NOT on the allowlist, used to prove the guardrail
# rejects hallucinated/unknown numbers.
MSISDN_NOT_ALLOWLISTED = "46700000999"
