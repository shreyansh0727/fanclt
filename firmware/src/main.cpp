// FanCtl-8 : ESP32 8-channel relay controller
//
// Boot: connect to WiFi -> serve local API on :80 -> sign in to Firebase and
// stream its Realtime Database node over HTTPS. Relays can be driven from the
// app over the internet, from the app directly over the LAN, or from the
// built-in web page at http://<ip>/.

#include <Arduino.h>
#include <ESPmDNS.h>
#include <WiFi.h>

#include "audio.h"
#include "cloud.h"
#include "config.h"
#include "device_http.h"
#include "display.h"
#include "ota.h"
#include "relays.h"
#include "storage.h"

namespace {

bool mdnsStarted = false;
String mdnsTxtId;  // device id last published in the _fanctl TXT record
uint32_t wifiRetryAt = 0;
uint32_t offlineSince = 0;
uint32_t buttonPressedAt = 0;
bool buttonHandled = false;
bool manualPortal = false;
uint32_t manualPortalAt = 0;
uint32_t nextStatusLogAt = 0;

String hostname() {
  String hw = store::hardwareId();
  return String(MDNS_PREFIX) + "-" + hw.substring(hw.length() - 4);
}

String apSsid() {
  String hw = store::hardwareId();
  return String(AP_SSID_PREFIX) + hw.substring(hw.length() - 4);
}

String apPassword() {
#if AP_PASSWORD_FROM_MAC
  String hw = store::hardwareId();
  return hw.substring(hw.length() - 8);
#else
  return "configme";
#endif
}

void ledWrite(bool on) {
#if STATUS_LED_PIN >= 0
  digitalWrite(STATUS_LED_PIN, ACTIVE_HIGH_LED ? on : !on);
#endif
}

void startStation() {
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(hostname().c_str());
#if WIFI_DISABLE_SLEEP
  WiFi.setSleep(false);
#endif
  WiFi.begin(store::wifiSsid().c_str(), store::wifiPass().c_str());
  wifiRetryAt = millis() + WIFI_RETRY_INTERVAL_MS;
}

bool waitForWifi(uint32_t timeoutMs) {
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < timeoutMs) {
    display::tick();
    delay(120);
    ledWrite(((millis() - start) / 200) % 2 == 0);
  }
  return WiFi.status() == WL_CONNECTED;
}

void handleConfigButton() {
#if CONFIG_BUTTON_PIN >= 0
  bool pressed = digitalRead(CONFIG_BUTTON_PIN) == LOW;

  if (!pressed) {
    // A short press does nothing on purpose, so a stray tap cannot reconfigure.
    buttonPressedAt = 0;
    buttonHandled = false;
    return;
  }

  if (buttonPressedAt == 0) {
    buttonPressedAt = millis();
    ledWrite(true);
    return;
  }

  uint32_t held = millis() - buttonPressedAt;
  // The reset is checked first and does not care that the portal already opened:
  // holding through the 3 s announcement must still reach the 8 s wipe.
  if (held >= FACTORY_RESET_HOLD_MS) {
    buttonHandled = true;
    Serial.println("[main] factory reset - wiping WiFi + pairing");
    for (int i = 0; i < 10; i++) {
      ledWrite(i % 2 == 0);
      delay(80);
    }
    store::factoryReset();
    ESP.restart();
  } else if (held >= PORTAL_HOLD_MS && !buttonHandled) {
    buttonHandled = true;
    manualPortal = true;
    manualPortalAt = millis();
    Serial.println("[main] opening setup portal");
    device_http::startPortal(apSsid(), apPassword());
    audio::announcePortal(apSsid());
  }
#endif
}

void tickLed() {
  uint32_t now = millis();
  if (cloud::identifying()) {
    ledWrite((now % 160) < 80);
  } else if (device_http::portalActive()) {
    ledWrite((now % 240) < 120);
  } else if (cloud::status() == cloud::READY) {
    ledWrite(true);
  } else if (WiFi.status() == WL_CONNECTED) {
    ledWrite((now % 2000) < 1000);
  } else {
    ledWrite((now % 4000) < 60);
  }
}

void logStatus() {
  if ((int32_t)(millis() - nextStatusLogAt) < 0) return;
  nextStatusLogAt = millis() + 30000;
  Serial.printf("[status] wifi=%s ip=%s rssi=%d cloud=%s heap=%u\n",
                WiFi.status() == WL_CONNECTED ? "up" : "down",
                WiFi.localIP().toString().c_str(),
                WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0,
                cloud::statusText(), (unsigned)ESP.getFreeHeap());
}

// While the device is online but unclaimed, read the pairing code out loud
// every PAIR_CODE_SPEAK_MS so the user can type it into the app.
void tickPairingPrompt() {
  static uint32_t nextAt = 0;
  if (store::isClaimed() || cloud::status() != cloud::WAITING_CLAIM) {
    nextAt = 0;
    return;
  }
  if (!audio::idle()) return;
  if (nextAt != 0 && (int32_t)(millis() - nextAt) < 0) return;
  audio::announcePairingCode(store::ensurePairCode());
  nextAt = millis() + PAIR_CODE_SPEAK_MS;
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println();
  Serial.println("---------------------------------------------");
  Serial.printf(" FanCtl %s (%s)\n", FW_VERSION, DEVICE_MODEL);
  Serial.printf(" hardware id : %s\n", store::hardwareId().c_str());
  Serial.println("---------------------------------------------");

#if STATUS_LED_PIN >= 0
  pinMode(STATUS_LED_PIN, OUTPUT);
  ledWrite(false);
#endif
#if CONFIG_BUTTON_PIN >= 0
  pinMode(CONFIG_BUTTON_PIN, INPUT_PULLUP);
#endif

  relays.begin();
  display::begin();
  audio::begin();
  relays.setChangeListener(audio::onRelayChange);
  device_http::setStateListener(cloud::requestPush);
  device_http::start();

  if (!store::hasWifiCredentials()) {
    Serial.println("[main] no WiFi credentials, starting setup portal");
    Serial.printf("[main] AP: %s  password: %s\n", apSsid().c_str(), apPassword().c_str());
    device_http::startPortal(apSsid(), apPassword());
    audio::announcePortal(apSsid());
    return;
  }

  startStation();
  if (!waitForWifi(20000)) {
    Serial.println("[main] WiFi connect timed out, will keep retrying");
    Serial.printf("[main] setup AP: %s  password: %s\n", apSsid().c_str(), apPassword().c_str());
  } else {
    Serial.printf("[main] WiFi ok, ip %s\n", WiFi.localIP().toString().c_str());
    audio::announceWifiConnected();
    if (store::isClaimed()) {
      Serial.println("[main] paired - local key required for LAN control");
    } else {
      Serial.printf("[main] not paired yet, pairing code: %s\n", store::ensurePairCode().c_str());
    }
  }

  cloud::begin();
}

void loop() {
  handleConfigButton();

  if (WiFi.status() == WL_CONNECTED) {
    offlineSince = 0;
    if (manualPortal) {
      // The AP came from the BOOT key, so a working station connection must
      // not close it straight away; it times out or ends with the reboot.
      if (millis() - manualPortalAt >= MANUAL_PORTAL_TIMEOUT_MS) {
        Serial.println("[main] setup portal timed out");
        device_http::stopPortal();
        manualPortal = false;
      }
    } else if (device_http::portalActive()) {
      device_http::stopPortal();
    }
    if (!mdnsStarted) {
      if (MDNS.begin(hostname().c_str())) {
        MDNS.addService("http", "tcp", LOCAL_HTTP_PORT);
        // Google's local fulfilment scans for this service; the TXT id ties
        // the discovered box to the controller registered in the account.
        MDNS.addService("fanctl", "tcp", LOCAL_HTTP_PORT);
        mdnsStarted = true;
        Serial.printf("[main] mDNS: http://%s.local/\n", hostname().c_str());
      } else {
        Serial.println("[main] mDNS failed to start");
        mdnsStarted = true;  // do not spam retries
      }
    }
    if (mdnsStarted && store::isClaimed() && mdnsTxtId != store::deviceId()) {
      // The board can be claimed while it is already running, so publish the
      // id as soon as it exists instead of waiting for the next reboot.
      MDNS.addServiceTxt("fanctl", "tcp", "id", store::deviceId());
      MDNS.addServiceTxt("fanctl", "tcp", "model", DEVICE_MODEL);
      mdnsTxtId = store::deviceId();
      Serial.printf("[main] mDNS fanctl id: %s\n", mdnsTxtId.c_str());
    }
  } else {
    if (offlineSince == 0) offlineSince = millis();
    if ((int32_t)(millis() - wifiRetryAt) >= 0 && store::hasWifiCredentials()) {
      Serial.println("[main] retrying WiFi");
      WiFi.begin(store::wifiSsid().c_str(), store::wifiPass().c_str());
      wifiRetryAt = millis() + WIFI_RETRY_INTERVAL_MS;
    }
    if (!device_http::portalActive() && offlineSince &&
        millis() - offlineSince > AP_FALLBACK_AFTER_MS) {
      Serial.printf("[main] offline too long, setup AP: %s  password: %s\n",
                    apSsid().c_str(), apPassword().c_str());
      device_http::startPortal(apSsid(), apPassword());
      audio::announcePortal(apSsid());
    }
  }

  device_http::loop();
  cloud::loop();
  audio::loop();
  ota::loop();
  // A latched alarm keeps ringing: once the chime falls silent, queue it again.
  static uint32_t lastAlarmChime = 0;
  if (display::alarmActive() && audio::idle() && millis() - lastAlarmChime >= 1000) {
    lastAlarmChime = millis();
    audio::announceAlarm();
  }
  tickPairingPrompt();
  relays.tick();
  display::tick();
  tickLed();
  logStatus();

  delay(2);
}
