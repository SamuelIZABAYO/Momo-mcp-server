# MoMo MCP Server

[![CI](https://github.com/SamuelIZABAYO/Momo-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/SamuelIZABAYO/Momo-mcp-server/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

**The safety-first, production-grade MTN Mobile Money (sandbox) MCP server.**

An [MCP](https://modelcontextprotocol.io) server that exposes MTN Mobile Money
operations as tools any MCP client (Claude, Cursor, …) can call — built to the
standard money requires: approval gates, spend limits, idempotency, an audit
trail, and a kill switch. Airtel Money is stubbed behind the same interface.

> **Sandbox only.** This repository contains no production endpoints or
> real-money code paths by policy. See [Hard rules](#hard-rules).

The differentiator is [**SAFETY.md**](docs/SAFETY.md): a scorecard of adversarial
attacks (oversized payouts, hallucinated numbers, forged/replayed approval codes,
mid-transaction crashes) with every one proven to fail closed — backed by
[`tests/test_agent_safety.py`](tests/test_agent_safety.py). That table, not the
happy path, is the reason to trust this.

---

## Install to first payment (in 9 steps)

**Prerequisites:** [Docker](https://docs.docker.com/get-docker/) *or* Python 3.11+.

1. **Register** at [momodeveloper.mtn.com](https://momodeveloper.mtn.com) (free).
2. **Subscribe** to **Collections** and **Disbursements**; copy each product's
   primary key (`Ocp-Apim-Subscription-Key`).
3. **Configure:** `cp .env.example .env`, paste the two keys in. (Keep values free
   of inline `#` comments — see the note in `.env.example`.)
4. **Install:**
   ```bash
   uv venv --python 3.12 && source .venv/bin/activate && uv pip install -e ".[dev]"
   ```
5. **Provision** sandbox API credentials (one time): `python scripts/provision.py`
   — paste the printed `MOMO_API_USER` / `MOMO_API_KEY` into `.env`.
6. **Verify config:** `python -m momo_mcp.server` (it validates and waits for an
   MCP client on stdio; Ctrl-C to exit).
7. **Connect an MCP client** — add the [config below](#connect-an-mcp-client) to
   Claude Desktop / Claude Code.
8. **In the client, validate then request a payment:** ask it to
   `request_payment` to `46733123453` for `5` EUR, then `check_payment_status`.
   In the default `DRY_RUN=true` mode this is fully simulated — zero real calls.
9. **See the controls:** ask it to `send_payout` — watch it return
   *pending approval* and refuse to move money until you `confirm_payout`.

Set `DRY_RUN=false` in `.env` to hit the real sandbox.

---

## Connect an MCP client

**Local (Python):**
```json
{
  "mcpServers": {
    "momo": {
      "command": "python",
      "args": ["-m", "momo_mcp.server"],
      "cwd": "/absolute/path/to/Momo-mcp-server"
    }
  }
}
```

**Docker** (zero Python setup; secrets via `--env-file`, never baked in):
```json
{
  "mcpServers": {
    "momo": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "--env-file", "/absolute/path/to/.env",
        "-v", "momo-data:/data",
        "ghcr.io/samuelizabayo/momo-mcp-server:latest"
      ]
    }
  }
}
```

Build the image yourself: `docker build -t momo-mcp-server .`

---

## MCP tools

| Tool | Purpose |
|---|---|
| `request_payment` | Ask a payer (MSISDN) to approve a charge (Collections). Returns `transaction_id`, status PENDING. |
| `check_payment_status` | Resolve a transaction → `PENDING/SUCCESSFUL/FAILED/REJECTED/TIMEOUT`. |
| `send_payout` | Send money to an MSISDN (Disbursements) — **approval-gated**; returns a one-time code, sends nothing. |
| `confirm_payout` | Execute a payout with its one-time approval code. |
| `get_balance` | Collection/disbursement balance (restricted in sandbox — see GOTCHAS). |
| `validate_account` | Pre-flight: is an MSISDN active? (unreliable in sandbox — see GOTCHAS). |
| `list_transactions` | Query the local SQLite ledger (never the API). |
| `get_provider_health` | Token validity, latency, daily usage vs. limits. |

The ledger is also exposed as an MCP **resource** (`ledger://transactions/recent`)
for clients that browse resources.

---

## Safety model

| Control | Behavior | Config |
|---|---|---|
| **Dry run** | Default on: realistic responses, zero HTTP, ledger rows flagged `dry_run`. | `DRY_RUN=true` |
| **Approval gate** | Payouts need a second `confirm_payout` with a one-time, single-use, amount-bound code. | `REQUIRE_PAYOUT_APPROVAL=true` |
| **Per-tx limit** | Amounts over the cap are rejected, never auto-split. | `MAX_AMOUNT_PER_TX` |
| **Daily limits** | Count + total caps; breach = hard stop until `scripts/reset_limits.py`. | `MAX_DAILY_TX_COUNT`, `MAX_DAILY_TOTAL` |
| **Allowlist** | In sandbox, only approved numbers; a hallucinated MSISDN can't fire. | `MSISDN_ALLOWLIST` |
| **Kill switch** | `touch PAUSE` → all mutations refuse instantly. No restart. | `PAUSE` file |
| **Idempotency** | Reference id persisted **before** send; crash-resume, no double charge. | always on |
| **Audit log** | One append-only row per call (input hashed, no raw values). | always on |

All of the above are verified in [SAFETY.md](docs/SAFETY.md).

---

## Timeout & retry policy

- HTTP timeout: 10s. Idempotent retries: max 2, exponential.
- Token: cached, refreshed proactively at 80% of its 1h lifetime; on a 401,
  refresh once and retry once — never a loop.
- `check_payment_status` polls with backoff (2/4/8/16/30s, ~60s cap) and never
  blocks `request_payment`.
- Client-side rate limit: token bucket (`RATE_LIMIT_PER_SEC`, default 5/s) —
  MTN's sandbox throttles aggressively.

---

## Development

```bash
ruff check .                 # lint
pytest -m "not live"         # unit + safety + server tests (no creds needed)
pytest -m live               # hits the real sandbox (needs provisioned .env)
pytest tests/test_agent_safety.py -v   # the safety scorecard
python scripts/export_ledger.py        # accountant CSV export
```

CI runs lint, the unit suite, a committed-secret scan, a Docker build, a Trivy
vulnerability scan, and SBOM generation on every push.

---

## Docs

- [SAFETY.md](docs/SAFETY.md) — adversarial scorecard (start here).
- [BUYER_README.md](docs/BUYER_README.md) — for a non-technical decision-maker.
- [GOTCHAS.md](docs/GOTCHAS.md) — real MTN sandbox quirks found during the build.
- [flow-diagram.md](docs/flow-diagram.md) — payment flow incl. timeout/crash paths.
- [GO_LIVE_RWANDA.md](docs/GO_LIVE_RWANDA.md) — sandbox→production-in-Rwanda playbook.

---

## Build phases

| Phase | Scope | Status |
|---|---|---|
| 1 | Skeleton + config + provisioning script | ✅ done |
| 2 | Auth + MTN Collections | ✅ done |
| 3 | Disbursements + approval gate + remaining tools | ✅ done |
| 4 | MCP stdio wiring + Airtel stub + Docker | ✅ done |
| 5 | Docs + demo + safety scorecard | ✅ done |

---

## Architecture

MCP tools never call MTN directly — they call the `PaymentProvider` interface
([`providers/base.py`](src/momo_mcp/providers/base.py)). MTN is the one concrete
implementation; Airtel ships as a stub. Adding a provider is one new file, zero
tool changes. Guardrails are enforced in the provider layer so no tool can bypass
them. See the [out-of-scope roadmap](docs/BUYER_README.md#what-youd-pay-for-beyond-this)
for what's deliberately not built in v1.

---

## Hard rules

1. Never invent API behavior — verify against momodeveloper.mtn.com docs.
2. No secrets in code, logs, tests, or git history. Ever.
3. Sandbox only. No production endpoints, no real-money code paths.
4. Every mutation idempotent and persisted-before-send. No exceptions.
5. Tests pass locally and in CI before any phase is accepted.

## License

[MIT](LICENSE)
