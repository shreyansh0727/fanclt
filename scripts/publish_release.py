#!/usr/bin/env python3
"""Publish a FanCtl release for the two update paths.

Uploads the firmware image and/or the release APK to Cloud Storage, makes each
object publicly readable and writes the manifest the backend serves at
/api/v1/updates. Controllers poll that manifest (see firmware/src/ota.cpp) and
the app checks it on launch (app/src/lib/updates.ts).

    .venv/Scripts/python.exe scripts/publish_release.py \
        --firmware firmware/.pio/build/esp32-c6-pico/firmware.bin \
        --app app/android/app/build/outputs/apk/release/app-release.apk \
        --notes "Latched alarm"

Versions default to firmware/src/config.h (FW_VERSION) and app/package.json.
Only the artifacts you pass are touched; the other half of the manifest is
kept as it is.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent

STORAGE_HOST = "https://storage.googleapis.com/{bucket}/{path}"

# The APK is ~77 MB; the library's defaults (one 100 MB request, 60 s per
# request, 120 s total retries) give up on a home uplink. Resumable 4 MB chunks
# with a 30 minute retry budget survive a slow or flapping connection.
CHUNK_SIZE = 4 * 1024 * 1024
UPLOAD_TIMEOUT_S = 300
UPLOAD_RETRY_BUDGET_S = 1800


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def firmware_version() -> str | None:
    header = ROOT / "firmware" / "src" / "config.h"
    if not header.exists():
        return None
    match = re.search(r'#define FW_VERSION "([^"]+)"', header.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def app_version() -> tuple[str | None, int | None]:
    package = ROOT / "app" / "package.json"
    gradle = ROOT / "app" / "android" / "app" / "build.gradle"
    version = None
    code = None
    if package.exists():
        version = json.loads(package.read_text(encoding="utf-8")).get("version")
    if gradle.exists():
        match = re.search(r"versionCode\s+(\d+)", gradle.read_text(encoding="utf-8"))
        if match:
            code = int(match.group(1))
    return version, code


def gcloud_credentials():
    from google.oauth2.credentials import Credentials

    command = ["gcloud", "auth", "print-access-token"]
    if os.name == "nt":
        # gcloud is a .cmd shim on Windows, which needs the shell to start.
        command = ["cmd", "/c", *command]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    token = result.stdout.strip()
    if not token:
        raise SystemExit("gcloud returned no access token (run: gcloud auth login)")
    return Credentials(
        token=token,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )


def upload(client, bucket_name: str, local: Path, object_path: str) -> tuple[str, int]:
    from google.api_core.retry import Retry

    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_path)
    blob.chunk_size = CHUNK_SIZE
    blob.upload_from_filename(
        str(local),
        content_type="application/octet-stream",
        timeout=UPLOAD_TIMEOUT_S,
        retry=Retry(timeout=UPLOAD_RETRY_BUDGET_S, initial=1.0, maximum=60.0),
    )
    # Only the release objects are public; the bucket itself stays private.
    blob.make_public()

    url = STORAGE_HOST.format(bucket=bucket_name, path=quote(object_path, safe="/"))
    return url, local.stat().st_size


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a FanCtl firmware/APK release.")
    parser.add_argument("--firmware", type=Path, help="firmware.bin from PlatformIO")
    parser.add_argument("--app", type=Path, help="release APK")
    parser.add_argument("--firmware-version", help="defaults to FW_VERSION in config.h")
    parser.add_argument("--app-version", help="defaults to app/package.json")
    parser.add_argument("--version-code", type=int, help="defaults to versionCode in build.gradle")
    parser.add_argument("--notes", default="", help="short text shown in the app")
    parser.add_argument("--project", default="hrllo-530fe")
    parser.add_argument("--bucket", default="hrllo-530fe.appspot.com")
    args = parser.parse_args()

    if not args.firmware and not args.app:
        parser.error("pass --firmware and/or --app")

    firmware_ver = args.firmware_version or firmware_version()
    app_ver, app_code = app_version()
    app_ver = args.app_version or app_ver
    app_code = args.version_code or app_code

    if args.firmware and not firmware_ver:
        parser.error("pass --firmware-version (config.h not found)")
    if args.app and not (app_ver and app_code):
        parser.error("pass --app-version and --version-code (package.json/gradle not found)")

    from google.cloud import firestore, storage

    credentials = gcloud_credentials()
    client = storage.Client(project=args.project, credentials=credentials)

    manifest: dict = {}
    for path, label in ((args.firmware, "firmware"), (args.app, "app")):
        if path is None:
            continue
        if not path.exists():
            print(f"missing {label} file: {path}", file=sys.stderr)
            return 1

    if args.firmware:
        object_path = f"releases/firmware/fanctl-{firmware_ver}.bin"
        url, size = upload(client, args.bucket, args.firmware, object_path)
        manifest["firmware"] = {
            "version": firmware_ver,
            "url": url,
            "sha256": sha256_of(args.firmware),
            "size": size,
            "notes": args.notes,
        }
        print(f"firmware {firmware_ver} ({size} bytes) -> {url}")

    if args.app:
        object_path = f"releases/app/fanctl-{app_ver}-{app_code}.apk"
        url, size = upload(client, args.bucket, args.app, object_path)
        manifest["app"] = {
            "version": app_ver,
            "version_code": app_code,
            "url": url,
            "size": size,
            "notes": args.notes,
        }
        print(f"app {app_ver} ({size} bytes) -> {url}")

    manifest["published_at"] = datetime.now(timezone.utc).isoformat()
    db = firestore.Client(project=args.project, credentials=credentials)
    db.collection("releases").document("current").set(manifest, merge=True)
    print(f"manifest updated: {', '.join(k for k in manifest if k in ('firmware', 'app'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
