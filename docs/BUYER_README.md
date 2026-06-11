# Accepting MTN Mobile Money — what it actually takes

*Written for a product or business owner, not an engineer. No code, no jargon.*

You want customers to pay you with MTN Mobile Money (MoMo), and you want an AI
assistant to be able to request those payments and send payouts safely. This
explains what that involves, what's ready today, and what production needs.

---

## How a MoMo payment works (the 60-second version)

1. **You ask the customer to pay.** The system sends a request to the customer's
   phone number. Their phone shows a MoMo prompt asking them to approve paying
   you a specific amount.
2. **The customer approves on their own phone** by entering their MoMo PIN. This
   works on *any* phone — including a basic button phone, through the SIM menu.
   They never touch your software.
3. **You get the result.** The payment comes back as *successful*, *rejected*
   (they declined), *expired* (they didn't respond in time), or *failed*.
4. **Money settles** into your MoMo merchant account.

Paying *out* (sending money to someone) works the same way in reverse.

You don't build the customer's experience — MTN owns that. You integrate with
MTN's system, and that's what this product does.

---

## What's safe about this one (the whole point)

Letting an AI move money is risky if nothing constrains it. This product is built
so the AI **cannot** do anything dangerous, and you can prove it:

- **It can't send more than a set limit** per transaction, or more than a daily
  cap — those are hard stops, not suggestions.
- **It can't pay a number you didn't approve.** If the AI "hallucinates" a phone
  number, the request is refused.
- **It can't send a payout on its own.** Every payout needs a second,
  human-approved step. The AI asks; a person confirms.
- **You can freeze it instantly.** Creating a file called `PAUSE` stops all money
  movement immediately — no technical help needed.
- **Everything is logged** in an audit trail, and the transaction history exports
  to a spreadsheet your accountant can reconcile.
- **It runs in a safe "dry run" mode by default** — it simulates everything with
  zero real money until you deliberately turn that off.

These claims aren't marketing — each is verified by an automated test, summarized
in [SAFETY.md](SAFETY.md) as a scorecard. Ask to see that table.

---

## Sandbox vs. production (important)

What's built and tested today runs against MTN's **sandbox** — a free test
environment. In sandbox:

- The currency is **euros (EUR)**, not Rwandan francs. That's an MTN sandbox
  limitation, not a choice.
- Phone numbers are fixed **test numbers** that simulate each outcome (success,
  rejection, timeout). No real phones, no real money.
- Some features (like checking your account balance) are restricted by MTN in
  the test tier and only work once you're live.

Going **live in Rwanda** requires steps only you (the business) can take:
applying for MTN production access, completing **KYC** (know-your-customer)
verification, and holding a merchant account in **Rwandan francs (RWF)**. The
realistic path and timeline are written up in
[GO_LIVE_RWANDA.md](GO_LIVE_RWANDA.md).

---

## What you'd pay for beyond this

This product is the safe, agent-facing payment engine. Things that are
deliberately *not* included (and why) — each is a separate, scoped piece of work:

- **A website or dashboard.** The AI assistant *is* the interface by design.
  A custom checkout page or merchant dashboard is built per-business.
- **Always-on hosting** with security hardening (TLS, isolation, backups). Built
  per-client because every business's infrastructure differs.
- **Instant push notifications** from MTN (webhooks). Today the system reliably
  *polls* for results, which is the dependable choice in sandbox; live
  deployments can add push for lower latency.
- **Other providers** (Airtel Money, etc.). The system is built so adding one is
  a small, contained job — the safety controls and tools stay identical.

---

## The one-paragraph summary for a busy decision-maker

This is a safety-first way to let an AI assistant take MTN MoMo payments and send
payouts, with hard spending limits, a human approval step for payouts, an instant
kill switch, and a complete audit trail — all proven by an automated attack-test
scorecard. It's fully working against MTN's free sandbox today. Going live in
Rwanda is a known, documented process (MTN approval + KYC + a francs merchant
account) that we'll walk through with you.
