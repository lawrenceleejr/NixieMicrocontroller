"""The main loop: read sources, format values, keep the counter up to date.

A playlist cycles through several sources, each shown for a while. Live
sources are fetched only as often as they change (`refresh_s`); if a fetch
fails, the last good value stays on the display and the fetch is retried
with backoff. Every so often the tubes are exercised (every digit lit in
turn), which helps prevent cathode poisoning in tubes that show the same
digits for months.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from .display import Device, Format
from .sources import Source, SourceError

log = logging.getLogger("nixiefeed")


def exercise_seconds(gate_ms: int) -> float:
    """How long the firmware's "T 2" takes: 10 digits x (2 gates + 0.5 s)."""
    return 10 * (2 * gate_ms / 1000 + 0.5) + 1


@dataclass
class Item:
    source: Source
    fmt: Format
    dwell_s: float = 60.0
    # runtime state
    value: float | None = None
    next_fetch: float = 0.0
    failures: int = 0

    def update(self, mono: float, now: datetime) -> None:
        if mono < self.next_fetch:
            return
        try:
            self.value = self.source.read(now)
            self.failures = 0
            self.next_fetch = mono + self.source.refresh_s
        except (SourceError, OSError, ValueError, KeyError, IndexError, TypeError) as e:
            self.failures += 1
            backoff = min(self.source.refresh_s, 10.0 * 2 ** min(self.failures, 6))
            log.warning("%s: %s (retry in %.0f s)", self.source.label(), e, backoff)
            self.next_fetch = mono + backoff


@dataclass
class Runner:
    items: list[Item]
    device: Device
    exercise_every_s: float | None = 3600.0
    resend_every_s: float = 60.0  # repeat the number in case the Arduino reset
    gate_ms: int = 1000
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    wallclock: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))

    _index: int = 0
    _item_started: float = 0.0
    _last_sent: int | None = None
    _last_send_time: float = 0.0
    _next_exercise: float = 0.0

    def __post_init__(self) -> None:
        if not self.items:
            raise ValueError("nothing to show")
        start = self.monotonic()
        self._item_started = start
        if self.exercise_every_s:
            self._next_exercise = start + self.exercise_every_s

    @property
    def current(self) -> Item:
        return self.items[self._index]

    def step(self) -> int | None:
        """One pass of the loop. Returns the number sent, if any."""
        mono = self.monotonic()

        if self.exercise_every_s and mono >= self._next_exercise:
            log.info("exercising tubes")
            self.device.command("T 2")
            self.sleep(exercise_seconds(self.gate_ms))
            mono = self.monotonic()
            self._next_exercise = mono + self.exercise_every_s
            self._last_sent = None  # firmware returns to its last number; resend anyway

        if len(self.items) > 1 and mono - self._item_started >= self.current.dwell_s:
            self._index = (self._index + 1) % len(self.items)
            self._item_started = mono
            self._last_sent = None

        item = self.current
        item.update(mono, self.wallclock())
        if item.value is None:
            return None
        n = item.fmt.to_counter(item.value)
        if hasattr(self.device, "label"):
            self.device.label = item.source.label()  # type: ignore[attr-defined]
        if n != self._last_sent or mono - self._last_send_time >= self.resend_every_s:
            self.device.show(n)
            if n != self._last_sent:
                log.info("%s: %s -> %s", item.source.label(), item.value, item.fmt.describe(n))
            self._last_sent = n
            self._last_send_time = mono
            return n
        return None

    def run(self, tick_s: float = 0.5, once: bool = False) -> None:
        while True:
            self.step()
            if once:
                return
            self.sleep(tick_s)
