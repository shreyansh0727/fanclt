#pragma once

// ---------------------------------------------------------------------------
// Identity
// ---------------------------------------------------------------------------
#define FW_VERSION "1.2.0"
#define DEVICE_MODEL "fanctl-4"  // 4-relay build; see RELAY_COUNT below

// ---------------------------------------------------------------------------
// Relay wiring
//
// 8-channel opto-isolated relay modules ("octocoupler" boards) are almost
// always ACTIVE LOW: pulling IN to GND energises the coil. Set this to 0 for
// the rare active-high board.
// ---------------------------------------------------------------------------
#ifndef RELAY_ACTIVE_LOW
#define RELAY_ACTIVE_LOW 1
#endif

// Safe output pins for the Waveshare ESP32-C6-Pico (ESP32-C6-MINI-1).
// Avoids everything the board or chip already uses:
//   12, 13      -> native USB-JTAG (D-/D+); repurposing kills USB and flashing
//   24 - 30     -> in-package SPI flash (not broken out on the headers)
//   16, 17      -> UART0 console (kept free for the serial log)
//   8           -> onboard WS2812 RGB LED, also a boot strap
//   9           -> BOOT button, also a boot strap
//   4, 5, 15    -> boot straps
//   22, 23      -> I2C to the onboard TCA9554PW I/O expander
// 0/1 are also XTAL_32K and 20/21 are also SDIO alternates, all fine as GPIO.
// Only the first RELAY_COUNT entries are driven; the full list also documents
// the 8-channel build.
static const int RELAY_PINS[8] = { 0, 1, 2, 3, 6, 7, 14, 20 };
#ifndef RELAY_COUNT
#define RELAY_COUNT 4
#endif

// Restore the last relay state after a power cut / reboot.
// Default 0 (everything off) because a fan or socket switching itself on
// unattended after an outage is usually unwanted. Lights can safely set 1.
#ifndef RESTORE_STATE_ON_BOOT
#define RESTORE_STATE_ON_BOOT 0
#endif

// ---------------------------------------------------------------------------
// Status LED / factory reset button
// ---------------------------------------------------------------------------
// The C6-Pico's onboard RGB LED is a WS2812 on GPIO8 and needs a NeoPixel
// driver, so status indication stays on the serial console (-1 disables the
// plain GPIO blink).
#define STATUS_LED_PIN -1
#define ACTIVE_HIGH_LED 1

// Momentary button between this pin and GND. On this board it is the BOOT
// key wired to GPIO9. Holding it while resetting enters the ROM bootloader;
// holding it while the firmware runs opens the setup portal. Keep holding
// past the 3 s announcement to reach the 8 s wipe.
#define CONFIG_BUTTON_PIN 9
#define PORTAL_HOLD_MS 3000      // hold 3s -> open setup portal
#define FACTORY_RESET_HOLD_MS 8000  // hold 8s -> wipe WiFi + pairing

// ---------------------------------------------------------------------------
// 1.3" OLED display (optional)
//
// SH1106 128x64 on the I2C bus shared with the onboard TCA9554PW expander
// (0x20). The panel answers at 0x3C. GP22 is SDA and GP23 is SCL on the
// C6-Pico headers; the pins are passed explicitly because the Arduino
// esp32c6 variant defaults to the opposite order. Set DISPLAY_ENABLED to 0 to
// compile the feature out.
// ---------------------------------------------------------------------------
#ifndef DISPLAY_ENABLED
#define DISPLAY_ENABLED 1
#endif
#define DISPLAY_SDA 22
#define DISPLAY_SCL 23
#define DISPLAY_ADDR 0x3C
// 100 kHz: 1.3" modules on jumper wires often miss ACKs at 400 kHz, and the
// full-frame refresh still runs at ~5 fps.
#define DISPLAY_BUS_HZ 100000
// 1 = SSD1306 controller (most 0.96" panels), 0 = SH1106 (most 1.3" panels).
// Both answer at 0x3C and look identical on the wire; pick the one your panel
// actually uses. This board's 1.3" panel is an SH1106: driving it as SSD1306
// leaves a 2 px stripe of stale pixels on the right edge, because the visible
// window starts at RAM column 2.
#define DISPLAY_SSD1306 0

// ---------------------------------------------------------------------------
// DFPlayer Mini (MP3-TF-16P) voice announcements
// ---------------------------------------------------------------------------
// Clips live on the SD card as /mp3/0001.mp3 ... /mp3/0051.mp3; see
// scripts/make_voice_clips.py for the full track table.
#ifndef AUDIO_ENABLED
#define AUDIO_ENABLED 1
#endif
#define AUDIO_UART_TX 18        // ESP32 TX -> module RX (1 kΩ series recommended)
#define AUDIO_UART_RX 19        // ESP32 RX <- module TX
#define AUDIO_BUSY_PIN 21       // module BUSY, LOW while a clip plays; -1 to disable
#define AUDIO_VOLUME 24         // 0..30
#define PAIR_CODE_SPEAK_MS 15000  // repeat the spoken pairing code this often

// ---------------------------------------------------------------------------
// Provisioning access point
// ---------------------------------------------------------------------------
#define AP_SSID_PREFIX "FanCtl-Setup-"
// AP password is derived from the chip MAC and printed on the serial console
// at 115200 baud, e.g. "AP password: 7F3C9A21".
#define AP_PASSWORD_FROM_MAC 1

// ---------------------------------------------------------------------------
// Networking
// ---------------------------------------------------------------------------
// The backend is only used for pairing: it hands the controller a Firebase
// custom token plus the Realtime Database and Auth endpoints. Point this at
// your backend; a LAN IP is fine while developing, a DNS name + TLS for a
// device outside your home network.
#ifndef CLOUD_HOST
#define CLOUD_HOST "fanctl-api-hxnciith6a-el.a.run.app"
#endif
#ifndef CLOUD_PORT
#define CLOUD_PORT 443
#endif
#ifndef CLOUD_USE_TLS
#define CLOUD_USE_TLS 1
#endif
#define CLOUD_REGISTER_PATH "/api/v1/device/register"
#define CLOUD_TOKEN_PATH "/api/v1/device/token"
#define CLOUD_UPDATE_PATH "/api/v1/updates"

// Over-the-air firmware updates. The first check waits for the network and the
// cloud link to settle; later checks are hours apart because a release only
// changes when someone publishes one. While any relay is on the install is
// deferred - an OTA reboot drops every channel - so a busy board re-checks on
// OTA_DEFER_MS instead of waiting out the full interval.
#define OTA_FIRST_CHECK_MS 45000UL
#define OTA_CHECK_INTERVAL_MS (6UL * 60UL * 60UL * 1000UL)
#define OTA_DEFER_MS (10UL * 60UL * 1000UL)
#define OTA_RETRY_MS (10UL * 60UL * 1000UL)
#define OTA_HTTP_TIMEOUT_MS 15000

// Fallbacks when the backend does not advertise where its database lives (an
// older deployment). Normal operation takes the endpoints from the token
// response, so moving between the emulator and production needs no rebuild.
#ifndef RTDB_HOST
#define RTDB_HOST ""
#endif
#ifndef RTDB_PORT
#define RTDB_PORT 443
#endif
#ifndef RTDB_USE_TLS
#define RTDB_USE_TLS 1
#endif
#ifndef RTDB_NAMESPACE
#define RTDB_NAMESPACE ""
#endif
#ifndef FIREBASE_API_KEY
#define FIREBASE_API_KEY ""
#endif

// Optional PEM root CA. Leave empty to accept any certificate (development
// only - it allows a man-in-the-middle to read the device credentials).
#ifndef CLOUD_CA_CERT
#define CLOUD_CA_CERT ""
#endif

#define LOCAL_HTTP_PORT 80
#define MDNS_PREFIX "fanctl"

#define STATE_SYNC_INTERVAL_MS 400
// info.ts is the heartbeat the app and backend use for presence: at 10 s the
// 40 s window on both sides survives a reconnect cycle and still clears
// quickly after a power cut.
#define TELEMETRY_INTERVAL_MS 10000
#define TOKEN_REFRESH_MARGIN_MS 300000UL  // refresh 5 min before expiry
// A dead stream leaves the socket open while separate HTTPS writes keep
// succeeding, so a silent stream must be noticed well before the old 75 s.
#define STREAM_IDLE_TIMEOUT_MS 60000UL   // the database keep-alives every 30 s
#define STREAM_RETRY_MIN_MS 3000UL
#define STREAM_RETRY_MAX_MS 30000UL
#define IDENTIFY_RETRY_MS 15000UL
#define CLAIMED_RETRY_MS 60000UL
#define WIFI_RETRY_INTERVAL_MS 15000
#define AP_FALLBACK_AFTER_MS 90000
// A portal opened with the BOOT key stays up this long, even if WiFi reconnects.
#define MANUAL_PORTAL_TIMEOUT_MS 600000
#define WIFI_DISABLE_SLEEP 1
