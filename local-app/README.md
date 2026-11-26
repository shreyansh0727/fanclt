# FanCtl local fulfillment app

Runs inside Google Home's local home runtime (hub / speaker / phone) and talks to
the controller over the LAN instead of the cloud, so "Hey Google, turn on the
fan" lands in ~200 ms and keeps working when Cloud Run or the uplink is slow.

The cloud path is untouched: if no hub has the app loaded, Google falls back to
`POST /google/fulfillment` on the backend as before.

## How it fits together

```
Google Assistant → hub (local runtime)
                     ↓ IDENTIFY        mDNS `_fanctl._tcp` scan, TXT id=<deviceId>
                     ↓ REACHABLE_DEVICES  <deviceId>.ch0 … .ch7
                     ↓ QUERY/EXECUTE    GET /api/state, POST /api/command
                   board (192.168.x.x:80)   header X-Local-Key: lk_…
```

| Intent | What the app does |
| --- | --- |
| `IDENTIFY` | Reads `mdnsScanData.txt.id`, returns the box as a proxy: `{id, isProxy: true, isLocalOnly: true}` |
| `REACHABLE_DEVICES` | Returns `verificationId: "<id>.ch<n>"` for channels 0–7 |
| `QUERY` | One `GET /api/state` per board, answers each channel as `{status, on, online}` |
| `EXECUTE` | `POST /api/command {"action":"set","ch":n,"on":bool}` per channel |

The channel ids are the same `<device_id>.ch<n>` strings the cloud SYNC ships in
`otherDeviceIds`, which is what lets Google use the local app for those devices.

## Credentials

The app sends `X-Local-Key` with a dedicated assistant key. It is **not** the
owner's `device_key` or `lan_key`:

- the backend mints it (`lk_…`) on the first SYNC after local fulfillment is
  enabled and stores it on the device doc as `local_key`;
- it is pushed to the board as a `set_assistant_key` command over the
  authenticated RTDB channel, and only marked provisioned (`local_key_pushed`)
  once the board acks;
- on the board it only unlocks `/api/state` and `/api/command`. `/api/wifi`
  (Wi-Fi + server changes) stays owner-key only.

To revoke, clear `local_key` and `local_key_pushed` on the device document and
run a SYNC (Google Home → device → "sync"/re-link). During the gap the app's
requests return 401, so local control fails closed and the cloud path takes over.

## Build

```bash
cd local-app
npm install
npm run typecheck
npm run build      # dist/index.js (bundled IIFE) + dist/index.html
```

`@google/local-home-sdk` is types-only; the runtime provides the global
`smarthome` object. The bundle therefore has no imports or exports and is loaded
as a plain `<script>` — that is what `build.mjs` produces.

## Host

Google loads the app from an HTTPS URL. Serve `dist/` anywhere static. Firebase
Hosting for the FanCtl project works and is what the console URLs below assume;
it is **currently not deployed** (hosting was disabled on 2026-11-26), so add
this block to the root `firebase.json` before deploying again:

```jsonc
"hosting": { "public": "local-app/dist", "ignore": ["firebase.json", "**/.*", "**/node_modules/**"] }
```

```bash
firebase deploy --only hosting --project hrllo-530fe
```

then `https://hrllo-530fe.web.app/local-home/index.html` (and `index.js`) serve
the app.

## Console registration (Google Home Developer Console)

Project → Cloud-to-cloud → integration → **Edit** → *Setup and configuration* →
**Local fulfillment** (toggle on):

1. **Testing URLs** — two fields, because the platform has two runtimes:
   - *Testing URL for Chrome* (speakers, Nest Minis, Hub displays): the HTTPS URL
     of the **HTML** page → `https://hrllo-530fe.web.app/local-home/index.html`
   - *Testing URL for Node* (Nest Wifi routers): the HTTPS URL of the
     **JavaScript** itself → `https://hrllo-530fe.web.app/local-home/index.js`

   Both point at the same bundle; it touches only the `smarthome` global, so it
   runs unchanged in either runtime. At submission the console asks for the same
   pair as production URLs.
2. **Device discovery** → **+ Add scan configuration** → **mDNS**:
   - **Service Name**: `_fanctl._tcp.local`
   - **Name** (regex on `instance.service.domain`):
     `fanctl-[0-9A-F]{4}\._fanctl\._tcp\.local`
     (the firmware advertises `<MDNS_PREFIX>-<last 4 hex of the MAC>`,
     e.g. `fanctl-EEFF`, plus TXT `id=<deviceId>` and `model=fanctl-8`)
3. Save, reboot the Nest device, and allow up to 30 minutes for the console
   change to propagate. `Hey Google, force local` disables the cloud fallback
   while testing; `Hey Google, force default` restores it.

The same mDNS service is only advertised once the controller is claimed, so the
scan never finds an unpaired board.

## Enable on the backend

```bash
# FANCTL_ prefix from Settings; deploy WITHOUT --set-env-vars
gcloud run services update fanctl-api --region asia-south1 \
  --update-env-vars FANCTL_LOCAL_FULFILLMENT=1
```

Order matters:

1. firmware ≥ 1.1.4 on the board (mDNS `_fanctl` service + assistant key);
2. backend flag on;
3. re-link the Google Home account (unlink → link, or "sync my devices").

Step 3 is what makes Google call SYNC again; only then do the entries carry
`otherDeviceIds`/`customData` and the board get its key. The key push runs in
the background with a short ack wait and is retried on the next SYNC, so an
offline board is fine — it just uses the cloud until it has the key.

## Checking a board by hand

```bash
# what the hub sees
dns-sd -B _fanctl._tcp            # macOS; on Windows: arp -a / mDNS browser

# same calls the app makes (key from the device doc's local_key)
curl -s http://192.168.1.42/api/state -H "X-Local-Key: lk_..."
curl -s -X POST http://192.168.1.42/api/command \
  -H "X-Local-Key: lk_..." -H "Content-Type: application/json" \
  -d '{"action":"set","ch":0,"on":true}'
```

## Limits

- Eight channels are assumed (`CHANNELS`); a fanctl-4 board would need a SYNC-side
  count instead of the constant.
- Only `action.devices.commands.OnOff` is handled; anything else answers
  `functionNotSupported`, so Google falls back to the cloud for it.
- QUERY reports `online: true` only when the board actually answered; an
  unreachable board answers `deviceOffline` and the cloud QUERY takes over.
