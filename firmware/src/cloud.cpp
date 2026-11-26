#include "cloud.h"

#include <ArduinoJson.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>

#include <stdlib.h>
#include <string.h>

#include "audio.h"
#include "config.h"
#include "display.h"
#include "relays.h"
#include "storage.h"

namespace {

// --------------------------------------------------------------- endpoints
struct Endpoint {
  String host;
  uint16_t port = 0;
  bool tls = false;
  String ns;      // database namespace, only used by the emulator
  String apiKey;  // Firebase Web API key (public), only used for sign-in
  bool valid = false;
};

struct Response {
  int code = -1;
  String body;
};

Endpoint backendEp;
Endpoint rtdbEp;
Endpoint authEp;

cloud::Status linkStatus = cloud::OFFLINE;

enum Phase { P_IDLE, P_IDENTIFY, P_CONNECT, P_STREAM };
Phase phase = P_IDLE;

String authToken;
uint32_t authTokenDeadline = 0;
uint32_t nextAttemptAt = 0;
uint32_t retryDelay = STREAM_RETRY_MIN_MS;

bool primed = false;          // first database snapshot has been consumed
bool streamCancelled = false;
bool greeted = false;
bool stateDirty = true;
bool alarmDirty = false;
bool lastSent[RELAY_COUNT] = {false};
uint32_t lastStateAt = 0;
uint32_t lastInfoAt = 0;
uint32_t identifyUntil = 0;
uint32_t restartAt = 0;
uint32_t lastStreamByteAt = 0;

// The controller has no RTC or NTP client, so the wall clock is derived from
// the Date header of any HTTP response. The backend compares info.ts against
// real time to decide whether the device is online.
int64_t clockOffsetMs = 0;
bool clockKnown = false;

String recentIds[8];
uint8_t recentIdCount = 0;
uint8_t recentIdCursor = 0;

// An ack whose write failed (dropped socket, token refresh in flight) is
// resend from the main loop instead of being lost - the backend treats a
// missing ack as "the controller never applied it", which shows up in the app
// as a failed toggle while the relay really did switch.
String ackRetryId;
String ackRetryError;
bool ackRetryOk = false;
uint8_t ackRetryCount = 0;
uint32_t ackRetryAt = 0;

// Server-sent event parser buffers.
String sseLine;
String sseEvent;
String sseData;

// ------------------------------------------------------------- prototypes
bool fetchIdentity();
bool openStream();
void closeStream();
void scheduleRetry();
void pumpStream();
void writeState();
void writeInfo();
bool sendAck(const String& id, bool ok, const char* error);
bool writeJson(const char* method, const String& suffix, const String& body);
void handleCommand(const String& id, JsonObjectConst command);
void handleEvent(const String& event, JsonObjectConst doc);
void dispatchSseEvent();
void handleSseLine(const String& line);
void sseByte(char ch);
void noteServerDate(const String& value);

// ------------------------------------------------------------------ socket
class Wire {
 public:
  Wire() {
    secure.setInsecure();
    if (strlen(CLOUD_CA_CERT) > 0) secure.setCACert(CLOUD_CA_CERT);
  }

  // Keeps the socket open between calls: the TLS handshake alone costs several
  // hundred milliseconds on this chip, which was most of a command's latency.
  // The connection is only replaced when the endpoint changes or the server
  // has closed it.
  bool open(const Endpoint& ep, uint32_t timeoutMs) {
    if (ready && tls == ep.tls && port == ep.port && host == ep.host && current->connected()) {
      return true;
    }
    close();
    if (ep.tls) {
      if (!secure.connect(ep.host.c_str(), ep.port, (int32_t)timeoutMs)) return false;
      current = &secure;
    } else {
      if (!plain.connect(ep.host.c_str(), ep.port, (int32_t)timeoutMs)) return false;
      current = &plain;
    }
    host = ep.host;
    port = ep.port;
    tls = ep.tls;
    ready = true;
    return true;
  }

  bool usable() { return ready && current->connected(); }

  WiFiClient& client() { return *current; }

  void close() {
    secure.stop();
    plain.stop();
    current = &plain;
    ready = false;
  }

 private:
  WiFiClient plain;
  WiFiClientSecure secure;
  WiFiClient* current = &plain;
  String host;
  uint16_t port = 0;
  bool tls = false;
  bool ready = false;
};

Wire callWire;    // short REST calls (pairing, acks, telemetry)
Wire streamWire;  // the long-lived event stream

// ------------------------------------------------------------------- clock
int64_t daysFromCivil(int year, int month, int day) {
  year -= (month <= 2) ? 1 : 0;
  int era = (year >= 0 ? year : year - 399) / 400;
  unsigned yoe = (unsigned)(year - era * 400);
  unsigned doy = (unsigned)((153 * (month + (month > 2 ? -3 : 9)) + 2) / 5 + day - 1);
  unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  return (int64_t)era * 146097 + (int64_t)doe - 719468;
}

void noteServerDate(const String& value) {
  // "Sun, 06 Nov 1994 08:49:37 GMT"
  char mon[4] = {0, 0, 0, 0};
  int day = 0, year = 0, hh = 0, mm = 0, ss = 0;
  if (sscanf(value.c_str(), "%*3s, %d %3s %d %d:%d:%d", &day, mon, &year, &hh, &mm, &ss) != 6) {
    return;
  }
  static const char* kMonths[12] = {"Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"};
  int month = -1;
  for (int i = 0; i < 12; i++) {
    if (strncmp(mon, kMonths[i], 3) == 0) month = i;
  }
  if (month < 0 || year < 2020 || day < 1 || day > 31) return;
  int64_t days = daysFromCivil(year, month + 1, day);
  int64_t epochMs = (days * 86400 + hh * 3600 + mm * 60 + ss) * 1000;
  clockOffsetMs = epochMs - (int64_t)millis();
  clockKnown = true;
}

int64_t nowMs() { return clockKnown ? clockOffsetMs + (int64_t)millis() : 0; }

// ------------------------------------------------------------ HTTP reading
bool readLine(WiFiClient& c, String& line, uint32_t timeoutMs) {
  line = "";
  uint32_t start = millis();
  while (millis() - start < timeoutMs) {
    while (c.available() > 0) {
      int value = c.read();
      if (value < 0) break;
      char ch = (char)value;
      if (ch == '\n') {
        if (line.endsWith("\r")) line.remove(line.length() - 1);
        return true;
      }
      if (line.length() < 1024) line += ch;
    }
    if (!c.connected() && c.available() <= 0) return false;
    delay(1);
  }
  return false;
}

int readHead(WiFiClient& c, uint32_t timeoutMs, bool& chunked, int64_t& length) {
  chunked = false;
  length = -1;
  String line;
  if (!readLine(c, line, timeoutMs)) return -1;

  int code = -1;
  if (line.startsWith("HTTP/")) {
    int space = line.indexOf(' ');
    if (space > 0) code = line.substring(space + 1).toInt();
  }

  while (readLine(c, line, timeoutMs)) {
    if (line.length() == 0) break;
    int colon = line.indexOf(':');
    if (colon <= 0) continue;
    String name = line.substring(0, colon);
    name.toLowerCase();
    String value = line.substring(colon + 1);
    value.trim();
    if (name == "transfer-encoding" && value.indexOf("chunked") >= 0) {
      chunked = true;
    } else if (name == "content-length") {
      length = value.toInt();
    } else if (name == "date") {
      noteServerDate(value);
    }
  }
  return code;
}

enum ChunkPhase { CH_SIZE, CH_DATA, CH_CRLF, CH_TRAILER };

struct Body {
  bool chunked = false;
  ChunkPhase phase = CH_SIZE;
  size_t remaining = 0;
  String sizeLine;
  String trailer;
  void (*sink)(char) = nullptr;
  bool done = false;

  void reset(bool isChunked, void (*target)(char)) {
    chunked = isChunked;
    phase = CH_SIZE;
    remaining = 0;
    sizeLine = "";
    trailer = "";
    sink = target;
    done = false;
  }

  void feed(char ch) {
    if (done) return;
    if (!chunked) {
      sink(ch);
      return;
    }
    switch (phase) {
      case CH_SIZE:
        if (ch == '\n') {
          sizeLine.trim();
          int semi = sizeLine.indexOf(';');
          if (semi >= 0) sizeLine = sizeLine.substring(0, semi);
          remaining = (size_t)strtoul(sizeLine.c_str(), nullptr, 16);
          sizeLine = "";
          phase = remaining == 0 ? CH_TRAILER : CH_DATA;
        } else if (ch != '\r') {
          sizeLine += ch;
        }
        break;
      case CH_DATA:
        sink(ch);
        if (--remaining == 0) phase = CH_CRLF;
        break;
      case CH_CRLF:
        if (ch == '\n') phase = CH_SIZE;
        break;
      case CH_TRAILER:
        if (ch == '\n') {
          if (trailer.length() == 0) done = true;
          trailer = "";
        } else if (ch != '\r') {
          trailer += ch;
        }
        break;
    }
  }
};

Body streamBody;

String* bodySink = nullptr;
void appendToBody(char ch) {
  if (bodySink) *bodySink += ch;
}

bool readBody(WiFiClient& c, Body& body, int64_t length, uint32_t timeoutMs) {
  size_t received = 0;
  uint32_t lastByteAt = millis();
  while (millis() - lastByteAt < timeoutMs) {
    if (c.available() > 0) {
      int value = c.read();
      if (value < 0) break;
      lastByteAt = millis();
      if (!body.chunked) received++;
      body.feed((char)value);
      if (body.done) return true;
      if (!body.chunked && length >= 0 && (int64_t)received >= length) return true;
    } else if (!c.connected()) {
      return true;
    } else {
      delay(1);
    }
  }
  return false;
}

Response httpCallOnce(const Endpoint& ep, const char* method, const String& path,
                      const String& payload) {
  Response out;
  if (!callWire.open(ep, 6000)) {
    Serial.printf("[cloud] cannot reach %s:%u\n", ep.host.c_str(), (unsigned)ep.port);
    return out;
  }

  String hostHeader = ep.host;
  if ((ep.tls && ep.port != 443) || (!ep.tls && ep.port != 80)) {
    hostHeader += ':';
    hostHeader += String(ep.port);
  }

  String req;
  req.reserve(256 + payload.length());
  req += method;
  req += ' ';
  req += path;
  req += " HTTP/1.1\r\nHost: ";
  req += hostHeader;
  req += "\r\nUser-Agent: FanCtl/" FW_VERSION "\r\nAccept: application/json\r\nConnection: keep-alive\r\n";
  if (payload.length()) {
    req += "Content-Type: application/json\r\nContent-Length: ";
    req += String(payload.length());
    req += "\r\n";
  }
  req += "\r\n";
  req += payload;

  callWire.client().print(req);

  bool chunked = false;
  int64_t length = -1;
  out.code = readHead(callWire.client(), 8000, chunked, length);
  if (out.code > 0) {
    if (!chunked && length < 0) {
      // Without framing the body would block until the read timeout, so drop
      // the socket instead of guessing.
      out.code = -1;
    } else {
      Body body;
      body.reset(chunked, &appendToBody);
      bodySink = &out.body;
      if (!readBody(callWire.client(), body, length, 8000)) out.code = -1;
      bodySink = nullptr;
    }
  }
  if (out.code <= 0 || !callWire.usable()) callWire.close();
  return out;
}

Response httpCall(const Endpoint& ep, const char* method, const String& path,
                  const String& payload) {
  if (!ep.valid) {
    Serial.println("[cloud] endpoint missing");
    return Response();
  }
  Response out = httpCallOnce(ep, method, path, payload);
  if (out.code > 0) return out;

  // A kept-alive socket that the server closed after its idle period fails on
  // the first byte; one clean retry hides that from every caller.
  callWire.close();
  return httpCallOnce(ep, method, path, payload);
}

String jsonBody(const JsonDocument& doc) {
  String out;
  serializeJson(doc, out);
  return out;
}

// ---------------------------------------------------------------- database
bool writeJson(const char* method, const String& suffix, const String& body) {
  if (!authToken.length() || !rtdbEp.valid) return false;
  String path = "/devices/" + store::deviceId() + suffix + ".json?auth=" + authToken;
  if (rtdbEp.ns.length()) path += "&ns=" + rtdbEp.ns;
  Response r = httpCall(rtdbEp, method, path, body);
  if (r.code == 401) {
    Serial.println("[cloud] database rejected the token, signing in again");
    authToken = "";
  } else if (r.code != 200 && r.code > 0) {
    Serial.printf("[cloud] %s %s -> %d\n", method, suffix.c_str(), r.code);
  } else if (r.code <= 0) {
    // A live TCP write can succeed while the long-lived stream socket is
    // already dead (NAT timeout, AP roam). Reopen it instead of sitting on a
    // stream that will never deliver another command.
    streamCancelled = true;
  }
  return r.code == 200;
}

void writeState() {
  JsonDocument doc;
  JsonArray arr = doc.to<JsonArray>();
  for (uint8_t i = 0; i < RELAY_COUNT; i++) arr.add(relays.get(i));
  writeJson("PUT", "/state", jsonBody(doc));
  relays.snapshot(lastSent);
  stateDirty = false;
  lastStateAt = millis();
}

void writeInfo() {
  JsonDocument doc;
  doc["ip"] = WiFi.localIP().toString();
  doc["port"] = LOCAL_HTTP_PORT;
  doc["fw"] = FW_VERSION;
  doc["model"] = DEVICE_MODEL;
  doc["rssi"] = WiFi.RSSI();
  doc["uptime_s"] = millis() / 1000;
  doc["heap"] = ESP.getFreeHeap();
  // The app shows "Stop alarm" from this flag; it clears on boot because the
  // display starts with no reaction.
  doc["alarm"] = display::alarmActive();
  int64_t ts = nowMs();
  if (ts > 0) doc["ts"] = ts;
  writeJson("PATCH", "/info", jsonBody(doc));
  alarmDirty = false;
  lastInfoAt = millis();
}

bool sendAck(const String& id, bool ok, const char* error) {
  JsonDocument doc;
  doc["ok"] = ok;
  if (!ok && error) doc["error"] = error;
  JsonArray arr = doc["state"].to<JsonArray>();
  for (uint8_t i = 0; i < RELAY_COUNT; i++) arr.add(relays.get(i));
  int64_t ts = nowMs();
  if (ts > 0) doc["ts"] = ts;
  const bool sent = writeJson("PUT", "/acks/" + id, jsonBody(doc));
  if (!sent && ackRetryId.length() == 0) {
    ackRetryId = id;
    ackRetryOk = ok;
    ackRetryError = error ? String(error) : String();
    ackRetryCount = 0;
    ackRetryAt = millis() + 600;
  }
  return sent;
}

// ----------------------------------------------------------------- commands
// The last 8 ids are remembered so a command replayed after a reconnect is
// acked again but not applied twice (important for "toggle").
bool isDuplicate(const String& id) {
  if (id.length() == 0) return false;
  for (uint8_t i = 0; i < recentIdCount; i++) {
    if (recentIds[i] == id) return true;
  }
  recentIds[recentIdCursor] = id;
  recentIdCursor = (uint8_t)((recentIdCursor + 1) % 8);
  if (recentIdCount < 8) recentIdCount++;
  return false;
}

void handleCommand(const String& id, JsonObjectConst command) {
  if (isDuplicate(id)) {
    Serial.println("[cloud] duplicate command acked without replay");
    sendAck(id, true, nullptr);
    return;
  }

  String action = command["action"] | "";

  if (action == "identify") {
    uint32_t seconds = command["seconds"] | 5;
    if (seconds > 60) seconds = 60;
    identifyUntil = millis() + seconds * 1000;
    if (seconds) audio::announceIdentify();
    sendAck(id, true, nullptr);
    return;
  }

  // The backend rotates this credential for the voice-assistant local app;
  // it is not a relay command, so nothing else here changes.
  if (action == "set_assistant_key") {
    String key = command["key"] | "";
    key.trim();
    if (key.length() < 16) {
      sendAck(id, false, "bad_key");
      return;
    }
    store::saveAssistantKey(key);
    Serial.println("[cloud] assistant LAN key stored");
    sendAck(id, true, nullptr);
    return;
  }

  // Any other reaction replaces the latched alarm scene, so drop the siren with
  // it and let the app hide its Stop button.
  const bool otherReaction = action == "pet" || action == "feed" || action == "play" ||
                             action == "sleep" || action == "wake" || action == "reminder";
  if (otherReaction && display::alarmActive()) {
    audio::stop();
    alarmDirty = true;
  }

  bool ok = true;
  bool relayTouched = false;
  const char* error = nullptr;
  if (action == "all") {
    relays.setAll(command["on"] | false);
    relayTouched = true;
  } else if (action == "set" || action == "toggle") {
    int ch = command["ch"] | -1;
    if (ch < 0 || ch >= RELAY_COUNT) {
      ok = false;
      error = "bad_channel";
    } else if (action == "set") {
      relays.set((uint8_t)ch, command["on"] | false);
      relayTouched = true;
    } else {
      relays.toggle((uint8_t)ch);
      relayTouched = true;
    }
  } else if (action == "pet") {
    display::react(display::Reaction::Pet);
    audio::announcePet();
  } else if (action == "feed") {
    display::react(display::Reaction::Feed);
    audio::announceFeed();
  } else if (action == "play") {
    display::react(display::Reaction::Play);
    audio::announcePlay();
  } else if (action == "sleep") {
    display::react(display::Reaction::Sleep);
    audio::announceSleep();
  } else if (action == "wake") {
    display::react(display::Reaction::Wake);
    audio::announceWake();
  } else if (action == "alarm") {
    display::react(display::Reaction::Alarm);
    audio::announceAlarm();
    alarmDirty = true;
  } else if (action == "alarm_stop") {
    display::dismissAlarm();
    audio::stop();
    alarmDirty = true;
  } else if (action == "reminder") {
    display::react(display::Reaction::Reminder);
    audio::announceReminder();
  } else {
    ok = false;
    error = "bad_action";
  }
  if (ok && relayTouched) stateDirty = true;
  sendAck(id, ok, error);
}

void doUnclaim() {
  Serial.println("[cloud] owner removed this controller, clearing pairing");
  store::clearClaim();
  authToken = "";
  restartAt = millis() + 700;
}

// -------------------------------------------------------------- event stream
// Owner-side edits land in the database node; keep a compact copy of the
// channel kinds so announcements can say "fan"/"light" instead of "channel N",
// plus the names the owner set in the app so the panel can show them.
void applyChannels(JsonArrayConst channels) {
  char types[RELAY_COUNT + 1];
  String names;
  int count = (int)channels.size();
  for (uint8_t i = 0; i < RELAY_COUNT; i++) {
    const char* kind = i < count ? (channels[i]["type"] | "other") : "other";
    types[i] = (kind[0] == 'f' || kind[0] == 'l' || kind[0] == 's') ? kind[0] : 'o';

    if (i) names += '\x1f';
    const char* name = i < count ? (channels[i]["name"] | "") : "";
    names += name;
  }
  types[RELAY_COUNT] = '\0';
  store::saveChannelTypes(String(types));
  store::saveChannelNames(names);
  Serial.printf("[cloud] channel types %s\n", types);
  String logged = store::channelNames();
  logged.replace('\x1f', '/');
  Serial.printf("[cloud] channel names %s\n", logged.c_str());
}

void handleEvent(const String& event, JsonObjectConst doc) {
  String path = doc["path"] | "";
  JsonVariantConst data = doc["data"];

  if (path == "/" || path.length() == 0) {
    if (data.is<JsonObjectConst>()) {
      JsonObjectConst root = data.as<JsonObjectConst>();
      if (root["unclaim"].is<bool>() && root["unclaim"].as<bool>()) {
        doUnclaim();
        return;
      }
      if (root["channels"].is<JsonArrayConst>()) {
        applyChannels(root["channels"].as<JsonArrayConst>());
      }
    }
    // Anything left in the first snapshot is stale (the backend gives up on a
    // command after a few seconds), so only live commands are executed.
    primed = true;
    return;
  }

  // A rename in the app PATCHes the node; the stream delivers the new array
  // here, so the panel updates without waiting for a reconnect.
  if (path == "/channels") {
    if (data.is<JsonArrayConst>()) applyChannels(data.as<JsonArrayConst>());
    return;
  }

  if (path == "/unclaim") {
    if (data.is<bool>() && data.as<bool>()) doUnclaim();
    return;
  }

  if (event == "put" && path.startsWith("/commands/")) {
    String id = path.substring(strlen("/commands/"));
    if (!primed || id.length() == 0) return;
    if (!data.is<JsonObjectConst>()) return;
    handleCommand(id, data.as<JsonObjectConst>());
    return;
  }

  if (event == "patch" && path.startsWith("/commands") && data.is<JsonObjectConst>()) {
    if (!primed) return;
    JsonObjectConst commands = data.as<JsonObjectConst>();
    for (JsonPairConst entry : commands) {
      if (!entry.value().is<JsonObjectConst>()) continue;
      handleCommand(entry.key().c_str(), entry.value().as<JsonObjectConst>());
    }
  }
}

void dispatchSseEvent() {
  if (sseData.length()) {
    String event = sseEvent.length() ? sseEvent : String("message");
    if (event == "put" || event == "patch") {
      JsonDocument doc;
      DeserializationError err = deserializeJson(doc, sseData);
      if (!err) {
        handleEvent(event, doc.as<JsonObjectConst>());
      } else {
        Serial.printf("[cloud] bad stream json: %s\n", err.c_str());
      }
    } else if (event == "cancel" || event == "auth_revoked") {
      Serial.printf("[cloud] stream %s\n", event.c_str());
      streamCancelled = true;
    }
  }
  sseEvent = "";
  sseData = "";
}

void handleSseLine(const String& line) {
  if (line.length() == 0) {
    dispatchSseEvent();
    return;
  }
  if (line[0] == ':') return;  // comment
  if (line.startsWith("event:")) {
    sseEvent = line.substring(6);
    sseEvent.trim();
    return;
  }
  if (line.startsWith("data:")) {
    String part = line.substring(5);
    if (part.startsWith(" ")) part.remove(0, 1);
    if (sseData.length()) sseData += '\n';
    if (sseData.length() < 8192) sseData += part;
  }
}

void sseByte(char ch) {
  if (ch == '\n') {
    if (sseLine.endsWith("\r")) sseLine.remove(sseLine.length() - 1);
    handleSseLine(sseLine);
    sseLine = "";
  } else if (sseLine.length() < 8192) {
    sseLine += ch;
  }
}

// ------------------------------------------------------------------- phases
void scheduleRetry() {
  nextAttemptAt = millis() + retryDelay;
  uint32_t doubled = retryDelay * 2;
  retryDelay = doubled > STREAM_RETRY_MAX_MS ? STREAM_RETRY_MAX_MS : doubled;
  phase = P_CONNECT;
}

bool endpointFrom(JsonVariantConst obj, const char* fallbackHost, uint16_t fallbackPort,
                  bool fallbackTls, Endpoint& ep) {
  ep = Endpoint();
  const char* host = obj["host"] | "";
  ep.host = (host && host[0]) ? String(host) : String(fallbackHost);
  ep.port = obj["port"].is<int>() ? (uint16_t)obj["port"].as<int>() : fallbackPort;
  ep.tls = obj["tls"].is<bool>() ? obj["tls"].as<bool>() : fallbackTls;
  ep.valid = ep.host.length() > 0 && ep.port > 0;
  return ep.valid;
}

String errorCode(const Response& r) {
  JsonDocument doc;
  if (deserializeJson(doc, r.body)) return String();
  return String(doc["error"]["code"] | "");
}

bool fetchIdentity() {
  // An unclaimed controller announces itself on every attempt: the backend
  // only accepts a claim while the registration is recent.
  if (!store::isClaimed()) {
    JsonDocument reg;
    reg["hw"] = store::hardwareId();
    reg["code"] = store::ensurePairCode();
    reg["secret"] = store::deviceSecret();
    reg["fw"] = FW_VERSION;
    reg["model"] = DEVICE_MODEL;
    reg["channels"] = RELAY_COUNT;
    Response r = httpCall(backendEp, "POST", CLOUD_REGISTER_PATH, jsonBody(reg));
    if (r.code == 409) {
      // The owner claimed between two announcements. Our secret still earns a
      // device key from /device/token, so fall through instead of giving up.
      Serial.println("[cloud] claimed already, asking for a token instead");
    } else if (r.code != 200) {
      Serial.printf("[cloud] register -> %d\n", r.code);
      return false;
    } else {
      linkStatus = cloud::WAITING_CLAIM;
    }
  }

  JsonDocument req;
  if (store::isClaimed()) {
    req["device_id"] = store::deviceId();
    req["device_key"] = store::deviceKey();
    req["hw"] = store::hardwareId();
  } else {
    req["hw"] = store::hardwareId();
    req["secret"] = store::deviceSecret();
  }

  Response t = httpCall(backendEp, "POST", CLOUD_TOKEN_PATH, jsonBody(req));
  if (t.code == 409) {
    if (errorCode(t) == "not_claimed") {
      linkStatus = cloud::WAITING_CLAIM;
    } else {
      linkStatus = cloud::ERROR_CLAIMED;
    }
    return false;
  }
  if (t.code == 401) {
    Serial.println("[cloud] backend rejected this controller's key");
    linkStatus = cloud::ERROR_CLAIMED;
    return false;
  }
  if (t.code != 200) {
    Serial.printf("[cloud] token -> %d\n", t.code);
    return false;
  }

  JsonDocument doc;
  if (deserializeJson(doc, t.body)) {
    Serial.println("[cloud] token response unreadable");
    return false;
  }
  String deviceId = doc["device_id"] | "";
  String deviceKey = doc["device_key"] | "";
  String customToken = doc["custom_token"] | "";
  if (!deviceId.length() || !deviceKey.length() || !customToken.length()) {
    Serial.println("[cloud] token response incomplete");
    return false;
  }

  bool firstTime = !store::isClaimed();
  if (firstTime || store::deviceId() != deviceId) store::saveClaim(deviceId, deviceKey);
  if (firstTime) {
    Serial.println("[cloud] paired to an owner");
    audio::stop();
    audio::announcePaired();
  }

  if (!endpointFrom(doc["rtdb"], RTDB_HOST, RTDB_PORT, RTDB_USE_TLS != 0, rtdbEp)) {
    Serial.println("[cloud] backend did not advertise a database (set FANCTL_DATABASE_URL)");
    return false;
  }
  if (doc["rtdb"]["ns"].is<const char*>()) {
    rtdbEp.ns = doc["rtdb"]["ns"].as<String>();
  } else if (strlen(RTDB_NAMESPACE) > 0) {
    rtdbEp.ns = RTDB_NAMESPACE;
  }

  if (!endpointFrom(doc["auth"], "", 443, true, authEp)) {
    Serial.println("[cloud] backend did not advertise a sign-in endpoint");
    return false;
  }
  const char* apiKey = doc["auth"]["api_key"] | "";
  if (apiKey && apiKey[0]) {
    authEp.apiKey = apiKey;
  } else if (strlen(FIREBASE_API_KEY) > 0) {
    authEp.apiKey = FIREBASE_API_KEY;
  }
  if (!authEp.apiKey.length()) {
    Serial.println("[cloud] no Firebase Web API key (set FANCTL_FIREBASE_API_KEY)");
    return false;
  }

  JsonDocument signIn;
  signIn["token"] = customToken;
  signIn["returnSecureToken"] = true;
  Response s = httpCall(authEp, "POST",
                        String("/v1/accounts:signInWithCustomToken?key=") + authEp.apiKey,
                        jsonBody(signIn));
  if (s.code != 200) {
    Serial.printf("[cloud] sign-in -> %d %s\n", s.code, s.body.c_str());
    return false;
  }

  JsonDocument session;
  if (deserializeJson(session, s.body)) return false;
  const char* idToken = session["idToken"] | "";
  if (!idToken || !*idToken) {
    Serial.println("[cloud] sign-in returned no token");
    return false;
  }
  authToken = idToken;

  uint32_t expiresIn = 3600;
  if (session["expiresIn"].is<const char*>()) {
    expiresIn = (uint32_t)String(session["expiresIn"].as<const char*>()).toInt();
  } else if (session["expiresIn"].is<uint32_t>()) {
    expiresIn = session["expiresIn"].as<uint32_t>();
  }
  if (expiresIn < 600) expiresIn = 3600;
  uint32_t lifetimeMs = expiresIn * 1000;
  authTokenDeadline = millis() + lifetimeMs -
                      (lifetimeMs > TOKEN_REFRESH_MARGIN_MS * 2 ? TOKEN_REFRESH_MARGIN_MS : lifetimeMs / 2);

  linkStatus = cloud::CONNECTING;
  Serial.printf("[cloud] signed in, device %s\n", deviceId.c_str());
  return true;
}

bool openStream() {
  if (!authToken.length() || !rtdbEp.valid) return false;
  if (!streamWire.open(rtdbEp, 8000)) {
    Serial.printf("[cloud] cannot reach the database at %s:%u\n", rtdbEp.host.c_str(),
                  (unsigned)rtdbEp.port);
    return false;
  }

  String path = "/devices/" + store::deviceId() + ".json?auth=" + authToken;
  if (rtdbEp.ns.length()) path += "&ns=" + rtdbEp.ns;

  String hostHeader = rtdbEp.host;
  if ((rtdbEp.tls && rtdbEp.port != 443) || (!rtdbEp.tls && rtdbEp.port != 80)) {
    hostHeader += ':';
    hostHeader += String(rtdbEp.port);
  }

  String req;
  req.reserve(256);
  req += "GET ";
  req += path;
  req += " HTTP/1.1\r\nHost: ";
  req += hostHeader;
  req += "\r\nUser-Agent: FanCtl/" FW_VERSION "\r\nAccept: text/event-stream\r\nConnection: keep-alive\r\n\r\n";
  streamWire.client().print(req);

  bool chunked = false;
  int64_t length = -1;
  int code = readHead(streamWire.client(), 8000, chunked, length);
  if (code != 200) {
    Serial.printf("[cloud] stream -> %d\n", code);
    streamWire.close();
    if (code == 401 || code == 403) authToken = "";
    return false;
  }

  streamBody.reset(chunked, &sseByte);
  sseLine = "";
  sseEvent = "";
  sseData = "";
  primed = false;
  streamCancelled = false;
  lastStreamByteAt = millis();
  lastInfoAt = 0;
  lastStateAt = 0;
  stateDirty = true;
  linkStatus = cloud::READY;
  retryDelay = STREAM_RETRY_MIN_MS;

  Serial.printf("[cloud] streaming devices/%s\n", store::deviceId().c_str());
  if (!greeted) {
    greeted = true;
    audio::announceReady();
  }
  return true;
}

void closeStream() {
  streamWire.close();
  if (linkStatus == cloud::READY) linkStatus = cloud::CONNECTING;
}

void pumpStream() {
  WiFiClient& c = streamWire.client();
  uint32_t start = millis();
  while (millis() - start < 20) {
    int available = c.available();
    if (available <= 0) break;
    for (int i = 0; i < available && millis() - start < 20; i++) {
      int value = c.read();
      if (value < 0) break;
      lastStreamByteAt = millis();
      streamBody.feed((char)value);
      if (streamBody.done) break;
    }
    if (streamBody.done) break;
  }

  if (streamCancelled) {
    Serial.println("[cloud] stream cancelled, signing in again");
    streamCancelled = false;
    closeStream();
    authToken = "";
    phase = P_IDENTIFY;
    nextAttemptAt = millis();
    return;
  }

  if (streamBody.done || !c.connected()) {
    Serial.println("[cloud] stream closed");
    closeStream();
    scheduleRetry();
    return;
  }

  if (millis() - lastStreamByteAt > STREAM_IDLE_TIMEOUT_MS) {
    Serial.println("[cloud] stream went quiet, reconnecting");
    closeStream();
    scheduleRetry();
  }
}

}  // namespace

namespace cloud {

void begin() {
  backendEp = Endpoint();
  backendEp.host = store::cloudHost();
  backendEp.port = store::cloudPort();
  backendEp.tls = store::cloudTls();
  backendEp.valid = backendEp.host.length() > 0 && backendEp.port > 0;

  phase = P_IDLE;
  linkStatus = OFFLINE;
  authToken = "";
  authTokenDeadline = 0;
  nextAttemptAt = millis();
  retryDelay = STREAM_RETRY_MIN_MS;
  Serial.printf("[cloud] backend %s://%s:%u\n", backendEp.tls ? "https" : "http",
                backendEp.host.c_str(), (unsigned)backendEp.port);
}

void loop() {
  if (restartAt && (int32_t)(millis() - restartAt) >= 0) {
    ESP.restart();
  }

  if (WiFi.status() != WL_CONNECTED) {
    if (phase == P_STREAM) closeStream();
    if (phase != P_IDLE || linkStatus != OFFLINE) {
      phase = P_IDLE;
      authToken = "";
      linkStatus = OFFLINE;
    }
    return;
  }

  switch (phase) {
    case P_IDLE:
      phase = P_IDENTIFY;
      nextAttemptAt = millis();
      break;

    case P_IDENTIFY:
      if ((int32_t)(millis() - nextAttemptAt) < 0) break;
      if (linkStatus != WAITING_CLAIM && linkStatus != ERROR_CLAIMED) {
        linkStatus = CONNECTING;
      }
      if (fetchIdentity()) {
        phase = P_CONNECT;
        nextAttemptAt = millis();
      } else {
        uint32_t delayMs = linkStatus == ERROR_CLAIMED ? CLAIMED_RETRY_MS : IDENTIFY_RETRY_MS;
        nextAttemptAt = millis() + delayMs;
      }
      break;

    case P_CONNECT:
      if ((int32_t)(millis() - nextAttemptAt) < 0) break;
      if (!authToken.length() ||
          (int32_t)(millis() - authTokenDeadline) >= 0) {
        phase = P_IDENTIFY;
        authToken = "";
        nextAttemptAt = millis();
        break;
      }
      if (openStream()) {
        phase = P_STREAM;
        // Publish presence, and clear any alarm flag left behind by a reboot,
        // as soon as the stream is up.
        writeInfo();
      } else {
        scheduleRetry();
      }
      break;

    case P_STREAM:
      if (!authToken.length() || (int32_t)(millis() - authTokenDeadline) >= 0) {
        Serial.println("[cloud] refreshing credentials");
        closeStream();
        authToken = "";
        phase = P_IDENTIFY;
        nextAttemptAt = millis();
        break;
      }

      pumpStream();
      if (phase != P_STREAM) break;

      if (stateDirty && millis() - lastStateAt >= STATE_SYNC_INTERVAL_MS) {
        writeState();
      }
      if (phase != P_STREAM) break;

      // Start and stop both land here so the app sees the flag immediately.
      if (alarmDirty) {
        writeInfo();
      }
      if (phase != P_STREAM) break;

      // Retries run here rather than inside the command handler so the stream
      // keeps flowing between attempts.
      if (ackRetryId.length() && (int32_t)(millis() - ackRetryAt) >= 0) {
        if (ackRetryCount >= 3) {
          Serial.println("[cloud] giving up on an unacked command");
          ackRetryId = "";
        } else {
          ackRetryCount++;
          ackRetryAt = millis() + 600;
          if (sendAck(ackRetryId, ackRetryOk,
                      ackRetryError.length() ? ackRetryError.c_str() : nullptr)) {
            ackRetryId = "";
          }
        }
      }
      if (phase != P_STREAM) break;

      if (millis() - lastInfoAt >= TELEMETRY_INTERVAL_MS) {
        writeInfo();
      }
      break;
  }
}

Status status() { return linkStatus; }

const char* statusText() {
  switch (linkStatus) {
    case READY: return "connected";
    case WAITING_CLAIM: return "waiting for pairing";
    case CONNECTING: return "connecting";
    case ERROR_CLAIMED: return "already paired to another account";
    default: return "offline";
  }
}

void requestPush() {
  stateDirty = true;
  lastStateAt = 0;
}

void requestAlarmSync() { alarmDirty = true; }

bool identifying() {
  return identifyUntil != 0 && (int32_t)(millis() - identifyUntil) < 0;
}

}  // namespace cloud
