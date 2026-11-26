// PulseEngine.h - square-wave output on AVR Timer1 using FracDivider.
//
// Output pin (OC1B):  Uno / Nano / Pro Mini / Leonardo / Micro: D10
//                     Mega 2560:                                 D12
#pragma once
#include <stdint.h>
#include "FracDivider.h"

namespace engine {

#if defined(__AVR_ATmega2560__) || defined(__AVR_ATmega1280__)
static const uint8_t kOutputPin = 12;
#else
static const uint8_t kOutputPin = 10;
#endif

// Shortest period in CPU cycles. The interrupt (92 cycles) runs once per
// period and needs headroom for the serial and millis() interrupts. At 16 MHz,
// 150 cycles allows up to 106.6 kHz: enough for 99999 even with a crystal
// up to 6 % slow.
static const uint16_t kMinCycles = 150;

void begin();

// Set the output frequency in millihertz (0 stops the output). On an error
// the previous output keeps running.
nixie::PlanResult setMilliHz(uint32_t mHz);
uint32_t milliHz();

// Clock correction in parts per billion (+ = this board's crystal is fast).
nixie::PlanResult setCalibrationPpb(int32_t ppb);
int32_t calibrationPpb();

// The highest frequency that can be produced (Hz), for status messages.
uint32_t maxHz();

}  // namespace engine
