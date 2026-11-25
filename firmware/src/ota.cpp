#include "ota.h"

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <HTTPUpdate.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>

#include <string.h>

#include "config.h"
#include "device_http.h"
#include "display.h"
#include "relays.h"
#include "storage.h"

namespace {

struct Release {
  String version;
  String url;
  String sha256;
};

uint32_t nextCheckAt = OTA_FIRST_CHECK_MS;
Release pending;
bool hasPending = false;

String manifestUrl() {
  String url = store::cloudTls() ? "https://" : "http://";
  url += store::cloudHost();
  uint16_t port = store::cloudPort();
  if ((store::cloudTls() && port != 443) || (!store::cloudTls() && port != 80)) {
    url += ':';
    url += String(port);
  }
  url += CLOUD_UPDATE_PATH;
  return url;
}

// Only the major.minor.patch prefix matters; a missing part counts as zero so
// "1.2" still compares sanely against "1.1.3".
bool versionNewer(const String& remote, const String& local) {
  int r[3] = {0, 0, 0};
  int l[3] = {0, 0, 0};
  if (sscanf(remote.c_str(), "%d.%d.%d", &r[0], &r[1], &r[2]) < 1) return false;
  sscanf(local.c_str(), "%d.%d.%d", &l[0], &l[1], &l[2]);
  for (int i = 0; i < 3; i++) {
    if (r[i] != l[i]) return r[i] > l[i];
  }
  return false;
}

void openSecure(WiFiClientSecure& client) {
  client.setInsecure();
  if (strlen(CLOUD_CA_CERT) > 0) client.setCACert(CLOUD_CA_CERT);
}

bool fetchRelease(Release& out) {
  WiFiClientSecure client;
  openSecure(client);

  HTTPClient http;
  http.setConnectTimeout(OTA_HTTP_TIMEOUT_MS);
  http.setTimeout(OTA_HTTP_TIMEOUT_MS);
  String url = manifestUrl();
  if (!http.begin(client, url)) {
    Serial.println("[ota] manifest url rejected");
    return false;
  }
  int code = http.GET();
  if (code != 200) {
    Serial.printf("[ota] manifest http %d\n", code);
    http.end();
    return false;
  }
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, http.getString());
  http.end();
  if (err) {
    Serial.printf("[ota] manifest parse: %s\n", err.c_str());
    return false;
  }

  JsonObjectConst fw = doc["firmware"];
  if (fw.isNull()) return false;
  out.version = (const char*)(fw["version"] | "");
  out.url = (const char*)(fw["url"] | "");
  out.sha256 = (const char*)(fw["sha256"] | "");
  if (!out.version.length() || !out.url.startsWith("http")) {
    Serial.println("[ota] manifest missing version/url");
    return false;
  }
  return true;
}

bool install(const Release& rel) {
  WiFiClientSecure client;
  openSecure(client);

  httpUpdate.rebootOnUpdate(false);
  httpUpdate.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
  // The default follows LED_BUILTIN, which shares the relay header area on
  // some boards; keep OTA away from any GPIO.
  httpUpdate.setLedPin(-1);

  Serial.printf("[ota] flashing %s\n", rel.version.c_str());
  if (rel.sha256.length() == 64) httpUpdate.setSHA256sum(rel.sha256);
  t_httpUpdate_return result = httpUpdate.update(client, rel.url, FW_VERSION);
  if (result == HTTP_UPDATE_OK) return true;
  if (result == HTTP_UPDATE_NO_UPDATES) {
    Serial.println("[ota] server has no new image");
  } else {
    Serial.printf("[ota] failed: %s\n", httpUpdate.getLastErrorString().c_str());
  }
  return false;
}

}  // namespace

void ota::loop() {
  uint32_t now = millis();
  if ((int32_t)(now - nextCheckAt) < 0) return;

  if (WiFi.status() != WL_CONNECTED || device_http::portalActive()) {
    nextCheckAt = now + OTA_RETRY_MS;
    return;
  }
  // An alarm is deliberately left alone: the reboot would silence it and the
  // owner may still be walking to the panel to press Stop.
  if (display::alarmActive()) {
    nextCheckAt = now + OTA_DEFER_MS;
    return;
  }

  if (!hasPending) {
    if (!fetchRelease(pending)) {
      nextCheckAt = now + OTA_RETRY_MS;
      return;
    }
    if (!versionNewer(pending.version, FW_VERSION)) {
      nextCheckAt = now + OTA_CHECK_INTERVAL_MS;
      return;
    }
    Serial.printf("[ota] %s available (running %s)\n", pending.version.c_str(), FW_VERSION);
    hasPending = true;
  }

  if (relays.mask() != 0) {
    Serial.println("[ota] relays are on, waiting");
    nextCheckAt = now + OTA_DEFER_MS;
    return;
  }

  if (install(pending)) {
    Serial.println("[ota] done, rebooting");
    delay(200);
    ESP.restart();
  }
  hasPending = false;
  nextCheckAt = now + OTA_RETRY_MS;
}
