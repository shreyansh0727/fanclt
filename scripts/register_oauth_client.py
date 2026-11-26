"""Create or rotate a voice-assistant OAuth client in Firestore.

The client secret is written to backend/.oauth-clients/<client_id>.json
(gitignored) rather than printed, so it never lands in a terminal log. Paste
those values into the Google Home developer console or SmartThings developer
centre, then delete the file.

Examples (Git Bash):
  python scripts/register_oauth_client.py --provider google --name "Google Home" \
      --redirect-uri https://oauth-redirect.googleusercontent.com/r/<home-project-id>
  python scripts/register_oauth_client.py --provider smartthings --name "SmartThings" --rotate \
      --redirect-uri https://api.smartthings.com/oauth/callback \
      --redirect-uri https://c2c-us.smartthings.com/oauth/callback \
      --redirect-uri https://c2c-eu.smartthings.com/oauth/callback \
      --redirect-uri https://c2c-ap.smartthings.com/oauth/callback

SmartThings redirects through the regional c2c-* hosts, and which one a given
account uses depends on shard assignment, so register all four. A missing host
surfaces as "This link is not valid: the client or redirect URI is not
registered" on the linking screen.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.security import hash_secret  # noqa: E402
from app.services.assistants import CLIENT_PEPPER, new_client_id  # noqa: E402

PROVIDER_PREFIX = {"google": "g_", "smartthings": "st_"}
SECRET_DIR = ROOT / "backend" / ".oauth-clients"


def gcloud_credentials():
    from google.oauth2.credentials import Credentials

    command = ["gcloud", "auth", "print-access-token"]
    if os.name == "nt":
        command = ["cmd", "/c", *command]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    token = result.stdout.strip()
    if not token:
        raise SystemExit("gcloud returned no access token (run: gcloud auth login)")
    return Credentials(token=token, scopes=["https://www.googleapis.com/auth/cloud-platform"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=sorted(PROVIDER_PREFIX), required=True)
    parser.add_argument("--name", required=True, help="shown on the consent page")
    parser.add_argument("--client-id", default=None)
    parser.add_argument("--redirect-uri", action="append", required=True,
                        help="repeatable; must match the platform console exactly")
    parser.add_argument("--rotate", action="store_true",
                        help="replace the secret of an existing client")
    parser.add_argument("--project", default="hrllo-530fe")
    parser.add_argument("--base-url", default="https://fanctl-api-hxnciith6a-el.a.run.app")
    args = parser.parse_args()

    client_id = args.client_id or new_client_id(PROVIDER_PREFIX[args.provider])

    from google.cloud import firestore

    db = firestore.Client(project=args.project, credentials=gcloud_credentials())
    ref = db.collection("oauth_clients").document(client_id)
    existing = ref.get()

    if existing.exists and not args.rotate:
        print(f"client {client_id} already exists; pass --rotate to replace its secret", file=sys.stderr)
        return 1

    import secrets

    secret = secrets.token_urlsafe(32)
    ref.set(
        {
            "id": client_id,
            "provider": args.provider,
            "name": args.name,
            "secret_hash": hash_secret(secret, pepper=CLIENT_PEPPER),
            "redirect_uris": list(args.redirect_uri),
            "created_at": datetime.now(timezone.utc),
        }
    )

    SECRET_DIR.mkdir(parents=True, exist_ok=True)
    secret_file = SECRET_DIR / f"{client_id}.json"
    secret_file.write_text(
        json.dumps(
            {
                "client_id": client_id,
                "client_secret": secret,
                "provider": args.provider,
                "authorization_url": f"{args.base_url}/oauth/authorize",
                "token_url": f"{args.base_url}/oauth/token",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"client_id:   {client_id}")
    print(f"provider:    {args.provider}")
    for uri in args.redirect_uri:
        print(f"redirect:    {uri}")
    print(f"authorize:   {args.base_url}/oauth/authorize")
    print(f"token:       {args.base_url}/oauth/token")
    print(f"secret file: {secret_file}  (gitignored - delete after configuring the console)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
