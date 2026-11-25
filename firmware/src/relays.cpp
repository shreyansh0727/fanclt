#include "relays.h"

#include "storage.h"

namespace {

constexpr uint32_t kPersistDelayMs = 3000;

inline int activeLevel() { return RELAY_ACTIVE_LOW ? LOW : HIGH; }
inline int inactiveLevel() { return RELAY_ACTIVE_LOW ? HIGH : LOW; }

}  // namespace

RelayBank relays;

void RelayBank::begin() {
  uint16_t restore = RESTORE_STATE_ON_BOOT ? store::relayMask() : 0;

  for (uint8_t i = 0; i < RELAY_COUNT; i++) {
    state_[i] = (restore >> i) & 0x01;
    // Latch the output level *before* enabling the driver so the coil never
    // sees a spurious pulse. External 10k pull-ups on the IN lines do the
    // same job during the ~200 ms before setup() runs.
    digitalWrite(RELAY_PINS[i], state_[i] ? activeLevel() : inactiveLevel());
    pinMode(RELAY_PINS[i], OUTPUT);
  }
  writePin(0);  // no-op re-apply, keeps behaviour obvious
  for (uint8_t i = 0; i < RELAY_COUNT; i++) writePin(i);

  if (!RESTORE_STATE_ON_BOOT && store::relayMask() != 0) store::saveRelayMask(0);
  dirty_ = false;
}

void RelayBank::writePin(uint8_t channel) {
  digitalWrite(RELAY_PINS[channel], state_[channel] ? activeLevel() : inactiveLevel());
}

void RelayBank::setChangeListener(ChangeListener cb) { listener_ = cb; }

void RelayBank::set(uint8_t channel, bool on) {
  if (channel >= RELAY_COUNT || state_[channel] == on) return;
  state_[channel] = on;
  writePin(channel);
  dirty_ = true;
  lastChangeAt_ = millis();
  if (listener_) listener_(channel, on);
}

void RelayBank::setAll(bool on) {
  bool any = false;
  for (uint8_t i = 0; i < RELAY_COUNT; i++) {
    if (state_[i] != on) {
      state_[i] = on;
      writePin(i);
      any = true;
    }
  }
  if (any) {
    dirty_ = true;
    lastChangeAt_ = millis();
    if (listener_) listener_(0xFF, on);
  }
}

void RelayBank::toggle(uint8_t channel) {
  if (channel >= RELAY_COUNT) return;
  set(channel, !state_[channel]);
}

bool RelayBank::get(uint8_t channel) const {
  return channel < RELAY_COUNT ? state_[channel] : false;
}

void RelayBank::snapshot(bool* out) const {
  for (uint8_t i = 0; i < RELAY_COUNT; i++) out[i] = state_[i];
}

uint16_t RelayBank::mask() const {
  uint16_t value = 0;
  for (uint8_t i = 0; i < RELAY_COUNT; i++) {
    if (state_[i]) value |= (1u << i);
  }
  return value;
}

bool RelayBank::changedSince(bool* reference) const {
  for (uint8_t i = 0; i < RELAY_COUNT; i++) {
    if (reference[i] != state_[i]) return true;
  }
  return false;
}

void RelayBank::tick() {
  if (!dirty_) return;
  if (millis() - lastChangeAt_ < kPersistDelayMs) return;
  store::saveRelayMask(mask());
  dirty_ = false;
}
