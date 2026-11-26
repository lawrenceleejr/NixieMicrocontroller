# Hardware

## Parts

* **Arduino Nano** (ATmega328P, 16 MHz). Choose one with a **quartz crystal**
  (a metal can marked 16.000). The Uno R3 clocks its main chip from a ceramic
  resonator, which is about 100× less stable. It works, but the last digit
  will wander unless you use the reference lock below.
* Two resistors (4.7 kΩ, 1 kΩ) and a BNC lead to the counter's input.
* Something to run `nixiefeed`: your PC, or a Raspberry Pi Zero 2 W to make
  the display standalone.

## Wiring

```
Arduino D10 ──[4.7 kΩ]──┬────────── BNC centre ──► IB-1100 INPUT
                         │
                      [1 kΩ]
                         │
Arduino GND ────────────┴────────── BNC shield
```

The divider turns the 5 V square wave into about 0.9 V peak to peak. That
is far above the sensitivity of any counter of this kind, and far below
anything that could stress its input. Check the input limits in your
manual.

Pins: the output is OC1B (D10 on Uno/Nano/Pro Mini/Leonardo, D12 on a Mega).
Don't use `analogWrite` on D9/D10, which also use Timer1.

## Counter settings

The number shown is *edges counted per gate*. Set the IB-1100 to the range
that uses its **1-second gate** (reading in Hz). Five digits then cover
00000–99999, and the Arduino tops out at 100 kHz. If you use a different gate,
tell the firmware: `nixiefeed run … --gate-ms 100` (or send `G 100`), and it
scales the frequency (a 0.1 s gate updates faster but needs 10× the frequency,
so only four digits are usable).

Turn the sensitivity / trigger level so the count is steady on a test value
(`nixiefeed show 12345 --port …`).

## Calibration: why the last digit flickers, and the fix

The counter reads N only if the Arduino and the counter agree on how long a
second is. If they differ by δ (in ppm), the reading is off by about
N·δ/10⁶ counts. When that comes to a fraction of a count, it shows up as
occasional ±1 flicker:

| relative error | at 1,234 | at 99,999 |
|---|---|---|
| 50 ppm (uncalibrated crystal) | 0.06 count, ~6 % of readings ±1 | 5 counts off |
| 5 ppm | 0.6 % flicker | 0.5 count: flickers half the time |
| 0.5 ppm (calibrated, warm) | invisible | 5 % flicker |

**Manual calibration** (do it after both instruments have warmed up for
30 minutes):

```sh
nixiefeed calibrate --port /dev/ttyUSB0
```

It sends 99999 Hz. Type what the counter shows (e.g. `100003`) for a coarse
correction, then use `+` / `-` to trim until the last digit is steady. The
result goes into EEPROM (`C` in the status line).

**Reference lock** (optional, removes drift). Connect a clean logic-level
clock of known frequency (≤ 20 kHz) to **D2** and send `R <Hz>`
(`nixiefeed raw "R 1000" --port …`). The firmware counts its edges for 10 s
at a time, works out how fast its own crystal is running, and corrects
continuously (`lock=` in the status line, in ppm).

* **Best: the counter's own time base.** Counters of this era divide a
  crystal oscillator down through a chain of decade-divider ICs; any TTL
  stage of 1 kHz–10 kHz is perfect. The Arduino then locks to the clock
  that sets the gate, so drift in either crystal cancels and calibration is
  unnecessary. Use a 1 kΩ series resistor into D2 and a common ground.
  ⚠ The IB-1100 is mains-powered and its Nixie supply is well over 100 V DC.
  Unplug it and let it discharge before probing, and trace the time base
  from the manual's schematic.
* **GPS 1PPS** (`R 1`) makes the Arduino accurate to absolute time. That
  only removes flicker if the counter's own time base is also right, so
  trim the counter's crystal against the same GPS first.

The manual and reference corrections add together.

## Running standalone

A Raspberry Pi Zero 2 W with the Nano plugged into its USB port works well.
`/etc/systemd/system/nixiefeed.service`:

```ini
[Unit]
Description=Nixie counter feed
After=network-online.target
Wants=network-online.target

[Service]
User=pi
ExecStart=/home/pi/.local/bin/nixiefeed run --config /home/pi/playlist.toml --port /dev/ttyUSB0
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`sudo systemctl enable --now nixiefeed`. Use `/dev/serial/by-id/…` instead
of `/dev/ttyUSB0` so that the name doesn't change between boots.

## Tube care

Nixie digits that are never lit slowly get contaminated ("cathode
poisoning"); a counter that shows the same few digits for months is at risk.
The firmware lights every digit at power-up, and `nixiefeed` does it once an
hour by default (`--exercise-minutes`, or `exercise_every_minutes` in the
playlist).
