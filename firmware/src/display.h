#pragma once

// 1.3" SH1106 128x64 OLED: animated pet, pairing code, relay tiles and link
// state. The display is optional - a missing panel only logs a note.
namespace display {

// Owner-triggered reactions from the app. They animate the pet and, in the
// audio module, play a matching clip. Alarm and Reminder also fire from the
// backend scheduler.
enum class Reaction { None, Pet, Feed, Play, Sleep, Wake, Alarm, Reminder };

void begin();
void tick();
void react(Reaction reaction);
// Alarm is latched: it stays until the app sends "stop". These expose that
// state so the cloud loop can mirror it and the chime can repeat.
bool alarmActive();
void dismissAlarm();

}  // namespace display
