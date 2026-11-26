#include "storage.h"

#include <Preferences.h>

#include "config.h"

namespace {

Preferences prefs;
bool ready = false;
String hwIdCache;

const char* kNamespace = "fanctl";
const char* ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";  // Crockford, no I/L/O/U

String makePairCode() {
  char raw[9];
  for (int i = 0; i < 8; i++) {
    raw[i] = ALPHABET[esp_random() % 32];
  }
  raw[8] = '\0';
  return String(raw).substring(0, 4) + "-" + String(raw).substring(4, 8);
}

}  // namespace

namespace store {

void begin() {
  if (!ready) {
    prefs.begin(kNamespace, false);
    ready = true;
  }
}

String str(const char* key, const String& fallback) {
  if (!ready) begin();
  return prefs.getString(key, fallback);
}

void setStr(const char* key, const String& value) {
  if (!ready) begin();
  prefs.putString(key, value);
}

bool flag(const char* key, bool fallback) {
  if (!ready) begin();
  return prefs.getBool(key, fallback);
}

void setFlag(const char* key, bool value) {
  if (!ready) begin();
  prefs.putBool(key, value);
}

uint16_t u16(const char* key, uint16_t fallback) {
  if (!ready) begin();
  return prefs.getUShort(key, fallback);
}

void setU16(const char* key, uint16_t value) {
  if (!ready) begin();
  prefs.putUShort(key, value);
}

String hardwareId() {
  if (hwIdCache.length() == 12) return hwIdCache;
  char buf[13];
  uint64_t mac = ESP.getEfuseMac() & 0xFFFFFFFFFFFFULL;
  snprintf(buf, sizeof(buf), "%012llX", (unsigned long long)mac);
  hwIdCache = String(buf);
  return hwIdCache;
}

String ensurePairCode() {
  String code = str("pair", "");
  if (code.length() == 0) {
    code = makePairCode();
    setStr("pair", code);
  }
  return code;
}

void clearPairCode() { setStr("pair", ""); }

String deviceId() { return str("dev_id", ""); }
String deviceKey() { return str("dev_key", ""); }
String assistantKey() { return str("asst_key", ""); }
void saveAssistantKey(const String& key) { setStr("asst_key", key); }

String deviceSecret() {
  String secret = str("dev_secret", "");
  if (secret.length() < 16) {
    char buf[33];
    for (int i = 0; i < 32; i++) buf[i] = "0123456789abcdef"[esp_random() % 16];
    buf[32] = '\0';
    secret = String(buf);
    setStr("dev_secret", secret);
  }
  return secret;
}

bool isClaimed() { return deviceKey().length() > 0 && deviceId().length() > 0; }

void saveClaim(const String& deviceId, const String& deviceKey) {
  setStr("dev_id", deviceId);
  setStr("dev_key", deviceKey);
  clearPairCode();
}

void clearClaim() {
  setStr("dev_id", "");
  setStr("dev_key", "");
  setStr("asst_key", "");
  clearPairCode();
}

String wifiSsid() { return str("ssid", ""); }
String wifiPass() { return str("pass", ""); }
bool hasWifiCredentials() { return wifiSsid().length() > 0; }

void saveWifi(const String& ssid, const String& pass) {
  setStr("ssid", ssid);
  setStr("pass", pass);
}

String channelTypes() {
  String types = str("ch_types", "");
  if ((int)types.length() != RELAY_COUNT) {
    // Same order as backend DEFAULT_CHANNEL_NAMES: fan, 3 lights, 2 sockets,
    // 2 spares.
    types = "flllssoo";
    while ((int)types.length() < RELAY_COUNT) types += 'o';
    types = types.substring(0, RELAY_COUNT);
  }
  return types;
}

void saveChannelTypes(const String& types) {
  if ((int)types.length() != RELAY_COUNT) return;
  if (str("ch_types", "") == types) return;  // avoid a flash write per reconnect
  setStr("ch_types", types);
}

char channelType(uint8_t channel) {
  String types = channelTypes();
  return channel < types.length() ? types[channel] : 'o';
}

const char* kDefaultChannelNames[] = { "Fan",     "Light 1", "Light 2", "Light 3",
                                       "Socket 1", "Socket 2", "Spare 1", "Spare 2" };
constexpr uint8_t kNameMax = 12;  // fit on the 128 px panel, keep NVS small
const char kNameSep = '\x1f';

String channelNames() {
  String joined = str("ch_names", "");
  if (joined.length() > 0) return joined;
  joined = "";
  for (uint8_t i = 0; i < RELAY_COUNT; i++) {
    if (i) joined += kNameSep;
    joined += i < 8 ? kDefaultChannelNames[i] : String("Channel ") + (i + 1);
  }
  return joined;
}

void saveChannelNames(const String& joined) {
  String clean = joined;
  clean.replace("\r", " ");
  clean.replace("\n", " ");
  String out;
  uint8_t index = 0;
  int start = 0;
  while (index < RELAY_COUNT) {
    int end = clean.indexOf(kNameSep, start);
    String name = end < 0 ? clean.substring(start) : clean.substring(start, end);
    name.trim();
    if (name.length() > kNameMax) name = name.substring(0, kNameMax);
    if (name.length() == 0) {
      name = index < 8 ? kDefaultChannelNames[index] : String("Channel ") + (index + 1);
    }
    if (index) out += kNameSep;
    out += name;
    index++;
    if (end < 0) break;
    start = end + 1;
  }
  while (index < RELAY_COUNT) {
    out += kNameSep;
    out += index < 8 ? kDefaultChannelNames[index] : String("Channel ") + (index + 1);
    index++;
  }
  if (str("ch_names", "") == out) return;  // avoid a flash write per reconnect
  setStr("ch_names", out);
}

String channelName(uint8_t channel) {
  String all = channelNames();
  int start = 0;
  for (uint8_t i = 0; i < RELAY_COUNT; i++) {
    int end = all.indexOf(kNameSep, start);
    if (i == channel) {
      return end < 0 ? all.substring(start) : all.substring(start, end);
    }
    if (end < 0) break;
    start = end + 1;
  }
  return String();
}

String cloudHost() {
  String host = str("srv_host", "");
  return host.length() ? host : String(CLOUD_HOST);
}

uint16_t cloudPort() {
  uint16_t port = u16("srv_port", 0);
  return port ? port : (uint16_t)CLOUD_PORT;
}

bool cloudTls() {
  String value = str("srv_tls", "");
  if (value.length() == 0) return CLOUD_USE_TLS != 0;
  return value == "1";
}

bool hasServerOverride() { return str("srv_host", "").length() > 0; }

void saveServer(const String& host, uint16_t port, bool tls) {
  setStr("srv_host", host);
  setU16("srv_port", port);
  setStr("srv_tls", tls ? "1" : "0");
}

void clearServer() {
  if (!ready) begin();
  prefs.remove("srv_host");
  prefs.remove("srv_port");
  prefs.remove("srv_tls");
}

uint16_t relayMask() { return u16("relays", 0); }
void saveRelayMask(uint16_t mask) { setU16("relays", mask); }

void factoryReset() {
  if (!ready) begin();
  prefs.clear();
  clearClaim();
}

}  // namespace store
