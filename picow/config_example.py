# Copy this file to config.py on the Pico W and fill it in.
# (config.py is git-ignored so your Wi-Fi password stays off GitHub.)

WIFI_SSID = "your-network"
WIFI_PASSWORD = "your-password"
HOSTNAME = "nixie"            # web page at http://nixie.local/ (or the IP shown at boot)

# Your town: used by "weather" and to work out the local time zone.
LATITUDE = 0.0
LONGITUDE = 0.0
UTC_OFFSET_HOURS = "auto"     # "auto" (from Open-Meteo, follows DST) or e.g. -5

# Hardware
OUTPUT_PIN = 16               # GP16 -> 2.2k -> counter input; 1k from input to GND
GATE_MS = 1000                # the counter's gate time (1 s = the Hz range)
DIGITS = 5
CAL_PPM = 0.0                 # starting crystal correction; the web page saves its own
REF_PIN = None                # optional reference clock input, e.g. 17
REF_HZ = 0                    # its frequency (counter time base tap, or 1 for GPS 1PPS)

EXERCISE_MINUTES = 60         # light every digit this often (prevents cathode poisoning)
SHOW_IP_AT_BOOT = True        # show the last part of the IP address for 10 s

# What to show. Each entry: source name, how long, optional params and format:
#   scale/offset: shown = value * scale + offset
#   overflow: "clip" | "wrap" | "overrange" (lights the lamp past 99999)
#   channel: 0-9 uses the first tube as a label
PLAYLIST = [
    {"source": "us_births_today", "minutes": 3},
    {"source": "us_traffic_deaths_ytd", "minutes": 3},
    # Outdoor temperature in kelvin to 0.01 K: 21.37 C -> 29452
    {"source": "weather", "minutes": 2,
     "params": {"temperature_unit": "celsius"}, "scale": 100, "offset": 27315},
    # Pressure in tenths of a hPa: 1013.2 -> 10132
    {"source": "weather", "minutes": 2,
     "params": {"variable": "surface_pressure"}, "scale": 10},
    {"source": "quakes", "minutes": 2},
    {"source": "solar_wind", "minutes": 2},
    # More: {"source": "clock"} (seconds since midnight), {"source": "kp_index"},
    # {"source": "countdown", "params": {"target": "2027-01-01"}},
    # {"source": "river", "params": {"site": "01646500"}},
    # {"source": "json", "params": {"url": "https://...", "path": "a.b.0"}},
]
