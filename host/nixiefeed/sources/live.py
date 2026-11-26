"""Live sources: free public data feeds that need no API key.

Each one is a thin wrapper around a URL plus the arithmetic to get one number
out. `json` is the general case: any URL that returns JSON, and a dotted path
to the number you want, so a town's open-data portal can be used without
writing new code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .base import Source, SourceError, dig, fetch_json, register


@register
@dataclass
class UsgsQuakes(Source):
    name = "quakes"
    summary = "USGS earthquakes: count (or biggest magnitude x10) in the last hour/day/week"
    refresh_s = 60.0

    window: str = "day"         # hour | day | week | month
    min_magnitude: str = "all"  # all | 1.0 | 2.5 | 4.5 | significant
    stat: str = "count"         # count | max_mag10 | latest_mag10

    URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/{mag}_{window}.geojson"

    def read(self, now: datetime) -> float:
        if self.window not in ("hour", "day", "week", "month"):
            raise SourceError("window must be hour, day, week or month")
        mag = {"1": "1.0", "1.0": "1.0", "2.5": "2.5", "4.5": "4.5"}.get(
            self.min_magnitude, self.min_magnitude)
        if mag not in ("all", "1.0", "2.5", "4.5", "significant"):
            raise SourceError("min_magnitude must be all, 1.0, 2.5, 4.5 or significant")
        data = fetch_json(self.URL.format(mag=mag, window=self.window))
        feats = data.get("features", [])
        if self.stat == "count":
            return float(len(feats))
        mags = [(f["properties"].get("time") or 0, f["properties"].get("mag"))
                for f in feats if f["properties"].get("mag") is not None]
        if not mags:
            return 0.0
        if self.stat == "max_mag10":
            return max(m for _, m in mags) * 10
        if self.stat == "latest_mag10":
            return max(mags)[1] * 10
        raise SourceError("stat must be count, max_mag10 or latest_mag10")


@register
@dataclass
class Weather(Source):
    name = "weather"
    summary = "Open-Meteo current weather for a place (temperature, pressure, wind...)"
    refresh_s = 900.0  # the model updates every 15 minutes

    latitude: float = 0.0
    longitude: float = 0.0
    variable: str = "temperature_2m"  # any Open-Meteo 'current' variable
    temperature_unit: str = "fahrenheit"  # or celsius
    wind_speed_unit: str = "mph"          # kmh | ms | mph | kn

    URL = "https://api.open-meteo.com/v1/forecast"

    def read(self, now: datetime) -> float:
        if self.latitude == 0.0 and self.longitude == 0.0:
            raise SourceError("set latitude= and longitude= for your town")
        data = fetch_json(self.URL, {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "current": self.variable,
            "temperature_unit": self.temperature_unit,
            "wind_speed_unit": self.wind_speed_unit,
            "timezone": "auto",
        })
        if data.get("error"):
            raise SourceError(f"Open-Meteo: {data.get('reason')}")
        return float(dig(data, f"current.{self.variable}"))


@register
@dataclass
class SolarWind(Source):
    name = "solar_wind"
    summary = "Solar wind speed at L1 in km/s (NOAA SWPC, updates every minute)"
    refresh_s = 60.0

    URL = "https://services.swpc.noaa.gov/products/summary/solar-wind-speed.json"

    def read(self, now: datetime) -> float:
        data = fetch_json(self.URL)
        row = data[0] if isinstance(data, list) else data
        return float(row["proton_speed"])


@register
@dataclass
class KpIndex(Source):
    name = "kp_index"
    summary = "Planetary Kp geomagnetic index x100 (NOAA SWPC; 500+ = storm, auroras)"
    refresh_s = 600.0
    default_scale = 100.0

    URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"

    def read(self, now: datetime) -> float:
        data = fetch_json(self.URL)
        last = data[-1]
        if isinstance(last, dict):
            return float(last["Kp"])
        return float(last[1])  # older list-of-lists format: [time, Kp, ...]


@register
@dataclass
class UsgsRiver(Source):
    name = "river"
    summary = "USGS stream gauge: river flow (ft3/s) or height (ft) at a site"
    refresh_s = 900.0

    site: str = ""              # USGS site number, find yours at waterdata.usgs.gov
    parameter: str = "00060"    # 00060 = discharge ft3/s, 00065 = gage height ft

    URL = "https://waterservices.usgs.gov/nwis/iv/"

    def read(self, now: datetime) -> float:
        if not self.site:
            raise SourceError("set site= to a USGS site number")
        data = fetch_json(self.URL, {"format": "json", "sites": self.site,
                                     "parameterCd": self.parameter})
        series = data["value"]["timeSeries"]
        if not series or not series[0]["values"][0]["value"]:
            raise SourceError(f"site {self.site} has no recent {self.parameter} data")
        return float(series[0]["values"][0]["value"][-1]["value"])


@register
@dataclass
class JsonValue(Source):
    name = "json"
    summary = "Any JSON URL: a number at a dotted path, or the length of a list"
    refresh_s = 300.0

    url: str = ""
    path: str = ""          # e.g. 'features.0.properties.mag', '' = whole document
    count: bool = False     # show len() of what the path points to
    refresh: float = 300.0  # seconds between fetches

    def __post_init__(self) -> None:
        self.refresh_s = max(5.0, float(self.refresh))  # per-instance override

    def read(self, now: datetime) -> float:
        if not self.url:
            raise SourceError("set url=")
        value = dig(fetch_json(self.url), self.path)
        if self.count:
            return float(len(value))
        try:
            return float(value)
        except (TypeError, ValueError) as e:
            raise SourceError(f"{self.path!r} is {value!r}, not a number") from e
