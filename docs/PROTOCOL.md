# FanCtl protocol reference

Four interfaces exist:

| Interface | Endpoint | Used by |
| --- | --- | --- |
| Device pairing REST | `http(s)://<backend>/api/v1/device/...` | ESP32 → backend |
| Realtime Database | `https://<db>.firebaseio.com/devices/<id>` | ESP32 (REST stream + writes), app (SDK) |
| LAN HTTP | `http://<device-ip>/api/...` | App and browser → ESP32 |
| REST | `https://<backend>/api/v1/...` | App → backend |

Conventions:

- JSON everywhere: HTTP bodies, server-sent events, database nodes.
- Channel indexes are 0-based (`0..channel_count-1`); "CH 1" on the tile is index `0`.
- State is a boolean array whose length equals `channel_count`.
- REST timestamps are RFC 3339 UTC strings (`2026-11-24T09:30:00Z`). The database
  heartbeat uses epoch milliseconds (`ts`), because the controller writes it.
- The device command frame calls the channel `ch`; the REST API calls it `channel`.

## Pairing code and device secret

The firmware generates 8 characters from the Crockford alphabet
`0123456789ABCDEFGHJKMNPQRSTVWXYZ` (no I, L, O, U) and prints them as `XXXX-XXXX`.

The backend normalises a submitted code by upper-casing and stripping everything that is not
`A-Z0-9`, then stores `sha256("fanctl-pair-v1:" + normalised)`. The plain code never reaches
Firestore. A code stays valid for `FANCTL_PAIR_CODE_TTL_S` (default 86400 s) from the latest
registration.

The controller also generates a 32-character random **device secret** on first boot and keeps
it in NVS. Only its hash (`sha256("fanctl-dev-secret-v1:" + secret)`) is stored, and the
secret is what proves "this is the same physical board" when it asks for a Firebase token.

---

## 1. Device pairing REST

Both endpoints are unauthenticated by design: they are how a factory-reset controller obtains
its identity and a Firebase token. Everything after that goes straight to the database.

### 1.1 `POST /api/v1/device/register`

```json
{
  "hw": "A1B2C3D4E5F6",
  "code": "ABCD-1234",
  "secret": "<32 hex chars>",
  "fw": "1.1.0",
  "model": "fanctl-8",
  "channels": 8
}
```

Creates the device document if it is new, then stores the pair-code hash, the secret hash, the
firmware version and `last_seen`. `channels` is clamped to `1..32`; changing it resets the
stored state to all-off.

An unclaimed controller repeats this every ~15 s. That keeps the pairing window and its
`last_seen` presence fresh, which is what makes a claim possible - there is no socket to hold.

| Status | Code | Meaning |
| --- | --- | --- |
| 200 | - | `{"status": "waiting"}` |
| 400 | `bad_hardware_id` | `hw` is not 12 hex characters |
| 400 | `bad_register` | Empty pairing code |
| 409 | `already_claimed` | This hardware id already has an owner; the board needs a factory reset |

### 1.2 `POST /api/v1/device/token`

A claimed controller sends its key:

```json
{"device_id": "dev_1f2e...", "device_key": "dk_...", "hw": "A1B2C3D4E5F6"}
```

An unclaimed one sends hw + secret (it is polled while pairing; `409 not_claimed` until an
owner claims it):

```json
{"hw": "A1B2C3D4E5F6", "secret": "<32 hex chars>"}
```

Success returns everything needed to reach the database:

```json
{
  "device_id": "dev_1f2e...",
  "device_key": "dk_...",
  "custom_token": "<firebase custom token>",
  "rtdb": {"host": "192.168.31.104", "port": 9000, "tls": false, "ns": "hrllo-530fe-default-rtdb"},
  "auth": {"host": "192.168.31.104", "port": 9099, "tls": false, "api_key": "fake-api-key"}
}
```

- `custom_token` carries the claim `dev=<device_id>`; the controller exchanges it at
  `POST <auth>/v1/accounts:signInWithCustomToken?key=<api_key>` and uses the returned ID token
  (~1 h) on every database request. Repeating this endpoint at ~55 min (5 min before expiry)
  refreshes it.
- `rtdb` / `auth` mirror the backend's own settings (emulator host or production URL), so the
  firmware never has to be rebuilt when the database moves.
- The endpoint also creates the RTDB owner node if it is missing, which migrates controllers
  paired while the backend still used sockets.

| Status | Code | Meaning |
| --- | --- | --- |
| 200 | - | Token below |
| 400 | `bad_request` | Neither auth pair supplied |
| 401 | `bad_key` | Device id/key mismatch |
| 401 | `bad_secret` | Secret mismatch; register again |
| 409 | `not_claimed` | Waiting for an owner |

---

## 2. Realtime Database

Everything live lives under `devices/<device_id>`:

```json
{
  "owner": "firebase-uid",
  "name": "Living room",
  "room": "",
  "channel_count": 8,
  "channels": [{"index": 0, "name": "Fan", "type": "fan"}],
  "info": {"ip": "192.168.1.42", "port": 80, "fw": "1.1.0", "rssi": -61,
           "uptime_s": 138, "heap": 214512, "alarm": false, "ts": 1790000000000},
  "state": [false, true, false, false, false, false, false, false],
  "commands": {"9f1c...": {"action": "set", "ch": 2, "on": true, "ts": 1790000000123}},
  "acks": {"9f1c...": {"ok": true, "state": [...], "ts": 1790000000190}}
}
```

| Node | Written by | Semantics |
| --- | --- | --- |
| `owner`, `name`, `room`, `channels`, `channel_count` | Backend (service account) | Mirrors the Firestore document so the app can read metadata live |
| `info` | Device, PATCH | Heartbeat, rewritten every 10 s. `online` = `ts` within 40 s of now (`FANCTL_DEVICE_ONLINE_S`) |
| `state` | Device, PUT | Authoritative relay state, rewritten 400 ms after a change |
| `commands/<id>` | Backend | One pending command; deleted once acked |
| `acks/<id>` | Device, PUT | Result of that command; deleted by the backend |
| `unclaim` | Backend | Replaces the whole node; the controller wipes pairing and restarts |

### 2.1 Device transport

The controller keeps one streaming HTTP request open:

```
GET /devices/<device_id>.json?auth=<idToken>[&ns=<namespace>] HTTP/1.1
Accept: text/event-stream
```

The response is chunked and uses the standard streaming events:

| Event | Payload | Meaning |
| --- | --- | --- |
| `put` | `{"path": "/", "data": {...}}` | Initial snapshot (sent once, then on node replacement) |
| `put` | `{"path": "/commands/<id>", "data": {...}}` | A command arrived / was deleted (`data: null`) |
| `put` | `{"path": "/unclaim", "data": true}` | Controller removed |
| `patch` | `{"path": "/", "data": {...}}` | Partial update (metadata edits) |
| `keep-alive` | - | Sent roughly every 30 s |

Rules the firmware follows:

- Commands **inside the first snapshot are stale** and are ignored; only commands that arrive
  afterwards are applied. This prevents replaying a command the backend already gave up on.
- The last 8 command ids are remembered. A duplicate is acked again **without** being applied,
  which keeps retries safe for `toggle`.
- `unclaim` (root or `/unclaim` node) clears the device id/key and restarts the board.
- If the stream is quiet for 75 s it is reopened. Token refresh (`auth_revoked`, HTTP 401/403,
  or the 55 min timer) re-runs sign-in before reconnecting.
- Writes use the plain REST form of the same path:
  `PUT /devices/<id>/state.json?auth=...`, `PATCH /devices/<id>/info.json?auth=...`,
  `PUT /devices/<id>/acks/<cmd>.json?auth=...`.

Command and ack:

```json
{"action": "set", "ch": 2, "on": true, "ts": 1790000000123}
{"ok": true, "state": [false, true, true, false, false, false, false, false], "ts": 1790000000190}
{"ok": false, "error": "bad_channel", "state": [...], "ts": null}
```

`action` is `set`, `all`, `toggle`, `identify`, one of the pet reactions `pet`, `feed`,
`play`, `sleep`, `wake`, one of the chimes `alarm`, `reminder`, or `alarm_stop`. The pet and
chime actions carry no `ch`; they animate the OLED and queue the matching voice clip (`sleep`
latches until `wake`). `alarm` starts the siren under a startled-cat scene and keeps ringing -
the firmware re-queues the clip whenever the queue drains - until `alarm_stop` arrives; it
never times out on its own. `reminder` plays a soft ding plus a meow for 4.5 s. None of these
touch a relay. While the alarm is latched the device mirrors the flag as `info.alarm` so the
app can show its Stop button, and it clears the flag on `alarm_stop` or on the next boot.
`identify` takes `seconds`, default 5, and makes the speaker say "here i am". `identify` is
fire-and-forget - the endpoint returns
202 - so an identify command/ack pair older than 15 s is pruned the next time identify is
pressed.

Channel renames are mirrored to `channels` on the node; the stream delivers the new array and
the controller stores the names for its panel (`channel_meta` on the LAN `/api/state`).

### 2.2 App transport

The app uses the Firebase JS SDK: `onValue(ref(db, 'devices/' + id))` per controller plus
`onValue(ref(db, '.info/connected'))` for the link badge. It derives `online` from `info.ts`
with the same 40 s window as the backend, and treats `{unclaim: true}` as "removed". A command
response also carries the backend's `online` verdict, which the app applies immediately. The device **list**
still comes from the REST API; the database only keeps the live fields fresh.

### 2.3 Security rules

`firebase/database.rules.json` (deploy with `firebase deploy --only database`):

```json
{
  "rules": {
    "devices": {
      "$dev": {
        ".read": "auth != null && (auth.token.dev == $dev || data.child('owner').val() == auth.uid)",
        "info":  { ".write": "auth != null && auth.token.dev == $dev" },
        "state": { ".write": "auth != null && auth.token.dev == $dev" },
        "online":{ ".write": "auth != null && auth.token.dev == $dev" },
        "acks": { "$cmd": { ".write": "auth != null && auth.token.dev == $dev" } },
        "commands": {
          "$cmd": { ".write": "auth != null && data.parent().parent().child('owner').val() == auth.uid" }
        }
      }
    }
  }
}
```

- A controller can only read and write its own subtree (its custom token carries `dev`).
- The owner can read the node and write commands.
- The backend uses a service account, which bypasses rules, for claims, metadata and cleanup.

---

## 3. LAN HTTP API (ESP32, port 80)

Base: `http://<device-ip>` or `http://fanctl-XXXX.local/` (mDNS, last 4 of the hardware id).

### Authentication

A claimed device requires `X-Local-Key: <device_key>` on `/api/state`, `/api/command` and
`/api/wifi`. `?key=<device_key>` is accepted as a query fallback. An unclaimed device accepts
both without a key. The owner obtains `device_key` as `lan_key` in the REST device object.

Responses carry `Access-Control-Allow-Origin: *`,
`Access-Control-Allow-Headers: Content-Type, X-Local-Key`, and `Cache-Control: no-store`.

### Endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/` | no | Control page (or setup portal page while the portal is active) |
| GET | `/api/health` | no | Liveness |
| GET | `/api/state` | yes | Full state |
| POST | `/api/command` | yes | Switch relays |
| POST | `/api/wifi` | yes | Store new WiFi credentials and reboot |
| POST | `/save` | portal only | Save WiFi + optional server override, then reboot |

`GET /api/health`:

```json
{"ok": true, "fw": "1.1.0", "uptime_s": 138}
```

`GET /api/state`:

```json
{
  "device_id": "dev_1f2e...",
  "hardware_id": "A1B2C3D4E5F6",
  "firmware": "1.1.0",
  "model": "fanctl-8",
  "claimed": true,
  "channels": 8,
  "state": [false, true, false, false, false, false, false, false],
  "wifi_rssi": -61,
  "wifi_ssid": "HomeNet",
  "ip": "192.168.1.42"
}
```

`POST /api/command` (this endpoint uses `ch`, unlike the REST API):

```json
{"action": "set", "ch": 2, "on": true}
{"action": "all", "on": false}
{"action": "toggle", "ch": 0}
{"action": "pet"}
{"action": "feed"}
{"action": "play"}
{"action": "sleep"}
{"action": "wake"}
```

It returns the same shape as `/api/state`. Errors:

| HTTP | `error` | Cause |
| --- | --- | --- |
| 400 | `empty_body` | No request body |
| 400 | `bad_json` | Unparseable JSON |
| 400 | `bad_action` | `action` is not `set`/`all`/`toggle`/`pet`/`feed`/`play`/`sleep`/`wake`/`alarm`/`alarm_stop`/`reminder` |
| 400 | `bad_channel` | Missing or out-of-range `ch` |
| 401 | `unauthorized` | Wrong/missing local key on a claimed device |
| 409 | `not_in_setup_mode` | `/save` called outside the setup portal |

`POST /api/wifi` stores new credentials and reboots the board so it joins the new network. The
caller must already reach the device over its current LAN address, so this only works while the
app/phone is on the same network as the controller:

```json
{"ssid": "HomeNet", "pass": "hunter2"}
```

`pass` may be empty for an open network. The reply is sent before the restart:

```json
{"ok": true, "rebooting": true, "ssid": "HomeNet"}
```

The board restarts about 400 ms later. If the new network does not come up it falls back to the
`FanCtl-Setup-XXXX` portal after roughly 90 s. Errors:

| HTTP | `error` | Cause |
| --- | --- | --- |
| 400 | `empty_body` | No request body |
| 400 | `bad_json` | Unparseable JSON |
| 400 | `missing_ssid` | `ssid` is absent or blank after trimming |
| 401 | `unauthorized` | Wrong/missing local key on a claimed device |
| 409 | `portal_active` | Called while the setup portal is running; use `/save` instead |

`POST /save` is `application/x-www-form-urlencoded` with `ssid`, `pass`, optional `host`,
`port`, and `tls=1`. It is only available while the setup portal is running. The board reboots
about 600 ms after replying.

The control page (`/`) polls `/api/state` every 2 s and sends the same LAN command frames. On a
claimed device the page embeds the device key, so anyone on the LAN who can load the page can
control the device.

---

## 4. REST API

Base: `/api/v1`. Every endpoint except `/health` requires:

```
Authorization: Bearer <Firebase ID token>
```

Errors use one envelope:

```json
{"error": {"code": "channel_out_of_range", "message": "channel must be 0..7"}}
```

Validation failures use code `validation_error` with a `details` array of pydantic errors.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | `{ok, version, env, repo}`; no auth |
| GET | `/devices` | List owned devices (database overlay applied) |
| POST | `/devices/claim` | Pair with `{code, name?}` |
| GET | `/devices/{id}` | One device |
| PATCH | `/devices/{id}` | `name?`, `room?`, `channels?` (mirrored to the database) |
| DELETE | `/devices/{id}` | Write `unclaim`, delete the document and its schedules |
| POST | `/devices/{id}/commands` | Send a command through the database |
| GET | `/devices/{id}/events?limit=50` | Recent command events |
| POST | `/devices/{id}/identify` | Ask the box to say "here i am", `202 {"sent":true}` |
| GET | `/schedules?device_id=` | List schedules |
| POST | `/schedules` | Create |
| PATCH | `/schedules/{id}` | Update |
| DELETE | `/schedules/{id}` | Delete |

### Device object

```json
{
  "id": "dev_1f2e...",
  "hardware_id": "A1B2C3D4E5F6",
  "name": "Living room",
  "room": "",
  "online": true,
  "last_seen": "2026-11-24T09:30:00Z",
  "firmware": "1.1.0",
  "local_ip": "192.168.1.42",
  "local_port": 80,
  "lan_key": "dk_...",
  "channel_count": 8,
  "channels": [{"index": 0, "name": "Fan", "type": "fan"}],
  "state": [false, true, false, false, false, false, false, false],
  "telemetry": {"rssi": -61, "uptime_s": 138, "free_heap": 214512},
  "created_at": "2026-11-20T10:00:00Z"
}
```

`lan_key` is the device key and must be treated as a secret. Channel `type` is one of
`fan`, `light`, `socket`, `other`. A PATCH of `channels` must contain exactly
`channel_count` entries covering indexes `0..count-1`.

### Claim

```json
POST /devices/claim
{"code": "ABCD-1234", "name": "Living room"}
```

| Status | Code | Meaning |
| --- | --- | --- |
| 404 | `invalid_code` | Unknown code |
| 409 | `already_claimed` | Device belongs to an account |
| 410 | `code_expired` | Pairing window closed; reboot the device |
| 409 | `device_offline` | The controller has not registered in the last `FANCTL_CLAIM_PRESENCE_S` (120 s) |

### Commands

```json
POST /devices/{id}/commands
{"action": "set", "channel": 2, "on": true}
{"action": "all", "on": false}
{"action": "toggle", "channel": 0}
{"action": "pet"}
{"action": "play"}
{"action": "alarm"}
{"action": "alarm_stop"}
{"action": "reminder"}
{"action": "set", "channel": 2, "on": true, "command_id": "my-own-id"}
```

`command_id` is optional; the backend generates a UUID hex string when omitted.

The backend writes `devices/<id>/commands/<id>`, polls the matching `acks/<id>` every 100 ms
for `FANCTL_COMMAND_TIMEOUT_S` (default 6 s), mirrors the reported state into Firestore, then
deletes the command and ack nodes.

The endpoint returns **HTTP 200 even for failures**. Inspect the body:

```json
{"command_id": "9f1c...", "acked": true, "online": true, "state": [...], "error": null}
{"command_id": "9f1c...", "acked": false, "online": false, "state": [...], "error": "device_offline"}
```

- `acked: false, online: false` - the heartbeat is stale; nothing was sent.
- `acked: false, online: true` - no ack within the timeout; `error` is `no_ack`, `bad_channel`
  or another device-side error.

Other status codes: `422 invalid_command` (missing `on`/`channel`), `422
channel_out_of_range`, `429 rate_limited` (per-device, default 6/s).

### Schedules

```json
{
  "id": "sch_ab12...",
  "device_id": "dev_1f2e...",
  "name": "Morning fan",
  "time": "07:30",
  "days": [0, 1, 2, 3, 4],
  "timezone": "Asia/Kolkata",
  "action": {"type": "set_channel", "on": true, "channel": 0},
  "enabled": true,
  "next_run_at": "2026-11-25T02:00:00Z",
  "last_run_at": null,
  "created_at": "2026-11-24T09:00:00Z"
}
```

- `time` is 24-hour `HH:MM`; `days` is `0` = Monday … `6` = Sunday, at least one, no duplicates.
- `timezone` is an IANA name and is validated with `zoneinfo`.
- `action.type` is `set_all` (no `channel`), `set_channel` (requires `channel`), or a chime:
  `alarm` / `reminder`. Chime actions carry neither `on` nor `channel`; they dispatch the same
  `alarm` / `reminder` command as the Commands endpoint, so the device plays the sound and the
  full-screen scene without changing a relay. An `alarm` schedule latches the alarm, so send
  `alarm_stop` (from the app's alarm banner) when you want it to go quiet.
- The backend computes `next_run_at` on create/update and after every run.
- Disabling a schedule clears `next_run_at`; enabling recomputes it.

The runner polls every `FANCTL_SCHEDULER_TICK_S` seconds, advances the cursor before
dispatching, and records an unacked run if the device is offline. Schedule-triggered commands
are written to the event log with `source: "schedule"`.

## 5. Releases and updates

Controllers and the app poll one public manifest. It is backed by the Firestore document
`releases/current`, written by `scripts/publish_release.py`; nothing secret is returned.

### `GET /api/v1/updates`

No authentication (the firmware has no user token yet and the app checks before sign-in is
useful). Responds `Cache-Control: no-store`.

```json
{
  "firmware": {
    "version": "1.1.1",
    "sha256": "…64 hex…",
    "size": 1347200,
    "url": "https://storage.googleapis.com/<bucket>/releases/firmware/fanctl-1.1.1.bin",
    "notes": ""
  },
  "app": {
    "version": "1.1.0",
    "version_code": 2,
    "size": 77658467,
    "notes": "",
    "url": "https://storage.googleapis.com/<bucket>/releases/app/fanctl-1.1.0-2.apk"
  }
}
```

The URLs are plain Cloud Storage objects carrying a public-read ACL (the bucket itself is not
public), so both clients can fetch them without credentials.

Either member is `null` (or absent) until something has been published — the endpoint then
returns `{"firmware": null, "app": null}`.

**Firmware behaviour (`firmware/src/ota.cpp`).** `version` is compared numerically against
`FW_VERSION`; a newer value is downloaded over HTTPS from `url`, verified against `sha256`
when present, and flashed into the inactive slot before `ESP.restart()`. Checks run 45 s after
boot and every 6 h; an available update is deferred every 10 min while any relay is on, the
setup portal is open, or the alarm is latched. No ack or telemetry is written for an update;
`info.fw` changes on the next boot.

**App behaviour (`app/src/lib/updates.ts`).** The app compares `version` with its bundled
`expo.version`, shows a card and downloads the APK on request. Android requires the user to
confirm the install; the APK must be signed with the same key as the installed app.
