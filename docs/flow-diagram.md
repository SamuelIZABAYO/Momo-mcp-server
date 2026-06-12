# Payment flow

These diagrams include the failure and recovery paths (timeout, crash,
reconciliation), not just the happy path.

## Collections: request_payment then check_payment_status

```mermaid
sequenceDiagram
    autonumber
    participant Agent as MCP Client
    participant Srv as MoMo MCP Server
    participant DB as SQLite ledger
    participant MTN as MTN MoMo Sandbox

    Agent->>Srv: request_payment(msisdn, amount)
    Srv->>Srv: guardrails (PAUSE, allowlist, per-tx, daily)
    alt rejected by a guardrail
        Srv->>DB: audit row rejected:<reason>
        Srv-->>Agent: rejected; inform user, do not retry
    else allowed
        Srv->>DB: INSERT transaction PENDING (reference_id), BEFORE send
        Srv->>MTN: POST /collection/v1_0/requesttopay (X-Reference-Id)
        MTN-->>Srv: 202 Accepted (empty body)
        Srv-->>Agent: transaction_id, status=PENDING
    end

    Note over Agent,MTN: status resolves asynchronously on the payer's phone

    Agent->>Srv: check_payment_status(transaction_id)
    loop backoff 2s,4s,8s,16s,30s (cap ~60s)
        Srv->>MTN: GET /collection/v1_0/requesttopay/{id}
        MTN-->>Srv: {status, reason}
        alt terminal (SUCCESSFUL / FAILED / REJECTED / TIMEOUT)
            Srv->>DB: UPDATE status (normalized from reason)
            Srv-->>Agent: final status
        else still PENDING
            Note over Srv: keep polling until budget exhausted
        end
    end
```

## Crash & reconciliation (no double charge)

```mermaid
sequenceDiagram
    autonumber
    participant Srv as MoMo MCP Server
    participant DB as SQLite ledger
    participant MTN as MTN MoMo

    Srv->>DB: INSERT PENDING (reference_id)
    Srv--xMTN: POST requesttopay … 💥 process crashes mid-call
    Note over Srv,DB: the PENDING row survives; it was written BEFORE the send

    rect rgb(235,245,255)
        Note over Srv: restart
        Srv->>DB: pending_transactions() → [reference_id]
        Srv->>MTN: GET status using the SAME reference_id
        MTN-->>Srv: current status (MTN dedupes on reference_id)
        Srv->>DB: UPDATE to terminal state
    end
    Note over Srv,MTN: idempotency key reuse ⇒ at most one charge
```

## Payout approval gate

```mermaid
sequenceDiagram
    autonumber
    participant Agent as MCP Client
    participant Srv as MoMo MCP Server
    participant Human
    participant MTN as MTN MoMo

    Agent->>Srv: send_payout(msisdn, amount)
    Srv->>Srv: guardrails
    Srv-->>Agent: pending_approval + one-time code (NOTHING sent)
    Agent->>Human: "a payout needs your approval"
    Human->>Agent: approves → provides code
    Agent->>Srv: confirm_payout(code)
    Srv->>Srv: code valid & unused & matches amount/payee?
    alt yes
        Srv->>MTN: POST /disbursement/v1_0/transfer
        MTN-->>Srv: 202 Accepted
        Srv-->>Agent: transaction_id, PENDING
    else forged / expired / replayed / mismatched
        Srv-->>Agent: rejected; inform user, do not retry
    end
```

## Status normalization (the sandbox quirk)

MTN returns `status: FAILED` for several distinct outcomes; the **`reason`**
field disambiguates them (see [GOTCHAS](GOTCHAS.md)):

| MTN `status` | MTN `reason` | Normalized |
|---|---|---|
| SUCCESSFUL | — | SUCCESSFUL |
| PENDING | — | PENDING |
| FAILED | APPROVAL_REJECTED | REJECTED |
| FAILED | EXPIRED | TIMEOUT |
| FAILED | (other) | FAILED |
