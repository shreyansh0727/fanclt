#include "display.h"

#include "config.h"

#if DISPLAY_ENABLED

#include <Arduino.h>
#include <U8g2lib.h>
#include <WiFi.h>
#include <Wire.h>
#include <math.h>

#include "cloud.h"
#include "device_http.h"
#include "relays.h"
#include "storage.h"

namespace {

// Full framebuffer, no reset pin (the module ties it to its RC delay) and
// explicit clock/data pins: the Arduino esp32c6 variant defaults SDA/SCL to
// 23/22, the reverse of the C6-Pico header labels.
#if DISPLAY_SSD1306
U8G2_SSD1306_128X64_NONAME_F_HW_I2C oled(U8G2_R0, U8X8_PIN_NONE, DISPLAY_SCL, DISPLAY_SDA);
constexpr const char* kPanelName = "ssd1306";
#else
U8G2_SH1106_128X64_NONAME_F_HW_I2C oled(U8G2_R0, U8X8_PIN_NONE, DISPLAY_SCL, DISPLAY_SDA);
constexpr const char* kPanelName = "sh1106";
#endif

constexpr uint32_t kFrameMs = 200;         // ~5 fps; a 1 KB frame takes ~90 ms at 100 kHz
// Reaction scenes animate the 2x pet, so they run faster. 150 ms still leaves
// the 100 kHz bus idle for ~40% of each period.
constexpr uint32_t kReactFrameMs = 150;
constexpr uint32_t kBlinkPeriodMs = 3200;
constexpr uint32_t kBlinkHoldMs = 160;
constexpr uint32_t kHappyMs = 1600;
constexpr uint32_t kSleepAfterMs = 6000;
constexpr uint32_t kProbeMs = 5000;
constexpr uint32_t kClaimCheckMs = 1000;
// The panel shares the 3V3 rail with the radio and the DFPlayer amp. A current
// spike can brown out the controller just enough to reset its registers - the
// glass goes dark while I2C keeps ACKing. Re-sending the pump + display-on is
// silent (GDDRAM is untouched, the next frame repaints) and recovers within
// one period, so keep it short.
constexpr uint32_t kKeepAliveMs = 1000;

enum class Mood { Idle, Happy, Asleep, Call, Eating, Excited };

// Re-assert what a controller reset clears. Both pump dialects are sent
// because a 1.3" module can carry either controller and stray commands are
// ignored by the other: 0x8D/0x14 is the SSD1306 charge pump, 0xAD/0x8B the
// SH1106 DC-DC. 0x81 contrast back to 200, 0xA4 display follows GDDRAM,
// 0xAF display on.
const uint8_t kWakeSeq[] = { U8X8_START_TRANSFER(),
                             U8X8_CA(0x08D, 0x014), U8X8_CA(0x0AD, 0x08B),
                             U8X8_CA(0x081, 0x0C8), U8X8_C(0x0A4), U8X8_C(0x0AF),
                             U8X8_END_TRANSFER(), U8X8_END() };

bool present = false;
uint8_t panelAddr = 0;
uint32_t lastFrameAt = 0;
uint32_t lastKeepAliveAt = 0;
uint32_t lastProbeAt = 0;
uint32_t happyUntil = 0;
uint32_t sleepSince = 0;
uint32_t nextClaimCheck = 0;
uint32_t nextScanLogAt = 0;
uint16_t lastMask = 0;
bool claimed = false;
String pairCode;
bool pairCodeLoaded = false;

// Sleep and Alarm latch until a command clears them; the other reactions run
// for a few seconds.
constexpr uint32_t kReactPetMs = 3500;
constexpr uint32_t kReactFeedMs = 4000;
constexpr uint32_t kReactPlayMs = 4500;
constexpr uint32_t kReactWakeMs = 1500;
constexpr uint32_t kReactReminderMs = 4500;
display::Reaction reaction = display::Reaction::None;
uint32_t reactionUntil = 0;
bool asleep = false;

bool reacting(uint32_t now) {
  if (reaction == display::Reaction::None) return false;
  if (reaction == display::Reaction::Sleep || reaction == display::Reaction::Alarm) return true;
  return (int32_t)(now - reactionUntil) < 0;
}

// Most 1.3" SH1106 modules strap the address pin low (0x3C); a few use 0x3D.
// Probed repeatedly at DISPLAY_BUS_HZ: on jumper wires a single first ACK can
// be missed.
uint8_t findPanel() {
  const uint8_t candidates[] = { DISPLAY_ADDR, 0x3D };
  for (int attempt = 0; attempt < 6; attempt++) {
    for (uint8_t addr : candidates) {
      Wire.beginTransmission(addr);
      uint8_t err = Wire.endTransmission();
      if (err == 0) return addr;
      if (attempt == 0) {
        Serial.printf("[display] probe 0x%02X -> i2c error %u\n", addr, err);
      }
    }
    delay(10);
  }
  return 0;
}

void logBusScan() {
  Serial.printf("[display] no %s at 0x%02X/0x3D on GP%d/GP%d\n",
                kPanelName, DISPLAY_ADDR, DISPLAY_SDA, DISPLAY_SCL);
  for (int pass = 0; pass < 3; pass++) {
    Serial.print("[display] i2c scan:");
    bool any = false;
    for (uint8_t addr = 0x08; addr < 0x78; addr++) {
      Wire.beginTransmission(addr);
      if (Wire.endTransmission() == 0) {
        Serial.printf(" 0x%02X", addr);
        any = true;
      }
    }
    if (any) {
      Serial.println();
    } else {
      Serial.println(" nothing - check SDA GP22, SCL GP23, VCC 3V3 and GND");
    }
    delay(20);
  }
}

// Address ACK test that also carries payload (0xAF = display on, safe).
uint8_t dataProbe(uint8_t addr) {
  Wire.beginTransmission(addr);
  Wire.write((uint8_t)0x00);
  Wire.write((uint8_t)0xAF);
  return Wire.endTransmission();
}

bool attachPanel() {
  uint8_t addr = findPanel();
  if (addr == 0) return false;
  // U8g2 stores the 8-bit form here (0x78 for 0x3C, 0x7A for 0x3D) and halves
  // it again before a transfer.
  oled.setI2CAddress(addr << 1);
  oled.setBusClock(DISPLAY_BUS_HZ);
  oled.begin();
  oled.setContrast(200);
  u8x8_cad_SendSequence(oled.getU8x8(), kWakeSeq);
  panelAddr = addr;
  lastKeepAliveAt = millis();
  Serial.printf("[display] %s 128x64 ready at 0x%02X (SDA GP%d, SCL GP%d)\n",
                kPanelName, addr, DISPLAY_SDA, DISPLAY_SCL);
  return true;
}

// Runs every kKeepAliveMs. A live controller only needs the pump/display-on
// registers re-asserted; a dead bus means the module lost power, so do a full
// re-init (U8g2 re-sends the init sequence and its address).
void ensurePanelAlive(uint32_t now) {
  if (dataProbe(panelAddr) == 0) {
    u8x8_cad_SendSequence(oled.getU8x8(), kWakeSeq);
    return;
  }
  Serial.printf("[display] no ack from 0x%02X - reinitialising\n", panelAddr);
  present = attachPanel();
  if (!present) {
    lastProbeAt = now;
    nextScanLogAt = now + 60000;
  }
}

void drawSignal(int x, int baseY) {
  int rssi = WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : -127;
  int level = rssi >= -60 ? 4 : rssi >= -70 ? 3 : rssi >= -80 ? 2 : rssi >= -90 ? 1 : 0;
  for (int i = 0; i < 4; i++) {
    int h = 2 + i * 2;
    if (i < level) {
      oled.drawBox(x + i * 3, baseY - h + 1, 2, h);
    } else {
      oled.drawFrame(x + i * 3, baseY - h + 1, 2, h);
    }
  }
}

const char* linkTag() {
  if (device_http::portalActive()) return "SETUP";
  switch (cloud::status()) {
    case cloud::READY: return "LINK";
    case cloud::WAITING_CLAIM: return "PAIR";
    case cloud::CONNECTING: return "SYNC";
    case cloud::ERROR_CLAIMED: return "ERR";
    default: break;
  }
  return WiFi.status() == WL_CONNECTED ? "SYNC" : "OFF";
}

void drawStatusBar() {
  oled.setFont(u8g2_font_5x7_tr);
  oled.setDrawColor(1);
  drawSignal(0, 7);

  String ssid = device_http::portalActive() ? String("setup AP") : store::wifiSsid();
  if (ssid.length() == 0) ssid = "no wifi";
  if (ssid.length() > 11) ssid = ssid.substring(0, 11);
  oled.drawStr(14, 8, ssid.c_str());

  const char* tag = linkTag();
  oled.drawStr(127 - (int)oled.getStrWidth(tag), 8, tag);
  oled.drawHLine(0, 10, 128);
}

// Soft ellipse on the ground plane. The pet floats a pixel or two when it
// bobs, so the shadow shrinks as it rises while staying on the ground line.
void drawShadow(int cx, int groundY, int lift, int rx = 10) {
  rx -= lift;
  for (int dy = -2; dy <= 2; dy++) {
    int half = rx - (dy * dy * rx) / 6;
    if (half < 1) continue;
    oled.drawHLine(cx - half, groundY + dy, 2 * half + 1);
  }
}

// Filled white cat head with pointy ears; features are punched out in black.
// Mood changes the eyes and mouth, blink closes them for a fraction of a frame.
// `s` scales the head - reaction scenes draw it at 2x - and `gaze` shifts the
// pupils sideways so the cat can follow the play ball.
void drawPet(int cx, int cy, Mood mood, bool blink, float s = 1.0f, int gaze = 0) {
  auto X = [&](float v) { return cx + (int)lroundf(v * s); };
  auto Y = [&](float v) { return cy + (int)lroundf(v * s); };
  auto D = [&](float v) { return (int)lroundf(v * s); };

  oled.setDrawColor(1);
  oled.drawTriangle(X(-11), Y(-8), X(-12), Y(-17), X(-3), Y(-9));
  oled.drawTriangle(X(11), Y(-8), X(12), Y(-17), X(3), Y(-9));
  oled.drawRBox(X(-12), Y(-9), D(25), D(20), D(5));

  oled.drawLine(X(-13), Y(1), X(-20), Y(-1));
  oled.drawLine(X(-13), Y(4), X(-20), Y(5));
  oled.drawLine(X(13), Y(1), X(20), Y(-1));
  oled.drawLine(X(13), Y(4), X(20), Y(5));

  oled.setDrawColor(0);
  switch (mood) {
    case Mood::Happy:
      oled.drawLine(X(-7), Y(-1), X(-5), Y(-3));
      oled.drawLine(X(-5), Y(-3), X(-3), Y(-1));
      oled.drawLine(X(3), Y(-1), X(5), Y(-3));
      oled.drawLine(X(5), Y(-3), X(7), Y(-1));
      oled.drawDisc(X(0), Y(4), D(2));
      break;
    case Mood::Asleep:
      oled.drawHLine(X(-7), Y(-2), D(4));
      oled.drawHLine(X(3), Y(-2), D(4));
      oled.drawLine(X(-2), Y(3), X(0), Y(5));
      oled.drawLine(X(0), Y(5), X(2), Y(3));
      break;
    case Mood::Call:
      oled.drawDisc(X(-5) + gaze, Y(-2), D(3));
      oled.drawDisc(X(5) + gaze, Y(-2), D(3));
      oled.setDrawColor(1);
      oled.drawDisc(X(-4) + gaze, Y(-3), D(1));
      oled.drawDisc(X(6) + gaze, Y(-3), D(1));
      oled.setDrawColor(0);
      oled.drawDisc(X(0), Y(5), D(1));
      break;
    case Mood::Eating:
      // Content, eyes squeezed shut, mouth open mid-bite.
      oled.drawLine(X(-7), Y(-1), X(-5), Y(-3));
      oled.drawLine(X(-5), Y(-3), X(-3), Y(-1));
      oled.drawLine(X(3), Y(-1), X(5), Y(-3));
      oled.drawLine(X(5), Y(-3), X(7), Y(-1));
      oled.drawDisc(X(0), Y(4), D(2));
      oled.drawBox(X(-1), Y(7), D(3), D(2));
      break;
    case Mood::Excited:
      oled.drawDisc(X(-5) + gaze, Y(-2), D(3));
      oled.drawDisc(X(5) + gaze, Y(-2), D(3));
      oled.setDrawColor(1);
      oled.drawDisc(X(-5) + gaze, Y(-2), D(1));
      oled.drawDisc(X(5) + gaze, Y(-2), D(1));
      oled.setDrawColor(0);
      oled.drawDisc(X(0), Y(4), D(2));
      break;
    case Mood::Idle:
    default:
      if (blink) {
        oled.drawHLine(X(-7), Y(-1), D(4));
        oled.drawHLine(X(3), Y(-1), D(4));
      } else {
        oled.drawDisc(X(-5) + gaze, Y(-2), D(2));
        oled.drawDisc(X(5) + gaze, Y(-2), D(2));
      }
      oled.drawLine(X(-2), Y(3), X(0), Y(5));
      oled.drawLine(X(0), Y(5), X(2), Y(3));
      break;
  }
  oled.setDrawColor(1);
}

int bobOffset(uint32_t now, Mood mood) {
  if (mood == Mood::Excited) return ((now / 150) % 2) ? -3 : 0;
  if (mood == Mood::Happy) return ((now / 200) % 2) ? -2 : 0;
  if (mood == Mood::Eating) return ((now / 300) % 2) ? -1 : 0;
  if (mood == Mood::Idle || mood == Mood::Call) return ((now / 600) % 2) ? -1 : 0;
  return 0;
}

// ---- Reaction scenes -------------------------------------------------------
// A reaction owns the whole panel: the status bar, the tiles and the IP line
// are hidden while it plays, and the pet is drawn at 2x with its own effect.

void drawSparkle(int x, int y, int r) {
  oled.setDrawColor(1);
  oled.drawPixel(x, y);
  oled.drawLine(x - r, y, x + r, y);
  oled.drawLine(x, y - r, x, y + r);
}

// Eight-pixel heart: two lobes and a point.
void drawBigHeart(int x, int y) {
  oled.setDrawColor(1);
  oled.drawDisc(x - 2, y, 2);
  oled.drawDisc(x + 2, y, 2);
  oled.drawTriangle(x - 4, y + 1, x + 4, y + 1, x, y + 7);
}

void drawBowl(int x, int rimY, int food) {
  oled.setDrawColor(1);
  oled.drawFrame(x, rimY, 24, 3);
  oled.drawLine(x + 1, rimY + 3, x + 5, rimY + 9);
  oled.drawLine(x + 22, rimY + 3, x + 18, rimY + 9);
  oled.drawHLine(x + 5, rimY + 9, 14);
  for (int i = 0; i < food && i < 5; i++) {
    oled.drawPixel(x + 7 + i * 3, rimY - 1);
    oled.drawPixel(x + 7 + i * 3, rimY - 2);
  }
}

void drawBall(int x, int y, bool squash) {
  oled.setDrawColor(1);
  if (squash) {
    oled.drawBox(x - 4, y - 2, 9, 5);
  } else {
    oled.drawCircle(x, y, 4);
  }
  oled.setDrawColor(0);
  oled.drawPixel(x - 2, y - 2);
  oled.setDrawColor(1);
}

// Bell silhouette: handle, dome, flared body, rim and clapper.
void drawBell(int x, int y) {
  oled.setDrawColor(1);
  oled.drawPixel(x, y);
  oled.drawDisc(x, y + 7, 6);
  oled.drawBox(x - 7, y + 7, 15, 6);
  oled.drawHLine(x - 9, y + 13, 19);
  oled.drawBox(x - 1, y + 14, 3, 2);
}

// Two ">>" style marks each side, travelling outward on alternating frames.
void drawRingMarks(int x, int y, uint32_t now) {
  for (int i = 0; i < 2; i++) {
    if (((now / 180) + i) % 2) continue;
    int dx = 11 + i * 5;
    oled.setDrawColor(1);
    oled.drawLine(x - dx, y - 4, x - dx - 2, y);
    oled.drawLine(x - dx - 2, y, x - dx, y + 4);
    oled.drawLine(x + dx, y - 4, x + dx + 2, y);
    oled.drawLine(x + dx + 2, y, x + dx, y + 4);
  }
}

// Small dial with a sweeping minute hand (one turn every 12 s).
void drawClock(int x, int y, uint32_t now) {
  oled.setDrawColor(1);
  oled.drawCircle(x, y, 5);
  oled.drawDisc(x, y, 1);
  float angle = (float)(now % 12000) / 12000.0f * 6.2831853f;
  oled.drawLine(x, y, x + (int)lroundf(sinf(angle) * 3.0f),
                y - (int)lroundf(cosf(angle) * 3.0f));
}

void drawReactionScene(uint32_t now, bool blink) {
  constexpr float kScale = 2.0f;
  switch (reaction) {
    case display::Reaction::Pet: {
      int bob = ((now / 260) % 2) ? -2 : 0;
      drawShadow(64, 61, -bob, 30);
      drawPet(64, 36 + bob, Mood::Happy, blink, kScale);
      // Four hearts on staggered phases drift up both margins.
      for (int i = 0; i < 4; i++) {
        int phase = (int)((now / 100 + i * 18) % 72);
        int y = 54 - phase;
        int x = (i % 2 ? 112 : 16) + ((phase / 12) % 2 ? 2 : -2);
        if (y > 4 && y < 58) drawBigHeart(x, y);
      }
      drawSparkle(10 + (int)((now / 300) % 3), 10, 1 + (int)((now / 300) % 2));
      drawSparkle(118 - (int)((now / 300) % 3), 22, 2 - (int)((now / 300) % 2));
      return;
    }
    case display::Reaction::Feed: {
      drawShadow(44, 61, 0, 22);
      // Alternate the open mouth with the closed one: chewing.
      bool chew = ((now / 220) % 2) != 0;
      drawPet(44, 36, chew ? Mood::Eating : Mood::Happy, blink, kScale);
      drawBowl(96, 50, (int)((now / 700) % 4));
      // Kibble falls in three staggered lanes and vanishes into the bowl.
      for (int i = 0; i < 3; i++) {
        int fall = (int)((now / 70 + i * 9) % 26);
        int y = 4 + fall;
        if (y >= 46) continue;
        int x = 101 + ((i * 7 + fall / 6) % 3) * 6;
        oled.setDrawColor(1);
        oled.drawBox(x, y, 2, 2);
      }
      return;
    }
    case display::Reaction::Play: {
      // The ball crosses the panel in ~3.4 s and the cat watches it go by.
      int lift = (int)((now / 140) % 8);
      int bx = 9 + ((int)((now / 140) % 24)) * 5;
      int by = 48 - lift * 5;
      int cx = 64 + (bx - 64) / 8;
      cx = cx < 56 ? 56 : (cx > 72 ? 72 : cx);
      int gaze = bx > cx + 10 ? 2 : (bx < cx - 10 ? -2 : 0);
      drawShadow(cx, 61, 0, 26);
      drawPet(cx, 36, Mood::Excited, blink, kScale, gaze);
      for (int i = 1; i <= 2; i++) {
        int tx = bx - i * 9;
        if (tx > 2) {
          oled.setDrawColor(1);
          oled.drawPixel(tx, by);
        }
      }
      drawBall(bx, by, lift == 0);
      return;
    }
    case display::Reaction::Sleep: {
      drawPet(64, 38, Mood::Asleep, false, kScale);
      // Crescent moon top-left.
      oled.setDrawColor(1);
      oled.drawDisc(14, 12, 7);
      oled.setDrawColor(0);
      oled.drawDisc(18, 10, 7);
      oled.setDrawColor(1);
      // Three z's grow as they drift up the right margin.
      for (int i = 0; i < 3; i++) {
        int age = (int)((now / 220 + i * 8) % 24);
        if (age > 9) continue;
        int zx = 96 + age;
        int zy = 36 - age * 2;
        if (age < 4) {
          oled.setFont(u8g2_font_5x7_tr);
        } else if (age < 7) {
          oled.setFont(u8g2_font_helvB12_tr);
        } else {
          oled.setFont(u8g2_font_helvB14_tr);
        }
        oled.drawStr(zx, zy, "z");
      }
      if ((now / 400) % 2) {
        drawSparkle(112, 30, 1);
      } else {
        drawSparkle(24, 20, 1);
      }
      return;
    }
    case display::Reaction::Wake: {
      static const int8_t kRays[8][2] = { { 30, 0 },  { 21, 21 },  { 0, 30 },   { -21, 21 },
                                          { -30, 0 }, { -21, -21 }, { 0, -30 },  { 21, -21 } };
      drawShadow(64, 61, 0, 30);
      drawPet(64, 36, Mood::Excited, false, kScale);
      for (int k = 0; k < 8; k++) {
        int x1 = 64 + kRays[k][0];
        int y1 = 36 + kRays[k][1];
        if ((k + (int)(now / 90)) % 2 == 0) {
          oled.setDrawColor(1);
          oled.drawLine(x1, y1, x1 + kRays[k][0] / 6, y1 + kRays[k][1] / 6);
        } else {
          drawSparkle(x1, y1, 1);
        }
      }
      return;
    }
    case display::Reaction::Alarm: {
      // The cat is startled: wide eyes, a jitter, and a bell ringing overhead.
      int shake = ((now / 90) % 2) ? 1 : -1;
      int cx = 42 + shake;
      drawShadow(cx, 61, 0, 24);
      drawPet(cx, 38, Mood::Call, false, kScale);
      int bellX = 100 + (((now / 200) % 2) ? 2 : -2);
      drawBell(bellX, 5);
      drawRingMarks(bellX, 19, now);
      if ((now / 250) % 2) {
        oled.setFont(u8g2_font_helvB14_tr);
        oled.drawStr(10, 20, "!");
      }
      return;
    }
    case display::Reaction::Reminder: {
      // A gentler nudge: the cat looks up at the bell next to a ticking clock.
      drawShadow(42, 61, 0, 24);
      drawPet(42, 38, Mood::Happy, blink, kScale, 2);
      int bellX = 101 + (((now / 300) % 2) ? 1 : 0);
      drawBell(bellX, 12);
      drawRingMarks(bellX, 26, now);
      drawClock(11, 13, now);
      return;
    }
    default:
      return;
  }
}

void drawPortal() {
  oled.setFont(u8g2_font_helvB12_tr);
  const char* title = "Wi-Fi SETUP";
  oled.drawStr((128 - (int)oled.getStrWidth(title)) / 2, 23, title);

  String hw = store::hardwareId();
  String ap = String(AP_SSID_PREFIX) + hw.substring(hw.length() - 4);

  oled.setFont(u8g2_font_5x7_tr);
  oled.drawStr(6, 36, "Join this network:");
  oled.drawStr(6, 47, ap.c_str());
  oled.drawStr(6, 59, "then 192.168.4.1");
}

void drawPairing(uint32_t now, Mood mood, bool blink) {
  if (!pairCodeLoaded) {
    pairCode = store::ensurePairCode();
    pairCodeLoaded = true;
  }

  drawPet(22, 29 + bobOffset(now, mood), mood, blink);

  oled.setFont(u8g2_font_5x7_tr);
  oled.drawStr(42, 20, "PAIR IN");
  oled.drawStr(42, 29, "THE APP");

  oled.setFont(u8g2_font_helvB14_tr);
  int w = (int)oled.getStrWidth(pairCode.c_str());
  oled.drawStr((128 - w) / 2, 56, pairCode.c_str());
}

void drawHome(uint32_t now, Mood mood, bool blink) {
  int bob = bobOffset(now, mood);
  drawShadow(26, 51, -bob);
  drawPet(26, 36 + bob, mood, blink);

  oled.setFont(u8g2_font_5x7_tr);
  if (mood == Mood::Asleep) {
    oled.drawStr(40, 24, "z");
    oled.drawStr(46, 17, "z");
  }

  for (uint8_t i = 0; i < RELAY_COUNT; i++) {
    int x = 50 + (i % 4) * 19;
    int y = 14 + (i / 4) * 20;
    if (relays.get(i)) {
      oled.drawBox(x, y, 17, 17);
      oled.setDrawColor(0);
    } else {
      oled.drawFrame(x, y, 17, 17);
    }
    // Owner-set names in the app; three characters fit the 17 px tile.
    String label = store::channelName(i);
    if (label.length() == 0) label = String(i + 1);
    if (label.length() > 3) label = label.substring(0, 3);
    int width = (int)oled.getStrWidth(label.c_str());
    oled.drawStr(x + (17 - width) / 2, y + 12, label.c_str());
    oled.setDrawColor(1);
  }

  String line;
  if (cloud::identifying()) {
    line = "HERE!";
  } else if (WiFi.status() == WL_CONNECTED) {
    line = WiFi.localIP().toString();
  } else {
    line = "no network";
  }
  oled.drawStr((128 - (int)oled.getStrWidth(line.c_str())) / 2, 63, line.c_str());
}

Mood currentMood(uint32_t now) {
  if (cloud::identifying()) return Mood::Call;
  if (reacting(now)) {
    switch (reaction) {
      case display::Reaction::Pet:
      case display::Reaction::Wake: return Mood::Happy;
      case display::Reaction::Feed: return Mood::Eating;
      case display::Reaction::Play: return Mood::Excited;
      case display::Reaction::Sleep: return Mood::Asleep;
      case display::Reaction::Alarm: return Mood::Call;
      case display::Reaction::Reminder: return Mood::Happy;
      default: break;
    }
  }
  // A manual Sleep latches until Wake, even while the link is healthy.
  if (asleep) return Mood::Asleep;
  if ((int32_t)(now - happyUntil) < 0) return Mood::Happy;
  if (sleepSince != 0 && (uint32_t)(now - sleepSince) > kSleepAfterMs) return Mood::Asleep;
  return Mood::Idle;
}

void render(uint32_t now) {
  bool blink = (now % kBlinkPeriodMs) < kBlinkHoldMs;
  Mood mood = currentMood(now);

  oled.clearBuffer();
  // A reaction owns the whole panel - no status bar, tiles or IP line.
  if (claimed && !device_http::portalActive() && reacting(now)) {
    drawReactionScene(now, blink);
    oled.sendBuffer();
    return;
  }
  drawStatusBar();
  if (device_http::portalActive()) {
    drawPortal();
  } else if (!claimed) {
    drawPairing(now, mood, blink);
  } else {
    drawHome(now, mood, blink);
  }
  oled.sendBuffer();
}

}  // namespace

namespace display {

void begin() {
  // Bring the bus up once so the panel can be probed before U8g2 talks to it.
  Wire.begin(DISPLAY_SDA, DISPLAY_SCL, DISPLAY_BUS_HZ);

  claimed = store::isClaimed();
  lastMask = relays.mask();
  nextClaimCheck = millis() + kClaimCheckMs;

  if (!attachPanel()) {
    present = false;
    lastProbeAt = millis();
    nextScanLogAt = millis() + 60000;
    logBusScan();
    return;
  }

  present = true;
  lastFrameAt = millis() - kFrameMs;
  render(millis());
}

void react(Reaction next) {
  uint32_t now = millis();
  reaction = next;
  if (next == Reaction::Sleep) {
    asleep = true;
    reactionUntil = 0;
    return;
  }
  asleep = false;
  if (next == Reaction::Wake) {
    reactionUntil = now + kReactWakeMs;
    happyUntil = now + kHappyMs;
  } else if (next == Reaction::Pet) {
    reactionUntil = now + kReactPetMs;
  } else if (next == Reaction::Feed) {
    reactionUntil = now + kReactFeedMs;
  } else if (next == Reaction::Play) {
    reactionUntil = now + kReactPlayMs;
  } else if (next == Reaction::Alarm) {
    reactionUntil = 0;  // latched: the app sends alarm_stop to clear it
  } else if (next == Reaction::Reminder) {
    reactionUntil = now + kReactReminderMs;
  }
}

bool alarmActive() { return reaction == Reaction::Alarm; }

void dismissAlarm() {
  if (reaction != Reaction::Alarm) return;
  reaction = Reaction::None;
  reactionUntil = 0;
}

void tick() {
  uint32_t now = millis();

  if (reaction != Reaction::None && reaction != Reaction::Sleep &&
      reaction != Reaction::Alarm &&
      (int32_t)(now - reactionUntil) >= 0) {
    reaction = Reaction::None;
  }

  if (!present) {
    if (lastProbeAt != 0 && (uint32_t)(now - lastProbeAt) < kProbeMs) return;
    lastProbeAt = now;
    // Panel wired up after boot: initialise it before rendering.
    if (!attachPanel()) {
      if ((int32_t)(now - nextScanLogAt) >= 0) {
        nextScanLogAt = now + 60000;
        logBusScan();
      }
      return;
    }
    present = true;
    lastMask = relays.mask();
    lastFrameAt = now - kFrameMs;
  }

  uint16_t mask = relays.mask();
  if (mask != lastMask) {
    lastMask = mask;
    happyUntil = now + kHappyMs;
  }

  bool linkDown = WiFi.status() != WL_CONNECTED || cloud::status() == cloud::OFFLINE;
  if (linkDown) {
    if (sleepSince == 0) sleepSince = now;
  } else {
    sleepSince = 0;
  }

  if ((int32_t)(now - nextClaimCheck) >= 0) {
    claimed = store::isClaimed();
    nextClaimCheck = now + kClaimCheckMs;
  }

  uint32_t interval = reacting(now) ? kReactFrameMs : kFrameMs;
  if ((int32_t)(now - lastFrameAt) < (int32_t)interval) return;
  lastFrameAt = now;

  if ((uint32_t)(now - lastKeepAliveAt) >= kKeepAliveMs) {
    lastKeepAliveAt = now;
    ensurePanelAlive(now);
  }
  if (!present) return;

  render(now);
}

}  // namespace display

#else

namespace display {

void begin() {}
void tick() {}
void react(Reaction) {}
bool alarmActive() { return false; }
void dismissAlarm() {}

}  // namespace display

#endif  // DISPLAY_ENABLED
