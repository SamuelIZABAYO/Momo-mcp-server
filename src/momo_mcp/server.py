"""MCP server entry point, FastMCP over stdio.

Every tool here delegates to the PaymentProvider (never MTN directly) and is
wrapped in :func:`audit_call` so each invocation lands exactly one append-only
audit row, including guardrail rejections and errors. Tool docstrings are
written for an LLM consumer: they state preconditions and what to do next.

Run:  momo-mcp-server         (console script)
  or  python -m momo_mcp.server
The server speaks MCP over stdio, so all human-readable logging goes to stderr
(configured in logging_conf) and stdout carries only the protocol.
"""

from __future__ import annotations

import sys
from dataclasses import asdict
from typing import Any

from mcp.server.fastmcp import FastMCP

from .app import AppContext, build_app
from .audit import audit_call
from .config import ConfigError
from .logging_conf import get_logger
from .providers.base import GuardrailRejection, ProviderError

log = get_logger("server")

# Built lazily in main() so importing this module (e.g. in tests) is side-effect
# free. Tools close over `_ctx`.
_ctx: AppContext | None = None

mcp = FastMCP(
    name="momo-mcp-server",
    instructions=(
        "MTN Mobile Money (sandbox) tools. Money-moving actions are guarded: "
        "amounts over the per-transaction limit, numbers off the allowlist, and "
        "payouts without human approval are REJECTED. When a tool returns a "
        "rejection, inform the user and do NOT retry. Payments resolve "
        "asynchronously: request_payment returns a transaction_id immediately; "
        "call check_payment_status to learn the outcome. Payouts require a second "
        "confirm_payout call with the one-time approval_code."
    ),
)


def _app() -> AppContext:
    global _ctx
    if _ctx is None:
        _ctx = build_app()
    return _ctx


def _result(obj: Any) -> dict[str, Any]:
    """Render a provider result dataclass as a plain dict for structured output."""
    if hasattr(obj, "__dataclass_fields__"):
        d = asdict(obj)
        # Enums → their value for clean JSON.
        for k, v in list(d.items()):
            if hasattr(v, "value"):
                d[k] = v.value
        return d
    return {"value": obj}


def _error(exc: Exception) -> dict[str, Any]:
    """Uniform, LLM-actionable error envelope (never leaks internals)."""
    if isinstance(exc, GuardrailRejection):
        return {"ok": False, "rejected": True, "reason_code": exc.reason_code, "message": str(exc)}
    if isinstance(exc, ProviderError):
        return {"ok": False, "error": True, "retryable": exc.retryable, "message": str(exc)}
    return {"ok": False, "error": True, "message": f"Unexpected error: {exc}"}


# NOTE on the audit/error pattern used by every tool below:
# the provider call runs INSIDE the `audit_call` context so a GuardrailRejection
# or ProviderError propagates to the audit wrapper and is recorded as
# `rejected:<reason>` / `error:<type>`. We then catch it OUTSIDE the `with` to
# convert it into the structured error envelope returned to the LLM. Catching
# inside the `with` would swallow the exception and mis-record the row as `ok`.


# ── tools ────────────────────────────────────────────────────────────────────
@mcp.tool()
async def request_payment(
    msisdn: str, amount: float, currency: str = "EUR",
    external_ref: str | None = None, note: str | None = None,
) -> dict[str, Any]:
    """Ask a payer (their MSISDN) to approve a charge (MTN Collections).

    Precondition: in sandbox the MSISDN must be on the allowlist. Returns a
    transaction_id immediately with status PENDING, the payer approves on their
    phone asynchronously. Next step: poll check_payment_status with the returned
    transaction_id to learn whether it became SUCCESSFUL/FAILED/REJECTED/TIMEOUT.
    """
    ctx = _app()
    payload = {"msisdn": msisdn, "amount": amount, "currency": currency}
    try:
        with audit_call(ctx.store, tool="request_payment", payload=payload) as scope:
            res = await ctx.provider.request_payment(
                msisdn=msisdn, amount=amount, currency=currency,
                external_ref=external_ref, note=note,
            )
            scope.reference_id = res.transaction_id
            scope.outcome = res.status.value
            return {"ok": True, **_result(res)}
    except (GuardrailRejection, ProviderError) as exc:
        return _error(exc)


@mcp.tool()
async def check_payment_status(transaction_id: str) -> dict[str, Any]:
    """Resolve a transaction's current status by its transaction_id.

    Polls MTN with backoff (up to ~60s) and returns one of PENDING / SUCCESSFUL /
    FAILED / REJECTED / TIMEOUT. If still PENDING, call again later. Reads the
    local ledger first, so terminal transactions answer instantly.
    """
    ctx = _app()
    try:
        with audit_call(ctx.store, tool="check_payment_status",
                        payload={"transaction_id": transaction_id}) as scope:
            scope.reference_id = transaction_id
            res = await ctx.provider.check_payment_status(transaction_id)
            scope.outcome = res.status.value
            return {"ok": True, **_result(res)}
    except (GuardrailRejection, ProviderError) as exc:
        return _error(exc)


@mcp.tool()
async def send_payout(
    msisdn: str, amount: float, currency: str = "EUR", note: str | None = None,
) -> dict[str, Any]:
    """Send money to an MSISDN (MTN Disbursements), APPROVAL-GATED.

    This does NOT move money by itself. When approval is required (the default),
    it returns pending_approval=true with a one-time approval_code and sends
    nothing. A human must approve; then call confirm_payout(approval_code) to
    execute. Amounts over the per-transaction limit, off-allowlist numbers, or a
    breached daily limit are rejected, inform the user, do not retry.
    """
    ctx = _app()
    payload = {"msisdn": msisdn, "amount": amount, "currency": currency}
    try:
        with audit_call(ctx.store, tool="send_payout", payload=payload) as scope:
            res = await ctx.provider.send_payout(
                msisdn=msisdn, amount=amount, currency=currency, note=note,
            )
            scope.reference_id = res.transaction_id
            scope.outcome = "pending_approval" if res.pending_approval else (
                res.status.value if res.status else "sent"
            )
            return {"ok": True, **_result(res)}
    except (GuardrailRejection, ProviderError) as exc:
        return _error(exc)


@mcp.tool()
async def confirm_payout(approval_code: str) -> dict[str, Any]:
    """Execute a payout previously requested via send_payout, using its one-time
    approval_code. The code is single-use and time-limited; a forged, expired, or
    already-used code is rejected. On success the payout is sent and a
    transaction_id is returned, poll check_payment_status for the outcome.
    """
    ctx = _app()
    try:
        with audit_call(ctx.store, tool="confirm_payout",
                        payload={"approval_code": approval_code}) as scope:
            res = await ctx.provider.confirm_payout(approval_code)
            scope.reference_id = res.transaction_id
            scope.outcome = res.status.value if res.status else "sent"
            return {"ok": True, **_result(res)}
    except (GuardrailRejection, ProviderError) as exc:
        return _error(exc)


@mcp.tool()
async def get_balance(account: str = "collection") -> dict[str, Any]:
    """Get the available balance for the 'collection' or 'disbursement' account.

    Note: balance is unreliable in the MTN sandbox tier (see GOTCHAS); a clear
    error here is expected in sandbox and resolves at production go-live.
    """
    ctx = _app()
    try:
        with audit_call(ctx.store, tool="get_balance", payload={"account": account}) as scope:
            res = await ctx.provider.get_balance(account)
            scope.outcome = "ok"
            return {"ok": True, **_result(res)}
    except (GuardrailRejection, ProviderError) as exc:
        return _error(exc)


@mcp.tool()
async def validate_account(msisdn: str) -> dict[str, Any]:
    """Pre-flight check whether an MSISDN is an active MoMo account before
    charging it. Cheap; reduces failed requests. Note: this endpoint is
    unreliable in the MTN sandbox (see GOTCHAS), trust it in production.
    """
    ctx = _app()
    try:
        with audit_call(ctx.store, tool="validate_account", payload={"msisdn": msisdn}) as scope:
            res = await ctx.provider.validate_account(msisdn)
            scope.outcome = "active" if res.is_active else "inactive"
            return {"ok": True, **_result(res)}
    except (GuardrailRejection, ProviderError) as exc:
        return _error(exc)


@mcp.tool()
async def list_transactions(
    status: str | None = None, msisdn: str | None = None, limit: int = 50,
) -> dict[str, Any]:
    """Query the LOCAL transaction ledger (never the MTN API). Filter by status
    (PENDING/SUCCESSFUL/FAILED/REJECTED/TIMEOUT), msisdn, or limit. Use this to
    review history, reconcile, or answer 'what did I send today?'.
    """
    ctx = _app()
    with audit_call(ctx.store, tool="list_transactions",
                    payload={"status": status, "msisdn": msisdn, "limit": limit}) as scope:
        rows = ctx.store.list_transactions(status=status, msisdn=msisdn, limit=limit)
        scope.outcome = "ok"
        return {"ok": True, "count": len(rows), "transactions": [asdict(r) for r in rows]}


@mcp.tool()
async def get_provider_health() -> dict[str, Any]:
    """Operational signal: token validity and last API latency for the provider.
    Use to check the integration is healthy before relying on it.
    """
    ctx = _app()
    with audit_call(ctx.store, tool="get_provider_health", payload={}) as scope:
        health = await ctx.provider.health()
        # Guardrail accounting counts dry-run so limits cover the default mode.
        limit_usage = ctx.store.daily_usage()
        # ...and real money actually moved (excludes dry-run).
        real_usage = ctx.store.daily_usage(include_dry_run=False)
        scope.outcome = "ok"
        return {
            "ok": True,
            **_result(health),
            "daily_tx_count": limit_usage.tx_count,
            "daily_total_counted_for_limits": limit_usage.total_amount,
            "daily_total_real_money": real_usage.total_amount,
            "dry_run": ctx.settings.dry_run,
        }


# ── ledger exposed as an MCP resource (good-citizen) ───────────────────
@mcp.resource("ledger://transactions/recent")
def recent_ledger() -> str:
    """The 100 most recent ledger transactions as JSON, readable by MCP clients
    that browse resources (e.g. Glama), without invoking a tool."""
    import json

    ctx = _app()
    rows = ctx.store.list_transactions(limit=100)
    return json.dumps([asdict(r) for r in rows], indent=2, default=str)


def main() -> int:
    """Validate config, then serve over stdio. Exit 2 on config error."""
    try:
        _app()  # build context now so config errors surface before serving
    except ConfigError as exc:
        print(f"Configuration error:\n  {exc}", file=sys.stderr)
        return 2
    log.info("serving MCP over stdio")
    mcp.run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
