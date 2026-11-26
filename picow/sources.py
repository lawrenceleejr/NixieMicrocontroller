# sources.py - things to show, for MicroPython on the Pico W (also runs on CPython).
#
# Every source has read(ctx) -> float and a refresh_s telling how often it
# is worth calling. ctx carries the time (UTC seconds), the local UTC offset,
# and the location from config.py.
#
# The Pico W has about 150 KB of free RAM, so live sources use endpoints with
# small responses (earthquakes come from USGS's "count" service), ask for a
# short time window (neutron monitors), or read a big file in pieces and keep
# only its end (GOES X-rays, http_text(tail=...)).

import json
import math
import time

try:
    import requests
except ImportError:  # CPython without requests: the tests patch http_json
    requests = None

USER_AGENT = "nixiefeed-picow/1.0"
SECONDS_PER_YEAR = 365.2425 * 86400


class SourceError(Exception):
    pass


class Context:
    def __init__(self, now=0, offset=0, latitude=0.0, longitude=0.0):
        self.now = now          # UTC, seconds since 1970
        self.offset = offset    # local time = now + offset
        self.latitude = latitude
        self.longitude = longitude

    @property
    def local(self):
        return self.now + self.offset


# ------------------------------------------------------------------ helpers --
def http_json(url):
    if requests is None:
        raise SourceError("no requests module")
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    except Exception as e:
        raise SourceError("%s: %s" % (url.split("?")[0], e))
    try:
        if r.status_code != 200:
            raise SourceError("HTTP %d from %s" % (r.status_code, url.split("?")[0]))
        return r.json()
    except ValueError:
        raise SourceError("bad JSON from %s" % url.split("?")[0])
    finally:
        r.close()


def http_text(url, tail=None):
    """GET a URL as text. With tail=N, stream it and keep only the last N
    bytes, so a file much bigger than the Pico's memory can still be read."""
    if requests is None:
        raise SourceError("no requests module")
    short = url.split("?")[0]
    try:
        # identity: no gzip, so the raw stream is plain text on every platform
        r = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
                         timeout=20, stream=True)
    except Exception as e:
        raise SourceError("%s: %s" % (short, e))
    try:
        if r.status_code != 200:
            raise SourceError("HTTP %d from %s" % (r.status_code, short))
        if tail is None:
            return r.content.decode()
        keep = b""
        while True:
            chunk = r.raw.read(1024)
            if not chunk:
                break
            keep = (keep + chunk)[-tail:]
        return keep.decode()
    except OSError as e:
        raise SourceError("%s: %s" % (short, e))
    finally:
        r.close()


def xml_tags(xml, name):
    """Attribute dicts of every <name .../> tag (enough for simple feeds;
    MicroPython's re module has no finditer)."""
    out = []
    start = 0
    opener = "<" + name + " "
    while True:
        i = xml.find(opener, start)
        if i < 0:
            return out
        j = xml.find(">", i)
        tag = xml[i + len(opener):j]
        attrs = {}
        k = 0
        while True:
            eq = tag.find('="', k)
            if eq < 0:
                break
            key = tag[tag.rfind(" ", 0, eq) + 1:eq].strip()
            end = tag.find('"', eq + 2)
            attrs[key] = tag[eq + 2:end]
            k = end + 1
        out.append(attrs)
        start = j


def dig(data, path):
    """Follow 'a.b.0.c' into JSON. Negative list indices count from the end."""
    for part in [p for p in path.split(".") if p]:
        if isinstance(data, list):
            try:
                data = data[int(part)]
            except (ValueError, IndexError):
                raise SourceError("bad list index %s in %s" % (part, path))
        elif isinstance(data, dict):
            if part not in data:
                raise SourceError("no key %s in %s" % (part, path))
            data = data[part]
        else:
            raise SourceError("cannot look inside a value at %s" % part)
    return data


def days_from_civil(y, m, d):
    """Days since 1970-01-01 for a calendar date (any year)."""
    y -= 1 if m <= 2 else 0
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def epoch(y, mo, d, h=0, mi=0, s=0):
    return days_from_civil(y, mo, d) * 86400 + h * 3600 + mi * 60 + s


def parse_iso(text):
    """'2027-01-01' or '2027-03-14T15:09[:26]' -> seconds (no time zone)."""
    text = text.replace(" ", "T")
    if "T" in text:
        date, clock = text.split("T", 1)
    else:
        date, clock = text, ""
    y, mo, d = [int(v) for v in date.split("-")]
    parts = [int(v) for v in clock.split(":")] if clock else []
    while len(parts) < 3:
        parts.append(0)
    return epoch(y, mo, d, parts[0], parts[1], parts[2])


def iso_utc(t):
    tm = time.gmtime(t)
    return "%04d-%02d-%02dT%02d:%02d:%02d" % tuple(tm[:6])


PERIODS = ("minute", "hour", "day", "year", "never")


def period_start(local, reset):
    tm = time.gmtime(local)
    if reset == "minute":
        return local - tm[5]
    if reset == "hour":
        return local - tm[4] * 60 - tm[5]
    if reset == "day":
        return epoch(tm[0], tm[1], tm[2])
    if reset == "year":
        return epoch(tm[0], 1, 1)
    raise SourceError("reset must be one of " + ", ".join(PERIODS))


def fnv1a(text):
    h = 0x811C9DC5
    for ch in text.encode():
        h = ((h ^ ch) * 0x01000193) & 0xFFFFFFFF
    return h


# ------------------------------------------------------------------ sources --
class Source:
    summary = ""
    detail = ""     # extra text for the web page, e.g. "Voyager 2, 21.7 billion km"
    refresh_s = 1
    scale = 1.0     # display defaults; the playlist can override them
    offset = 0.0
    overflow = "clip"

    def __init__(self, **params):
        for key, value in params.items():
            if not hasattr(self, key):
                raise SourceError("%s has no parameter '%s'" % (type(self).__name__, key))
            setattr(self, key, value)
        self.label = self.summary

    def read(self, ctx):
        raise NotImplementedError


class RateCounter(Source):
    """Events at an average yearly rate since the start of the minute, hour,
    day or year (local time). poisson=True spaces them randomly, like real
    births, with a sequence that is the same after a reboot."""

    summary = "events at an average yearly rate"
    per_year = 1.0
    reset = "day"
    start = ""        # for reset="never": "YYYY-MM-DD"
    poisson = False

    def __init__(self, **params):
        Source.__init__(self, **params)
        if self.reset not in PERIODS:
            raise SourceError("reset must be one of " + ", ".join(PERIODS))
        self._key = None

    def read(self, ctx):
        local = ctx.local
        if self.reset == "never":
            if not self.start:
                raise SourceError("reset='never' needs start='YYYY-MM-DD'")
            t0 = parse_iso(self.start)
        else:
            t0 = period_start(local, self.reset)
        elapsed = max(0, local - t0)
        rate = self.per_year / SECONDS_PER_YEAR
        if not self.poisson:
            return float(int(rate * elapsed))
        return float(self._poisson(t0, elapsed, rate))

    # A Poisson process, generated lazily: only the next arrival time and the
    # count so far are kept, so a year of traffic deaths needs no memory.
    def _uniform(self):
        x = self._state  # xorshift32: identical on MicroPython and CPython
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        self._state = x
        return ((x >> 8) + 0.5) / 16777216.0

    def _poisson(self, t0, elapsed, rate):
        if rate <= 0:
            return 0
        key = (t0, self.per_year)
        if key != self._key or elapsed < self._seen:
            self._key = key
            self._state = (fnv1a(self.label) ^ (t0 & 0xFFFFFFFF)) or 1
            self._count = 0
            self._next = -math.log(self._uniform()) / rate
        while self._next <= elapsed:
            self._count += 1
            self._next += -math.log(self._uniform()) / rate
        self._seen = elapsed
        return self._count


class Clock(Source):
    summary = "time of day"
    style = "seconds"  # seconds (0-86399) | minutes | hhmm | hhmmss | day_of_year

    def read(self, ctx):
        tm = time.gmtime(ctx.local)
        if self.style == "seconds":
            return tm[3] * 3600 + tm[4] * 60 + tm[5]
        if self.style == "minutes":
            return tm[3] * 60 + tm[4]
        if self.style == "hhmm":
            return tm[3] * 100 + tm[4]
        if self.style == "hhmmss":  # six digits: needs overflow="overrange"
            return tm[3] * 10000 + tm[4] * 100 + tm[5]
        if self.style == "day_of_year":
            return tm[7]
        raise SourceError("style must be seconds, minutes, hhmm, hhmmss or day_of_year")


class Countdown(Source):
    summary = "time until a date"
    target = ""      # "2027-01-01" or "2027-03-14T15:09" (local time)
    unit = "days"    # days | hours | minutes | seconds

    def read(self, ctx):
        sizes = {"days": 86400, "hours": 3600, "minutes": 60, "seconds": 1}
        if not self.target or self.unit not in sizes:
            raise SourceError("countdown needs target='YYYY-MM-DD' and a valid unit")
        delta = abs(parse_iso(self.target) - ctx.local)
        size = sizes[self.unit]
        if self.unit == "days":
            return float(-(-delta // size))  # round up: "1" on the last day
        return float(delta // size)


class Quakes(Source):
    summary = "earthquakes worldwide (USGS)"
    refresh_s = 120
    hours = 24
    min_magnitude = 0.0
    stat = "count"   # count | max_mag10

    URL = "https://earthquake.usgs.gov/fdsnws/event/1/"

    def read(self, ctx):
        q = "format=geojson&starttime=%s" % iso_utc(ctx.now - int(self.hours * 3600))
        if self.min_magnitude:
            q += "&minmagnitude=%s" % self.min_magnitude
        if self.stat == "count":
            return float(http_json(self.URL + "count?" + q)["count"])
        if self.stat == "max_mag10":
            data = http_json(self.URL + "query?" + q + "&orderby=magnitude&limit=1")
            feats = data.get("features") or []
            return feats[0]["properties"]["mag"] * 10 if feats else 0.0
        raise SourceError("stat must be count or max_mag10")


class Weather(Source):
    summary = "current weather (Open-Meteo)"
    refresh_s = 900
    variable = "temperature_2m"     # any Open-Meteo 'current' variable
    temperature_unit = "fahrenheit"  # or celsius
    wind_speed_unit = "mph"
    latitude = None                  # default: config.py
    longitude = None

    def read(self, ctx):
        lat = ctx.latitude if self.latitude is None else self.latitude
        lon = ctx.longitude if self.longitude is None else self.longitude
        if not (lat or lon):
            raise SourceError("set LATITUDE and LONGITUDE in config.py")
        url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&current=%s"
               "&temperature_unit=%s&wind_speed_unit=%s&timezone=auto"
               % (lat, lon, self.variable, self.temperature_unit, self.wind_speed_unit))
        data = http_json(url)
        if data.get("error"):
            raise SourceError("Open-Meteo: %s" % data.get("reason"))
        return float(dig(data, "current." + self.variable))


class SolarWind(Source):
    summary = "solar wind speed, km/s (NOAA)"
    refresh_s = 60

    def read(self, ctx):
        data = http_json("https://services.swpc.noaa.gov/products/summary/solar-wind-speed.json")
        row = data[0] if isinstance(data, list) else data
        return float(row["proton_speed"])


class KpIndex(Source):
    summary = "Kp geomagnetic index x100 (NOAA)"
    refresh_s = 600
    scale = 100.0

    def read(self, ctx):
        data = http_json("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
        last = data[-1]
        return float(last["Kp"] if isinstance(last, dict) else last[1])


class River(Source):
    summary = "river flow ft3/s or height ft (USGS)"
    refresh_s = 900
    site = ""
    parameter = "00060"  # 00060 = discharge ft3/s, 00065 = gage height ft

    def read(self, ctx):
        if not self.site:
            raise SourceError("river needs site='USGS site number'")
        data = http_json("https://waterservices.usgs.gov/nwis/iv/?format=json&sites=%s"
                         "&parameterCd=%s" % (self.site, self.parameter))
        series = data["value"]["timeSeries"]
        if not series or not series[0]["values"][0]["value"]:
            raise SourceError("site %s has no recent data" % self.site)
        return float(series[0]["values"][0]["value"][-1]["value"])


class JsonValue(Source):
    summary = "a number from any JSON URL"
    refresh_s = 300
    url = ""
    path = ""
    count = False  # show the length of the list/object at path

    def read(self, ctx):
        if not self.url:
            raise SourceError("json needs url=")
        value = dig(http_json(self.url), self.path)
        if self.count:
            return float(len(value))
        try:
            return float(value)
        except (TypeError, ValueError):
            raise SourceError("%s is not a number" % (self.path or "the response"))


# --------------------------------------------------------- live science --
class CosmicRays(Source):
    """Cosmic-ray neutrons counted by a neutron monitor, from NMDB.

    Neutron monitors count secondary particles from cosmic rays (protons and
    nuclei from beyond the solar system) hitting the atmosphere. The rate dips
    for days after big solar storms (Forbush decreases) and, very rarely,
    jumps when the Sun itself accelerates particles (ground level events).
    Data: NMDB, www.nmdb.eu (see picow/README.md for the acknowledgment).
    """

    summary = "cosmic-ray neutrons per minute (NMDB)"
    refresh_s = 60
    station = "OULU"      # OULU (Finland, since 1964), THUL (Greenland), JUNG (Swiss Alps)...
    unit = "per_minute"   # per_minute | per_second
    max_age_min = 30

    URL = ("https://www.nmdb.eu/nest/draw_graph.php?formchk=1&stations%%5B%%5D=%s"
           "&tabchoice=revori&dtype=corr_for_efficiency&tresolution=0&yunits=0"
           "&date_choice=bydate&start_year=%d&start_month=%d&start_day=%d&start_hour=%d"
           "&start_min=%d&end_year=%d&end_month=%d&end_day=%d&end_hour=%d&end_min=%d"
           "&output=ascii")

    def url(self, now):
        a, b = time.gmtime(now - 20 * 60), time.gmtime(now)
        return self.URL % (self.station.upper(), a[0], a[1], a[2], a[3], a[4],
                           b[0], b[1], b[2], b[3], b[4])

    def read(self, ctx):
        when, rate = parse_nmdb(http_text(self.url(ctx.now)))
        age = (ctx.now - when) // 60
        if age > self.max_age_min:
            raise SourceError("%s data is %d min old" % (self.station, age))
        self.detail = "%s: %.2f counts/s at %s UTC" % (self.station.upper(), rate,
                                                       iso_utc(when)[11:16])
        return rate * 60 if self.unit == "per_minute" else rate


def parse_nmdb(text):
    """Last 'YYYY-MM-DD HH:MM:SS;value' line of an NMDB ascii page."""
    last = None
    for line in text.split("\n"):
        line = line.strip()
        if len(line) > 20 and line[4] == "-" and line[19] == ";":
            last = line
    if last is None:
        raise SourceError("no neutron monitor data in the reply")
    try:
        return parse_iso(last[:19]), float(last[20:])
    except ValueError:
        raise SourceError("unreadable NMDB line: %s" % last)


class SunXrays(Source):
    """The Sun's X-ray brightness from NOAA's GOES satellite (1-8 A band), in
    nanowatts per square metre. The number of digits is the flare class:
    A = 2 digits, B = 3, C = 4, M = 5, and an X-class flare lights the
    overrange lamp. Flares come in bursts from active regions."""

    summary = "the Sun's X-ray brightness, nW/m2 (GOES)"
    refresh_s = 60
    overflow = "overrange"
    band = "0.1-0.8nm"
    max_age_min = 30

    URL = "https://services.swpc.noaa.gov/json/goes/primary/xrays-6-hour.json"

    def read(self, ctx):
        # The smallest file is 6 hours (~160 KB): keep only its last records.
        when, flux = parse_goes_tail(http_text(self.URL, tail=3000), self.band)
        age = (ctx.now - when) // 60
        if age > self.max_age_min:
            raise SourceError("GOES data is %d min old" % age)
        self.detail = "class %s at %s UTC" % (flare_class(flux), iso_utc(when)[11:16])
        return flux * 1e9


def parse_goes_tail(text, band):
    """Newest record for `band` in the tail of a GOES X-ray JSON list."""
    best = None
    i = text.find("{")
    while i >= 0:
        j = text.find("}", i)
        if j < 0:
            break
        try:
            rec = json.loads(text[i:j + 1])
            if rec.get("energy") == band and rec.get("flux") is not None:
                best = rec
        except ValueError:
            pass  # the first, cut-off record
        i = text.find("{", j)
    if best is None:
        raise SourceError("no %s X-ray record found" % band)
    return parse_iso(best["time_tag"].rstrip("Z")), float(best["flux"])


def flare_class(flux):
    for letter, low in (("X", 1e-4), ("M", 1e-5), ("C", 1e-6), ("B", 1e-7), ("A", 1e-8)):
        if flux >= low:
            return "%s%.1f" % (letter, flux / low)
    return "<A1"


SPACECRAFT = {
    "VGR1": "Voyager 1", "VGR2": "Voyager 2", "NHPC": "New Horizons", "JWST": "Webb",
    "MRO": "Mars Reconnaissance Orbiter", "M01O": "Mars Odyssey", "MVN": "MAVEN",
    "M20": "Perseverance", "MSL": "Curiosity", "TGO": "ExoMars TGO", "MEX": "Mars Express",
    "JNO": "Juno", "PSYC": "Psyche", "LUCY": "Lucy", "EURC": "Europa Clipper",
    "SPP": "Parker Solar Probe", "SOHO": "SOHO", "ACE": "ACE", "CHDR": "Chandra",
    "KPLO": "Danuri (lunar orbiter)",
}


class DeepSpace(Source):
    """NASA's Deep Space Network right now (eyes.nasa.gov/dsn).

    stat = "farthest": light-seconds to the farthest spacecraft the network is
                       talking to (Voyager 2 is ~72,000; Mars ~600; Webb ~6)
           "kbps":     total data arriving from space, kilobits per second
           "spacecraft": how many spacecraft are sending data right now
    """

    summary = "light-seconds to the farthest spacecraft NASA is talking to"
    refresh_s = 30
    stat = "farthest"

    URL = "https://eyes.nasa.gov/dsn/data/dsn.xml"

    def read(self, ctx):
        info = parse_dsn(http_text(self.URL))
        if self.stat == "farthest":
            if info["farthest"] is None:
                raise SourceError("no spacecraft with a known distance right now")
            name, rtlt, km = info["farthest"]
            self.detail = "%s, %.1f billion km" % (SPACECRAFT.get(name, name), km / 1e9)
            return rtlt / 2
        if self.stat == "kbps":
            self.detail = "%d spacecraft sending" % len(info["sending"])
            return info["bps"] / 1000
        if self.stat == "spacecraft":
            self.detail = ", ".join(SPACECRAFT.get(n, n) for n in info["sending"])
            return len(info["sending"])
        raise SourceError("stat must be farthest, kbps or spacecraft")


def parse_dsn(xml):
    farthest = None
    for t in xml_tags(xml, "target"):
        try:
            rtlt = float(t.get("rtlt", "-1"))
            km = float(t.get("downlegRange", "-1"))
        except ValueError:
            continue
        if rtlt > 0 and (farthest is None or rtlt > farthest[1]):
            farthest = (t.get("name", "?"), rtlt, km)
    bps = 0.0
    sending = []
    for d in xml_tags(xml, "downSignal"):
        if d.get("active") == "true" and d.get("spacecraft"):
            try:
                bps += float(d.get("dataRate") or 0)
            except ValueError:
                pass
            if d["spacecraft"] not in sending:
                sending.append(d["spacecraft"])
    return {"farthest": farthest, "bps": bps, "sending": sending}


class IssDistance(Source):
    """Kilometres from you (config.py LATITUDE/LONGITUDE) to the people on the
    International Space Station, in a straight line. Changes every second:
    from ~420 km overhead to ~13,000 km when it's over the far side of Earth."""

    summary = "km between you and the astronauts on the ISS"
    refresh_s = 5
    URL = "https://api.wheretheiss.at/v1/satellites/25544"

    def read(self, ctx):
        if not (ctx.latitude or ctx.longitude):
            raise SourceError("set LATITUDE and LONGITUDE in config.py")
        data = http_json(self.URL)
        self.detail = "over %.1f, %.1f" % (data["latitude"], data["longitude"])
        return iss_distance_km(ctx.latitude, ctx.longitude,
                               data["latitude"], data["longitude"], data["altitude"])


def iss_distance_km(lat1, lon1, lat2, lon2, alt_km, radius=6371.0):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    cos_angle = (math.sin(p1) * math.sin(p2) +
                 math.cos(p1) * math.cos(p2) * math.cos(math.radians(lon2 - lon1)))
    r1, r2 = radius, radius + alt_km
    return math.sqrt(max(0.0, r1 * r1 + r2 * r2 - 2 * r1 * r2 * cos_angle))


# Hardware counters set up by main.py from config.DETECTOR, by name.
HARDWARE = {}


class DetectorTally(Source):
    """Particles counted by a detector since local midnight (or the hour...).

    The day's tally is saved to flash every few minutes, so a power cut or a
    reboot loses at most that much, and the clock jump when the Pico first
    sets its time over the network doesn't reset the count.
    """

    summary = "cosmic-ray muons through the detector since midnight"
    refresh_s = 0           # read on every display tick (it is just a counter)
    overflow = "overrange"  # past 99999 the overrange lamp lights
    device = "detector"
    reset = "day"
    store = "tally.json"
    save_every_s = 300

    WRAP = 0x3FFFFFFF

    def __init__(self, **params):
        Source.__init__(self, **params)
        if self.reset not in PERIODS[:4]:
            raise SourceError("reset must be minute, hour, day or year")
        self.period = None
        self.count = 0
        self._last_raw = 0
        self._saved_at = 0

    def read(self, ctx):
        counter = HARDWARE.get(self.device)
        if counter is None:
            raise SourceError("no detector set up: see DETECTOR in config.py")
        raw = counter.total()
        start = period_start(ctx.local, self.reset)
        if start != self.period:
            # First reading (pulses counted since boot belong to today), a new
            # day, or the clock was just set: carry on from the saved tally.
            first = self.period is None
            self.period = start
            self.count = self._restore(start) + (raw if first else 0)
            self._last_raw = raw
            self._saved_at = ctx.now
        else:
            self.count += (raw - self._last_raw) & self.WRAP
            self._last_raw = raw
        if ctx.now - self._saved_at >= self.save_every_s:
            self._save()
            self._saved_at = ctx.now
        return float(self.count)

    def _restore(self, start):
        try:
            with open(self.store) as f:
                saved = json.load(f)
            if saved.get("period") == start:
                return int(saved.get("count", 0))
        except (OSError, ValueError):
            pass
        return 0

    def _save(self):
        try:
            with open(self.store, "w") as f:
                json.dump({"period": self.period, "count": self.count}, f)
        except OSError:
            pass


# ----------------------------------------------------------------- registry --
# name: (class, summary, preset parameters)
# Yearly rates: US births 3,628,934 (CDC/NCHS, 2024 final); US traffic deaths
# 39,345 (NHTSA early estimate for 2024); world ~132 M births and ~62 M deaths
# (UN World Population Prospects 2024, rounded); lightning ~44 flashes/s (NASA).
SOURCES = {
    "rate": (RateCounter, "your own model: per_year, reset", {}),
    "us_births_today": (RateCounter, "US births so far today",
                        {"per_year": 3628934, "reset": "day", "poisson": True}),
    "us_traffic_deaths_ytd": (RateCounter, "US traffic deaths so far this year",
                              {"per_year": 39345, "reset": "year", "poisson": True}),
    "world_births_hour": (RateCounter, "births worldwide this hour",
                          {"per_year": 132000000, "reset": "hour", "poisson": True}),
    "world_deaths_hour": (RateCounter, "deaths worldwide this hour",
                          {"per_year": 62000000, "reset": "hour", "poisson": True}),
    "lightning_minute": (RateCounter, "lightning flashes on Earth this minute",
                         {"per_year": 44 * SECONDS_PER_YEAR, "reset": "minute",
                          "poisson": True}),
    "heartbeats_today": (RateCounter, "heartbeats today at 70 bpm",
                         {"per_year": 70 * 60 * 24 * 365.2425, "reset": "day"}),
    "clock": (Clock, Clock.summary, {}),
    "countdown": (Countdown, Countdown.summary, {}),
    "quakes": (Quakes, Quakes.summary, {}),
    "weather": (Weather, Weather.summary, {}),
    "solar_wind": (SolarWind, SolarWind.summary, {}),
    "kp_index": (KpIndex, KpIndex.summary, {}),
    "river": (River, River.summary, {}),
    "json": (JsonValue, JsonValue.summary, {}),
    "cosmic_rays": (CosmicRays, "cosmic-ray neutrons per minute at Oulu, Finland", {}),
    "sun_xrays": (SunXrays, SunXrays.summary, {}),
    "deep_space": (DeepSpace, DeepSpace.summary, {}),
    "iss_distance": (IssDistance, IssDistance.summary, {}),
    "muons_today": (DetectorTally, DetectorTally.summary, {}),
    "detector": (DetectorTally, "particles counted by the detector", {}),
}


def make(name, params=None):
    if name not in SOURCES:
        raise SourceError("unknown source '%s'" % name)
    cls, summary, preset = SOURCES[name]
    merged = dict(preset)
    merged.update(params or {})
    src = cls(**merged)
    src.label = summary
    return src
