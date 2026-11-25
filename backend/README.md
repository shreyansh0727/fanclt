# FanCtl backend

FastAPI service that pairs ESP32 controllers, mints per-device Firebase custom tokens, dispatches
cloud commands and runs schedules. Firebase provides Auth (ID + custom tokens), Firestore (via
the Admin SDK) and the Realtime Database the controllers stream from. The app talks to the
database directly for live state; the backend is the only writer for scheduled commands and
device metadata.

See `../docs/PROTOCOL.md` for every endpoint and frame.

## Requirements

- Python 3.11+ (tested on 3.13)
- A Firebase project with Email/Password sign-in enabled and a Realtime Database, or the Firebase
  emulators
- Java only if you run the Firebase emulators

## Install and run

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate       # Git Bash on Windows
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Bind to `0.0.0.0`, not `127.0.0.1`, so the ESP32 and your phone can reach it. In development
`--reload` is fine; do not combine reload with multiple workers.

Health check: `curl http://127.0.0.1:8000/api/v1/health`.

## Configuration

Every variable is prefixed `FANCTL_` and read from the environment or `backend/.env`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `FANCTL_ENV` | `dev` | Reported by `/health` |
| `FANCTL_REPO_BACKEND` | `firestore` | `firestore` or `memory` |
| `FANCTL_PROJECT_ID` | - | Firebase project id (also the RTDB namespace prefix) |
| `FANCTL_CREDENTIALS_FILE` | - | Service account JSON path; falls back to `GOOGLE_APPLICATION_CREDENTIALS` |
| `FANCTL_FIREBASE_API_KEY` | - | Public web API key the controller uses for `signInWithCustomToken` |
| `FANCTL_DATABASE_URL` | - | Realtime Database URL, sent to the controller (e.g. `https://p-default-rtdb.firebaseio.com`) |
| `FANCTL_RTDB_BACKEND` | `auto` | `auto`, `rest` or `memory` |
| `FANCTL_DATABASE_EMULATOR_HOST` | - | RTDB emulator host; also read from `FIREBASE_DATABASE_EMULATOR_HOST` |
| `FANCTL_AUTH_EMULATOR_HOST` | - | Auth emulator host; also read from `FIREBASE_AUTH_EMULATOR_HOST` |
| `FANCTL_DEVICE_ONLINE_S` | `40` | `info.ts` age above which a controller counts as offline (heartbeat is 10 s) |
| `FANCTL_CLAIM_PRESENCE_S` | `120` | How recently a controller must have polled `/device/token` to be claimable |
| `FANCTL_CORS_ORIGINS` | `*` | Comma-separated origins |
| `FANCTL_COMMAND_TIMEOUT_S` | `6` | How long to wait for a device ack |
| `FANCTL_COMMAND_RATE_PER_SEC` | `6` | Per-device token-bucket rate |
| `FANCTL_PAIR_CODE_TTL_S` | `86400` | Pairing window from last registration |
| `FANCTL_LOG_EVENTS` | `true` | Write command events to Firestore |
| `FANCTL_SCHEDULER_ENABLED` | `true` | Start the schedule runner |
| `FANCTL_SCHEDULER_TICK_S` | `20` | Poll interval for due schedules |
| `FANCTL_LOG_LEVEL` | `INFO` | Python log level |

`FANCTL_REPO_BACKEND=memory` keeps devices and schedules in process memory (lost on restart)
and skips Firestore entirely; it also forces the in-memory Realtime Database unless
`FANCTL_RTDB_BACKEND=rest` (or an emulator host) says otherwise. Token verification still needs
Firebase credentials or the emulators; the pytest suite sidesteps this by stubbing the verifier.

## Firebase setup

1. Create a project, enable **Authentication → Sign-in method → Email/Password**.
2. Create a **Realtime Database** and copy its URL into `FANCTL_DATABASE_URL`.
3. Create a web app (Project settings → Your apps) and copy its config into
   `app/app.json` under `expo.extra`, including `firebaseDatabaseUrl`. Copy the web API key into
   `FANCTL_FIREBASE_API_KEY` so the controller can sign in.
4. Create a service account (Project settings → Service accounts → Generate new private key).
   Store the JSON outside version control and point `FANCTL_CREDENTIALS_FILE` at it.
5. Deploy both rule sets from the repository root:

   ```bash
   firebase deploy --only firestore:rules,database
   ```

   Firestore is deny-all (the backend uses the Admin SDK, which bypasses rules). The database
   rules in `../firebase/database.rules.json` let a controller touch only its own node and an
   owner write `commands/*`.

No Firestore indexes are required: the queries are single-field equality plus one range query
on `next_run_at`, and ordering is done in Python.

## Local emulators

From the repository root:

```bash
firebase emulators:start --only auth,firestore,database
```

PowerShell users should quote the list (`--only "auth,firestore,database"`). Then, in the shell
that runs uvicorn:

```bash
export FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099
export FIRESTORE_EMULATOR_HOST=127.0.0.1:8080
export FIREBASE_DATABASE_EMULATOR_HOST=127.0.0.1:9000
export FANCTL_PROJECT_ID=demo-fanctl
export FANCTL_REPO_BACKEND=firestore
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The token endpoint advertises the emulator hosts it was configured with, so a controller on the
LAN follows them without a rebuild. The database namespace is `<project id>-default-rtdb` unless
`FANCTL_RTDB_NAMESPACE` overrides it.

Mint a token for API calls:

```bash
TOKEN=$(python ../scripts/dev_token.py --email dev@example.com --password secret123)
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/v1/devices
```

The Auth emulator accepts any API key; the script handles creating the user on first use.

## Tests

```bash
python -m pytest
```

The suite uses the memory repository and `MemoryRtdb`, an in-process fake Firebase verifier, and
FastAPI `TestClient` over plain HTTP. It covers registration and the custom-token exchange,
claiming without a device socket, command acks and timeouts (a fake controller polls the RTDB
node), ownership checks, schedule validation and timezone/DST occurrence maths.

## How it fits together

- `api/device_api.py` - controller registration + `/device/token`: validates the hardware id and
  device secret/key, mints the custom token, and advertises the RTDB/Auth endpoints.
- `api/devices.py` - owner-facing device list/patch/delete, claim, identify and the command
  endpoint. Delete replaces the node with `{"unclaim": true}`.
- `rtdb.py` - `Rtdb` protocol with `HttpRtdb` (REST + service-account access token) and
  `MemoryRtdb` for tests.
- `services/commands.py` - validation, rate limit, offline short-circuit, write to
  `devices/<id>/commands`, poll for the ack, mirror state to Firestore, prune, event log.
- `services/live.py` - presence (`last_seen`) and online (`info.ts` freshness) helpers.
- `services/runner.py` - schedule polling every tick; advances the schedule cursor before
  dispatching so a slow device cannot double-fire it.
- `repo/` - `Repo` protocol with `MemoryRepo` and `FirestoreRepo` implementations.

Data lives in three Firestore collections (`devices`, `schedules`, `events`) plus the
Realtime Database tree under `devices/<id>` (`info`, `state`, `acks`, `commands`, `owner`,
`channels`).

## Deploy on Cloud Run

The container is `backend/Dockerfile`; Cloud Run runs `uvicorn` on `$PORT`. No inbound port at
home and no WebSocket: the controller streams Firebase directly, so every request here is short
(REST + an ack poll of at most `FANCTL_COMMAND_TIMEOUT_S`).

### 1. Firebase project

In the Firebase console (same project for Firestore, Auth and the Realtime Database):

1. Create **Firestore** (Native mode) and a **Realtime Database**. Note the database URL:
   `https://<project>-default-rtdb.firebaseio.com` (us-central1) or
   `https://<project>-default-rtdb.<region>.firebasedatabase.app`.
2. Enable **Authentication → Sign-in method → Email/Password**. Add your web app under
   Project settings and copy its config for the mobile app, and the **web API key** for
   `FANCTL_FIREBASE_API_KEY`.
3. Deploy both rule sets from the repository root:

   ```bash
   firebase deploy --only firestore:rules,database --project <project-id>
   ```

Keep the web API key usable for **Identity Toolkit**: if you add API restrictions, include
`Identity Toolkit API`, or controllers will fail `signInWithCustomToken`. Do not add HTTP
referrer restrictions - the ESP32 sends no `Referer`.

### 2. Runtime service account

Without a JSON key: Cloud Run's metadata server provides Application Default Credentials. The
custom-token signer uses IAM, so the account needs `iam.serviceAccountTokenCreator` **on
itself** in addition to data access.

```bash
PROJECT=<project-id>
SA=fanctl-run@$PROJECT.iam.gserviceaccount.com

gcloud iam service-accounts create fanctl-run \
  --project $PROJECT --display-name "FanCtl API"

gcloud projects add-iam-policy-binding $PROJECT \
  --member serviceAccount:$SA --role roles/datastore.user
gcloud projects add-iam-policy-binding $PROJECT \
  --member serviceAccount:$SA --role roles/firebasedatabase.admin
gcloud iam service-accounts add-iam-policy-binding $SA \
  --member serviceAccount:$SA --role roles/iam.serviceAccountTokenCreator
```

PowerShell does not use `NAME=value`: assign with `$SA = "fanctl-run@$PROJECT.iam.gserviceaccount.com"`
and quote the member strings (`--member "serviceAccount:$SA"`). Line continuation is a backtick,
not a backslash. `iam service-accounts create` failing with `ALREADY_EXISTS` is harmless.

Leave `FANCTL_CREDENTIALS_FILE` unset in production - the code falls back to ADC, and
`FANCTL_FIREBASE_API_KEY` is public data, not a secret.

### 3. Deploy

```bash
gcloud run deploy fanctl-api \
  --source backend \
  --project $PROJECT \
  --region asia-south1 \
  --service-account $SA \
  --allow-unauthenticated \
  --port 8080 \
  --cpu 1 --memory 512Mi \
  --min-instances 1 --max-instances 1 \
  --no-cpu-throttling \
  --set-env-vars FANCTL_ENV=prod,FANCTL_PROJECT_ID=$PROJECT,FANCTL_DATABASE_URL=https://$PROJECT-default-rtdb.firebaseio.com,FANCTL_FIREBASE_API_KEY=<web-api-key>
```

If the database is outside us-central1, pass its regional URL
(`https://<project>-default-rtdb.<region>.firebasedatabase.app`) instead.

Why these flags:

- `--max-instances 1` keeps the in-process schedule runner single. With more replicas each
  schedule fires once per replica.
- `--min-instances 1 --no-cpu-throttling` keep the runner alive between requests. With CPU
  throttled, asyncio timers stall while the service is idle and schedules are skipped. This is
  the main cost of the deployment - one always-allocated vCPU, on the order of tens of dollars
  a month. Drop both only if you move the runner to Cloud Scheduler + an authenticated trigger
  endpoint.
- `--allow-unauthenticated` is required for device pairing and for the app's bearer-token
  calls. Authorisation is the Firebase bearer token, not Cloud Run IAM.
- The command limiter and claim-presence checks are in-process, so they stay coherent with one
  instance.

Re-deploy after code changes with the same command; environment variables survive. Rotate the
web API key with `gcloud run services update fanctl-api --region asia-south1
--update-env-vars FANCTL_FIREBASE_API_KEY=<new-key>`.

### 4. Verify

```bash
URL=$(gcloud run services describe fanctl-api --region asia-south1 --format='value(status.url)')
curl "$URL/api/v1/health"
```

Then point the clients at it:

- **Firmware**: build with `-DCLOUD_HOST=\"<host from $URL>\" -DCLOUD_PORT=443 -DCLOUD_USE_TLS=1`.
  Leave `RTDB_HOST`/`FIREBASE_API_KEY` empty - the token response advertises the production
  database and API key. A board paired against the local backend still holds that emulator
  device id/key in NVS; production answers `bad_key` and the link shows `ERR` until you hold
  BOOT for 8 s (factory reset) and pair again against the deployed backend.
- **App**: `EXPO_PUBLIC_API_BASE=https://<host from $URL>`, the real Firebase config,
  `EXPO_PUBLIC_FIREBASE_DATABASE_URL`, and blank the two emulator variables. Users created in
  the Auth emulator do not exist in the real project - sign up again.

## Production notes

- **One worker.** Device state no longer lives in the process, so commands work across workers.
  The schedule runner, however, is in-process: with `uvicorn --workers N` or several Cloud Run
  instances each schedule fires once per process.
- Cloud Run terminates TLS. The controller talks to Firebase over HTTPS directly; set
  `CLOUD_CA_CERT` in the firmware if you want certificate pinning instead of
  `setInsecure()`.
- Restrict `FANCTL_CORS_ORIGINS` to the web origins you actually use. The mobile app does not
  need CORS.
- `/api/v1/device/register` and `/api/v1/device/token` are unauthenticated by design (a
  factory-reset board has no credentials). Each register upserts one Firestore document, so
  before exposing the service widely, put a quota or per-IP limiter in front of those two
  paths (Cloud Armor or an in-app limiter); `--max-instances 1` keeps an in-process limiter
  consistent.
- Back up Firestore or at least the service account project; losing the `devices` collection
  means re-pairing every controller.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `The default Firebase app does not exist` | No credentials and no emulator env vars. Set `FANCTL_CREDENTIALS_FILE` or `GOOGLE_APPLICATION_CREDENTIALS`. |
| `FANCTL_DATABASE_URL is required` at startup | No database URL and no emulator host. Set one, or `FANCTL_RTDB_BACKEND=memory`. |
| 401 `invalid_token` from every call | Token from a different project, expired, or Auth sign-in method disabled. |
| Claim returns 409 `device_offline` | The controller has not polled `/device/token` in `FANCTL_CLAIM_PRESENCE_S` (120 s). Check `CLOUD_HOST` and the serial log. |
| RTDB reads/writes return 401/403 in the Cloud Run log | The runtime service account is missing `roles/firebasedatabase.admin` (or Firestore data needs `roles/datastore.user`). |
| Token endpoint fails with a signing error | The runtime service account cannot sign: grant `roles/iam.serviceAccountTokenCreator` to itself (IAM `signBlob`). |
| Device sign-in fails right after `/device/token` | The web API key rejects Identity Toolkit, or `FANCTL_FIREBASE_API_KEY` is unset. |
| Commands return `acked: false, error: "no_ack"` | The controller is streaming but did not write `acks/<id>` within the timeout; look for a stuck stream or firmware crash. |
| Commands return `online: false` | `info.ts` is older than `FANCTL_DEVICE_ONLINE_S`; the stream is stale. |
| Schedules never fire | `FANCTL_SCHEDULER_ENABLED=false`, or `next_run_at` is in the past because the timezone was changed without an update. |
| `ZoneInfoNotFoundError` on Windows | `pip install tzdata` (already in `requirements.txt`). |
| App shows `No LAN` | `local_ip` is missing until the controller writes its first `info`; it also needs a private IPv4 address reachable by the phone. |
