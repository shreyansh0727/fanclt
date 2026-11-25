#pragma once

#include <Arduino.h>

// Voice announcements through a DFPlayer Mini (MP3-TF-16P).
//
// The module cannot synthesise speech, so every word is a short clip on the
// SD card: /mp3/0001.mp3 ... /mp3/0059.mp3 (0052 is the cat's meow, 0053-0057 the
// pet reactions and 0058/0059 the alarm siren and reminder ding; see
// scripts/make_voice_clips.py). Phrases are queued and played one at a time;
// loop() must be called often.
namespace audio {

void begin();
void loop();

void setEnabled(bool on);
bool enabled();
bool idle();  // nothing queued and nothing playing
void stop();  // drop the queue and silence the clip in progress

// Each of these queues one or more clips.
void announcePortal(const String& ssid);
void announceWifiConnected();
void announcePaired();
void announceReady();
void announceIdentify();
void announcePairingCode(const String& code);
void announceChannel(uint8_t channel, bool on);   // uses the channel type
void announceAll(bool on);

// Pet reactions triggered from the app.
void announcePet();
void announceFeed();
void announcePlay();
void announceSleep();
void announceWake();
void announceAlarm();
void announceReminder();

// RelayBank change hook: channel 0xFF means setAll().
void onRelayChange(uint8_t channel, bool on);

}  // namespace audio
