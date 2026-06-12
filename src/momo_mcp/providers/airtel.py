"""Airtel Money provider, stub.

The interface is ready; the implementation is not. Every method raises a clear,
LLM-actionable ``NotImplementedError`` so a misconfiguration surfaces as a
readable message rather than an AttributeError. Implementing Airtel for real is
one file's worth of work against the same :class:`PaymentProvider` contract,
with zero tool changes, that is the architectural payoff of.
"""

from __future__ import annotations

from .base import (
    AccountValidation,
    BalanceResult,
    PaymentProvider,
    PaymentResult,
    PayoutResult,
    ProviderHealth,
)

_MSG = (
    "Airtel Money is not implemented in v1. The provider interface is ready; "
    "implementing it is a separate engagement (see docs/ roadmap). "
    "Use the MTN provider for sandbox operations."
)


class AirtelProvider(PaymentProvider):
    name = "airtel"

    async def request_payment(self, **_: object) -> PaymentResult:
        raise NotImplementedError(_MSG)

    async def check_payment_status(self, transaction_id: str) -> PaymentResult:
        raise NotImplementedError(_MSG)

    async def get_balance(self, account: str) -> BalanceResult:
        raise NotImplementedError(_MSG)

    async def validate_account(self, msisdn: str) -> AccountValidation:
        raise NotImplementedError(_MSG)

    async def send_payout(self, **_: object) -> PayoutResult:
        raise NotImplementedError(_MSG)

    async def confirm_payout(self, approval_code: str) -> PayoutResult:
        raise NotImplementedError(_MSG)

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            token_valid=False,
            details={"status": "not_implemented", "message": _MSG},
        )
