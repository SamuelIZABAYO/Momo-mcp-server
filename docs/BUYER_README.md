# Accepting MTN Mobile Money

Written for a product or business owner, not an engineer. No code.

You want customers to pay you with MTN Mobile Money (MoMo), and you want the
payment tools to handle requests and payouts safely. This explains what that
involves, what works today, and what production needs.

---

## How a MoMo payment works

1. You ask the customer to pay. The system sends a request to their phone number.
   Their phone shows a MoMo prompt asking them to approve paying you a set amount.
2. The customer approves on their own phone with their MoMo PIN. This works on
   any phone, including a basic button phone, through the SIM menu. They never
   touch your software.
3. You get the result: successful, rejected (they declined), expired (no
   response in time), or failed.
4. Money settles into your MoMo merchant account.

Paying out (sending money to someone) works the same way in reverse.

You don't build the customer's experience. MTN owns that. You integrate with
MTN's system, which is what this product does.

---

## What's safe about it

An assistant that can move money needs limits. Payment actions here are
constrained, and you can check each constraint:

- No transaction above a set per-transaction limit, and no more than a daily cap.
  These are hard stops.
- No payment to a number you didn't approve. An invented or wrong number is
  refused.
- No payout on its own. Every payout needs a second, human-approved step. The
  assistant requests it; a person confirms.
- You can freeze it. Creating a file called `PAUSE` stops all money movement.
- Every call is logged in an audit trail, and the transaction history exports to
  a spreadsheet for reconciliation.
- A "dry run" mode is on by default. It simulates everything with no real money
  until you turn it off.

Each of these is covered by an automated test, summarized in
[SAFETY.md](SAFETY.md).

---

## Sandbox vs. production

What works today runs against MTN's sandbox, a free test environment. In sandbox:

- The currency is euros (EUR), not Rwandan francs. That is an MTN sandbox
  limitation.
- Phone numbers are fixed test numbers that simulate each outcome (success,
  rejection, timeout). No real phones, no real money.
- Some features (like checking your account balance) are restricted by MTN in the
  test tier and only work once you are live.

Going live in Rwanda requires steps only the business can take: applying for MTN
production access, completing KYC (know-your-customer) verification, and holding
a merchant account in Rwandan francs (RWF). The path and timeline are in
[GO_LIVE_RWANDA.md](GO_LIVE_RWANDA.md).

---

## Production additions

This server provides the payment tools. The following are not included, and each
is a separate piece of work:

- A website or dashboard. A custom checkout page or merchant dashboard is built
  per business.
- Always-on hosting with security hardening (TLS, isolation, backups). Built per
  client, since every business's infrastructure differs.
- Push notifications from MTN (webhooks). Today the system polls for results.
  Live deployments can add push for lower latency.
- Other providers (Airtel Money, etc.). The system is built so adding one is a
  contained job; the safety controls and tools stay the same.

---

## Summary

The server lets an MCP client request MTN MoMo payments and payouts, with
spending limits, a human approval step for payouts, a kill switch, and an audit
trail, all covered by automated tests. It works against MTN's free sandbox today.
Going live in Rwanda is a documented process (MTN approval, KYC, and a francs
merchant account).
