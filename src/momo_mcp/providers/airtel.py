"""Airtel Money provider (stub).

The interface is in place; the implementation is not. Every method raises a clear
NotImplementedError so a misconfiguration surfaces as a readable message rather
than an AttributeError. Implementing Airtel is one file against the same
:class:`PaymentProvider` contract, with no tool changes.
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
    "Airtel Money is not implemented. The provider interface is in place; "
    "use the MTN provider for sandbox operations."
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
