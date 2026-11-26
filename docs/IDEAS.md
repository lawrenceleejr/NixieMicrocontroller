# Things to show

Five digits, no letters, one update a second, and a warm orange glow. The
best numbers for that are ones that **tick visibly** (you can watch them
change), **mean something at a glance**, and **fit in 0–99999**.

## Built in

| | source | why it's good on Nixies |
|---|---|---|
| 👶 | `us_births_today` | Ticks every ~9 s and arrives at random, so it looks alive. ~9,900 by midnight. |
| 🚗 | `us_traffic_deaths_ytd` | One every ~13 minutes, about 39,000 by New Year's Eve. A sobering, slow odometer. |
| 🌍 | `world_births_hour` vs `world_deaths_hour` | Run both in a playlist: ~15,000 against ~7,000 each hour, i.e. population growth. |
| ⚡ | `lightning_minute` | Races to ~2,600 and snaps back to zero every minute. |
| 🌡 | `weather` in kelvin ×100 | `29452`: room-temperature physics, and it never goes negative. |
| 🧭 | `weather` pressure ×10 | `10132` hPa; falling numbers mean weather is coming. |
| 🌋 | `quakes` | Earthquakes worldwide in the last 24 h (often 150–300). |
| ☀ | `solar_wind`, `kp_index` | Kp ≥ 500 (×100) means a geomagnetic storm and aurora alerts. |
| 🏞 | `river` | Your local river's flow in ft³/s from a USGS gauge. Watch it rise after rain. |
| ⏱ | `clock` | Seconds since midnight is 0–86399, a clock that fits exactly. |
| 📅 | `countdown` | Days to a deadline, launch, holiday or retirement. |

## With the `json` source (no new code)

`nixiefeed run json -p url=… -p path=… [-p count=true]`

* **Your town's open-data portal.** Many cities run Socrata/ArcGIS portals
  with JSON APIs: 311 requests opened today, building permits this month,
  library checkouts, police calls, bike-share trips. A query URL with
  `$where=date > today` plus `count=true` gives "how many today".
* **Air quality**: Open-Meteo's air-quality API (`us_aqi`, PM2.5).
* **Bitcoin block height** (mempool.space): goes up roughly every 10 minutes;
  use `--overflow wrap` to show the last five digits.
* **CO₂ at Mauna Loa** ×100 (`42612` = 426.12 ppm) from NOAA GML's data files.
* **Your own things**: a GitHub repo's stars, open issues, CI minutes; Home
  Assistant sensors (it has a REST API); a lab's run number or luminosity, if
  the experiment publishes it as JSON.

## With a small new source (a few lines of Python)

Add a class in `host/nixiefeed/sources/` with `@register`, set `refresh_s`,
and return a float from `read()`.

* **arXiv submissions today** in your category (the arXiv API returns Atom
  XML, so it needs `xml.etree` rather than `json`).
* **Wikipedia edits this minute** from the Wikimedia EventStreams feed (a
  streaming source: count events in a background thread).
* **Next bus/train in seconds**, from your transit agency's GTFS-realtime
  feed.
* **ISS distance from your town** in km (from its orbital elements).

## Hardware ideas: it *is* a counter

* **Cosmic ray muons.** Plug a Geiger counter or a CosmicWatch-style
  scintillator straight into the IB-1100 and it counts particles per second
  with no Arduino at all. Or feed detector pulses to the Arduino, keep a
  running total, and show "muons through this desk since 9 am".
* **The leading tube as a label**: `--channel N` (or `channel = N` in the
  playlist) reserves the first digit as a source number, so a rotating
  playlist reads `1 9832`, `2 0142`, `3 9452`. Print a small legend card for
  the front panel.
* **Labels with lamps**: spare Arduino pins can drive small LEDs or
  incandescent grain-of-wheat bulbs behind a printed legend ("BIRTHS",
  "QUAKES", "K") that light with each playlist entry.
* **Faster boards**: an RP2040 (PIO) or ESP32 can generate exact frequencies
  into the MHz. That would let you use the counter's shorter gates (several
  readings a second), light the overrange lamp as a sixth digit "1", and fetch
  data over Wi-Fi without a host computer.
