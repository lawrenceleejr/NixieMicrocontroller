"""Model counters: things that happen at a known average rate.

"How many babies have been born in the US so far today?" Nobody publishes
that live, but the yearly total is known, so the count since midnight is
rate x elapsed time. With `poisson=true` the count goes up at random moments
(a Poisson process with the same average), like real births, instead of on a
perfectly even beat. The random sequence is seeded from the period start, so
restarting the program does not make the number jump.

Rates are yearly totals; the sources are listed next to each preset. Pass
`-p per_year=...` to use a newer figure.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from .base import Source, SourceError, register, register_preset

SECONDS_PER_YEAR = 365.2425 * 86400

PERIODS = ("minute", "hour", "day", "year", "never")


def period_start(now: datetime, reset: str, epoch: datetime | None = None) -> datetime:
    """Start of the current counting period in `now`'s time zone."""
    if reset == "minute":
        return now.replace(second=0, microsecond=0)
    if reset == "hour":
        return now.replace(minute=0, second=0, microsecond=0)
    if reset == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if reset == "year":
        return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    if reset == "never":
        if epoch is None:
            raise SourceError("reset=never needs a start date")
        return epoch
    raise SourceError(f"reset must be one of {', '.join(PERIODS)}")


@register
@dataclass
class RateCounter(Source):
    name = "rate"
    summary = "Generic counter: events at an average yearly rate since the period started"

    per_year: float = 1.0
    reset: str = "day"            # minute | hour | day | year | never
    start: str = ""               # ISO date, for reset=never
    timezone: str = ""            # IANA name; empty = this computer's local time
    poisson: bool = False         # random arrivals instead of an even beat

    _cache_key: tuple | None = field(default=None, init=False, repr=False)
    _arrivals: list = field(default_factory=list, init=False, repr=False)
    _rng: random.Random | None = field(default=None, init=False, repr=False)
    _t_last: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.reset not in PERIODS:
            raise SourceError(f"reset must be one of {', '.join(PERIODS)}")
        if self.per_year < 0:
            raise SourceError("per_year must be >= 0")

    @property
    def per_second(self) -> float:
        return self.per_year / SECONDS_PER_YEAR

    def _localize(self, now: datetime) -> datetime:
        if self.timezone:
            return now.astimezone(ZoneInfo(self.timezone))
        return now.astimezone()

    def read(self, now: datetime) -> float:
        now = self._localize(now)
        epoch = None
        if self.reset == "never":
            if not self.start:
                raise SourceError("reset=never needs start=YYYY-MM-DD")
            epoch = datetime.fromisoformat(self.start)
            if epoch.tzinfo is None:
                epoch = epoch.replace(tzinfo=now.tzinfo)
        t0 = period_start(now, self.reset, epoch)
        elapsed = max(0.0, (now - t0).total_seconds())
        if not self.poisson:
            return float(int(self.per_second * elapsed))
        return float(self._poisson_count(t0, elapsed))

    def _poisson_count(self, t0: datetime, elapsed: float) -> int:
        """Arrivals in [0, elapsed] of a Poisson process, reproducible per period."""
        key = (t0.isoformat(), self.per_year)
        if key != self._cache_key or elapsed < self._t_last:
            seed = hashlib.sha256(f"{self.label()}|{key}".encode()).digest()
            self._rng = random.Random(seed)
            self._arrivals = []
            self._t_last = 0.0
            self._cache_key = key
        rate = self.per_second
        if rate <= 0:
            return 0
        assert self._rng is not None
        while self._t_last <= elapsed:
            self._t_last += self._rng.expovariate(rate)
            self._arrivals.append(self._t_last)
        # every stored arrival except the last one is <= elapsed
        return len(self._arrivals) - 1


@register
@dataclass
class Clock(Source):
    name = "clock"
    summary = "Time of day: seconds since midnight (0-86399), or HHMM"

    style: str = "seconds"  # seconds | hhmm | minutes | day_of_year
    timezone: str = ""

    def read(self, now: datetime) -> float:
        now = now.astimezone(ZoneInfo(self.timezone)) if self.timezone else now.astimezone()
        if self.style == "seconds":
            return now.hour * 3600 + now.minute * 60 + now.second
        if self.style == "minutes":
            return now.hour * 60 + now.minute
        if self.style == "hhmm":
            return now.hour * 100 + now.minute
        if self.style == "day_of_year":
            return now.timetuple().tm_yday
        raise SourceError("style must be seconds, minutes, hhmm or day_of_year")


@register
@dataclass
class Countdown(Source):
    name = "countdown"
    summary = "Time until (or since) a date, in days, hours, minutes or seconds"

    target: str = ""        # ISO date/time, e.g. 2027-01-01 or 2027-03-14T15:09
    unit: str = "days"      # days | hours | minutes | seconds
    timezone: str = ""

    _UNITS = {"days": 86400, "hours": 3600, "minutes": 60, "seconds": 1}

    def read(self, now: datetime) -> float:
        if not self.target:
            raise SourceError("countdown needs target=YYYY-MM-DD")
        if self.unit not in self._UNITS:
            raise SourceError("unit must be days, hours, minutes or seconds")
        tz = ZoneInfo(self.timezone) if self.timezone else now.astimezone().tzinfo
        target = datetime.fromisoformat(self.target)
        if target.tzinfo is None:
            target = target.replace(tzinfo=tz)
        delta = abs((target - now).total_seconds())
        # Round days up so "1" shows on the last day, like an advent calendar.
        size = self._UNITS[self.unit]
        return float(-(-int(delta) // size)) if self.unit == "days" else float(int(delta // size))


# --------------------------------------------------------------- presets --
# Yearly totals. Update these when newer figures come out.
#   US births 2024 (final):            3,628,934  CDC/NCHS, Births in the United States, 2024
#   US traffic deaths 2024 (estimate):    39,345  NHTSA early estimate, April 2025
#   World births ~132 million / year,  deaths ~62 million / year (UN World Population
#   Prospects 2024, rounded)
#   Lightning: ~44 flashes per second worldwide (NASA OTD/LIS satellite climatology)
register_preset("us_births_today", "Babies born in the US so far today (CDC 2024 average)",
                RateCounter, per_year=3_628_934, reset="day", poisson=True)
register_preset("us_traffic_deaths_ytd",
                "People killed in US traffic crashes so far this year (NHTSA 2024 average)",
                RateCounter, per_year=39_345, reset="year", poisson=True)
register_preset("world_births_hour", "Babies born worldwide so far this hour (UN average)",
                RateCounter, per_year=132_000_000, reset="hour", poisson=True)
register_preset("world_deaths_hour", "Deaths worldwide so far this hour (UN average)",
                RateCounter, per_year=62_000_000, reset="hour", poisson=True)
register_preset("lightning_minute", "Lightning flashes on Earth so far this minute (~44/s)",
                RateCounter, per_year=44 * SECONDS_PER_YEAR, reset="minute", poisson=True)
register_preset("heartbeats_today", "Your heartbeats so far today, at 70 per minute",
                RateCounter, per_year=70 * 60 * 24 * 365.2425, reset="day")
