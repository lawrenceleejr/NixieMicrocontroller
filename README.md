# NixieMicrocontroller

Turn a **Heathkit IB-1100** Nixie frequency counter (1972–75: five Nixie
digits plus an overrange lamp, 1 Hz – 30 MHz) into a live number display.

A frequency counter shows how many edges arrive during its gate time. With a
1-second gate, a square wave of exactly **N Hz** reads **N**. An Arduino makes
that square wave, and a small Python program decides what N should be:
births in the US so far today, earthquakes in the last 24 hours, the outdoor
temperature in kelvin, or any number from any JSON feed.

```
 data source ──► nixiefeed (Python, on a PC / Raspberry Pi) ──USB serial──►
     Arduino Nano/Uno "NixieCounter" ──D10: N-Hz square wave──► IB-1100 INPUT
```

## Why this is harder than `tone(pin, N)`

A timer can only make periods that are a whole number of clock ticks, so
16 MHz / 7 Hz = 2,285,714.29 ticks has to be rounded. Rounded periods read as
the wrong number, or a last digit that flickers. The firmware uses a
**fractional-N divider**: each period is either *k* or *k*+1 ticks, chosen
(Bresenham-style) so that **every** 1-second window holds exactly N edges. The
pin is driven by Timer1 hardware, so edges land on exact clock ticks, and a
short interrupt (92 cycles) picks each next period.

The remaining error is the Arduino's crystal against the counter's own time
base (typically 10–50 ppm, i.e. 1–5 counts at 99999). The firmware handles
this two ways:

* **Manual calibration**: `nixiefeed calibrate` walks you through it and
  stores the correction in EEPROM.
* **Reference lock (optional)**: feed a clock into D2, ideally tapped from the
  counter's own time base, and the firmware measures and cancels the error
  continuously.

The host tests simulate the timer edge by edge, slide a 1-second gate across
the output, and check the count for numbers from 1 to 100000, with and
without crystal error.

## Quick start

**1. Firmware** (Arduino IDE: open `firmware/NixieCounter/NixieCounter.ino`, or PlatformIO):

```sh
pio run -d firmware -e nanoatmega328 -t upload   # or -e uno, -e nanoatmega328new
```

**2. Wiring**: D10 → 4.7 kΩ → counter input centre; 1 kΩ from the input to
GND; Arduino GND → BNC shield. Set the counter to its 1-second gate (Hz). See
[docs/HARDWARE.md](docs/HARDWARE.md).

**3. Host software**:

```sh
pip install -e host
nixiefeed list                                    # what can be shown
nixiefeed run us_births_today                     # preview in the terminal
nixiefeed run us_births_today --port /dev/ttyUSB0 # on the tubes
nixiefeed calibrate --port /dev/ttyUSB0           # once, with the counter warmed up
nixiefeed run --config examples/playlist.toml     # rotate through several
```

On power-up, before any host connects, the firmware lights every digit
(00000, 11111 … 99999) and then shows 00000.

## What it can show

| source | what | example |
|---|---|---|
| `us_births_today` | US births since midnight, at the CDC 2024 rate (3,628,934/yr), arriving at random like real births | ~9,900 by midnight |
| `us_traffic_deaths_ytd` | US traffic deaths this year, at NHTSA's 2024 estimate (39,345/yr) | one every ~13 min |
| `world_births_hour` / `world_deaths_hour` | worldwide, this hour (UN averages) | ~15,000 / ~7,000 |
| `lightning_minute` | lightning flashes on Earth this minute (~44/s) | resets every minute |
| `heartbeats_today` | yours, at 70 bpm | |
| `clock` | seconds since midnight (0–86399 fits!), HHMM, day of year | |
| `countdown` | days/hours/… until a date | |
| `weather` | Open-Meteo, any variable, any place: e.g. kelvin ×100, hPa ×10 | 29452 = 21.37 °C |
| `quakes` | USGS: count in the last hour/day/week, or largest magnitude ×10 | |
| `solar_wind`, `kp_index` | NOAA space weather; Kp ≥ 5 means aurora | |
| `river` | any USGS stream gauge: flow or height | |
| `json` | **any JSON URL + a dotted path**, or the length of a list | town open-data portals |
| `rate` | your own model: anything at N per year, reset each minute/hour/day/year | |

More ideas, including hardware ones, are in [docs/IDEAS.md](docs/IDEAS.md).

## Layout

```
firmware/NixieCounter/   Arduino sketch (Uno, Nano, Pro Mini, Leonardo, Mega)
  FracDivider.h          the exact-frequency maths (portable, unit-tested)
  PulseEngine.*          AVR Timer1 back end
  CommandParser.h        serial protocol parser (portable, unit-tested)
firmware/test/           host-side C++ tests: `make -C firmware/test test`
host/nixiefeed/          Python: sources, formatting, runner, CLI
host/tests/              pytest suite (no network or hardware needed)
examples/playlist.toml   a rotating playlist
docs/                    hardware, protocol, ideas
```

## Tests

```sh
make -C firmware/test test        # timer simulation + parser
cd host && python -m pytest       # host package
pio run -d firmware               # firmware compiles for the Nano
```
