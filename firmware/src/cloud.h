#pragma once

#include <Arduino.h>

// Cloud link: the controller signs in to Firebase Auth with a custom token
// minted by the backend, then keeps a REST event stream open on its own
// Realtime Database node (/devices/<id>). Commands arrive as stream events,
// acks and telemetry go back as small REST writes. The backend is
// authoritative for channel names; the device is authoritative for relay
// state.
namespace cloud {

enum Status {
  OFFLINE,        // no WiFi or no stream
  CONNECTING,     // fetching a token and opening the stream
  WAITING_CLAIM,  // registered, waiting for the owner to pair this controller
  READY,
  ERROR_CLAIMED,  // this hardware id belongs to someone else / key rejected
};

void begin();
void loop();
Status status();
const char* statusText();
bool identifying();   // true while the app is asking "which box is this?"
void requestPush();  // queue a state write on the next loop
void requestAlarmSync();  // mirror the latched alarm flag to info/alarm now

}  // namespace cloud
