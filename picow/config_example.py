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

EXERCISE_MINUTES = 60         # light every digit this often (prevents cathode poisoning;
                              # the tally keeps counting meanwhile)
SHOW_IP_AT_BOOT = True        # show the last part of the IP address for 10 s

# Optional particle detector for the "muons_today" source (see picow/README.md).
DETECTOR = None
# Pulse output, e.g. a MightyOhm Geiger kit (3 V, 100 us pulses on its PULSE header):
#   DETECTOR = {"type": "pulse", "pin": 18}
# Two pulse detectors stacked, counting only coincidences (mostly muons):
#   DETECTOR = {"type": "pulse", "pin": 18, "pin_b": 19, "window_us": 100}
# CosmicWatch in coincidence mode: the slave's serial TX through a divider to GP1:
#   DETECTOR = {"type": "serial", "uart": 0, "rx_pin": 1, "baud": 9600}

# What to show: one number, all day. Each entry: source name, how long,
# optional params and format (scale/offset, overflow "clip"|"wrap"|"overrange",
# channel 0-9). More sources: see picow/README.md.
PLAYLIST = [
    {"source": "cosmic_rays"},   # neutrons from cosmic rays per minute, Oulu, Finland
]
# Other live science to try (one line each):
#   {"source": "cosmic_rays", "params": {"station": "THUL"}}  # Thule, Greenland
#   {"source": "sun_xrays"}      # the Sun's X-rays: digits = flare class, X lights the lamp
#   {"source": "deep_space"}     # light-seconds to the farthest spacecraft NASA hears now
#   {"source": "deep_space", "params": {"stat": "kbps"}}  # data arriving from space
#   {"source": "iss_distance"}   # km to the astronauts (needs LATITUDE/LONGITUDE)
#   {"source": "muons_today"}    # your own detector (needs DETECTOR above)
