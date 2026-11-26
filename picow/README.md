# Pico W: standalone, on Wi-Fi

One Raspberry Pi Pico W does everything: counts cosmic rays (or fetches data
over Wi-Fi) and drives the IB-1100. No computer and no Arduino needed.

The default configuration shows one number all day: **cosmic-ray muons that
have passed through the detector beside the counter since midnight**. Each
tick is a particle from an air shower that started high in the atmosphere,
usually set off by a cosmic ray from far outside the solar system. Arrivals
are random, so the count goes up in clumps and pauses, and now and then
several particles from one shower arrive together.

```
Pico W GP16 ──[2.2 kΩ]──┬──────── BNC centre ──► IB-1100 INPUT
                         │
                      [1 kΩ]
                         │
Pico W GND ─────────────┴──────── BNC shield          (≈1 V peak to peak)

detector:  pulse output ──► GP18  (or two detectors: GP18 + GP19)
           or CosmicWatch serial TX ──[10 kΩ]──┬──► GP1        (5 V logic → 3.3 V)
                                            [20 kΩ]
                                               └── GND
optional: counter time-base tap or GPS 1PPS ──[1 kΩ]──► GP17   (3.3 V logic only!)
```

Power the Pico from any USB charger.

## Cosmic-ray detector

| detector | what it counts | rate | wiring |
|---|---|---|---|
| **Two CosmicWatch detectors in coincidence** (recommended) | muons: both scintillators must fire within ~30 µs | ~0.28/s measured by the designers (4th floor of an office building), i.e. ~17/min, **~24,000 per day** | slave's serial TX through the divider to GP1: `DETECTOR = {"type": "serial", ...}` |
| One CosmicWatch | muons plus some radioactivity and noise | higher | same |
| **Geiger counter kit** with a pulse output, e.g. MightyOhm (3 V, 100 µs pulse per count on its PULSE header) | all background radiation; cosmic rays are only part of it, most is local radioactivity | tens per minute | PULSE → GP18 directly: `DETECTOR = {"type": "pulse", "pin": 18}` |
| Two Geiger tubes, stacked | coincidences: mostly muons | much lower than singles | GP18 + GP19, `"pin_b": 19` |

[CosmicWatch](https://github.com/spenceraxani/CosmicWatch-Desktop-Muon-Detector-v2)
(MIT; Axani et al., arXiv:1801.03029) is an open-source scintillator and
silicon-photomultiplier detector you build from a kit of parts. In coincidence
mode the two detectors are joined by a cable, and the second one (the
"slave") records an event only when the first also fired. Each event is one
line on its serial output (9600 baud; header lines start with `###`), so
the Pico counts lines that start with a number. The serial TX is the Arduino
Nano's **D1** pin, at about 5 V: use the 10 k / 20 k divider. This line
format is confirmed from the v2 firmware source. The current **v3X** ships
only compiled firmware, so check its output in a serial monitor first.

Expect roughly 24,000 by midnight with a CosmicWatch pair, so the five digits
roll through a satisfying range each day. If the count ever passes 99999, the
overrange lamp lights and the digits carry on.

The day's tally is saved to flash every 5 minutes, so a power cut loses at
most that much, and it restarts at **local** midnight (time zone from your
latitude/longitude).

### Why the display never steps backwards

While a number is being counted, its value changes about once a second, often
in the middle of the counter's gate. The pulses in each 1-second frame sit
1 µs apart at the start of the frame, so going from N to N+1 appends one pulse
and moves nothing else. A gate that straddles the change reads N or N+1,
never N−1 or N+2. The tests check this with random updates while the
emulated PIO runs: zero bad readings, against about 5 % of gate positions
when the pulses were spread evenly across the second.

## How the frequency is made

The counter shows the number of rising edges in its 1-second gate. The
RP2040's PIO (a tiny programmable I/O processor) plays a repeating
**frame** that is exactly one gate long, 125,000,000 clock ticks, and holds
exactly N edges: N pulses 1 µs apart, then a pause for the rest of the
second. Because the pattern repeats exactly every gate, any 1-second window
holds exactly N edges, wherever the counter's gate starts. A DMA channel feeds
the PIO the same four numbers over and over, so after start-up the output
uses no CPU at all; Wi-Fi traffic and slow web requests can't disturb it.

Compared with the Arduino version this is exact, with no interrupt to keep up
with, and it can show up to 999,999 per 1-second gate. That means:

* `"overflow": "overrange"` works: values above 99999 light the counter's
  overrange lamp and show the last five digits.
* Shorter counter gates (e.g. `GATE_MS = 100`) for faster updates.

The one remaining error is the Pico's crystal against the counter's (~10–30
ppm). Calibrate once from the web page, or connect a reference to GP17 (see
`docs/HARDWARE.md`, *Reference lock*).

## Install

1. Hold BOOTSEL, plug in the Pico W, and copy the latest **MicroPython for
   Pico W** `.uf2` (micropython.org/download/RPI_PICO_W) onto the drive that
   appears.
2. `pip install mpremote`
3. Copy `config_example.py` to `config.py` and fill in your Wi-Fi network,
   latitude/longitude and playlist.
4. Copy the files over and restart:

   ```sh
   cd picow
   mpremote cp app.py detector.py display.py main.py nixie_pio.py sources.py web.py config.py :
   mpremote reset
   ```

   `mpremote` (no arguments) opens the console and shows the boot messages.

## Using it

At power-up the tubes show `00000` while connecting, then **the last part of
the Pico's IP address** for 10 s (`00042` means 192.168.1.42), then light every
digit once, then start the playlist.

Open `http://nixie.local/` (or `http://192.168.1.42/`) on your phone:

* see what's showing, the raw value and any fetch error
* **show** a fixed number, **hold** the current item, skip to the **next** one
* **calibrate**: show 99999, wait for both instruments to warm up, type what
  the counter reads, then nudge with ±0.2 ppm until the last digit is steady.
  The correction is saved on the Pico (`settings.json`).

`/api` returns the same status as JSON.

## Sources

Same ideas as the PC version (`nixiefeed`), rewritten for MicroPython and
only using APIs with small responses:

`us_births_today`, `us_traffic_deaths_ytd`, `world_births_hour`,
`world_deaths_hour`, `lightning_minute`, `heartbeats_today`, `rate` (your own
model), `clock`, `countdown`, `quakes`, `weather`, `solar_wind`, `kp_index`,
`river`, `json` (any URL + dotted path), and `muons_today` / `detector`
(the hardware counter).

To add your own, write a class in `sources.py` with `read(ctx)` returning a
number and add it to `SOURCES`.

## Tests (on a PC)

```sh
python -m pytest picow/tests
```

These run the real PIO program in a cycle-accurate emulator, along with the
sources, calibration, playlist and web routes, using stand-ins for the Pico's
hardware modules. They do not prove the code on real MicroPython: the first
run on the board is the real test.
