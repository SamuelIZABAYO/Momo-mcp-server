# GOTCHAS — real MTN MoMo sandbox quirks found during the build

Audience: a skeptical senior engineer evaluating this for production. Every item
below was **observed directly against the live sandbox** during the build
(Hard Rule #1: verified, not assumed), with the date noted. Where the build spec
disagreed with observed behavior, observed behavior wins and the discrepancy is
flagged.

---

## 1. `requesttopay` returns `202` with an **empty body**

`POST /collection/v1_0/requesttopay` responds `202 Accepted` and **no JSON body**.
There is no transaction id in the response — *you* supply it as the
`X-Reference-Id` header, and that same id is how you poll status. If your HTTP
client tries to `.json()` the 202, it will throw. Treat 202 as "accepted, now
poll `GET .../requesttopay/{your-reference-id}`".

## 2. Outcome is keyed by the payer MSISDN, but the *reason* matters more than the *status*

The sandbox simulates outcomes deterministically by test number, but every
non-success outcome comes back as `status: "FAILED"` with a distinguishing
`reason`. You must read `reason` to normalize correctly. Observed
(2026-06-11):

| Payer MSISDN | Raw `status` | Raw `reason` | Normalized |
|---|---|---|---|
| `46733123450` | `FAILED` | `INTERNAL_PROCESSING_ERROR` | `FAILED` |
| `46733123451` | `FAILED` | `APPROVAL_REJECTED` | `REJECTED` |
| `46733123452` | `FAILED` | `EXPIRED` | `TIMEOUT` |
| `46733123453` | `SUCCESSFUL` (after a brief `PENDING`) | — | `SUCCESSFUL` |

**Discrepancy flagged:** the build spec described "magic MSISDNs to simulate
success / failed / rejected / timeout / pending" as if each were a distinct
`status`. In reality rejected and timeout both surface as `status: FAILED` and
are only distinguishable via `reason`. Our normalization maps
`reason` → canonical status (see [`tests/fixtures.py`](../tests/fixtures.py)).

## 3. A request briefly reports `PENDING` before resolving

`46733123453` returned `PENDING` on a fast poll and `SUCCESSFUL` a moment later.
`request_payment` must **never block** on resolution — it returns the
transaction id immediately; `check_payment_status` polls with backoff. This is
why the architecture splits the two (spec §3.4).

## 4. Successful collections carry a `financialTransactionId`

Only terminal `SUCCESSFUL` responses include `financialTransactionId` (MTN's own
settlement id). It is absent while `PENDING` and on failures. Store it when
present — it is what an accountant reconciles against.

## 5. `account/balance` is blocked in this sandbox (both products)

`GET /collection/v1_0/account/balance` is **inconsistent** in sandbox: across
runs on 2026-06-11 it returned `500 NOT_ALLOWED_TARGET_ENVIRONMENT`, then `404
RESOURCE_NOT_FOUND`, and on at least one run a `200`. The disbursement equivalent
`GET /disbursement/v1_0/account/balance` returned `500 {"code":"NOT_ALLOWED",
"message":"Authorization failed. Insufficient permissions."}`. Balance retrieval
is effectively **not reliable in either sandbox tier**. `get_balance` is
implemented against the documented contract and surfaces the real error honestly
(non-200 → clear `ProviderError`); the live test tolerates whichever outcome the
sandbox returns on the day, while the deterministic blocked-path behavior is
pinned in the mocked unit tests. This is a production go-live/permissions item,
not a code bug.

## 6. `accountholder/.../active` is inconsistent in sandbox

`GET /collection/v1_0/accountholder/msisdn/{number}/active` returned `404
RESOURCE_NOT_FOUND` for the magic test numbers (e.g. `46733123450`) but `200
{"result":true}` for an arbitrary `00000000`. So in sandbox, "validate account"
cannot be trusted to reflect the numbers you actually transact with. We surface
the raw result honestly and document that this endpoint is only meaningful in
production.

## 7. Sandbox currency is **EUR**, production Rwanda is **RWF**

All sandbox amounts are EUR. Production for Rwanda uses RWF and requires MTN
go-live approval + KYC. `MOMO_CURRENCY` is whitelisted to `{EUR, RWF}` and
defaults to EUR; see [`BUYER_README.md`](BUYER_README.md) and
[`GO_LIVE_RWANDA.md`](GO_LIVE_RWANDA.md).

## 8. Access token lifetime is 3600s (1 hour), `token_type: "access_token"`

`POST /collection/token/` returns
`{"access_token": "...", "token_type": "access_token", "expires_in": 3600}`.
Note `token_type` is the literal string `"access_token"`, not `"Bearer"` — you
still send it as `Authorization: Bearer <token>`. We cache the token and refresh
proactively at 80% of lifetime (≈48 min) rather than waiting for a 401.

## 9. Provisioning (`/v1_0/apiuser`) also returns empty bodies

`POST /v1_0/apiuser` returns `201` with no body — the `X-Reference-Id` you sent
*is* the API user id. Only the subsequent `POST /v1_0/apiuser/{id}/apikey`
returns a body (`{"apiKey": "..."}`). Don't parse the first response.

## 10. Disbursements reuse the same provisioned API user/key

The API user + key created by `scripts/provision.py` (against the Collections
subscription key) **also authenticate the Disbursements product** — `POST
/disbursement/token/` with the same Basic credentials + the disbursement
subscription key returns a valid token. One provisioning step covers both
products; you do not provision twice.

## 11. `disbursement/v1_0/transfer` mirrors `requesttopay` exactly

`POST /disbursement/v1_0/transfer` → `202` empty body; poll `GET
/disbursement/v1_0/transfer/{referenceId}`. Same status/`reason` semantics as
collections (verified 2026-06-11: `APPROVAL_REJECTED` → REJECTED, `EXPIRED` →
TIMEOUT). The one body difference: the counterparty key is `payee` (not
`payer`). This is why the provider shares one normalization path for both flows.

## 12. The Docker image is ~242MB, not <200MB — a deliberate trade-off

The build spec (§4.8) set a <200MB image target. We don't meet it, on purpose,
and document why rather than chase the number through fragility. The official
`mcp` Python SDK depends on `starlette`, `uvicorn`, and `cryptography` to support
its HTTP/SSE transports — none of which this server uses, because MCP clients
launch it over **stdio**. Those unused-transport deps set a ~240MB floor on a
`python:3.12-slim` base. We pruned everything safe to remove (pip, setuptools,
`__pycache__`, bundled test dirs), which got us from 274MB to 242MB.

Switching the base to `python:3.12-alpine` would save ~70MB but is a known
source of `cryptography` musl-wheel build failures; a 190MB image won through
brittleness is a worse outcome than a robust 242MB one for a payments component.
**Revised target: <250MB with the rationale documented.** If the `mcp` SDK ever
splits its transport extras (so stdio-only installs skip starlette/uvicorn), the
sub-200MB target becomes reachable without the alpine risk.
