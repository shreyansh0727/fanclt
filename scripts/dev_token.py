#!/usr/bin/env python
"""Mint a Firebase Auth ID token from the local Auth emulator.

Start the emulator first (from the repository root):

    firebase emulators:start --only auth,firestore

Then capture the token - only the token is written to stdout:

    TOKEN=$(python scripts/dev_token.py --email dev@example.com --password secret123)
    curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/v1/devices

This talks to the emulator REST API only. It is not a way to obtain tokens from a real
Firebase project.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# The Auth emulator ignores the API key, but the REST endpoint requires one.
FAKE_API_KEY = "fake-api-key"


def post(host: str, method: str, payload: dict) -> dict:
    url = f"http://{host}/identitytoolkit.googleapis.com/v1/accounts:{method}?key={FAKE_API_KEY}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def http_error_message(exc: urllib.error.HTTPError) -> str:
    try:
        return json.load(exc)["error"]["message"]
    except Exception:
        return f"HTTP {exc.code}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default="dev@example.com")
    parser.add_argument("--password", default="secret123", help="at least 6 characters")
    parser.add_argument(
        "--host",
        default=os.environ.get("FIREBASE_AUTH_EMULATOR_HOST", ""),
        help="Auth emulator host:port (default: $FIREBASE_AUTH_EMULATOR_HOST)",
    )
    args = parser.parse_args()

    host = args.host.strip().removeprefix("http://").removeprefix("https://").rstrip("/")
    if not host:
        print(
            "Set --host or FIREBASE_AUTH_EMULATOR_HOST, for example 127.0.0.1:9099",
            file=sys.stderr,
        )
        return 2

    payload = {"email": args.email, "password": args.password, "returnSecureToken": True}

    try:
        data = post(host, "signUp", payload)
    except urllib.error.HTTPError as exc:
        message = http_error_message(exc)
        if "EMAIL_EXISTS" not in message:
            print(f"sign-up failed: {message}", file=sys.stderr)
            return 1
        try:
            data = post(host, "signInWithPassword", payload)
        except urllib.error.HTTPError as exc2:
            print(f"sign-in failed: {http_error_message(exc2)}", file=sys.stderr)
            return 1
    except urllib.error.URLError as exc:
        print(f"cannot reach the Auth emulator at {host}: {exc.reason}", file=sys.stderr)
        return 1

    token = data.get("idToken")
    if not token:
        print("the emulator returned no idToken", file=sys.stderr)
        return 1

    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
