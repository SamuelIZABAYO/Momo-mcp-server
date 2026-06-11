#!/usr/bin/env python3
"""One-time sandbox provisioning: create an MTN API user + API key (spec §3.1).

Flow (verify against momodeveloper.mtn.com before relying on it — Hard Rule #1):
  1. POST {BASE}/v1_0/apiuser
        headers: X-Reference-Id: <uuid4>, Ocp-Apim-Subscription-Key: <collection key>
        body:    {"providerCallbackHost": "<host>"}
     -> 201 Created, empty body. The uuid4 we sent IS the API user id.
  2. POST {BASE}/v1_0/apiuser/{uuid}/apikey
        headers: Ocp-Apim-Subscription-Key: <collection key>
     -> 201 Created, body: {"apiKey": "..."}

This script is interactive and side-effecting against the sandbox, so it is NOT
run in CI and NOT imported by the server. It prints the resulting credentials
and the exact .env lines to paste — it never writes .env itself, so secrets are
never committed (Hard Rule #2). Run it once, by hand, after subscription keys
are in your .env.

Usage:
    python scripts/provision.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import httpx

# Allow running as a plain script (python scripts/provision.py) without install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from momo_mcp.config import ConfigError, load_settings  # noqa: E402


def provision() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration error:\n  {exc}", file=sys.stderr)
        return 2

    sub_key = settings.collection_subscription_key
    base = settings.base_url
    api_user = str(uuid.uuid4())

    # The user id we are about to register is the uuid we send as X-Reference-Id.
    headers = {
        "X-Reference-Id": api_user,
        "Ocp-Apim-Subscription-Key": sub_key,
        "Content-Type": "application/json",
    }
    body = {"providerCallbackHost": settings.callback_host}

    print(f"Provisioning sandbox API user against {base} …")
    try:
        with httpx.Client(timeout=10.0) as client:
            # Step 1 — create API user.
            r1 = client.post(f"{base}/v1_0/apiuser", headers=headers, json=body)
            if r1.status_code not in (201, 200):
                print(
                    f"apiuser creation failed: HTTP {r1.status_code}\n"
                    f"  body: {r1.text[:500]}\n"
                    "  Check the subscription key and that the Collections "
                    "product is subscribed at momodeveloper.mtn.com.",
                    file=sys.stderr,
                )
                return 1

            # Step 2 — generate the API key for that user.
            r2 = client.post(
                f"{base}/v1_0/apiuser/{api_user}/apikey",
                headers={"Ocp-Apim-Subscription-Key": sub_key},
            )
            if r2.status_code not in (201, 200):
                print(
                    f"apikey creation failed: HTTP {r2.status_code}\n"
                    f"  body: {r2.text[:500]}",
                    file=sys.stderr,
                )
                return 1
            api_key = r2.json().get("apiKey")
    except httpx.HTTPError as exc:
        print(f"Network error talking to the sandbox: {exc}", file=sys.stderr)
        return 1

    if not api_key:
        print("Unexpected: apikey response had no 'apiKey' field.", file=sys.stderr)
        return 1

    # Print instructions only — never write .env (Hard Rule #2).
    print("\n✅ Provisioning succeeded. Add these lines to your .env:\n")
    print(f"MOMO_API_USER={api_user}")
    print(f"MOMO_API_KEY={api_key}")
    print(
        "\nDo NOT commit .env. These credentials are sandbox-only. "
        "Re-run this script to rotate them."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(provision())
