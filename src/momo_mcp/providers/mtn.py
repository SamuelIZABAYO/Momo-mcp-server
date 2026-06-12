"""MTN MoMo provider.

All endpoint behavior below was verified against the live sandbox on 2026-06-11.
See docs/GOTCHAS.md for the quirks this code defends against: empty 202 bodies,
reason-derived status, EUR-only, blocked balance, etc.

Safety properties enforced here so no tool can bypass them:
  * guardrail checks run BEFORE any HTTP call;
  * the idempotency row is persisted in SQLite BEFORE the request is sent,
    so a crash leaves a recoverable PENDING row and a retry reuses the same
    X-Reference-Id (MTN dedupes, no double charge);
  * DRY_RUN short-circuits to a simulated response with zero HTTP calls;
  * a 401 triggers exactly one forced token refresh + one retry, never a loop.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

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
    GuardrailRejection,
    PaymentProvider,
    PaymentResult,
    PaymentStatus,
    PayoutResult,
    ProviderError,
    ProviderHealth,
)

log = get_logger("mtn")

# Approval code time-to-live in minutes.
_APPROVAL_TTL_MIN = 15

# Transient-failure retry budget for idempotent calls (5xx and network errors).
# Total attempts = _MAX_RETRIES + 1. Backoff is _RETRY_BACKOFF_BASE * 2**(n-1).
_MAX_RETRIES = 2
_RETRY_BACKOFF_BASE = 0.5

# Map MTN's raw (status, reason) onto our normalized PaymentStatus (GOTCHAS).
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
    ``REJECTED``/``TIMEOUT`` respectively, see GOTCHAS."""
    raw = (raw_status or "").upper()
    if raw == "SUCCESSFUL":
        return PaymentStatus.SUCCESSFUL
    if raw == "PENDING":
        return PaymentStatus.PENDING
    if raw == "FAILED":
        return _REASON_TO_STATUS.get((reason or "").upper(), PaymentStatus.FAILED)
    # Unknown/unexpected, treat as FAILED rather than silently passing through.
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
        # Token managers per product.
        self._collection_tokens = TokenManager(
            client=self._client,
            base_url=settings.base_url,
            product="collection",
            api_user=settings.api_user or "",
            api_key=settings.api_key or "",
            subscription_key=settings.collection_subscription_key,
        )
        # Disbursements reuse the same provisioned API user/key (GOTCHAS),
        # with the disbursement subscription key.
        self._disbursement_tokens = TokenManager(
            client=self._client,
            base_url=settings.base_url,
            product="disbursement",
            api_user=settings.api_user or "",
            api_key=settings.api_key or "",
            subscription_key=settings.disbursement_subscription_key,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _product_ctx(self, product: str) -> tuple[TokenManager, str]:
        """Return (token manager, subscription key) for a product."""
        if product == "collection":
            return self._collection_tokens, self._settings.collection_subscription_key
        if product == "disbursement":
            return self._disbursement_tokens, self._settings.disbursement_subscription_key
        raise ValueError(f"unknown product {product!r}")

    # ── shared request helper: rate limit, 401-retry-once, transient retry ────
    async def _authed_request(
        self,
        method: str,
        path: str,
        *,
        product: str = "collection",
        extra_headers: dict[str, str] | None = None,
        json_body: dict | None = None,
    ) -> httpx.Response:
        tokens, sub_key = self._product_ctx(product)
        url = f"{self._settings.base_url}{path}"

        async def _do(token: str) -> httpx.Response:
            headers = {
                "Authorization": f"Bearer {token}",
                "X-Target-Environment": self._settings.target_env,
                "Ocp-Apim-Subscription-Key": sub_key,
            }
            if extra_headers:
                headers.update(extra_headers)
            t0 = time.monotonic()
            resp = await self._client.request(method, url, headers=headers, json=json_body)
            self._last_latency_ms = int((time.monotonic() - t0) * 1000)
            return resp

        # Retry transient failures only for idempotent calls: GETs, and mutations
        # that carry an X-Reference-Id (MTN dedupes on it, so a resend is safe).
        idempotent = method.upper() == "GET" or (
            extra_headers is not None and "X-Reference-Id" in extra_headers
        )
        attempts = _MAX_RETRIES + 1 if idempotent else 1

        last_exc: httpx.HTTPError | None = None
        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(_RETRY_BACKOFF_BASE * (2 ** (attempt - 1)))
            await self._bucket.acquire()
            try:
                token = await tokens.get_token()
                resp = await _do(token)
            except httpx.HTTPError as exc:
                # Network error / timeout: retry if budget remains, else surface.
                last_exc = exc
                if attempt + 1 < attempts:
                    log.info("transient network error; retrying",
                             extra={"path": path, "attempt": attempt + 1})
                    continue
                raise

            if resp.status_code == 401:
                # Refresh the token once and retry this attempt, never loop.
                log.info("401 received; forcing token refresh and retrying once",
                         extra={"path": path})
                await self._bucket.acquire()
                token = await tokens.get_token(force_refresh=True)
                resp = await _do(token)

            # Retry on a 5xx for idempotent calls; otherwise return as-is.
            if resp.status_code >= 500 and attempt + 1 < attempts:
                log.info("upstream 5xx; retrying",
                         extra={"path": path, "status": resp.status_code, "attempt": attempt + 1})
                continue
            return resp

        # The loop returns or raises on every path; this is a fallback only.
        if last_exc is not None:
            raise last_exc
        raise ProviderError(f"request to {path} failed after retries.")

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
        # 1) Guardrails BEFORE anything else, raises GuardrailRejection.
        enforce_mutation(
            msisdn=msisdn, amount=amount, settings=self._settings, store=self._store
        )

        # 2) Generate + persist the idempotency key BEFORE the HTTP call.
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

        # 3) DRY_RUN: zero HTTP calls, simulated response.
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

        # 4) Real call. 202 with empty body is success (GOTCHAS).
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

        # Do NOT parse the 202 body, it is empty (GOTCHAS).
        return PaymentResult(
            transaction_id=reference_id,
            status=PaymentStatus.PENDING,
            message=(
                "Payment request accepted. The payer is being prompted to approve. "
                "Poll check_payment_status with this transaction_id for the outcome."
            ),
            raw_status="PENDING",
        )

    # ── check_payment_status (poll with backoff) ───────────────────────
    async def check_payment_status(self, transaction_id: str) -> PaymentResult:
        tx = self._store.get_transaction(transaction_id)
        if tx is None:
            raise ProviderError(
                f"No transaction with id {transaction_id} in the ledger. "
                "Pass a transaction_id returned by request_payment."
            )

        if tx.dry_run:
            # In dry-run, a status check resolves the transaction to SUCCESSFUL
            # and persists it, with the ledger row flagged dry_run.
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

        # Status path + product depend on whether this was a collection or a
        # disbursement (GOTCHAS): transfers poll a different URL.
        if tx.kind == "disbursement":
            status_path = f"/disbursement/v1_0/transfer/{transaction_id}"
            product = "disbursement"
        else:
            status_path = f"/collection/v1_0/requesttopay/{transaction_id}"
            product = "collection"

        # Poll with capped backoff: 2,4,8,16,30 → ~60s total.
        delays = [0, 2, 4, 8, 16, 30]
        last: PaymentResult | None = None
        for delay in delays:
            if delay:
                await asyncio.sleep(delay)
            try:
                resp = await self._authed_request("GET", status_path, product=product)
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

    # ── send_payout (Disbursements transfer, approval-gated) ────────────
    async def send_payout(
        self,
        *,
        msisdn: str,
        amount: float,
        currency: str,
        approval_code: str | None = None,
        note: str | None = None,
    ) -> PayoutResult:
        # Guardrails first, same checks as collections.
        enforce_mutation(
            msisdn=msisdn, amount=amount, settings=self._settings, store=self._store
        )

        # Approval gate: when required and no code supplied, mint a one-time code
        # and STOP. The money does not move until confirm_payout is called with
        # this code.
        if self._settings.require_payout_approval and not approval_code:
            code = uuid.uuid4().hex[:12].upper()
            expires_at = (
                datetime.now(UTC) + timedelta(minutes=_APPROVAL_TTL_MIN)
            ).isoformat()
            self._store.create_approval(
                code=code, msisdn=msisdn, amount=amount,
                currency=currency, expires_at=expires_at,
            )
            return PayoutResult(
                transaction_id=None,
                status=None,
                message=(
                    f"Payout of {amount} {currency} to …{msisdn[-4:]} requires "
                    f"human approval. It has NOT been sent. Call confirm_payout "
                    f"with approval_code={code} (valid {_APPROVAL_TTL_MIN} min) to "
                    "execute it. Tell the user a human must approve."
                ),
                pending_approval=True,
                approval_code=code,
            )

        # If a code was supplied, validate+consume it (single-use, not expired).
        if self._settings.require_payout_approval:
            row = self._store.consume_approval(approval_code or "")
            if row is None:
                raise GuardrailRejection(
                    "Approval code is invalid, expired, or already used. The "
                    "payout was NOT sent. Request a fresh approval. Inform the "
                    "user; do not retry with the same code.",
                    reason_code="approval_invalid",
                )
            # The code binds to a specific msisdn+amount; refuse mismatches so a
            # valid code can't be redirected to a different payout.
            if abs(row["amount"] - amount) > 1e-9 or row["msisdn"] != msisdn:
                raise GuardrailRejection(
                    "Approval code does not match this payout's amount/recipient. "
                    "The payout was NOT sent. Inform the user; do not retry.",
                    reason_code="approval_mismatch",
                )

        return await self._execute_transfer(msisdn, amount, currency, note)

    async def confirm_payout(self, approval_code: str) -> PayoutResult:
        """Execute a previously-requested payout using its one-time code.

        Looks up the pending approval (without consuming it yet, _execute path
        via send_payout consumes it), then runs send_payout with the code so the
        single consume+execute path is shared.
        """
        # Peek the approval to recover msisdn/amount/currency for the transfer.
        # We consume inside send_payout to keep one atomic consume point.
        row = self._store.get_approval(approval_code)  # read-only peek
        if row is None:
            raise GuardrailRejection(
                "Unknown approval code. No payout was sent. Inform the user.",
                reason_code="approval_unknown",
            )
        return await self.send_payout(
            msisdn=row["msisdn"], amount=row["amount"],
            currency=row["currency"], approval_code=approval_code,
        )

    async def _execute_transfer(
        self, msisdn: str, amount: float, currency: str, note: str | None
    ) -> PayoutResult:
        reference_id = str(uuid.uuid4())
        # Persist before send.
        self._store.create_transaction(
            reference_id=reference_id, kind="disbursement", tool="send_payout",
            msisdn=msisdn, amount=amount, currency=currency,
            dry_run=self._settings.dry_run, note=note,
        )
        if self._settings.dry_run:
            return PayoutResult(
                transaction_id=reference_id, status=PaymentStatus.PENDING,
                message="DRY_RUN: simulated payout accepted (no HTTP call).",
                dry_run=True,
            )

        body = {
            "amount": str(amount),
            "currency": currency,
            "externalId": reference_id,
            "payee": {"partyIdType": "MSISDN", "partyId": msisdn},  # payee, not payer
            "payerMessage": note or "Payout",
            "payeeNote": note or "Payout",
        }
        try:
            resp = await self._authed_request(
                "POST", "/disbursement/v1_0/transfer", product="disbursement",
                extra_headers={"X-Reference-Id": reference_id, "Content-Type": "application/json"},
                json_body=body,
            )
        except AuthError as exc:
            raise ProviderError(str(exc)) from exc
        if resp.status_code != 202:
            raise ProviderError(
                f"send_payout failed (HTTP {resp.status_code}): "
                f"{resp.text[:200] or '<empty>'}. Recorded PENDING locally as "
                f"{reference_id}; reconcile via check_payment_status.",
                retryable=resp.status_code >= 500,
            )
        return PayoutResult(
            transaction_id=reference_id, status=PaymentStatus.PENDING,
            message=(
                "Payout accepted and is being processed. Poll check_payment_status "
                "with this transaction_id for the outcome."
            ),
        )

    # ── get_balance (blocked in sandbox, GOTCHAS) ────────────────────────
    async def get_balance(self, account: str) -> BalanceResult:
        if account not in ("collection", "disbursement"):
            raise ProviderError("account must be 'collection' or 'disbursement'.")
        if self._settings.dry_run:
            return BalanceResult(
                account=account, available_balance="1000.00",
                currency=self._settings.currency, dry_run=True,
            )
        path = f"/{account}/v1_0/account/balance"
        try:
            resp = await self._authed_request("GET", path, product=account)
        except AuthError as exc:
            raise ProviderError(str(exc)) from exc
        if resp.status_code != 200:
            raise ProviderError(
                f"get_balance unavailable (HTTP {resp.status_code}): "
                f"{resp.text[:200]}. Note: balance is not permitted in the sandbox "
                "tier (see GOTCHAS); this works once go-live permissions are "
                "granted. Not a retryable error."
            )
        data = resp.json()
        return BalanceResult(
            account=account,
            available_balance=str(data.get("availableBalance", "")),
            currency=data.get("currency", self._settings.currency),
        )

    # ── validate_account (inconsistent in sandbox, GOTCHAS) ──────────────
    async def validate_account(self, msisdn: str) -> AccountValidation:
        if self._settings.dry_run:
            return AccountValidation(
                msisdn_masked=mask_msisdn(msisdn), is_active=True,
                message="DRY_RUN: simulated active account.", dry_run=True,
            )
        path = f"/collection/v1_0/accountholder/msisdn/{msisdn}/active"
        try:
            resp = await self._authed_request("GET", path, product="collection")
        except AuthError as exc:
            raise ProviderError(str(exc)) from exc
        if resp.status_code == 200:
            active = bool(resp.json().get("result", False))
            return AccountValidation(
                msisdn_masked=mask_msisdn(msisdn), is_active=active,
                message="Account is active." if active else "Account is not active.",
            )
        if resp.status_code == 404:
            return AccountValidation(
                msisdn_masked=mask_msisdn(msisdn), is_active=False,
                message=(
                    "Account not found. Note: in sandbox this endpoint is "
                    "unreliable for the magic test numbers (GOTCHAS)."
                ),
            )
        raise ProviderError(
            f"validate_account failed (HTTP {resp.status_code}): {resp.text[:200]}."
        )

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            token_valid=self._collection_tokens.has_valid_token,
            last_latency_ms=self._last_latency_ms,
        )
