# Pico W: standalone, on Wi-Fi

One Raspberry Pi Pico W does everything: fetches data over Wi-Fi and drives
the IB-1100. No computer and no Arduino needed.

```
Pico W GP16 ──[2.2 kΩ]──┬──────── BNC centre ──► IB-1100 INPUT
                         │
                      [1 kΩ]
                         │
Pico W GND ─────────────┴──────── BNC shield          (≈1 V peak to peak)

optional: counter time-base tap or GPS 1PPS ──[1 kΩ]──► GP17   (3.3 V logic only!)
```

Power the Pico from any USB charger.

## How the frequency is made

The counter shows the number of rising edges in its 1-second gate. The
RP2040's PIO (a tiny programmable I/O processor) plays a repeating
**frame** that is exactly one gate long, 125,000,000 clock ticks, and holds
exactly N edges: N periods of `q` ticks, then a short pause of the leftover
ticks. Because the pattern repeats exactly every gate, any 1-second window
holds exactly N edges, wherever the counter's gate starts. A DMA channel feeds
the PIO the same four numbers over and over, so after start-up the output
uses no CPU at all; Wi-Fi traffic and slow web requests can't disturb it.

Compared with the Arduino version this is exact, with no interrupt to keep up
with, and it goes up to ~17 MHz. That means:

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
   mpremote cp app.py display.py main.py nixie_pio.py sources.py web.py config.py :
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
`river`, `json` (any URL + dotted path).

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
