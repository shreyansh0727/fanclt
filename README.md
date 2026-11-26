# FanCtl-8

An ESP32 controller for mains loads (fan, bulbs, sockets) with a FastAPI + Firebase backend
and a React Native app. Everything works from outside the house over the internet, and
switches straight over the LAN when your phone is at home.

The channel count is a build-time setting: `RELAY_COUNT` in `firmware/src/config.h`
defaults to **4** (the current 4-relay board); build with `-D RELAY_COUNT=8` for the
8-channel board. Everything downstream — registration, state arrays, the app's tile
count, assistant discovery — follows the value the firmware reports, so one codebase
serves both.

```
                       internet / LAN
  ┌──────────────┐  RTDB REST stream + writes  ┌─────────────────────────┐
  │ ESP32 + 8ch  │◄───────────────────────────►│ Firebase Realtime DB    │
  │ opto relays  │                             │  devices/<id>/...       │
  └──────┬───────┘                             └────────────▲────────────┘
         │ http :80 (LAN)                                    │ SDK (live)
         │                                    ┌──────────────┴──────────┐
  ┌──────▼───────┐   REST (pairing, list,     │  FanCtl app (Expo/RN)   │
  │ browser page │    schedules, commands)    └──────────────▲──────────┘
  └──────────────┘◄─────────────────────────────────────────┘
                                               ┌─────────────────────────┐
                                               │ FastAPI (backend/)      │
                                               │  ├ claims + tokens      │
                                               │  ├ scheduler            │
                                               │  └ Firebase Admin SDK   │
                                               └───────┬─────────────────┘
                                                       │
                                               ┌───────▼────────┐
                                               │ Firestore      │
                                               │ Firebase Auth  │
                                               └────────────────┘
```

- **Firmware** (`firmware/`): ESP32-C6 (Waveshare ESP32-C6-Pico), Arduino/PlatformIO.
  Active-low optocoupler relay board, 4 channels on this build (`RELAY_COUNT`), NVS-persisted
  state, captive setup portal, mDNS, local HTTP API, and one outbound HTTPS stream on its own
  Realtime Database node. No inbound ports needed at home.
- **Backend** (`backend/`): Python 3.11+, FastAPI, Firebase Auth ID tokens, Firestore via the
  Admin SDK, device registration + custom-token minting, command acknowledgement with
  timeouts, and a server-side schedule runner. It handles claim, device metadata, schedules
  and cloud commands (app commands use it only when the LAN is unreachable); writes land in
  Realtime Database, which the controller streams.
- **App** (`app/`): Expo + TypeScript + React Native. LAN-first commands with a cloud
  fallback, live state via `firebase/database`, optimistic UI, pairing, channel naming,
  schedules.

## Repository layout

```
backend/            FastAPI service, tests, .env.example
firmware/           PlatformIO project (ESP32 Arduino)
app/                Expo React Native app
docs/PROTOCOL.md    Device pairing REST, Realtime DB, LAN HTTP, REST reference
firebase/           Firestore + Realtime Database security rules
scripts/            Dev helpers (emulator token, voice clips, firmware build, release publish)
.github/workflows/  CI: firmware build, backend tests, app typecheck
firebase.json       Emulator + deploy config
```

## Hardware

### Bill of materials

| Part | Notes |
| --- | --- |
| Waveshare ESP32-C6-Pico | ESP32-C6-MINI-1, 4 MB flash, WiFi 6 + BLE, native USB-C |
| 4-channel 5 V optocoupler relay module | This build. Active-low, `VCC`/`JD-VCC` jumper if the board has one, screw terminals. An 8-channel board is the same wiring extended to `IN5..IN8` |
| 5 V supply — 1 A for 4 coils, 2 A if the DFPlayer shares the rail | Powers relay coils; do not take coil current from the ESP32 3.3 V rail |
| 4 × 10 kΩ resistors (8 for the 8-channel build) | Pull-ups from each `IN` pin to 3.3 V (holds relays off during boot) |
| DFPlayer Mini (MP3-TF-16P) + microSD | Optional voice announcements; FAT32 card, 32 GB or smaller |
| 8 Ω 0.5 W speaker | Connects to `SPK1`/`SPK2` on the DFPlayer |
| 1 kΩ resistor | Series resistor in the ESP32 → DFPlayer RX line |
| 1.3" SH1106 OLED, I2C, 128x64 | Optional status display: pet, pairing code, relay tiles (GP22/GP23) |
| Fuse + holder, MOV or RC snubber per inductive load | Protect relay contacts driving fans |
| Enclosure, terminal blocks, 22 AWG wire | Separate mains and low-voltage compartments |

### Wiring

| API channel | App tile | ESP32-C6 GPIO | Relay board |
| --- | --- | --- | --- |
| 0 | CH 1 | GP0 | IN1 |
| 1 | CH 2 | GP1 | IN2 |
| 2 | CH 3 | GP2 | IN3 |
| 3 | CH 4 | GP3 | IN4 |
| 4 | CH 5 | GP6 | IN5 |
| 5 | CH 6 | GP7 | IN6 |
| 6 | CH 7 | GP14 | IN7 |
| 7 | CH 8 | GP20 | IN8 |

Plus: relay `GND` to ESP32 `GND`; relay `VCC` (opto input side) to ESP32 `3V3`; relay
`JD-VCC` from the separate 5 V supply, with the `VCC`/`JD-VCC` jumper removed. `3V3` for the
opto side matters: it makes `INx` high a true 0 V across the opto LED, so the relay cannot
half-energise when the ESP32 drives 3.3 V into a 5 V opto board.

Only the first `RELAY_COUNT` rows apply on a given build: the 4-channel board wires
channels 0–3 (`D0,D1,D2,D3` → `IN1..IN4`) and leaves the rest of the table as reference
for the 8-channel variant. A cheap board without a `VCC`/`JD-VCC` jumper feeds its coils
from `VCC`, so that pin goes to **5 V** instead of `3V3`; expect a small risk of a relay
not releasing (≈1.7 V across the opto LED with a high 3.3 V output) and add a 1 kΩ series
resistor per `IN` if it shows up.

Full drawing: [docs/wiring.svg](docs/wiring.svg).

These pins avoid what the C6-Pico already uses: USB-JTAG (GP12/13), in-package flash
(GPIO24-30), the UART0 console (GP16/17), the RGB LED (GP8), the BOOT key (GP9), the boot
straps (GP4/5/8/9/15) and the TCA9554 I2C bus (GP22/23, shared with the optional OLED). The setup-portal button is the
onboard BOOT key (GP9), so no external button is needed — hold it 3 s while running to enter
the portal, and keep holding to 8 s to factory reset.

Notes:

- Most opto boards are **active low**: pulling `INx` to GND energises the coil. If yours is
  active high, build with `-D RELAY_ACTIVE_LOW=0`.
- With the `VCC`/`JD-VCC` jumper fitted the coil supply is shared with the opto side. Remove
  it and power `JD-VCC` from the separate 5 V supply; `VCC` stays on `3V3`. Most modules still
  tie both sides to the same `GND`, so this separates the supplies, not the ground.
- The 10 kΩ pull-ups keep `IN1..IN8` high (relays off) during reset before `setup()` runs.
  Firmware also writes the inactive level before switching each pin to `OUTPUT`, so the coils
  never see a boot pulse.
- Relay contacts are usually rated 10 A resistive. Derate substantially for fans and other
  inductive loads, and fit an RC snubber or MOV across the contacts.
- The C6-Pico has no USB-UART bridge: the console and flashing both use the chip's native
  USB-JTAG. Firmware is built with `ARDUINO_USB_CDC_ON_BOOT=1` so `Serial` appears on the
  USB-C port at 115200 baud.

### Voice announcements (DFPlayer Mini)

Optional. Without a module the firmware logs `[audio] dfplayer not detected` and runs exactly
as before; with one it speaks the pairing code and announces relay changes.

| DFPlayer pin | ESP32-C6 |
| --- | --- |
| `VCC` | `5V`/`VBUS` |
| `GND` | `GND` |
| `RX` | GP18 through a 1 kΩ series resistor |
| `TX` | GP19 |
| `BUSY` | GP21 (LOW while a clip plays) |
| `SPK1` / `SPK2` | 8 Ω 0.5 W speaker |

- Format the microSD as FAT32 (32 GB or smaller) and copy the contents of
  `firmware/sdcard/` to the card root, so the module sees `/mp3/0001.mp3 … /mp3/0052.mp3`.
  The folder name must be lowercase `mp3` and the file names exactly four digits.
- Track 52 is the pet's meow: it is synthesised by the same script (not speech) and plays
  before the spoken state on every relay change and identify. Tracks 53-57 are the app's pet
  reactions (`purr purr`, `yum yum`, `let's play`, `good night`, `good morning`). Replace
  `0052.mp3` with a real recording if you have one - the firmware only cares about the number.
- Regenerate the clips on Windows with `.venv/Scripts/python.exe scripts/make_voice_clips.py
  --voice "Microsoft Zira Desktop" --rate -1` (System.Speech + lameenc). Letters are spoken as
  "letter, NATO word" (`bee bravo`) because `B`/`V` and `D`/`E` are indistinguishable through a
  small speaker; see `firmware/sdcard/README.txt` for the track table.
- Volume, pins and `AUDIO_ENABLED` live in `firmware/src/config.h`. Keep `SPK1`/`SPK2` wires
  short and add a 100 µF capacitor across the module's 5 V/GND if the speaker crackles.
- `BUSY` is only a hint: if your module clones the line, clips are still paced by a timeout.

### OLED display (SSD1306 / SH1106)

Optional 1.3" 128x64 I2C panel. It shows a small animated cat, the pairing code while the
box is unclaimed, live relay tiles and the LAN IP once paired, and the hotspot details while
the setup portal is up. Without a panel the firmware logs `[display] no ssd1306 ...` and runs
exactly as before.

| OLED pin | ESP32-C6 |
| --- | --- |
| `VCC` | `3V3` |
| `GND` | `GND` |
| `SDA` | GP22 |
| `SCL` | GP23 |

- The panel answers at 0x3C (some modules use 0x3D; both are probed) on the same bus as the
  onboard TCA9554PW expander at 0x20, so no extra pins are needed. Modules that only accept
  5 V on `VCC` still work from `3V3` on most SH1106 boards.
- `DISPLAY_ENABLED`, `DISPLAY_SDA`, `DISPLAY_SCL`, `DISPLAY_ADDR`, `DISPLAY_BUS_HZ` and
  `DISPLAY_SSD1306` live in `firmware/src/config.h`. The bus runs at 100 kHz: on jumper wires
  these modules often miss ACKs at 400 kHz, which looks like a missing display.
- `DISPLAY_SSD1306` picks the controller: `1` for SSD1306, `0` for SH1106. Both sit at 0x3C
  and answer identically, so on a dark panel flip this switch first. If the panel is an
  SH1106 being driven as SSD1306, the picture is fine but shifted two pixels left (the left
  edge clips, `x_offset` is 0 instead of 2) - use `0` for the correct columns.
- The panel shares the 3V3 rail with the radio and the DFPlayer. A current spike can brown it
  out: the picture shows for a second, then the glass goes black while I2C still ACKs, because
  the controller reset its registers. The firmware re-sends the charge-pump/DC-DC, contrast
  and display-on registers every second (`kWakeSeq` in `display.cpp`), so the image comes
  back on its own. If it blinks with every announcement, the wiring is the problem: short,
  thick `VCC`/`GND` wires, a 10 µF capacitor across the panel, or `5V`/`VBUS` on `VCC` if the
  module has its own regulator. A 4-pin module has no reset pin to hold.
- Boot prints `[display] ssd1306 128x64 ready at 0x3C (SDA GP22, SCL GP23)` (or `sh1106`).
  If it prints `no ssd1306 at 0x3C/0x3D ... i2c scan: 0x20`, the bus and pins are fine and
  only the panel is not answering - check `3V3`/`GND` and that `SDA`/`SCL` are not swapped.
  An empty scan means the bus itself is down.
- The pet blinks and bobs at rest above a soft ground shadow that shrinks as it rises, jumps
  with an open mouth when a relay changes, opens wide eyes and shows `HERE!` during an
  identify request, and sleeps (`z z`) after 6 seconds without a link. The top bar shows RSSI
  bars, the SSID and a link tag (`SETUP`/`PAIR`/`LINK`/`SYNC`/`OFF`/`ERR`). With a DFPlayer
  the cat also meows (track 52) before each spoken relay change.
- The app's **Pet** buttons drive full-screen reaction scenes: the status bar, tiles and IP line
  disappear and the cat is drawn at 2x. `pet` floats hearts up both margins, `play` bounces a
  ball across the panel while the cat's pupils follow it, `feed` drops kibble into a bowl with a
  chewing muzzle, `sleep` latches a crescent moon and growing `z z z` until `wake`, and `wake`
  pops a sparkle burst. Alarm and reminder schedules reuse the same scenes: `alarm` shakes a
  wide-eyed cat under a ringing bell with a pulsing `!` and keeps ringing until the app sends
  `alarm_stop`, `reminder` has the cat look up at a
  smaller bell beside a ticking clock. Scenes repaint every 150 ms instead of 200 ms. The relay
  tiles show the first three characters of each channel name set in the app.

### Mains safety

This project switches mains voltage. Work with a qualified electrician and follow local
rules. Use an enclosure, screw terminals and strain relief - never a breadboard. Put each
switched live line on a fuse, keep mains and low-voltage wiring physically separated, and
bond earth where required. Test with lamps before connecting a fan or socket load. Do not
work on live wiring.

## Firmware

Prerequisites: [PlatformIO](https://platformio.org/) (`pio`) and a USB **data** cable.

1. Edit `firmware/src/config.h`:
   - `CLOUD_HOST` / `CLOUD_PORT` / `CLOUD_USE_TLS`: where the backend runs (pairing and token
     requests). A LAN IP is fine while developing; use a DNS name and HTTPS outside the home
     network.
   - `RTDB_HOST` / `RTDB_PORT` / `RTDB_USE_TLS` / `RTDB_NAMESPACE` / `FIREBASE_API_KEY`: the
     Realtime Database and Identity Toolkit endpoints. These are normally left empty - the
     token response advertises them, so the same firmware works against the emulator and
     production. Set them only to pin a fixed target.
   - `RELAY_ACTIVE_LOW` if your board is active high.
   - `RESTORE_STATE_ON_BOOT`: `0` (default) leaves everything off after a power cut; `1`
     restores the last relay mask from NVS.
   - `CLOUD_CA_CERT` for a private CA. Leave empty only in development - it accepts any
     certificate.
2. Build and flash:

```bash
cd firmware
pio run
pio run -t upload
pio device monitor -b 115200
```

3. First boot with no WiFi credentials starts the setup hotspot (also printed on serial), and
   the speaker announces "setup mode":
   - SSID: `FanCtl-Setup-XXXX` (last 4 of the hardware id)
   - password: the last 8 hex characters of the hardware id
   - Join it and open `http://192.168.4.1/`, enter your WiFi, and optionally a server
     host/port/TLS override. Save; the board reboots.
4. Once the board joins WiFi the speaker says "connected to wifi". If it is not paired yet it
   then reads the pairing code (`ABCD-1234`) aloud and repeats it every 15 seconds until the
   app claims it. The serial console prints the same code, and a fitted OLED shows it in
   large type.

Useful overrides in `firmware/platformio.ini` (mind the quoting):

```ini
build_flags =
    -DCLOUD_HOST=\"api.example.com\"
    -DCLOUD_PORT=443
    -DCLOUD_USE_TLS=1
    -DRTDB_HOST=\"your-project-default-rtdb.firebaseio.com\"
    -DFIREBASE_API_KEY=\"AIza...\"
```

The board also runs a control page at `http://<ip>/` (mDNS:
`http://fanctl-XXXX.local/`) for use when the app is unavailable. On a claimed device that
page embeds the device key, so it can send authenticated commands from the LAN.

### Over-the-air updates

Both halves update themselves from one manifest that the backend serves at
`GET /api/v1/updates`; see [docs/PROTOCOL.md](docs/PROTOCOL.md#5-releases-and-updates).

**Controller.** `firmware/partitions.csv` defines two 1.5 MB app slots plus `otadata`
(`huge_app.csv` had a single slot with nowhere to write an update). Switching to it needs one
more USB flash; NVS keeps its offset, so Wi-Fi credentials, relay names and the pairing
survive. After that, `ota.cpp` asks the manifest endpoint 45 s after boot and every 6 hours.
A newer `version` is downloaded over the same HTTPS endpoint as the cloud link, checked
against the manifest `sha256`, flashed into the inactive slot and the board reboots itself.

The install is deliberately quiet: it waits until **all 8 relays are off** (re-checking every
10 minutes), and it is skipped while the setup portal or a latched alarm is active, because
the reboot would cut power to the loads and silence the alarm. The board announces nothing
during an update; serial logs `[ota] …`. A release that fails to boot can be recovered with a
USB flash, since the bootloader still has the previous slot.

**App.** The app checks the same manifest on launch and when it returns to the foreground
(`app/src/lib/updates.ts`). A newer `app.version` raises a floating card; **Update now**
downloads the APK into the cache with a progress bar and opens the system installer. Android
always asks for confirmation, and the first install needs "Allow from this source" for FanCtl.
APKs must keep the same signing key (this project builds release with the checked-in debug
keystore) or Android refuses the update.

**Publishing.** Build, then upload both artifacts and update the manifest in one step:

```bash
.venv/Scripts/pio.exe run -d firmware
cd app/android && ./gradlew assembleRelease && cd ..
cd .. && .venv/Scripts/python.exe scripts/publish_release.py \
    --firmware firmware/.pio/build/esp32-c6-pico/firmware.bin \
    --app app/android/app/build/outputs/apk/release/app-release.apk \
    --notes "What changed"
```

The script uploads to Cloud Storage (`releases/firmware/…`, `releases/app/…`), makes each object
publicly readable, and writes `releases/current` in Firestore, which the backend reads. Only the
release objects are public — the bucket itself stays private. The Cloud Run revision must include
the `/api/v1/updates` route; publishing does not redeploy the backend.

## Backend

Prerequisites: Python 3.11+, a Firebase project with **Email/Password** sign-in enabled, a
Realtime Database, and a service account JSON. Firestore uses the Admin SDK and the database
uses the REST API, both bypassing client rules.

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate      # Git Bash on Windows
pip install -r requirements.txt -r requirements-dev.txt   # dev adds pytest + httpx
cp .env.example .env               # then edit
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Minimum `.env` for a real project:

```dotenv
FANCTL_PROJECT_ID=your-project-id
FANCTL_CREDENTIALS_FILE=./service-account.json
FANCTL_CORS_ORIGINS=*
FANCTL_DATABASE_URL=https://your-project-default-rtdb.firebaseio.com
FANCTL_FIREBASE_API_KEY=AIza...
```

`FANCTL_DATABASE_URL` is passed to the controller in the token response; `FANCTL_FIREBASE_API_KEY`
is the public web API key the controller uses for `signInWithCustomToken`. Health check:
`curl http://127.0.0.1:8000/api/v1/health`.

Run the backend with **one worker** while developing: the schedule runner is in-process and
would fire each schedule once per worker. Device and app traffic no longer passes through the
process, so horizontal scale-out only needs a single-runner lock (or moving the runner to a
Cloud Function).
Deploy the database rules once per project:

```bash
firebase deploy --only database
```

Tests (memory repository, no Firebase needed):

```bash
cd backend
python -m pytest
```

Windows note: `zoneinfo` needs the `tzdata` package (already in `requirements.txt`).

### Firebase emulators (local, no real project)

Requires **JDK 21+** and the Firebase CLI. firebase-tools 15 rejects older JDKs, and it checks
the `java` on `PATH` (not `JAVA_HOME` alone), so on Windows point both at a 21+ install:

```powershell
$env:JAVA_HOME = "C:\Program Files\Java\jdk-23"
$env:Path = "$env:JAVA_HOME\bin;$env:Path"
```

```bash
firebase emulators:start --only auth,firestore,database
```

**PowerShell users:** quote comma-separated values - `--only "auth,firestore,database"`. An
unquoted list is parsed as an array and the CLI starts with no emulators selected
("No emulators to start").

The first run downloads the emulator jars and the Emulator UI, which can take a few minutes.
The database emulator listens on `0.0.0.0:9000` (see `firebase.json`); the namespace is
`<project id>-default-rtdb`.

In another shell:

```bash
export FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099
export FIRESTORE_EMULATOR_HOST=127.0.0.1:8080
export FIREBASE_DATABASE_EMULATOR_HOST=127.0.0.1:9000
export FANCTL_PROJECT_ID=demo-fanctl
export FANCTL_REPO_BACKEND=firestore
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

With `FIREBASE_DATABASE_EMULATOR_HOST` set the backend talks to the database emulator over
REST (namespace `demo-fanctl-default-rtdb`); without it you must set `FANCTL_DATABASE_URL` to
a real database. `FANCTL_REPO_BACKEND=memory` also forces an in-memory database, which is what
the test-suite uses.

The token response advertises the RTDB and Auth emulator hosts, so a controller on the same
network follows them automatically. Leave `RTDB_HOST` unset in the firmware; only pin
`-DRTDB_HOST=\"<lan-ip>\" -DRTDB_PORT=9000 -DRTDB_USE_TLS=0` if you need the board to ignore
those hints.

Mint a test ID token and call the API:

```bash
TOKEN=$(python scripts/dev_token.py --email dev@example.com --password secret123)
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/v1/devices
```

`FANCTL_REPO_BACKEND=memory` drops Firestore, but token verification still needs Firebase
credentials (or the emulator). The pytest suite avoids Firebase by stubbing the verifier.

### Production: Firebase + Cloud Run

The backend ships with a container image (`backend/Dockerfile`) for Cloud Run. In short:

1. Create Firestore, a Realtime Database, and enable **Email/Password** in the Firebase
   project. Note the database URL (`https://<project>-default-rtdb.firebaseio.com` or the
   regional `.firebasedatabase.app` form).
2. Deploy rules: `firebase deploy --only firestore:rules,database --project <id>`.
3. Give the Cloud Run service account `roles/datastore.user`,
   `roles/firebasedatabase.admin` and `roles/iam.serviceAccountTokenCreator` on itself - it
   signs custom tokens through IAM, so no key file is needed.
4. `gcloud run deploy fanctl-api --source backend ... --min-instances 1 --max-instances 1
   --no-cpu-throttling` with `FANCTL_PROJECT_ID`, `FANCTL_DATABASE_URL` and
   `FANCTL_FIREBASE_API_KEY`.
5. Point the firmware at the Cloud Run host (`CLOUD_HOST`, port 443, TLS on; leave the `RTDB_*`
   flags empty) and the app at the service URL plus the real Firebase config.

Min/max instances 1 with CPU always allocated are what keep the in-process scheduler ticking;
the full runbook, IAM commands and verification steps are in
[backend/README.md](backend/README.md#deploy-on-cloud-run).

## Mobile app

```bash
cd app
npm install
# edit app.json -> expo.extra (apiBase, Firebase web config, firebaseDatabaseUrl)
npx expo start
```

See `app/README.md` for emulator/networking details and the LAN-first behaviour.

**Change Wi-Fi from the app.** Open a controller and use *Change Wi-Fi network*. The phone
posts the new SSID/password straight to the device over the LAN
(`POST /api/wifi`, authorised with the device key) and the board restarts into the new
network. The phone must be on the same network as the controller for this; if the new network
fails the device falls back to the `FanCtl-Setup-XXXX` hotspot after 90 seconds.

## Continuous integration

`.github/workflows/ci.yml` runs on pushes to `main`, pull requests and manual dispatch:

- **firmware** - `pio run` in `firmware/` (Linux runner, PlatformIO cache), publishes
  `firmware.bin` and `firmware.elf` as artifacts
- **backend** - `pytest` against the in-memory repository, no Firebase required
- **app** - `npm ci` + `tsc --noEmit`

This folder is not a git repository yet; `git init` and push to GitHub for the workflow to
run. The same build locally:

```bash
scripts/build.sh                               # build only
scripts/build.sh -t upload --upload-port COM12 # build and flash
```

## Pairing walkthrough

1. Power the controller with no saved WiFi. It opens the `FanCtl-Setup-XXXX` hotspot; the
   speaker says "setup mode, connect to hotspot …" and the portal at `http://192.168.4.1/`
   shows the same SSID and password.
2. Save your home WiFi in the portal. The board reboots, joins your network, and the speaker
   says "connected to wifi".
3. It POSTs its hardware id, pairing code and a random device secret to
   `/api/v1/device/register`, then polls `/api/v1/device/token`. While unclaimed the speaker
   reads "pairing code is …" every 15 seconds, spelling the 8 characters.
4. In the app: sign in → **Pair a controller** → type the spoken code (the `-` is optional).
5. The backend hashes the code, marks the device owned, and the next token poll returns the
   `device_id`, `device_key`, a Firebase **custom token** carrying `dev=<device_id>`, and the
   RTDB/Auth endpoints. The speaker says "paired"; both values are saved to NVS.
6. The firmware signs in with the custom token (`signInWithCustomToken`), opens an HTTPS
   event-stream on `devices/<id>`, says "ready", and the app shows it online. The mirrored
   `channels` array gives announcements their "fan"/"light"/"socket" wording.
7. Codes expire after 24 hours by default (`FANCTL_PAIR_CODE_TTL_S`); a reboot issues the same
   code and re-opens the window.

Announcements afterwards: each relay change made from the app, the LAN API or the web page is
spoken ("fan on", "light 2 off", "all off"), and **Identify** in the app says "here i am".
Nothing is announced while `AUDIO_ENABLED` is 0 or no DFPlayer is detected.

Removing a controller in the app calls `DELETE /api/v1/devices/{id}`; the backend replaces its
database node with `{"unclaim": true}`, and the device erases its key and reboots when it sees
it on the stream. Schedules go with the device.

## Schedules

Schedules are stored in Firestore and executed by the backend every
`FANCTL_SCHEDULER_TICK_S` seconds (default 20). Each one has a 24-hour `HH:MM` time, a day
list (`0` = Monday … `6` = Sunday), an IANA timezone, and one action: switch all channels,
switch one channel, ring the **alarm** siren, or play the **reminder** chime. Alarm and reminder
runs also play a full-screen cat scene on the panel (tracks 58/59 on the SD card) and never
change a relay. The alarm does not time out: it re-queues the siren until the device's
**Alarm ringing** banner in the app is pressed, which sends `alarm_stop` (over the LAN when
possible). Times are evaluated in that timezone, so DST changes are handled.

Schedules fire even when the phone is off. If the device is offline at firing time the run is
recorded as unacked and the schedule still advances to its next occurrence.

## Status indication

The C6-Pico's RGB LED is wired to the WS2812 data line on GP8 and is not driven by this
firmware. Status is visible on the serial console (`115200` baud) and through the speaker:
"connected to wifi", the spoken pairing code while unclaimed, "ready" once when the cloud
stream first connects, and "paired" at the end of pairing. The **Identify** button in the app
also says "here i am".

## Re-provision / factory reset

Everything here is done with the onboard **BOOT** key (GP9). The **RST/EN** key only reboots
the chip; it never erases anything.

Hold BOOT while the firmware is running:

- **3 seconds** - open the setup hotspot (`FanCtl-Setup-XXXX`, password = last 8 characters of
  the hardware id) without erasing anything. The speaker says "setup mode". The hotspot stays
  up for 10 minutes, even if the controller is still connected to your old network, so the
  local cloud control keeps working while you fix WiFi.
- **8 seconds** - *keep holding through the 3 s announcement* to reach the wipe: WiFi
  credentials, pairing key, channel types and the relay mask are erased, the LED blinks, and
  the board reboots straight into the setup portal.

If the controller lost its network on its own (router replaced, password changed), you do not
need the button: after ~90 seconds offline it starts the same `FanCtl-Setup-XXXX` hotspot by
itself. Join it, open `http://192.168.4.1`, save the new WiFi, and the board reboots and joins
it. Pairing and the relay state are untouched.

Steps when you cannot reach the controller at all:

1. Power-cycle it and watch the serial console at 115200 baud for the `[status]` line.
2. If it is alive but on the wrong network, wait for the fallback hotspot or hold BOOT 8 s.
3. If the firmware does not respond, recover over USB: hold **BOOT**, tap **RST**, release
   both - the ROM bootloader shows up on COM12 and `pio run -t upload` works again.
4. To wipe the stored configuration without the button, erase the NVS partition:

   ```bash
   /c/Users/<you>/.platformio/packages/tool-esptool/esptool.exe \
     -p COM12 erase-region 0x9000 0x5000
   ```

   The next boot opens the setup portal with WiFi and pairing gone.

## Security notes and trade-offs

- Firestore client access is deny-all (`firebase/firestore.rules`); all Firestore reads and
  writes go through the backend.
- Realtime Database rules (`firebase/database.rules.json`): a device may read its own node and
  write only `info`, `state`, `online` and `acks/*` under it, gated on `auth.token.dev`; the
  owner may read the node (`owner == auth.uid`) and write only `commands/*`. The backend uses
  the service-account credentials, which bypass rules.
- Pairing codes are 8 Crockford characters, stored as `sha256("fanctl-pair-v1:" + code)`, with a
  24 h window.
- `lan_key` is the device key exposed to the owner so the app can control the device directly.
  It is powerful: anyone who has it and is on the LAN can switch relays. That is the price of
  working without the internet. Treat it like a password.
- The device control page embeds the key when loaded, so anyone on the LAN who loads the page
  can control the device. Give the controller a trusted network.
- Use HTTPS/TLS with a real certificate outside your home network. Empty `CLOUD_CA_CERT`
  accepts any certificate and is for development only.
- Cloud commands are rate-limited per device in the backend (`FANCTL_COMMAND_RATE_PER_SEC`).
  The rules also let an owner write `commands/*` directly, which would bypass that limit - the
  app does not do this; keep it in mind if you add one that does.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `invalid_code` / device never pairs | The controller must have polled `/device/token` in the last 120 s. Check serial for the register/token status and that `CLOUD_HOST` is reachable. |
| `device_offline` on claim | Same as above - old pairing windows close; reboot the board to re-register. |
| `already paired to another account` on serial | The NVS still holds a key from another account. Hold BOOT 8 s to factory reset. |
| App says `No LAN` | Phone is on a different subnet, or `local_ip`/`lan_key` is missing because the device has not written `info` yet. |
| Cloud command returns `no_ack` | The device is streaming but did not answer within 6 s; check its serial for the command frame and that its clock is fresh (`ts` from the HTTP `Date` header). |
| Command returns `online: false` | The backend thinks the device is offline (`info.ts` older than 40 s). The stream may be stuck: check the serial for reconnect/backoff lines. |
| Cloud link reconnects in a loop | Wrong RTDB/Auth host, an expired custom token, or database rules rejecting the device token. The serial prints the HTTP status (401/403) and the reconnect delay. |
| App shows no live state | `firebaseDatabaseUrl` (or `EXPO_PUBLIC_FIREBASE_DATABASE_URL`) is unset, or the app points at a different database/emulator than the backend. |
| Schedules fire at the wrong time | Check the schedule timezone, not just the phone's. |
| Relay chatters at power-up | Fit the 10 kΩ pull-ups; confirm `RELAY_ACTIVE_LOW` matches the board. |
| `ZoneInfoNotFoundError` on Windows | `pip install tzdata` (already in requirements). |
