#include "device_http.h"

#include <ArduinoJson.h>
#include <ESPmDNS.h>
#include <WebServer.h>
#include <WiFi.h>

#include "audio.h"
#include "cloud.h"
#include "config.h"
#include "display.h"
#include "relays.h"
#include "storage.h"

namespace {

WebServer server(LOCAL_HTTP_PORT);
bool started = false;
bool portal = false;
String portalSsid;
String portalPass;
device_http::StateListener stateListener = nullptr;

const char* kPortalPage = R"HTML(<!doctype html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FanCtl setup</title><style>
body{font-family:system-ui,sans-serif;background:#0f1216;color:#e8ecf1;margin:0;padding:24px}
h1{font-size:20px;margin:0 0 4px}p{color:#93a1b1;font-size:13px;margin:0 0 20px}
label{display:block;font-size:12px;color:#93a1b1;margin:14px 0 6px}
input{width:100%;box-sizing:border-box;padding:11px 12px;border-radius:10px;border:1px solid #26303a;background:#171d24;color:#e8ecf1;font-size:15px}
input[type=checkbox]{width:auto;margin-right:8px}
button{width:100%;margin-top:22px;padding:13px;border:0;border-radius:10px;background:#2f81f7;color:#fff;font-size:15px;font-weight:600}
code{background:#171d24;padding:2px 6px;border-radius:5px}
</style></head><body>
<h1>FanCtl setup</h1>
<p>Connect to the <code>{{SSID}}</code> hotspot (password <code>{{PASS}}</code>) and open
<code>http://192.168.4.1</code>. Save your home WiFi below: the controller joins it, then
reads its pairing code out loud - enter that code in the app.</p>
<form method="POST" action="/save">
<label>WiFi name (SSID)</label><input name="ssid" required autocomplete="off">
<label>WiFi password</label><input name="pass" type="password" autocomplete="off">
<label>Server host (optional, blank uses the built-in {{HOST}})</label>
<input name="host" value="{{SRVHOST}}" placeholder="{{HOST}}" autocomplete="off">
<label>Server port (blank uses {{PORT}})</label><input name="port" value="{{SRVPORT}}" inputmode="numeric" placeholder="{{PORT}}">
<label><input type="checkbox" name="tls" value="1"{{TLSCHECKED}}>Use TLS (https, port 443)</label>
<button type="submit">Save and reboot</button>
</form>
<p style="margin-top:22px">Pairing code: <code>{{CODE}}</code><br>Device ID: <code>{{HW}}</code></p>
</body></html>)HTML";

const char* kControlPage = R"HTML(<!doctype html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FanCtl</title><style>
body{font-family:system-ui,sans-serif;background:#0f1216;color:#e8ecf1;margin:0;padding:20px}
header{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:18px}
h1{font-size:20px;margin:0}small{color:#7f8b99}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px}
button.ch{background:#171d24;border:1px solid #26303a;border-radius:14px;padding:16px;color:#e8ecf1;text-align:left;font-size:15px;cursor:pointer}
button.ch b{display:block;font-size:12px;color:#7f8b99;font-weight:500;margin-top:6px}
button.ch.on{background:#12351f;border-color:#2ea043}
.row{display:flex;gap:10px;margin-top:16px}
.row button{flex:1;padding:12px;border-radius:10px;border:1px solid #26303a;background:#171d24;color:#e8ecf1}
</style></head><body>
<header><h1>FanCtl</h1><small id="st">connecting…</small></header>
<div class="grid" id="grid"></div>
<div class="row"><button onclick="all(1)">All on</button><button onclick="all(0)">All off</button></div>
<script>
const key=new URLSearchParams(location.search).get('key')||'';
const q=key?('?key='+encodeURIComponent(key)):'';
let state=[];
function render(s){
 if(s&&s.state)state=s.state;
 const meta=(s&&s.channel_meta)||[];
 grid.innerHTML='';
 state.forEach((on,i)=>{
  const b=document.createElement('button');
  b.className='ch'+(on?' on':'');
  const t=document.createElement('span');
  t.textContent=(meta[i]&&meta[i].name)||('CH '+(i+1));
  const v=document.createElement('b');
  v.textContent=on?'ON':'OFF';
  b.appendChild(t);b.appendChild(v);
  b.onclick=()=>send({action:'set',ch:i,on:!on});
  grid.appendChild(b);
 });
 st.textContent=state.map(v=>v?'1':'0').join(' ')+'  ·  '+location.host;
}
async function refresh(){try{const r=await fetch('/api/state'+q);if(!r.ok)throw 0;render(await r.json());}catch(e){st.textContent='unreachable';}}
async function send(body){try{await fetch('/api/command'+q,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}catch(e){}refresh();}
function all(on){send({action:'all',on:!!on});}
refresh();setInterval(refresh,2000);
</script></body></html>)HTML";

void cors() {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.sendHeader("Access-Control-Allow-Headers", "Content-Type, X-Local-Key");
  server.sendHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  server.sendHeader("Cache-Control", "no-store");
}

void sendJson(int code, const String& body) {
  cors();
  server.send(code, "application/json", body);
}

void sendError(int code, const char* error) {
  JsonDocument doc;
  doc["error"] = error;
  String out;
  serializeJson(doc, out);
  sendJson(code, out);
}

// A device that is not claimed yet accepts LAN commands (it is in setup mode
// anyway). Once claimed, a paired device requires the local key that the
// backend handed to the owner's app.
String presentedKey() {
  String provided = server.header("X-Local-Key");
  if (provided.length() == 0) provided = server.arg("key");
  return provided;
}

bool keyEquals(const String& expected, const String& provided) {
  if (expected.length() == 0 || expected.length() != provided.length()) return false;
  uint8_t diff = 0;
  for (size_t i = 0; i < expected.length(); i++) {
    diff |= (uint8_t)(expected[i] ^ provided[i]);
  }
  return diff == 0;
}

// The assistant key is a second, revocable credential so a voice-assistant
// runtime on the LAN never holds the owner's device key. It is accepted for
// state and commands only - network changes stay owner-only.
bool authorized() {
  String owner = store::deviceKey();
  if (owner.length() == 0) return true;
  String provided = presentedKey();
  if (keyEquals(owner, provided)) return true;
  String assistant = store::assistantKey();
  return assistant.length() > 0 && keyEquals(assistant, provided);
}

bool authorizedOwner() {
  String owner = store::deviceKey();
  if (owner.length() == 0) return true;
  return keyEquals(owner, presentedKey());
}

void handleOptions() {
  cors();
  server.send(204);
}

void handleRoot() {
  cors();
  if (portal) {
    String page = kPortalPage;
    page.replace("{{CODE}}", store::ensurePairCode());
    page.replace("{{HW}}", store::hardwareId());
    page.replace("{{SSID}}", portalSsid);
    page.replace("{{PASS}}", portalPass);
    // Only surface the override when it differs from the built-in endpoint;
    // otherwise a blank form means "keep using the compiled default".
    String srvHost = store::hasServerOverride() ? store::cloudHost() : String();
    if (srvHost == CLOUD_HOST) srvHost = "";
    String srvPort = srvHost.length() ? String(store::cloudPort()) : String();
    if (srvPort == String(CLOUD_PORT)) srvPort = "";
    bool tlsChecked = srvHost.length() ? store::cloudTls() : (CLOUD_USE_TLS != 0);
    page.replace("{{HOST}}", CLOUD_HOST);
    page.replace("{{PORT}}", String(CLOUD_PORT));
    page.replace("{{SRVHOST}}", srvHost);
    page.replace("{{SRVPORT}}", srvPort);
    page.replace("{{TLSCHECKED}}", tlsChecked ? " checked" : "");
    server.send(200, "text/html", page);
    return;
  }
  String page = kControlPage;
  if (store::deviceKey().length() > 0) {
    // The page drives the API from JavaScript, so give it the key it needs.
    page.replace("const key=new URLSearchParams(location.search).get('key')||'';",
                 "const key='" + store::deviceKey() + "';");
  }
  server.send(200, "text/html", page);
}

void handleState() {
  if (!authorized()) {
    sendError(401, "unauthorized");
    return;
  }
  sendJson(200, device_http::stateJson());
}

void handleHealth() {
  JsonDocument doc;
  doc["ok"] = true;
  doc["fw"] = FW_VERSION;
  doc["uptime_s"] = millis() / 1000;
  String out;
  serializeJson(doc, out);
  sendJson(200, out);
}

void handleCommand() {
  if (!authorized()) {
    sendError(401, "unauthorized");
    return;
  }
  if (!server.hasArg("plain") || server.arg("plain").length() == 0) {
    sendError(400, "empty_body");
    return;
  }

  JsonDocument doc;
  if (deserializeJson(doc, server.arg("plain"))) {
    sendError(400, "bad_json");
    return;
  }

  String action = doc["action"] | "";
  // Any other reaction replaces the latched alarm scene, so drop the siren with
  // it and let the app hide its Stop button.
  const bool otherReaction = action == "pet" || action == "feed" || action == "play" ||
                             action == "sleep" || action == "wake" || action == "reminder";
  if (otherReaction && display::alarmActive()) {
    audio::stop();
    cloud::requestAlarmSync();
  }
  if (action == "all") {
    relays.setAll(doc["on"] | false);
  } else if (action == "set" || action == "toggle") {
    int ch = doc["ch"] | -1;
    if (ch < 0 || ch >= RELAY_COUNT) {
      sendError(400, "bad_channel");
      return;
    }
    if (action == "set") {
      relays.set((uint8_t)ch, doc["on"] | false);
    } else {
      relays.toggle((uint8_t)ch);
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
    cloud::requestAlarmSync();
  } else if (action == "alarm_stop") {
    display::dismissAlarm();
    audio::stop();
    cloud::requestAlarmSync();
  } else if (action == "reminder") {
    display::react(display::Reaction::Reminder);
    audio::announceReminder();
  } else {
    sendError(400, "bad_action");
    return;
  }

  if (stateListener) stateListener();
  sendJson(200, device_http::stateJson());
}

void handleSave() {
  if (!portal) {
    sendError(409, "not_in_setup_mode");
    return;
  }
  String ssid = server.arg("ssid");
  ssid.trim();
  if (ssid.length() == 0) {
    cors();
    server.send(400, "text/plain", "SSID is required");
    return;
  }
  store::saveWifi(ssid, server.arg("pass"));

  String host = server.arg("host");
  host.trim();
  if (host.length() > 0) {
    bool tls = server.hasArg("tls");
    uint32_t port = server.arg("port").toInt();
    if (port == 0) port = tls ? 443 : 8000;
    store::saveServer(host, (uint16_t)port, tls);
  } else {
    store::clearServer();
  }

  cors();
  server.send(200, "text/html",
              "<body style=\"font-family:sans-serif;background:#0f1216;color:#e8ecf1;padding:24px\">"
              "<h3>Saved. Rebooting…</h3></body>");
  delay(600);
  ESP.restart();
}

// Points the controller at a different network from the app over the LAN. The
// setup portal does the same job in AP mode; here the device is already on a
// network and the caller holds the local key. The board restarts to apply it
// and falls back to the portal if the new network does not come up.
void handleWifi() {
  if (!authorizedOwner()) {
    sendError(401, "unauthorized");
    return;
  }
  if (portal) {
    sendError(409, "portal_active");
    return;
  }
  if (!server.hasArg("plain") || server.arg("plain").length() == 0) {
    sendError(400, "empty_body");
    return;
  }

  JsonDocument doc;
  if (deserializeJson(doc, server.arg("plain"))) {
    sendError(400, "bad_json");
    return;
  }

  String ssid = doc["ssid"] | "";
  ssid.trim();
  if (ssid.length() == 0) {
    sendError(400, "missing_ssid");
    return;
  }
  String pass = doc["pass"] | "";
  store::saveWifi(ssid, pass);

  JsonDocument out;
  out["ok"] = true;
  out["rebooting"] = true;
  out["ssid"] = ssid;
  String body;
  serializeJson(out, body);
  sendJson(200, body);

  Serial.printf("[http] wifi changed to %s, restarting\n", ssid.c_str());
  delay(400);
  ESP.restart();
}

void handleNotFound() {
  if (server.method() == HTTP_OPTIONS) {
    handleOptions();
  } else if (portal) {
    handleRoot();
  } else {
    sendError(404, "not_found");
  }
}

void bindRoutes() {
  // WebServer only stores Authorization and ETag unless a header is collected;
  // without this the local key arrives as an empty string and every LAN call 401s.
  static const char* kKeys[] = {"X-Local-Key"};
  server.collectHeaders(kKeys, 1);
  server.on("/", HTTP_GET, handleRoot);
  server.on("/api/state", HTTP_GET, handleState);
  server.on("/api/health", HTTP_GET, handleHealth);
  server.on("/api/command", HTTP_POST, handleCommand);
  server.on("/api/wifi", HTTP_POST, handleWifi);
  server.on("/save", HTTP_POST, handleSave);
  server.onNotFound(handleNotFound);
}

}  // namespace

namespace device_http {

void setStateListener(StateListener cb) { stateListener = cb; }

void start() {
  if (started) return;
  // Arduino core 3+ no longer brings lwIP up at boot. WebServer::begin()
  // opens a listening socket, which asserts without the TCP/IP task, so the
  // network stack has to be initialised first.
  if (WiFi.getMode() == WIFI_MODE_NULL) WiFi.mode(WIFI_STA);
  bindRoutes();
  server.begin();
  started = true;
}

void startPortal(const String& ssid, const String& pass) {
  if (portal) return;
  WiFi.mode(WIFI_AP_STA);
  WiFi.softAP(ssid.c_str(), pass.c_str());
  portalSsid = ssid;
  portalPass = pass;
  portal = true;
  if (!started) start();
  Serial.printf("[http] setup portal up: %s / %s at http://%s\n",
                ssid.c_str(), pass.c_str(), WiFi.softAPIP().toString().c_str());
}

void stopPortal() {
  if (!portal) return;
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_STA);
  portal = false;
  Serial.println("[http] setup portal closed");
}

bool portalActive() { return portal; }

void loop() {
  if (started) server.handleClient();
}

String stateJson() {
  JsonDocument doc;
  doc["device_id"] = store::deviceId();
  doc["hardware_id"] = store::hardwareId();
  doc["firmware"] = FW_VERSION;
  doc["model"] = DEVICE_MODEL;
  doc["claimed"] = store::isClaimed();
  doc["channels"] = RELAY_COUNT;
  JsonArray state = doc["state"].to<JsonArray>();
  JsonArray meta = doc["channel_meta"].to<JsonArray>();
  for (uint8_t i = 0; i < RELAY_COUNT; i++) {
    state.add(relays.get(i));
    JsonObject channel = meta.add<JsonObject>();
    channel["index"] = i;
    channel["name"] = store::channelName(i);
  }
  doc["wifi_rssi"] = WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0;
  doc["wifi_ssid"] = WiFi.status() == WL_CONNECTED ? store::wifiSsid() : "";
  doc["ip"] = WiFi.localIP().toString();
  String out;
  serializeJson(doc, out);
  return out;
}

}  // namespace device_http
