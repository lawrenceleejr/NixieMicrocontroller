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

// Shortest period in CPU cycles. The interrupt must run once per period, and
// it must not fall too far behind when the serial or millis() interrupts
// are also running. At 16 MHz, 160 cycles allows up to 100 kHz.
static const uint16_t kMinCycles = 160;

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
