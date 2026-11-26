// FracDivider.h - exact-average frequency synthesis from an integer timer.
//
// The IB-1100 shows the number of input edges it counts during its gate.
// With a 1 s gate, a square wave of exactly N Hz shows "N". A hardware timer
// can only make periods that are a whole number of clock ticks, so most
// frequencies cannot be hit exactly with one fixed period (e.g. 16 MHz / 7 Hz
// = 2285714.28... ticks).
//
// The fix is a fractional-N divider (Bresenham's algorithm): every
// output period is either `whole` or `whole + 1` ticks, chosen so the running
// average equals the exact non-integer period. Any window that is exactly
// one second long then contains exactly N edges.
//
// The fraction is kept to 16 bits so the per-period interrupt stays short
// (~70 cycles on an AVR). The rounding error is at most 2^-17 tick per period,
// which is 0.1 ppm at the 160-tick minimum period: below a crystal's drift.
//
// This header has no hardware dependencies so it can be unit-tested on a PC
// (see firmware/test/).
#pragma once
#include <stdint.h>

namespace nixie {

// Clock prescalers available on the AVR 16-bit Timer1.
static const uint16_t kPrescalers[] = {1, 8, 64, 256, 1024};
static const uint8_t kNumPrescalers = sizeof(kPrescalers) / sizeof(kPrescalers[0]);

struct PeriodPlan {
  uint8_t prescIndex;  // index into kPrescalers
  uint16_t whole;      // integer timer ticks per output period
  uint16_t frac;       // fractional ticks per period, in units of 2^-16
};

enum PlanResult : uint8_t {
  PLAN_OK = 0,
  PLAN_OFF,       // 0 Hz requested: stop the output
  PLAN_TOO_FAST,  // period shorter than minTicks CPU cycles
  PLAN_TOO_SLOW,  // period does not fit 16 bits even at the largest prescaler
  PLAN_BAD_CAL,   // calibration outside +/-5 %
};

// Work out how to make `mHz` millihertz from a timer clocked at `fcpu`.
//
//   fcpu      nominal CPU clock in Hz (F_CPU)
//   ppb       clock error in parts per billion; positive means the crystal
//             runs fast (more ticks per true second than nominal)
//   mHz       requested output frequency in millihertz
//   minTicks  shortest allowed period in CPU cycles (the ISR must keep up)
inline PlanResult planPeriod(uint32_t fcpu, int32_t ppb, uint32_t mHz,
                             uint16_t minTicks, PeriodPlan &out) {
  if (mHz == 0) return PLAN_OFF;
  if (ppb > 50000000L || ppb < -50000000L) return PLAN_BAD_CAL;

  // num / mHz = ticks per output period, where num = f_eff * 1000.
  // f_eff = fcpu * (1 + ppb * 1e-9)  ->  num = fcpu*1000 + fcpu*ppb/1e6
  const int64_t corr = ((int64_t)fcpu * (int64_t)ppb) / 1000000LL;
  const uint64_t num = (uint64_t)fcpu * 1000ULL + (uint64_t)corr;

  for (uint8_t i = 0; i < kNumPrescalers; ++i) {
    const uint64_t den = (uint64_t)kPrescalers[i] * (uint64_t)mHz;
    const uint64_t whole = num / den;
    if (i == 0 && whole < minTicks) return PLAN_TOO_FAST;
    if (whole > 65535ULL) continue;  // try a bigger prescaler
    if (den >> 40) return PLAN_TOO_FAST;  // keeps (rem << 17) inside 64 bits
    const uint64_t rem = num % den;
    // Round the fraction to 16 bits; a result of 65536 carries into whole.
    uint32_t frac = (uint32_t)(((rem << 17) / den + 1) >> 1);
    uint64_t w = whole;
    if (frac > 0xFFFF) {
      frac = 0;
      ++w;
      if (w > 65535ULL) continue;
    }
    out.prescIndex = i;
    out.whole = (uint16_t)w;
    out.frac = (uint16_t)frac;
    return PLAN_OK;
  }
  return PLAN_TOO_SLOW;
}

// Hands out the sequence of integer periods. Called once per output period
// from the timer interrupt, so it is kept tiny: one 16-bit add and a carry.
class FracDivider {
 public:
  void load(uint16_t whole, uint16_t frac) {
    whole_ = whole;
    frac_ = frac;
  }
  void reset() { acc_ = 0x8000; }  // start half-way: symmetric rounding

  // Next period length in ticks: whole_, or whole_ + 1 when the fractional
  // accumulator wraps.
  __attribute__((always_inline)) inline uint16_t next() {
    const uint16_t a = acc_ + frac_;
    const uint16_t p = whole_ + (a < acc_ ? 1 : 0);
    acc_ = a;
    return p;
  }

 private:
  uint16_t whole_ = 0;
  uint16_t frac_ = 0;
  uint16_t acc_ = 0x8000;
};

// Output frequency (mHz) needed to show `number` on a counter whose gate is
// `gateMs` milliseconds: the counter displays edges-per-gate. Returns false if
// the result does not fit 32 bits.
inline bool numberToMilliHz(uint32_t number, uint32_t gateMs, uint32_t &mHz) {
  if (gateMs == 0) return false;
  const uint64_t v = ((uint64_t)number * 1000000ULL) / gateMs;
  if (v > 0xFFFFFFFFULL) return false;
  mHz = (uint32_t)v;
  return true;
}

// Clock error in ppb, measured against a reference of known frequency.
//   edges      reference edges counted
//   refHz      true frequency of the reference
//   elapsedUs  time those edges took, measured with the local clock (micros())
// A positive result means the local clock runs fast.
inline int32_t clockErrorPpb(uint32_t edges, uint32_t refHz, uint32_t elapsedUs) {
  if (edges == 0 || refHz == 0) return 0;
  // ratio = elapsedUs / (edges * 1e6 / refHz); ppb = (ratio - 1) * 1e9
  const int64_t diff = (int64_t)elapsedUs * refHz - (int64_t)edges * 1000000LL;
  return (int32_t)((diff * 1000LL) / (int64_t)edges);
}

}  // namespace nixie
