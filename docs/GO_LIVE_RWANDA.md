# Going live with MTN MoMo in Rwanda

The path from this sandbox build to production in Rwanda. Keep operational
details here only after they have been verified against MTN Rwanda's current
process.

---

## 0. At a glance

| Item | Sandbox (today) | Production (Rwanda) |
|---|---|---|
| Currency | EUR | RWF |
| Credentials | self-service API user/key | MTN-approved production keys |
| KYC | none | required (business + signatory) |
| Account | none | RWF merchant account |
| Approval to go live | none | MTN go-live review |
| Typical lead time | minutes | Confirm with MTN Rwanda during onboarding |

---

## 1. Prerequisites before you apply

- [ ] A production callback host (HTTPS) if using webhooks

## 2. MTN production access request

Confirm the current application route, required submissions, MTN contact path,
and review timing directly with MTN Rwanda before committing a go-live date.

## 3. KYC requirements

Confirm the current business, signatory, address, banking, and any in-person or
notarized requirements directly with MTN Rwanda during onboarding.

## 4. RWF specifics

- [ ] Set `MOMO_CURRENCY=RWF` (already whitelisted in config) once approved.

Confirm production transaction limits, amount precision, and fee treatment with
MTN Rwanda before enabling live RWF traffic.

## 5. Settlement timing

Confirm collection availability, payout settlement timing, reconciliation
cadence, and statement format with MTN Rwanda and the finance owner.

## 6. Production cutover checklist (technical)

- [ ] Swap sandbox base URL → production base URL (config change only)
- [ ] Replace sandbox subscription keys with production keys (env only)
- [ ] Re-run provisioning against production (`scripts/provision.py`)
- [ ] Set `MOMO_TARGET_ENV` appropriately. Note: this repo is pinned
      sandbox-only by policy, so production cutover happens in a deployment
      configured for it, not in this repo as-is.
- [ ] Set a real `MSISDN_ALLOWLIST`, or remove allowlist enforcement with
      documented sign-off.
- [ ] Set real spend limits (`MAX_AMOUNT_PER_TX`, `MAX_DAILY_*`)
- [ ] Turn off `DRY_RUN`
- [ ] Stand up the hardened deployment (TLS, isolation, backups)
- [ ] Smoke-test one small real-money transaction end to end
- [ ] Confirm the audit log + ledger export satisfy the accountant

## 7. Realistic timeline

Set the launch timeline only after MTN Rwanda confirms onboarding requirements,
review timing, credential issuance, and first-transaction readiness.
