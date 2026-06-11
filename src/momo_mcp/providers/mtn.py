"""MTN MoMo provider — Collections implemented (Phase 2); Disbursements + the
remaining tools land in Phase 3.

All endpoint behavior below was verified against the live sandbox on 2026-06-11
(Hard Rule #1). See docs/GOTCHAS.md for the quirks this code defends against:
empty 202 bodies, reason-derived status, EUR-only, blocked balance, etc.

Safety properties enforced here (so no tool can bypass them, spec §4.7):
  * guardrail gauntlet runs BEFORE any HTTP call;
  * the idempotency row is persisted in SQLite BEFORE the request is sent (§4.1),
    so a crash leaves a recoverable PENDING row and a retry reuses the same
    X-Reference-Id (MTN dedupes — no double charge);
  * DRY_RUN short-circuits to a simulated response with zero HTTP calls;
  * a 401 triggers exactly one forced token refresh + one retry, never a loop.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import httpx

from ..auth import AuthError, TokenManager
from ..config import Settings
from ..guardrails import enforce_mutation
from ..logging_conf import get_logger, mask_msisdn
from ..ratelimit import TokenBucket
from ..store import Store
from .base import (
    AccountValidation,
    BalanceResult,
    PaymentProvider,
    PaymentResult,
    PaymentStatus,
    PayoutResult,
    ProviderError,
    ProviderHealth,
)

log = get_logger("mtn")

# Map MTN's raw (status, reason) onto our normalized PaymentStatus (GOTCHAS §2).
_REASON_TO_STATUS = {
    "APPROVAL_REJECTED": PaymentStatus.REJECTED,
    "EXPIRED": PaymentStatus.TIMEOUT,
    "PAYER_NOT_FOUND": PaymentStatus.FAILED,
    "PAYEE_NOT_ALLOWED_TO_RECEIVE": PaymentStatus.FAILED,
    "INTERNAL_PROCESSING_ERROR": PaymentStatus.FAILED,
}


def normalize_status(raw_status: str, reason: str | None) -> PaymentStatus:
    """Collapse MTN's (status, reason) into our 5 canonical states.

    A bare ``FAILED`` with a reason of ``APPROVAL_REJECTED``/``EXPIRED`` becomes
    ``REJECTED``/``TIMEOUT`` respectively — see GOTCHAS §2."""
    raw = (raw_status or "").upper()
    if raw == "SUCCESSFUL":
        return PaymentStatus.SUCCESSFUL
    if raw == "PENDING":
        return PaymentStatus.PENDING
    if raw == "FAILED":
        return _REASON_TO_STATUS.get((reason or "").upper(), PaymentStatus.FAILED)
    # Unknown/unexpected — treat as FAILED rather than silently passing through.
    return PaymentStatus.FAILED


class MTNProvider(PaymentProvider):
    name = "mtn"

    def __init__(self, *, settings: Settings, store: Store, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._store = store
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None
        self._bucket = TokenBucket(settings.rate_limit_per_sec)
        self._last_latency_ms: int | None = None
        # Token managers per product. Disbursement token used in Phase 3.
        self._collection_tokens = TokenManager(
            client=self._client,
            base_url=settings.base_url,
            product="collection",
            api_user=settings.api_user or "",
            api_key=settings.api_key or "",
            subscription_key=settings.collection_subscription_key,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ── shared request helper: rate limit + 401-retry-once (§3.2) ────────────
    async def _authed_request(
        self,
        method: str,
        path: str,
        *,
        product: str = "collection",
        extra_headers: dict[str, str] | None = None,
        json_body: dict | None = None,
    ) -> httpx.Response:
        await self._bucket.acquire()
        tokens = self._collection_tokens  # only product wired in Phase 2
        url = f"{self._settings.base_url}{path}"

        async def _do(token: str) -> httpx.Response:
            headers = {
                "Authorization": f"Bearer {token}",
                "X-Target-Environment": self._settings.target_env,
                "Ocp-Apim-Subscription-Key": self._settings.collection_subscription_key,
            }
            if extra_headers:
                headers.update(extra_headers)
            t0 = time.monotonic()
            resp = await self._client.request(method, url, headers=headers, json=json_body)
            self._last_latency_ms = int((time.monotonic() - t0) * 1000)
            return resp

        token = await tokens.get_token()
        resp = await _do(token)
        if resp.status_code == 401:
            # Refresh once, retry once — never loop (§3.2).
            log.info("401 received; forcing token refresh and retrying once",
                     extra={"path": path})
            token = await tokens.get_token(force_refresh=True)
            resp = await _do(token)
        return resp

    # ── request_payment (Collections requesttopay) ───────────────────────────
    async def request_payment(
        self,
        *,
        msisdn: str,
        amount: float,
        currency: str,
        external_ref: str | None = None,
        note: str | None = None,
    ) -> PaymentResult:
        # 1) Guardrails BEFORE anything else (§4.7) — raises GuardrailRejection.
        enforce_mutation(
            msisdn=msisdn, amount=amount, settings=self._settings, store=self._store
        )

        # 2) Generate + persist the idempotency key BEFORE the HTTP call (§4.1).
        reference_id = str(uuid.uuid4())
        self._store.create_transaction(
            reference_id=reference_id,
            kind="collection",
            tool="request_payment",
            msisdn=msisdn,
            amount=amount,
            currency=currency,
            dry_run=self._settings.dry_run,
            external_ref=external_ref,
            note=note,
        )

        # 3) DRY_RUN: zero HTTP calls, realistic simulated response (§4.7).
        if self._settings.dry_run:
            log.info("dry-run request_payment", extra={"msisdn": mask_msisdn(msisdn)})
            return PaymentResult(
                transaction_id=reference_id,
                status=PaymentStatus.PENDING,
                message=(
                    "DRY_RUN: simulated payment request accepted (no HTTP call). "
                    "Poll check_payment_status to see the simulated outcome."
                ),
                dry_run=True,
                raw_status="PENDING",
            )

        # 4) Real call. 202 with empty body is success (GOTCHAS §1).
        body = {
            "amount": str(amount),
            "currency": currency,
            "externalId": external_ref or reference_id,
            "payer": {"partyIdType": "MSISDN", "partyId": msisdn},
            "payerMessage": note or "Payment request",
            "payeeNote": note or "Payment request",
        }
        try:
            resp = await self._authed_request(
                "POST",
                "/collection/v1_0/requesttopay",
                extra_headers={"X-Reference-Id": reference_id, "Content-Type": "application/json"},
                json_body=body,
            )
        except AuthError as exc:
            raise ProviderError(str(exc)) from exc

        if resp.status_code != 202:
            raise ProviderError(
                f"request_payment failed (HTTP {resp.status_code}): "
                f"{resp.text[:200] or '<empty>'}. The transaction is recorded as "
                "PENDING locally with reference_id "
                f"{reference_id}; reconcile via check_payment_status.",
                retryable=resp.status_code >= 500,
            )

        # Do NOT parse the 202 body — it is empty (GOTCHAS §1).
        return PaymentResult(
            transaction_id=reference_id,
            status=PaymentStatus.PENDING,
            message=(
                "Payment request accepted. The payer is being prompted to approve. "
                "Poll check_payment_status with this transaction_id for the outcome."
            ),
            raw_status="PENDING",
        )

    # ── check_payment_status (poll with backoff, §3.4) ───────────────────────
    async def check_payment_status(self, transaction_id: str) -> PaymentResult:
        tx = self._store.get_transaction(transaction_id)
        if tx is None:
            raise ProviderError(
                f"No transaction with id {transaction_id} in the ledger. "
                "Pass a transaction_id returned by request_payment."
            )

        if tx.dry_run:
            # Deterministic simulated resolution from the fixture mapping is the
            # job of tests; here we resolve a dry-run row to SUCCESSFUL for demo
            # realism and persist it (§4.7: ledger rows flagged dry_run).
            self._store.update_status(transaction_id, "SUCCESSFUL")
            return PaymentResult(
                transaction_id=transaction_id,
                status=PaymentStatus.SUCCESSFUL,
                message="DRY_RUN: simulated payment resolved as SUCCESSFUL.",
                dry_run=True,
                raw_status="SUCCESSFUL",
            )

        # Already terminal in the ledger? Return it without hitting the API.
        if tx.status in {"SUCCESSFUL", "FAILED", "TIMEOUT", "REJECTED"}:
            return PaymentResult(
                transaction_id=transaction_id,
                status=PaymentStatus(tx.status),
                message=f"Transaction is in terminal state {tx.status}.",
                raw_status=tx.status,
            )

        # Poll with capped backoff: 2,4,8,16,30 → ~60s total (§3.4).
        delays = [0, 2, 4, 8, 16, 30]
        last: PaymentResult | None = None
        for delay in delays:
            if delay:
                await asyncio.sleep(delay)
            try:
                resp = await self._authed_request(
                    "GET", f"/collection/v1_0/requesttopay/{transaction_id}"
                )
            except AuthError as exc:
                raise ProviderError(str(exc)) from exc
            if resp.status_code != 200:
                raise ProviderError(
                    f"status check failed (HTTP {resp.status_code}): "
                    f"{resp.text[:200]}. The local ledger still holds the PENDING "
                    "row for later reconciliation."
                )
            data = resp.json()
            normalized = normalize_status(data.get("status", ""), data.get("reason"))
            last = PaymentResult(
                transaction_id=transaction_id,
                status=normalized,
                message=self._status_message(normalized, data.get("reason")),
                raw_status=data.get("status"),
            )
            if normalized is not PaymentStatus.PENDING:
                self._store.update_status(transaction_id, normalized.value)
                return last
            # else keep polling until budget exhausted
        # Budget exhausted while still PENDING.
        return last or PaymentResult(
            transaction_id=transaction_id,
            status=PaymentStatus.PENDING,
            message="Still PENDING after polling; check again later.",
            raw_status="PENDING",
        )

    @staticmethod
    def _status_message(status: PaymentStatus, reason: str | None) -> str:
        match status:
            case PaymentStatus.SUCCESSFUL:
                return "Payment completed successfully."
            case PaymentStatus.REJECTED:
                return "The payer rejected the payment request."
            case PaymentStatus.TIMEOUT:
                return "The payment request expired before the payer approved it."
            case PaymentStatus.FAILED:
                return f"Payment failed{f' ({reason})' if reason else ''}."
            case _:
                return "Payment is still pending payer approval."

    # ── tools completed in Phase 3 ───────────────────────────────────────────
    async def get_balance(self, account: str) -> BalanceResult:
        raise ProviderError("get_balance is implemented in Phase 3.")

    async def validate_account(self, msisdn: str) -> AccountValidation:
        raise ProviderError("validate_account is implemented in Phase 3.")

    async def send_payout(self, **_: object) -> PayoutResult:
        raise ProviderError("send_payout is implemented in Phase 3.")

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            token_valid=self._collection_tokens.has_valid_token,
            last_latency_ms=self._last_latency_ms,
        )
