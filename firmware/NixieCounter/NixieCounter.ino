// NixieCounter - turn a frequency counter (Heathkit IB-1100) into a number display.
//
// The counter shows how many edges it sees per gate. This sketch outputs a
// square wave with exactly the right number of edges per gate, so the counter
// shows whatever number the host sends.
//
// Wiring (see docs/HARDWARE.md):
//   D10 (Mega: D12) -> attenuator -> counter INPUT     the number signal
//   D2              <- optional reference clock         see "R" command
//   GND             -- counter ground
//
// Serial: 115200 baud, one command per line (see docs/PROTOCOL.md):
//   N 12345   show a number            F 1234.567  raw frequency (Hz)
//   G 1000    counter gate in ms       C -12.5     clock correction (ppm)
//   R 1000    lock to a reference      T 2         test pattern
//   ?         status
#include <Arduino.h>
#include <EEPROM.h>
#include "CommandParser.h"
#include "FracDivider.h"
#include "PulseEngine.h"

static const char kVersion[] = "NIXIE 1.0";
static const uint8_t kRefPin = 2;  // INT0 on Uno/Nano/Mega

// ---------------------------------------------------------------- settings --
struct Settings {
  uint32_t magic;
  uint32_t gateMs;  // counter gate time
  int32_t calPpb;   // manual clock correction
  uint32_t refHz;   // reference clock on D2, 0 = off
};
static const uint32_t kMagic = 0x4E495831UL;  // "NIX1"
static Settings g_set;

static void loadSettings() {
  EEPROM.get(0, g_set);
  if (g_set.magic != kMagic || g_set.gateMs == 0 || g_set.gateMs > 100000UL) {
    g_set.magic = kMagic;
    g_set.gateMs = 1000;
    g_set.calPpb = 0;
    g_set.refHz = 0;
  }
}
static void saveSettings() { EEPROM.put(0, g_set); }  // put() skips unchanged bytes

// ----------------------------------------------------------- display state --
enum Mode : uint8_t { MODE_NUMBER, MODE_FREQ, MODE_COUNT_UP, MODE_EXERCISE };
static Mode g_mode = MODE_NUMBER;
static uint32_t g_number = 0;     // the number being shown (MODE_NUMBER)
static uint32_t g_freqMilli = 0;  // raw frequency (MODE_FREQ)
static uint32_t g_testValue = 0;
static uint32_t g_testNextMs = 0;

static const __FlashStringHelper *planError(nixie::PlanResult r) {
  switch (r) {
    case nixie::PLAN_TOO_FAST: return F("too fast");
    case nixie::PLAN_TOO_SLOW: return F("too slow");
    case nixie::PLAN_BAD_CAL: return F("calibration out of range");
    default: return F("unknown");
  }
}

static nixie::PlanResult showNumber(uint32_t n) {
  uint32_t mHz;
  if (!nixie::numberToMilliHz(n, g_set.gateMs, mHz)) return nixie::PLAN_TOO_FAST;
  return engine::setMilliHz(mHz);
}

// Re-apply whatever is meant to be on display (after a gate change etc.).
static void refresh() {
  switch (g_mode) {
    case MODE_NUMBER: showNumber(g_number); break;
    case MODE_FREQ: engine::setMilliHz(g_freqMilli); break;
    default: showNumber(g_testValue); break;
  }
}

// ------------------------------------------------------ reference locking --
// Count edges of a reference clock (for example the counter's own time base,
// or a GPS 1PPS) and measure how long they take with micros(). The ratio tells
// how fast this board's crystal is, and that correction is applied on top of
// the manual one.
static volatile uint32_t g_refEdges = 0;
static volatile uint32_t g_refLastUs = 0;
static uint32_t g_refStartEdges = 0, g_refStartUs = 0;
static bool g_refPrimed = false;
static int32_t g_refPpb = 0;  // filtered measurement
static bool g_refValid = false;

static void onRefEdge() {
  g_refLastUs = micros();
  g_refEdges++;
}

static void startReference() {
  detachInterrupt(digitalPinToInterrupt(kRefPin));
  g_refPrimed = false;
  g_refValid = false;
  g_refPpb = 0;
  if (g_set.refHz == 0) return;
  pinMode(kRefPin, INPUT);
  attachInterrupt(digitalPinToInterrupt(kRefPin), onRefEdge, RISING);
}

static void applyCalibration() {
  const int32_t total = g_set.calPpb + (g_refValid ? g_refPpb : 0);
  engine::setCalibrationPpb(total);
}

static void serviceReference() {
  if (g_set.refHz == 0) return;
  noInterrupts();
  const uint32_t edges = g_refEdges;
  const uint32_t lastUs = g_refLastUs;
  interrupts();

  if (!g_refPrimed) {
    if (edges == 0) return;
    g_refStartEdges = edges;
    g_refStartUs = lastUs;
    g_refPrimed = true;
    return;
  }
  // Measure over ~10 s (at least 10 edges) for sub-ppm resolution.
  uint32_t window = g_set.refHz * 10UL;
  if (window < 10) window = 10;
  const uint32_t n = edges - g_refStartEdges;
  if (n < window) return;

  const int32_t ppb = nixie::clockErrorPpb(n, g_set.refHz, lastUs - g_refStartUs);
  g_refStartEdges = edges;
  g_refStartUs = lastUs;
  if (ppb > 20000000L || ppb < -20000000L) {  // >2 %: wrong refHz or noisy input
    g_refValid = false;
    return;
  }
  // Exponential smoothing; the first measurement is taken as-is.
  g_refPpb = g_refValid ? g_refPpb + (ppb - g_refPpb) / 4 : ppb;
  g_refValid = true;
  applyCalibration();
}

// ---------------------------------------------------------- test patterns --
static void serviceTest() {
  if (g_mode != MODE_COUNT_UP && g_mode != MODE_EXERCISE) return;
  const uint32_t now = millis();
  if ((int32_t)(now - g_testNextMs) < 0) return;
  if (g_mode == MODE_COUNT_UP) {
    g_testValue = (g_testValue + 1) % 100000UL;
    g_testNextMs = now + (g_set.gateMs < 500 ? 1000 : 2 * g_set.gateMs);
  } else {
    // 00000, 11111, ... 99999 - lights every cathode to fight cathode
    // poisoning, then goes back to the number.
    const uint8_t d = (uint8_t)(g_testValue % 10);
    if (g_testValue == 99999UL) {
      g_mode = MODE_NUMBER;
      refresh();
      Serial.println(F("# exercise done"));
      return;
    }
    g_testValue = (d + 1) * 11111UL;
    g_testNextMs = now + 2 * g_set.gateMs + 500;
  }
  showNumber(g_testValue);
}

static void startTest(uint8_t which) {
  if (which == 1) {
    g_mode = MODE_COUNT_UP;
    g_testValue = 0;
  } else {
    g_mode = MODE_EXERCISE;
    g_testValue = 0;
  }
  g_testNextMs = millis() + 2 * g_set.gateMs + 500;
  showNumber(g_testValue);
}

// ----------------------------------------------------------------- status --
static void printMilli(int64_t v) {
  if (v < 0) {
    Serial.print('-');
    v = -v;
  }
  Serial.print((uint32_t)(v / 1000));
  Serial.print('.');
  const uint16_t f = (uint16_t)(v % 1000);
  if (f < 100) Serial.print('0');
  if (f < 10) Serial.print('0');
  Serial.print(f);
}

static void printStatus() {
  Serial.print(F("OK "));
  Serial.print(kVersion);
  Serial.print(F(" mode="));
  const char *modes[] = {"number", "freq", "countup", "exercise"};
  Serial.print(modes[g_mode]);
  Serial.print(F(" N="));
  Serial.print(g_mode == MODE_NUMBER ? g_number : g_testValue);
  Serial.print(F(" F="));
  printMilli(engine::milliHz());
  Serial.print(F(" G="));
  Serial.print(g_set.gateMs);
  Serial.print(F(" C="));
  printMilli(g_set.calPpb);  // ppb / 1000 = ppm
  Serial.print(F(" R="));
  Serial.print(g_set.refHz);
  if (g_set.refHz) {
    Serial.print(g_refValid ? F(" lock=") : F(" lock=waiting"));
    if (g_refValid) printMilli(g_refPpb);
  }
  Serial.print(F(" max="));
  Serial.println(engine::maxHz());
}

// --------------------------------------------------------------- commands --
static void reply(nixie::PlanResult r) {
  if (r == nixie::PLAN_OK || r == nixie::PLAN_OFF) {
    printStatus();
  } else {
    Serial.print(F("ERR "));
    Serial.println(planError(r));
  }
}

static void handle(const char *line) {
  nixie::Command c;
  const nixie::ParseResult pr = nixie::parseCommand(line, c);
  if (pr == nixie::PARSE_EMPTY) return;
  if (pr != nixie::PARSE_OK) {
    Serial.println(F("ERR syntax"));
    return;
  }
  const bool needsArg = c.op != '?';
  if (needsArg && !c.hasArg) {
    Serial.println(F("ERR missing value"));
    return;
  }
  const bool nonNeg = c.op != 'C';
  if (nonNeg && c.milli < 0) {
    Serial.println(F("ERR negative"));
    return;
  }

  switch (c.op) {
    case 'N': {  // number (integer part only)
      const int64_t n = c.milli / 1000;
      if (n > 999999) {
        Serial.println(F("ERR too big"));
        return;
      }
      const nixie::PlanResult r = showNumber((uint32_t)n);
      if (r == nixie::PLAN_OK || r == nixie::PLAN_OFF) {
        g_mode = MODE_NUMBER;
        g_number = (uint32_t)n;
      }
      reply(r);
      return;
    }
    case 'F': {  // raw frequency in Hz with up to 3 decimals
      if (c.milli > 0xFFFFFFFFLL) {
        Serial.println(F("ERR too big"));
        return;
      }
      const nixie::PlanResult r = engine::setMilliHz((uint32_t)c.milli);
      if (r == nixie::PLAN_OK || r == nixie::PLAN_OFF) {
        g_mode = MODE_FREQ;
        g_freqMilli = (uint32_t)c.milli;
      }
      reply(r);
      return;
    }
    case 'G': {  // gate in ms
      const int64_t ms = c.milli / 1000;
      if (ms < 1 || ms > 100000) {
        Serial.println(F("ERR gate must be 1..100000 ms"));
        return;
      }
      g_set.gateMs = (uint32_t)ms;
      saveSettings();
      refresh();
      printStatus();
      return;
    }
    case 'C': {  // manual calibration in ppm (milli-ppm == ppb)
      if (c.milli > 50000000LL || c.milli < -50000000LL) {
        Serial.println(F("ERR calibration out of range"));
        return;
      }
      g_set.calPpb = (int32_t)c.milli;
      saveSettings();
      applyCalibration();
      printStatus();
      return;
    }
    case 'R': {  // reference frequency in Hz, 0 = off
      const int64_t hz = c.milli / 1000;
      if (hz > 20000) {
        Serial.println(F("ERR reference must be <= 20000 Hz"));
        return;
      }
      g_set.refHz = (uint32_t)hz;
      saveSettings();
      startReference();
      applyCalibration();
      printStatus();
      return;
    }
    case 'T': {  // test pattern: 0 off, 1 count up, 2 exercise all digits
      const int64_t t = c.milli / 1000;
      if (t == 0) {
        g_mode = MODE_NUMBER;
        refresh();
      } else if (t == 1 || t == 2) {
        startTest((uint8_t)t);
      } else {
        Serial.println(F("ERR test must be 0, 1 or 2"));
        return;
      }
      printStatus();
      return;
    }
    case '?':
      printStatus();
      return;
    default:
      Serial.println(F("ERR unknown command"));
  }
}

// ------------------------------------------------------------ setup/loop --
static char g_line[40];
static uint8_t g_len = 0;
static bool g_overflow = false;

void setup() {
  Serial.begin(115200);
  loadSettings();
  engine::begin();
  startReference();
  applyCalibration();
  startTest(2);  // power-on: run every digit once, then show 0
  Serial.print(kVersion);
  Serial.println(F(" READY"));
}

void loop() {
  while (Serial.available()) {
    const char ch = (char)Serial.read();
    if (ch == '\n' || ch == '\r') {
      if (!g_overflow) {
        g_line[g_len] = 0;
        handle(g_line);
      } else {
        Serial.println(F("ERR line too long"));
      }
      g_len = 0;
      g_overflow = false;
    } else if (g_len < sizeof(g_line) - 1) {
      g_line[g_len++] = ch;
    } else {
      g_overflow = true;
    }
  }
  serviceReference();
  serviceTest();
}
