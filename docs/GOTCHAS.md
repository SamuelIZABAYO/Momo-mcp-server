# GOTCHAS: MTN MoMo sandbox quirks

Each item below was observed directly against the live sandbox during the build,
with the date noted. These are the things worth knowing before relying on the
integration.

---

## 1. `requesttopay` returns `202` with an empty body

`POST /collection/v1_0/requesttopay` responds `202 Accepted` with no JSON body.
There is no transaction id in the response. You supply it as the `X-Reference-Id`
header, and that same id is how you poll status. Calling `.json()` on the 202
will throw. Treat 202 as "accepted, now poll `GET .../requesttopay/{reference-id}`".

## 2. Outcome is keyed by the payer MSISDN, but read the reason, not just the status

The sandbox simulates outcomes by test number. Every non-success outcome comes
back as `status: "FAILED"` with a `reason`. You have to read `reason` to
normalize correctly. Observed (2026-06-11):

| Payer MSISDN | Raw `status` | Raw `reason` | Normalized |
|---|---|---|---|
| `46733123450` | `FAILED` | `INTERNAL_PROCESSING_ERROR` | `FAILED` |
| `46733123451` | `FAILED` | `APPROVAL_REJECTED` | `REJECTED` |
| `46733123452` | `FAILED` | `EXPIRED` | `TIMEOUT` |
| `46733123453` | `SUCCESSFUL` (after a brief `PENDING`) | — | `SUCCESSFUL` |

The magic MSISDNs are often described as simulating "success / failed / rejected
/ timeout" as if each were a distinct `status`. In practice, rejected and timeout
both come back as `status: FAILED` and are only told apart by `reason`. The
normalization maps `reason` to a canonical status (see
[`tests/fixtures.py`](../tests/fixtures.py)).

## 3. A request reports `PENDING` before it resolves

`46733123453` returned `PENDING` on a fast poll and `SUCCESSFUL` shortly after.
`request_payment` does not block on resolution. It returns the transaction id
immediately, and `check_payment_status` polls with backoff. That is why the two
are separate calls.

## 4. Successful collections carry a `financialTransactionId`

Only terminal `SUCCESSFUL` responses include `financialTransactionId`, MTN's own
settlement id. It is absent while `PENDING` and on failures. Store it when
present; it is what an accountant reconciles against.

## 5. `account/balance` is unreliable in this sandbox (both products)

`GET /collection/v1_0/account/balance` is inconsistent: across runs on 2026-06-11
it returned `500 NOT_ALLOWED_TARGET_ENVIRONMENT`, then `404 RESOURCE_NOT_FOUND`,
and on one run a `200`. The disbursement equivalent returned `500
{"code":"NOT_ALLOWED","message":"Authorization failed. Insufficient
permissions."}`. Balance retrieval is not reliable in either sandbox tier.
`get_balance` is implemented against the documented contract and returns a clear
`ProviderError` on non-200. The live test accepts whichever outcome the sandbox
gives that day, and the blocked-path behavior is pinned in the mocked unit tests.
This is a go-live/permissions item, not a code bug.

## 6. `accountholder/.../active` is inconsistent in sandbox

`GET /collection/v1_0/accountholder/msisdn/{number}/active` returned `404
RESOURCE_NOT_FOUND` for the magic test numbers (e.g. `46733123450`) but `200
{"result":true}` for an arbitrary `00000000`. In sandbox, "validate account"
can't be trusted to reflect the numbers you actually transact with. The tool
returns the raw result and notes that this endpoint is only meaningful in
production.

## 7. Sandbox currency is EUR, production Rwanda is RWF

Sandbox amounts are EUR. Production for Rwanda uses RWF and requires MTN go-live
approval plus KYC. `MOMO_CURRENCY` is whitelisted to `{EUR, RWF}` and defaults to
EUR. See [`BUYER_README.md`](BUYER_README.md) and
[`GO_LIVE_RWANDA.md`](GO_LIVE_RWANDA.md).

## 8. Access token lifetime is 3600s, `token_type` is `"access_token"`

`POST /collection/token/` returns
`{"access_token": "...", "token_type": "access_token", "expires_in": 3600}`.
`token_type` is the literal string `"access_token"`, not `"Bearer"`, but you
still send it as `Authorization: Bearer <token>`. The token is cached and
refreshed at 80% of its lifetime (about 48 min) rather than waiting for a 401.

## 9. Provisioning (`/v1_0/apiuser`) also returns empty bodies

`POST /v1_0/apiuser` returns `201` with no body. The `X-Reference-Id` you sent is
the API user id. Only the next call, `POST /v1_0/apiuser/{id}/apikey`, returns a
body (`{"apiKey": "..."}`). Don't parse the first response.

## 10. Disbursements reuse the same provisioned API user/key

The API user and key created by `scripts/provision.py` (against the Collections
subscription key) also authenticate the Disbursements product. `POST
/disbursement/token/` with the same Basic credentials plus the disbursement
subscription key returns a valid token. One provisioning step covers both
products.

## 11. `disbursement/v1_0/transfer` mirrors `requesttopay`

`POST /disbursement/v1_0/transfer` returns `202` with an empty body; poll `GET
/disbursement/v1_0/transfer/{referenceId}`. Same status and `reason` semantics as
collections (verified 2026-06-11: `APPROVAL_REJECTED` to REJECTED, `EXPIRED` to
TIMEOUT). One body difference: the counterparty key is `payee`, not `payer`. The
provider shares one normalization path for both flows.

## 12. The Docker image is about 242MB

The `mcp` Python SDK depends on `starlette`, `uvicorn`, and `cryptography` for its
HTTP/SSE transports, which this server doesn't use (clients launch it over
stdio). Those deps set a ~240MB floor on a `python:3.12-slim` base. Pip,
setuptools, `__pycache__`, and bundled test dirs are pruned, which brings the
image from 274MB to 242MB.

A `python:3.12-alpine` base would save about 70MB but is a known source of
`cryptography` musl-wheel build failures, so it isn't worth the fragility here. If
the `mcp` SDK ever splits its transport extras so stdio-only installs skip
starlette/uvicorn, a sub-200MB image becomes reachable.
