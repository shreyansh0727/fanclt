#pragma once

#include <Arduino.h>

// Thin wrapper over NVS. All values live in one namespace so a factory reset
// is a single clear().
namespace store {

void begin();

String str(const char* key, const String& fallback = "");
void setStr(const char* key, const String& value);
bool flag(const char* key, bool fallback);
void setFlag(const char* key, bool value);
uint16_t u16(const char* key, uint16_t fallback);
void setU16(const char* key, uint16_t value);

// 12 uppercase hex chars derived from the eFuse MAC. Stable per chip.
String hardwareId();

// Pairing code, generated once and kept until the device is claimed.
String ensurePairCode();
void clearPairCode();

String deviceId();
String deviceKey();
// Random per-board secret proving this controller to the backend when it asks
// for a Firebase token. Generated once and kept in NVS.
String deviceSecret();
bool isClaimed();
void saveClaim(const String& deviceId, const String& deviceKey);
void clearClaim();

String wifiSsid();
String wifiPass();
bool hasWifiCredentials();
void saveWifi(const String& ssid, const String& pass);

// Per-channel type codes, one char per relay: 'f' fan, 'l' light, 's' socket,
// 'o' other. Pushed by the backend so announcements can name the load.
String channelTypes();
void saveChannelTypes(const String& types);
char channelType(uint8_t channel);

// Per-channel display names, pushed by the backend when the owner renames a
// channel in the app. Kept as one NVS string joined by 0x1F (names may contain
// spaces but not that separator).
String channelNames();
void saveChannelNames(const String& joined);
String channelName(uint8_t channel);

String cloudHost();
uint16_t cloudPort();
bool cloudTls();
bool hasServerOverride();
void saveServer(const String& host, uint16_t port, bool tls);
void clearServer();

uint16_t relayMask();
void saveRelayMask(uint16_t mask);

void factoryReset();

}  // namespace store
