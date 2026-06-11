# MoMo MCP Server

[![CI](https://github.com/SamuelIZABAYO/Momo-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/SamuelIZABAYO/Momo-mcp-server/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

**The safety-first, production-grade MTN Mobile Money (sandbox) MCP server.**

An [MCP](https://modelcontextprotocol.io) server that exposes MTN Mobile Money
operations as tools any MCP client (Claude, Cursor, …) can call — built to the
standard money requires: approval gates, spend limits, idempotency, an audit
trail, and a kill switch. Airtel Money is stubbed behind the same interface for
later.

> **Sandbox only.** This repository contains no production endpoints or
> real-money code paths by policy. See [Hard Rules](#hard-rules).

> **Build status:** Phase 1 (skeleton + config + provisioning) complete.
> Tools land in Phases 2–4 — see the [build phases](#build-phases).

---

## Why this exists

Other payments MCP servers optimize for breadth and demo ease. This one
deliberately optimizes for what they don't market — the controls that make it
safe to let an AI agent touch money:

| Differentiator | Where |
|---|---|
| Idempotency (persisted **before** send) | [`store.py`](src/momo_mcp/store.py) |
| Append-only audit log, secrets/MSISDN redaction | [`store.py`](src/momo_mcp/store.py), [`logging_conf.py`](src/momo_mcp/logging_conf.py) |
| Approval gate on payouts (`confirm_payout`) | Phase 3 |
| Spend limits + allowlist + `DRY_RUN` + `PAUSE` kill switch | Phase 3 |
| Adversarial agent-safety test suite + `SAFETY.md` scorecard | Phase 5 |

Every safety claim is meant to be verifiable in the code — that's the point.

---

## Quick start (developer)

> Full install-to-first-payment walkthrough lands in Phase 5. The steps below
> cover the current (Phase 1) skeleton.

### Prerequisites

1. Register at [momodeveloper.mtn.com](https://momodeveloper.mtn.com) (free).
2. Subscribe to **Collections** and **Disbursements**; copy each product's
   primary key (`Ocp-Apim-Subscription-Key`).
3. Copy `.env.example` to `.env` and paste the keys in. **Never commit `.env`.**

### Set up

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### Provision sandbox API credentials (one time)

```bash
python scripts/provision.py
```

This creates a sandbox API user + key and prints the `MOMO_API_USER` /
`MOMO_API_KEY` lines to paste into `.env`. It never writes `.env` itself, so
credentials never land in git.

### Verify configuration

```bash
python -m momo_mcp.server   # validates config, reports readiness (no tools yet)
```

### Develop

```bash
ruff check .
pytest -m "not live"        # unit tests; the live sandbox suite is opt-in
```

---

## MCP tools (the public surface)

These land across Phases 2–4. The interface is fixed now.

| Tool | Purpose |
|---|---|
| `request_payment` | Ask a payer (MSISDN) to approve a charge (Collections) |
| `check_payment_status` | Poll a transaction → `PENDING/SUCCESSFUL/FAILED/TIMEOUT/REJECTED` |
| `get_balance` | Collections or disbursements account balance |
| `validate_account` | Pre-flight: is an MSISDN active/registered? |
| `send_payout` | Disbursement transfer — **approval-gated** |
| `confirm_payout` | Execute a payout with its one-time approval code |
| `list_transactions` | Query the local SQLite ledger (never the API) |
| `get_provider_health` | Token validity, latency, recent error rate |

---

## Safety model (overview)

- **`DRY_RUN=true` by default** — zero HTTP calls; realistic simulated responses
  written to the ledger flagged `dry_run`. The safe mode for demos.
- **`PAUSE` kill switch** — `touch PAUSE` and every mutating tool refuses
  instantly. No code, no restart.
- **Spend limits** — `MAX_AMOUNT_PER_TX`, `MAX_DAILY_TX_COUNT`,
  `MAX_DAILY_TOTAL`; breach = hard stop until a human runs `scripts/reset_limits.py`.
- **MSISDN allowlist** — in sandbox, only the magic test numbers are accepted,
  so a hallucinated number can never fire a request.
- **Approval gate** — payouts require a second `confirm_payout` call with a
  one-time code; the AI cannot move money unilaterally.

Full configuration reference: [`.env.example`](.env.example).

---

## Build phases

| Phase | Scope | Status |
|---|---|---|
| 1 | Skeleton + config + provisioning script | ✅ done |
| 2 | Auth + MTN Collections (`request_payment`, `check_payment_status`) | ⏳ |
| 3 | Disbursements + approval gate + remaining tools | ⏳ |
| 4 | MCP wiring (stdio) + Airtel stub + Docker | ⏳ |
| 5 | Docs + demo + safety scorecard | ⏳ |

---

## Hard rules

1. Never invent API behavior — verify against momodeveloper.mtn.com docs.
2. No secrets in code, logs, tests, or git history. Ever.
3. Sandbox only. No production endpoints, no real-money code paths.
4. Every mutation idempotent and persisted-before-send. No exceptions.
5. Tests pass locally and in CI before any phase is accepted.

---

## License

[MIT](LICENSE)
