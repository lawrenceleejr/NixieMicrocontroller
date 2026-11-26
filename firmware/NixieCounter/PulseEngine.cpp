// PulseEngine.cpp - AVR Timer1 back end.
//
// Timer1 runs in Fast PWM mode 15: it counts 0..OCR1A, so OCR1A sets the
// period, and pin OC1B goes high at 0 and low at OCR1B, giving a ~50 % duty
// square wave. Both registers are double-buffered and only take a new value
// when the counter wraps, so a new period can be loaded without a glitch.
//
// The overflow interrupt fires once per period and loads the next value from
// the FracDivider. The pin is toggled by hardware, so the edges stay on exact
// clock ticks however late the interrupt runs.
//
// The interrupt runs during period k+1 and its value is used for period k+2.
// If it is ever so late that a whole period passes, the hardware just reuses
// the previous period length (whole or whole+1 ticks): a timing error of at
// most one tick, never a lost or extra edge.
#include <Arduino.h>
#include "PulseEngine.h"

namespace engine {
namespace {

nixie::FracDivider g_div;  // used by the ISR
uint8_t g_csBits = 0;      // current clock-select bits, 0 = stopped
uint32_t g_mHz = 0;
int32_t g_ppb = 0;

const uint8_t kCs[] = {
    _BV(CS10),              // /1
    _BV(CS11),              // /8
    _BV(CS11) | _BV(CS10),  // /64
    _BV(CS12),              // /256
    _BV(CS12) | _BV(CS10),  // /1024
};

__attribute__((always_inline)) inline void loadCompare(uint16_t p) {
  OCR1A = p - 1;         // TOP: counter runs 0..p-1, i.e. p ticks
  OCR1B = (p >> 1) - 1;  // high for p/2 ticks
}

void stopOutput() {
  const uint8_t sreg = SREG;
  cli();
  TIMSK1 &= ~_BV(TOIE1);
  TCCR1B = 0;  // clock off
  TCCR1A = 0;  // OC1B disconnected: pin returns to PORT control
  g_csBits = 0;
  SREG = sreg;
  digitalWrite(kOutputPin, LOW);
}

nixie::PlanResult apply() {
  nixie::PeriodPlan plan;
  const nixie::PlanResult r = nixie::planPeriod(F_CPU, g_ppb, g_mHz, kMinCycles, plan);
  if (r == nixie::PLAN_OFF) {
    stopOutput();
    return r;
  }
  if (r != nixie::PLAN_OK) return r;

  const uint8_t cs = kCs[plan.prescIndex];
  const uint8_t sreg = SREG;
  cli();
  g_div.load(plan.whole, plan.frac);
  if (cs != g_csBits) {
    // A new prescaler needs a clean restart (this only happens when the
    // frequency moves to another range).
    TIMSK1 &= ~_BV(TOIE1);
    TCCR1B = 0;  // stop, and select normal mode: OCR writes are immediate
    TCCR1A = 0;
    g_div.reset();
    const uint16_t p0 = g_div.next();
    loadCompare(p0);
    TCNT1 = 0;
    TCCR1A = _BV(COM1B1) | _BV(WGM11) | _BV(WGM10);  // fast PWM, non-inverting
    TCCR1B = _BV(WGM13) | _BV(WGM12);                // mode 15, still stopped
    loadCompare(p0);  // fill the double buffer too
    TIFR1 = _BV(TOV1);
    TIMSK1 |= _BV(TOIE1);
    TCCR1B = _BV(WGM13) | _BV(WGM12) | cs;  // go
    g_csBits = cs;
  }
  SREG = sreg;
  return nixie::PLAN_OK;
}

}  // namespace

void begin() {
  pinMode(kOutputPin, OUTPUT);
  stopOutput();
}

nixie::PlanResult setMilliHz(uint32_t mHz) {
  const uint32_t old = g_mHz;
  g_mHz = mHz;
  const nixie::PlanResult r = apply();
  if (r != nixie::PLAN_OK && r != nixie::PLAN_OFF) g_mHz = old;
  return r;
}

uint32_t milliHz() { return g_mHz; }

nixie::PlanResult setCalibrationPpb(int32_t ppb) {
  if (ppb > 50000000L || ppb < -50000000L) return nixie::PLAN_BAD_CAL;
  const int32_t old = g_ppb;
  g_ppb = ppb;
  if (g_mHz == 0) return nixie::PLAN_OK;
  const nixie::PlanResult r = apply();
  if (r != nixie::PLAN_OK) {
    g_ppb = old;
    apply();
  }
  return r;
}

int32_t calibrationPpb() { return g_ppb; }

uint32_t maxHz() { return F_CPU / kMinCycles; }

}  // namespace engine

ISR(TIMER1_OVF_vect) { engine::loadCompare(engine::g_div.next()); }
