#pragma once

#include <Arduino.h>

// Local control surface: setup portal in AP mode, plain HTTP API + mDNS in
// station mode. Both are served by the same WebServer on port 80.
namespace device_http {

using StateListener = void (*)();

void setStateListener(StateListener cb);
void start();                            // bring the HTTP server up (once)
void startPortal(const String& ssid, const String& pass);
void stopPortal();
bool portalActive();
void loop();
String stateJson();

}  // namespace device_http
