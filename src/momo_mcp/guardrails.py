"""Agent guardrails, enforced in the provider layer so no tool can bypass them.
Each check raises :class:`GuardrailRejection` with a reason code on breach;
rejections are never retryable and the message tells the LLM to inform the user,
not retry.

Checks live here, decoupled from any provider, so tests can exercise them
directly and the same logic protects every provider.
"""

from __future__ import annotations

from pathlib import Path

from .config import Settings
from .providers.base import GuardrailRejection
from .store import Store

PAUSE_FILE = "PAUSE"


def _inform(msg: str) -> str:
    return msg + " Inform the user; do not retry."


def check_pause(workdir: str | Path = ".") -> None:
    """Kill switch: if a PAUSE file exists, refuse all mutations instantly."""
    if (Path(workdir) / PAUSE_FILE).exists():
        raise GuardrailRejection(
            _inform(
                "Operations are paused: a PAUSE file is present in the working "
                "directory. A human has halted the agent."
            ),
            reason_code="paused",
        )


def check_allowlist(msisdn: str, settings: Settings) -> None:
    """In sandbox, refuse any MSISDN not on the allowlist."""
    if settings.msisdn_allowlist and msisdn not in settings.msisdn_allowlist:
        raise GuardrailRejection(
            _inform(
                f"MSISDN ending …{msisdn[-4:]} is not on the sandbox allowlist, "
                "so this request is refused. Only pre-approved test numbers are "
                "permitted in sandbox mode."
            ),
            reason_code="msisdn_not_allowlisted",
        )


def check_amount(amount: float, settings: Settings) -> None:
    """Reject any single transaction above MAX_AMOUNT_PER_TX, never auto-split."""
    if amount > settings.max_amount_per_tx:
        raise GuardrailRejection(
            _inform(
                f"Amount {amount} exceeds the per-transaction limit of "
                f"{settings.max_amount_per_tx}. The request is refused and was "
                "NOT split into smaller amounts."
            ),
            reason_code="amount_over_limit",
        )


def check_daily_limits(amount: float, settings: Settings, store: Store) -> None:
    """Enforce daily count and total caps; breach = hard stop until reset."""
    usage = store.daily_usage()
    if usage.tx_count + 1 > settings.max_daily_tx_count:
        raise GuardrailRejection(
            _inform(
                f"Daily transaction count limit reached "
                f"({usage.tx_count}/{settings.max_daily_tx_count}). A human must "
                "reset limits (scripts/reset_limits.py) before more can be sent."
            ),
            reason_code="daily_count_exceeded",
        )
    if usage.total_amount + amount > settings.max_daily_total:
        raise GuardrailRejection(
            _inform(
                f"Daily total limit would be exceeded "
                f"({usage.total_amount} + {amount} > {settings.max_daily_total}). "
                "A human must reset limits before more can be sent."
            ),
            reason_code="daily_total_exceeded",
        )


def enforce_mutation(
    *,
    msisdn: str,
    amount: float,
    settings: Settings,
    store: Store,
    workdir: str | Path = ".",
) -> None:
    """Run all guardrail checks for a money-moving mutation, in order.

    Order matters: PAUSE first (cheapest, hardest stop), then identity
    (allowlist), then size (per-tx), then aggregate (daily). The first breach
    raises and stops evaluation.
    """
    check_pause(workdir)
    check_allowlist(msisdn, settings)
    check_amount(amount, settings)
    check_daily_limits(amount, settings, store)
