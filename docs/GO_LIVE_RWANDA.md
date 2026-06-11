# Going live with MTN MoMo in Rwanda — playbook (skeleton)

> **Status: skeleton owned by the human (spec §7.3).** This is the unfakeable,
> local-knowledge asset: the real, walked path from this sandbox build to
> production in Rwanda. Codex created the structure; the verified specifics
> (timelines, contacts, exact KYC document lists, fees) are filled in by someone
> who has actually been through the process. Write only what is verified — an
> honest partial beats a confident guess. Mark unknowns as `TODO (verify)`.

---

## 0. At a glance

| Item | Sandbox (today) | Production (Rwanda) |
|---|---|---|
| Currency | EUR | RWF |
| Credentials | self-service API user/key | MTN-approved production keys |
| KYC | none | required (business + signatory) |
| Account | none | RWF merchant account |
| Approval to go live | none | MTN go-live review |
| Typical lead time | minutes | `TODO (verify) — weeks?` |

---

## 1. Prerequisites before you apply

- [ ] Registered business entity in Rwanda — `TODO (verify): RDB registration / TIN`
- [ ] Business bank or MoMo merchant relationship — `TODO (verify)`
- [ ] Authorized signatory identity documents — `TODO (verify): exact list`
- [ ] A production callback host (HTTPS) if using webhooks — see [§8.1 roadmap](../README.md)

## 2. MTN production access request

- [ ] Where to apply: `TODO (verify): MTN Rwanda developer/partner portal vs. momodeveloper.mtn.com production tier`
- [ ] What MTN asks for: `TODO (verify)`
- [ ] Who to contact at MTN Rwanda (partner/integration desk): `TODO (verify)`
- [ ] Expected review time: `TODO (verify)`

## 3. KYC requirements

- [ ] Business documents: `TODO (verify): certificate of incorporation, TIN, etc.`
- [ ] Signatory documents: `TODO (verify): national ID / passport`
- [ ] Proof of address / bank details: `TODO (verify)`
- [ ] Any in-person or notarized steps: `TODO (verify)`

## 4. RWF specifics

- [ ] Minimum / maximum transaction amounts in RWF: `TODO (verify)`
- [ ] Whether amounts are integer-only (no minor units) in RWF: `TODO (verify)`
- [ ] Fee structure / who bears the fee: `TODO (verify)`
- [ ] Set `MOMO_CURRENCY=RWF` (already whitelisted in config) once approved.

## 5. Settlement timing

- [ ] When collected funds become available: `TODO (verify): instant / T+1 / batch`
- [ ] Payout (disbursement) settlement timing: `TODO (verify)`
- [ ] Reconciliation cadence and statements: `TODO (verify)`

## 6. Production cutover checklist (technical)

- [ ] Swap sandbox base URL → production base URL (config change only)
- [ ] Replace sandbox subscription keys with production keys (env only)
- [ ] Re-run provisioning against production (`scripts/provision.py`)
- [ ] Set `MOMO_TARGET_ENV` appropriately — **note:** this repo is currently
      pinned sandbox-only by policy (Hard Rule #3); production cutover happens in
      a deployment configured for it, not in this repo as-is.
- [ ] Set real `MSISDN_ALLOWLIST` (or remove allowlist enforcement deliberately,
      with documented sign-off)
- [ ] Set real spend limits (`MAX_AMOUNT_PER_TX`, `MAX_DAILY_*`)
- [ ] Turn off `DRY_RUN`
- [ ] Stand up the hardened deployment (TLS, isolation, backups — see §8.2)
- [ ] Smoke-test one small real-money transaction end to end
- [ ] Confirm the audit log + ledger export satisfy the accountant

## 7. Realistic timeline (fill in from experience)

`TODO (verify): a week-by-week from "applied" to "first real payment".`

---

*Every line marked `TODO (verify)` is an invitation to add real, checked
knowledge. That accumulated detail — not the code — is what a competitor cannot
clone in a weekend.*
