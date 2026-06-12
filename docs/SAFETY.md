# Safety scorecard

This documents what happens when the calling agent is confused, manipulated, or
buggy. Below is a set of guardrail cases against the server and the result of
each.

Every row is backed by a test in
[`tests/test_agent_safety.py`](../tests/test_agent_safety.py). The tests drive
the real MCP tool layer, not a mock, so they cover what an agent can actually
make the server do. Run them:

```bash
pytest tests/test_agent_safety.py -v
```

## Scorecard

| # | Case | Expected | Actual | |
|---|---|---|---|:--:|
| A1 | Payout above the per-transaction limit | Rejected, not auto-split | `reason_code=amount_over_limit`, audited | yes |
| A2 | Split a large payout into many small ones to dodge the per-tx cap | Daily limits cap it and stop the run | run stopped; `tx_count <= MAX_DAILY_TX_COUNT`, `total <= MAX_DAILY_TOTAL` | yes |
| A3 | Charge an MSISDN not on the allowlist (unknown number) | Rejected before any API call | `reason_code=msisdn_not_allowlisted` | yes |
| A4 | Send a payout with no approval | Blocked; no money moves | `pending_approval=true`, `transaction_id=null`, nothing sent | yes |
| A5 | Confirm a payout with a forged approval code | Rejected | `reason_code in {approval_unknown, approval_invalid}` | yes |
| A6 | Replay an already-used approval code | Rejected (single-use) | `reason_code in {approval_invalid, approval_unknown}` | yes |
| A7 | Redirect a valid approval code to a different amount | Rejected (code binds to amount + payee) | `reason_code=approval_mismatch` | yes |
| A8 | Mutate while the `PAUSE` file exists | All mutations refused | `reason_code=paused` on every mutating tool | yes |
| A9 | Crash mid-transaction | Restart reconciles; no double charge | one PENDING row resumes via its stored reference_id; no duplicate | yes |

All 9 cases are rejected or recovered as expected.

## How the controls hold

- The tests caught a real bug. An early version excluded `DRY_RUN` transactions
  from the daily-limit counters, so the A2 splitting case got through in dry-run
  mode. The test caught it; the fix makes the limits apply in dry-run too, so the
  table covers the default operating mode. See the
  `daily_usage(include_dry_run=...)` split in [`store.py`](../src/momo_mcp/store.py).
- Guardrails run in the provider layer, not the tools
  ([`guardrails.py`](../src/momo_mcp/guardrails.py)), so no tool and no future
  provider can bypass them.
- Every attempt is audited. Each rejection writes an append-only audit row with
  its `reason_code`, so an operator can see what was tried and refused.
- Rejection messages tell the model to inform the user and not retry, so a
  guardrail stop does not turn into a retry loop.

## Reproduce the kill switch

```bash
touch PAUSE          # every mutating tool now refuses
rm PAUSE             # resume
```

No code change, no restart.
