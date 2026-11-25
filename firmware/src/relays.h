#pragma once

#include <Arduino.h>

#include "config.h"

// Relay bank. Single-threaded by design: every mutation happens from loop()
// (WebServer requests, cloud command frames and the status tick all run in the
// same task), so no locking is needed.
class RelayBank {
 public:
  // Called after a relay actually changed. channel 0xFF means "all channels"
  // (setAll), so a single announcement covers the whole bank.
  using ChangeListener = void (*)(uint8_t channel, bool on);

  void begin();
  void tick();  // flush the state to NVS once it has been quiet for a while

  void setChangeListener(ChangeListener cb);
  void set(uint8_t channel, bool on);
  void setAll(bool on);
  void toggle(uint8_t channel);
  bool get(uint8_t channel) const;
  void snapshot(bool* out) const;
  uint16_t mask() const;
  uint8_t count() const { return RELAY_COUNT; }
  bool changedSince(bool* reference) const;

 private:
  void writePin(uint8_t channel);
  bool state_[RELAY_COUNT] = {false};
  bool dirty_ = false;
  uint32_t lastChangeAt_ = 0;
  ChangeListener listener_ = nullptr;
};

extern RelayBank relays;
