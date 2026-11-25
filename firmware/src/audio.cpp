#include "audio.h"

#include <DFRobotDFPlayerMini.h>

#include "config.h"
#include "storage.h"

#if AUDIO_ENABLED

namespace {

// Track numbers on the SD card (/mp3/NNNN.mp3). Keep in sync with
// scripts/make_voice_clips.py.
enum Clip : uint16_t {
  CLIP_PAIRING_CODE_IS = 1,
  CLIP_WIFI_CONNECTED = 2,
  CLIP_CONNECT_TO_HOTSPOT = 3,
  CLIP_OPEN_PORTAL = 4,
  CLIP_SETUP_MODE = 5,
  CLIP_PAIRED = 6,
  CLIP_READY = 7,
  CLIP_CHANNEL = 8,
  CLIP_ALL = 9,
  CLIP_ON = 10,
  CLIP_OFF = 11,
  CLIP_FAN = 12,
  CLIP_LIGHT = 13,
  CLIP_SOCKET = 14,
  CLIP_IDENTIFY = 15,
  CLIP_ZERO = 16,
  CLIP_LETTER_A = 26,
  CLIP_MEOW = 52,
  CLIP_PURR = 53,
  CLIP_YUM = 54,
  CLIP_PLAY = 55,
  CLIP_NIGHT = 56,
  CLIP_MORNING = 57,
  CLIP_ALARM = 58,
  CLIP_REMINDER = 59,
};

constexpr uint8_t kQueueSize = 24;
constexpr uint32_t kGapMs = 150;       // settle time between clips
constexpr uint32_t kMinClipMs = 700;   // ignore BUSY this early after start
constexpr uint32_t kMaxClipMs = 8000;  // watchdog for a stuck BUSY line

DFRobotDFPlayerMini player;

uint16_t queue_[kQueueSize];
uint8_t head_ = 0;
uint8_t tail_ = 0;
uint8_t count_ = 0;

bool ready_ = false;
bool enabled_ = true;
bool playing_ = false;
uint32_t startedAt_ = 0;
uint32_t lastStartAt_ = 0;

void push(Clip clip) {
  if (!ready_ || !enabled_) return;
  if (count_ == kQueueSize) return;  // drop new clips instead of stalling
  queue_[tail_] = clip;
  tail_ = (uint8_t)((tail_ + 1) % kQueueSize);
  count_++;
}

void spell(char c) {
  if (c >= '0' && c <= '9') {
    push((Clip)(CLIP_ZERO + (c - '0')));
  } else if (c >= 'A' && c <= 'Z') {
    push((Clip)(CLIP_LETTER_A + (c - 'A')));
  } else if (c >= 'a' && c <= 'z') {
    push((Clip)(CLIP_LETTER_A + (c - 'a')));
  }
  // '-' and other separators stay silent; the clip gap marks the break.
}

void spellNumber(uint8_t value) {
  if (value >= 10) push((Clip)(CLIP_ZERO + value / 10));
  push((Clip)(CLIP_ZERO + value % 10));
}

uint8_t countType(const String& types, char type) {
  uint8_t n = 0;
  for (uint16_t i = 0; i < types.length(); i++) {
    if (types[i] == type) n++;
  }
  return n;
}

}  // namespace

namespace audio {

void begin() {
  pinMode(AUDIO_BUSY_PIN, INPUT_PULLUP);
  Serial1.begin(9600, SERIAL_8N1, AUDIO_UART_RX, AUDIO_UART_TX);
  delay(300);  // module needs a moment after the 5 V rail comes up

  // ACK mode for the handshake so a missing module is detected here instead of
  // blocking every later command for the 500 ms reply timeout.
  ready_ = player.begin(Serial1, true, true);
  if (ready_) {
    player.begin(Serial1, false, false);  // fire-and-forget playback
    player.setTimeOut(500);
    player.volume(AUDIO_VOLUME);
    Serial.println("[audio] dfplayer ready");
  } else {
    Serial.println("[audio] dfplayer not detected - announcements disabled");
  }
}

void loop() {
  if (!ready_ || !enabled_) return;
  uint32_t now = millis();

  if (playing_) {
    bool busy = AUDIO_BUSY_PIN >= 0 && digitalRead(AUDIO_BUSY_PIN) == LOW;
    uint32_t elapsed = now - startedAt_;
    if (elapsed >= kMaxClipMs || (elapsed >= kMinClipMs && !busy)) playing_ = false;
    return;
  }

  if (count_ == 0 || now - lastStartAt_ < kGapMs) return;

  uint16_t clip = queue_[head_];
  head_ = (uint8_t)((head_ + 1) % kQueueSize);
  count_--;
  player.playMp3Folder(clip);
  playing_ = true;
  startedAt_ = now;
  lastStartAt_ = now;
}

void setEnabled(bool on) {
  enabled_ = on;
  if (on) {
    if (ready_) player.volume(AUDIO_VOLUME);
  } else {
    stop();
  }
}

bool enabled() { return enabled_; }

bool idle() { return count_ == 0 && !playing_; }

void stop() {
  head_ = 0;
  tail_ = 0;
  count_ = 0;
  playing_ = false;
  if (ready_) player.stop();
}

void announcePortal(const String& ssid) {
  push(CLIP_SETUP_MODE);
  push(CLIP_CONNECT_TO_HOTSPOT);
  String suffix = ssid.substring(ssid.length() > 4 ? ssid.length() - 4 : 0);
  for (uint16_t i = 0; i < suffix.length(); i++) spell(suffix[i]);
  push(CLIP_OPEN_PORTAL);
}

void announceWifiConnected() { push(CLIP_WIFI_CONNECTED); }

void announcePaired() { push(CLIP_PAIRED); }

void announceReady() { push(CLIP_READY); }

void announceIdentify() {
  push(CLIP_MEOW);
  push(CLIP_IDENTIFY);
}

void announcePairingCode(const String& code) {
  push(CLIP_PAIRING_CODE_IS);
  for (uint16_t i = 0; i < code.length(); i++) spell(code[i]);
}

void announceChannel(uint8_t channel, bool on) {
  String types = store::channelTypes();
  char type = channel < types.length() ? types[channel] : 'o';
  Clip word;
  if (type == 'f') {
    word = CLIP_FAN;
  } else if (type == 'l') {
    word = CLIP_LIGHT;
  } else if (type == 's') {
    word = CLIP_SOCKET;
  } else {
    word = CLIP_CHANNEL;
  }

  push((Clip)word);
  if (word == CLIP_CHANNEL || countType(types, type) > 1) spellNumber(channel + 1);
  push(on ? CLIP_ON : CLIP_OFF);
}

void announceAll(bool on) {
  push(CLIP_ALL);
  push(on ? CLIP_ON : CLIP_OFF);
}

void onRelayChange(uint8_t channel, bool on) {
  // The pet meows first, then the state is spoken: "meow ... fan three on".
  push(CLIP_MEOW);
  if (channel == 0xFF) {
    announceAll(on);
  } else {
    announceChannel(channel, on);
  }
}

void announcePet() {
  push(CLIP_MEOW);
  push(CLIP_PURR);
}

void announceFeed() { push(CLIP_YUM); }

void announcePlay() {
  push(CLIP_PLAY);
  push(CLIP_MEOW);
}

void announceSleep() { push(CLIP_NIGHT); }

void announceWake() { push(CLIP_MORNING); }

// Two siren beeps fill the 8 s alarm scene.
void announceAlarm() {
  push(CLIP_ALARM);
  push(CLIP_ALARM);
}

void announceReminder() {
  push(CLIP_REMINDER);
  push(CLIP_MEOW);
}

}  // namespace audio

#else  // AUDIO_ENABLED

namespace audio {

void begin() {}
void loop() {}
void setEnabled(bool) {}
bool enabled() { return false; }
bool idle() { return true; }
void stop() {}
void announcePortal(const String&) {}
void announceWifiConnected() {}
void announcePaired() {}
void announceReady() {}
void announceIdentify() {}
void announcePairingCode(const String&) {}
void announceChannel(uint8_t, bool) {}
void announceAll(bool) {}
void announcePet() {}
void announceFeed() {}
void announcePlay() {}
void announceSleep() {}
void announceWake() {}
void announceAlarm() {}
void announceReminder() {}
void onRelayChange(uint8_t, bool) {}

}  // namespace audio

#endif  // AUDIO_ENABLED
