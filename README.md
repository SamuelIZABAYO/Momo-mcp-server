# MoMo MCP Server

[![CI](https://github.com/SamuelIZABAYO/Momo-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/SamuelIZABAYO/Momo-mcp-server/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

An [MCP](https://modelcontextprotocol.io) server for MTN Mobile Money (sandbox).
It exposes MoMo payment operations as tools an MCP client (Claude, Cursor, etc.)
can call, with approval gates, spend limits, idempotency, an audit log, and a
kill switch.

> Sandbox only. This repository has no production endpoints or real-money code
> paths. See [design constraints](#design-constraints).

[SAFETY.md](docs/SAFETY.md) covers the guardrail cases (oversized payouts,
unknown numbers, forged/replayed approval codes, mid-transaction crashes), each
verified by a test in
[`tests/test_agent_safety.py`](tests/test_agent_safety.py).

---

## Install to first payment

Prerequisites: [Docker](https://docs.docker.com/get-docker/) or Python 3.11+.

1. Register at [momodeveloper.mtn.com](https://momodeveloper.mtn.com) (free).
2. Subscribe to Collections and Disbursements. Copy each product's primary key
   (`Ocp-Apim-Subscription-Key`).
3. `cp .env.example .env`, paste the two keys in, and set
   `MOMO_CALLBACK_HOST` to an HTTPS callback host. Keep values free of inline
   `#` comments (see the note in `.env.example`).
4. Install:
   ```bash
   uv venv --python 3.12 && source .venv/bin/activate && uv pip install -e ".[dev]"
   ```
5. Provision sandbox API credentials once: `python scripts/provision.py`. Paste
   the printed `MOMO_API_USER` / `MOMO_API_KEY` into `.env`.
6. Verify config: `python -m momo_mcp.server`. It validates and then waits for an
   MCP client on stdio (Ctrl-C to exit).
7. Connect an MCP client using the [config below](#connect-an-mcp-client).
8. In the client, ask it to `request_payment` to `46733123453` for `5` EUR, then
   `check_payment_status`. With the default `DRY_RUN=true` this is simulated and
   makes no real calls.
9. Ask it to `send_payout`. It returns a pending approval and will not move money
   until you `confirm_payout`.

Set `DRY_RUN=false` in `.env` to hit the real sandbox.

---

## Connect an MCP client

Local (Python):
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

Docker (no Python setup; secrets passed via `--env-file`, not baked into the
image):
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
| `check_payment_status` | Resolve a transaction to `PENDING/SUCCESSFUL/FAILED/REJECTED/TIMEOUT`. |
| `send_payout` | Send money to an MSISDN (Disbursements). Approval-gated: returns a one-time code and sends nothing. |
| `confirm_payout` | Execute a payout with its one-time approval code. |
| `get_balance` | Collection/disbursement balance (restricted in sandbox; see GOTCHAS). |
| `validate_account` | Check whether an MSISDN is active (unreliable in sandbox; see GOTCHAS). |
| `list_transactions` | Query the local SQLite ledger. Never calls the API. |
| `get_provider_health` | Token validity, latency, daily usage vs. limits. |
| `list_audit` | Read the append-only audit log (tool, input hash, outcome, latency). |

The ledger and the audit log are also exposed as MCP resources
(`ledger://transactions/recent` and `audit://recent`) for clients that browse
resources.

---

## Safety model

| Control | Behavior | Config |
|---|---|---|
| Dry run | On by default: simulated responses, no HTTP, ledger rows flagged `dry_run`. | `DRY_RUN=true` |
| Approval gate | Payouts need a second `confirm_payout` with a one-time, single-use, amount-bound code. | `REQUIRE_PAYOUT_APPROVAL=true` |
| Per-tx limit | Amounts over the cap are rejected, not auto-split. | `MAX_AMOUNT_PER_TX` |
| Daily limits | Count and total caps. Breach is a hard stop until `scripts/reset_limits.py`. | `MAX_DAILY_TX_COUNT`, `MAX_DAILY_TOTAL` |
| Allowlist | In sandbox, only approved numbers. An unknown MSISDN cannot fire. | `MSISDN_ALLOWLIST` |
| Kill switch | `touch PAUSE` and all mutations refuse. No restart needed. | `PAUSE` file |
| Idempotency | Reference id persisted before send. Crash-resume without double charge. | always on |
| Audit log | One append-only row per call (input hashed, no raw values). | always on |

These are covered by the tests in [SAFETY.md](docs/SAFETY.md).

---

## Timeout and retry policy

- HTTP timeout 10s.
- Transient failures (5xx and network errors) are retried for idempotent calls
  only: GETs, and mutations that carry an `X-Reference-Id` (MTN dedupes on it).
  Max 2 retries, exponential backoff (0.5s, 1s). Non-idempotent calls are not
  retried.
- Token cached, refreshed at 80% of its 1h lifetime. On a 401, refresh once and
  retry once, then stop.
- `check_payment_status` polls with backoff (2/4/8/16/30s, ~60s cap) and does
  not block `request_payment`.
- Client-side rate limit: token bucket (`RATE_LIMIT_PER_SEC`, default 5/s).
  MTN's sandbox throttles aggressively.

---

## Development

```bash
ruff check .                 # lint
pytest -m "not live"         # unit + safety + server tests (no creds needed)
pytest -m live               # hits the real sandbox (needs a provisioned .env)
pytest tests/test_agent_safety.py -v
python scripts/export_ledger.py        # CSV ledger export
```

CI runs lint, the unit suite, a committed-secret scan, a Docker build, a Trivy
vulnerability scan, and SBOM generation on every push.

---

## Docs

- [SAFETY.md](docs/SAFETY.md): safety test results.
- [BUYER_README.md](docs/BUYER_README.md): non-technical overview.
- [GOTCHAS.md](docs/GOTCHAS.md): MTN sandbox quirks found during the build.
- [flow-diagram.md](docs/flow-diagram.md): payment flow, including timeout and crash paths.
- [GO_LIVE_RWANDA.md](docs/GO_LIVE_RWANDA.md): sandbox to production in Rwanda.

---

## Architecture

MCP tools never call MTN directly. They call the `PaymentProvider` interface
([`providers/base.py`](src/momo_mcp/providers/base.py)). MTN is the concrete
implementation; Airtel is a stub on the same contract. Adding a provider is one
new file with no tool changes. Guardrails run in the provider layer so no tool
can bypass them. See [BUYER_README](docs/BUYER_README.md#production-additions) for
what is not built in v1.

---

## Design constraints

1. API behavior is verified against momodeveloper.mtn.com docs, not assumed.
2. No secrets in code, logs, tests, or git history.
3. Sandbox only. No production endpoints or real-money code paths.
4. Every mutation is idempotent and persisted before send.
5. Tests pass locally and in CI.

## License

[MIT](LICENSE)
