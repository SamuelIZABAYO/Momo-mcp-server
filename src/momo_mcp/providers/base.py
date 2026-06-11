"""The ``PaymentProvider`` abstract interface — the seam that makes this server
provider-agnostic (spec §1).

MCP tools depend only on this interface and on the structured result objects
below. MTN is the one concrete implementation in v1; Airtel ships as a stub
implementing the same contract (§8.5). The provider layer is also where agent
guardrails are enforced so no tool can bypass them (§4.7) — that enforcement
lands with the MTN implementation in Phase 2/3; this file defines the shapes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum


class PaymentStatus(StrEnum):
    """Normalized, provider-independent status. MTN's raw statuses map onto
    these in the MTN provider (§2: check_payment_status mapping)."""

    PENDING = "PENDING"
    SUCCESSFUL = "SUCCESSFUL"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class PaymentResult:
    """Outcome of a request_payment / status check."""

    transaction_id: str          # internal reference_id (the X-Reference-Id)
    status: PaymentStatus
    message: str                 # human-readable, LLM-actionable
    dry_run: bool = False
    raw_status: str | None = None  # provider's own status string, for debugging


@dataclass(frozen=True)
class PayoutResult:
    """Outcome of a disbursement. ``pending_approval`` is the approval-gate
    state (§4.3) — the payout has NOT been sent; a confirm step is required."""

    transaction_id: str | None
    status: PaymentStatus | None
    message: str
    pending_approval: bool = False
    approval_code: str | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class BalanceResult:
    account: str                 # 'collection' | 'disbursement'
    available_balance: str
    currency: str
    dry_run: bool = False


@dataclass(frozen=True)
class AccountValidation:
    msisdn_masked: str
    is_active: bool
    message: str
    dry_run: bool = False


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    token_valid: bool
    last_latency_ms: int | None = None
    error_rate_1h: float | None = None
    details: dict[str, object] = field(default_factory=dict)


class ProviderError(RuntimeError):
    """Base for provider-level failures. ``message`` is safe to surface to the
    LLM and should tell it what to do next (spec §2)."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class GuardrailRejection(ProviderError):
    """A guardrail blocked the action (§4.7). Never retryable — the LLM must
    inform the user, not retry. Carries a ``reason_code`` for the audit log."""

    def __init__(self, message: str, *, reason_code: str):
        super().__init__(message, retryable=False)
        self.reason_code = reason_code


class PaymentProvider(ABC):
    """Abstract contract every provider implements. All methods are async; the
    MTN implementation uses httpx.AsyncClient."""

    name: str = "base"

    @abstractmethod
    async def request_payment(
        self,
        *,
        msisdn: str,
        amount: float,
        currency: str,
        external_ref: str | None = None,
        note: str | None = None,
    ) -> PaymentResult:
        """Collections requesttopay. Returns immediately with a transaction id;
        status resolves asynchronously (poll via :meth:`check_payment_status`)."""

    @abstractmethod
    async def check_payment_status(self, transaction_id: str) -> PaymentResult:
        """Resolve a transaction's current status from the provider."""

    @abstractmethod
    async def get_balance(self, account: str) -> BalanceResult:
        """Balance for the 'collection' or 'disbursement' account."""

    @abstractmethod
    async def validate_account(self, msisdn: str) -> AccountValidation:
        """Pre-flight: is the MSISDN active/registered?"""

    @abstractmethod
    async def send_payout(
        self,
        *,
        msisdn: str,
        amount: float,
        currency: str,
        approval_code: str | None = None,
        note: str | None = None,
    ) -> PayoutResult:
        """Disbursement transfer — approval-gated (§4.3)."""

    async def confirm_payout(self, approval_code: str) -> PayoutResult:
        """Execute a previously-requested payout using its one-time approval code
        (§4.3). Default raises NotImplementedError; providers that support the
        approval gate override it."""
        raise NotImplementedError("This provider does not implement payout approval.")

    @abstractmethod
    async def health(self) -> ProviderHealth:
        """Token validity, latency, recent error rate."""

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        """Release any held resources (HTTP client, etc.)."""
        return None
