# app.py - the playlist logic: which number should be on the tubes right now.
# Hardware-free, so it is tested on a PC (picow/tests).

import json

from display import Format
from sources import Context, SourceError, make

SETTINGS_FILE = "settings.json"


class Item:
    def __init__(self, entry):
        self.source = make(entry["source"], entry.get("params"))
        self.fmt = Format(scale=entry.get("scale", self.source.scale),
                          offset=entry.get("offset", self.source.offset),
                          digits=entry.get("digits", 5),
                          overflow=entry.get("overflow", "clip"),
                          channel=entry.get("channel"))
        self.dwell = entry.get("seconds", 0) + 60 * entry.get("minutes", 0) or 60
        self.label = entry.get("label", self.source.label)
        self.value = None
        self.error = ""
        self.next_fetch = 0
        self.failures = 0

    def update(self, now, ctx):
        """Fetch if due. On failure keep the old value and back off."""
        if now < self.next_fetch:
            return
        try:
            self.value = self.source.read(ctx)
            self.error = ""
            self.failures = 0
            self.next_fetch = now + self.source.refresh_s
        except (SourceError, OSError, ValueError, KeyError, IndexError, TypeError) as e:
            self.failures += 1
            self.error = str(e)
            self.next_fetch = now + min(self.source.refresh_s, 10 * 2 ** min(self.failures, 6))


class App:
    """Holds the playlist and any manual override from the web page."""

    def __init__(self, playlist, latitude=0.0, longitude=0.0, offset=0):
        self.items = [Item(e) for e in playlist]
        if not self.items:
            raise ValueError("PLAYLIST is empty")
        self.index = 0
        self.started = None
        self.manual = None      # number set from the web page
        self.hold = False       # stay on the current item
        self.ctx = Context(0, offset, latitude, longitude)

    @property
    def current(self):
        return self.items[self.index]

    def next_item(self, now):
        self.index = (self.index + 1) % len(self.items)
        self.started = now

    def number(self, now):
        """The number to display at UTC time `now`, or None if not known yet."""
        if self.manual is not None:
            return self.manual
        if self.started is None:
            self.started = now
        if not self.hold and len(self.items) > 1 and now - self.started >= self.current.dwell:
            self.next_item(now)
        item = self.current
        self.ctx.now = now
        item.update(now, self.ctx)
        if item.value is None:
            return None
        return item.fmt.to_counter(item.value)


def calibrate_from_reading(ppm, shown, reading):
    """New crystal correction after the counter read `reading` while showing
    `shown`. Reading high means this board's clock is fast: raise the ppm."""
    return ppm + (reading - shown) / shown * 1e6


def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)


class Controller:
    """Everything the main loop and the web page do, with the engine injected
    (a fake one in the tests)."""

    def __init__(self, app, engine, settings=None, gate_ms=1000, exercise_minutes=60):
        self.app = app
        self.engine = engine
        self.settings = settings if settings is not None else {}
        self.gate_ms = gate_ms
        self.cal_ppm = float(self.settings.get("cal_ppm", 0.0))
        self.ref_ppb = None
        self.exercise_every = int(exercise_minutes * 60)
        self.exercise_start = None
        self.next_exercise = None
        self.step = max(2, (2 * gate_ms + 500 + 999) // 1000)  # s per digit
        self.save = save_settings
        self.apply_calibration()

    # ---------------------------------------------------------------- loop --
    def tick(self, now):
        """Call about twice a second with the UTC time in seconds."""
        if self.next_exercise is None:
            self.next_exercise = now + self.exercise_every if self.exercise_every else None
        if self.exercise_start is None and self.next_exercise is not None \
                and now >= self.next_exercise:
            self.exercise_start = now
        if self.exercise_start is not None:
            digit = int((now - self.exercise_start) // self.step)
            if digit < 10:
                self._show(digit * 11111)
                return
            self.exercise_start = None
            if self.exercise_every:
                self.next_exercise = now + self.exercise_every
        n = self.app.number(now)
        if n is not None:
            self._show(n)

    def _show(self, n):
        if n != self.engine.number:
            try:
                self.engine.show(n)
            except ValueError as e:  # too fast for this gate
                self.app.current.error = str(e)

    # ------------------------------------------------------------ controls --
    def show_manual(self, n):
        self.app.manual = max(0, int(n))

    def resume(self):
        self.app.manual = None
        self.app.hold = False

    def hold(self):
        self.app.hold = True

    def next(self, now):
        self.app.manual = None
        self.app.next_item(now)

    def exercise(self, now):
        self.exercise_start = now

    def set_cal_ppm(self, ppm):
        if not -50000 <= ppm <= 50000:
            raise ValueError("calibration must be within +/-5 %")
        self.cal_ppm = ppm
        self.settings["cal_ppm"] = ppm
        self.save(self.settings)
        self.apply_calibration()

    def calibrate_reading(self, reading):
        shown = self.engine.number
        if shown <= 0:
            raise ValueError("show a number first (99999 is best)")
        self.set_cal_ppm(calibrate_from_reading(self.cal_ppm, shown, reading))

    def set_reference(self, ppb):
        self.ref_ppb = ppb
        self.apply_calibration()

    def apply_calibration(self):
        total = int(self.cal_ppm * 1000) + (self.ref_ppb or 0)
        self.engine.set_ppb(total)

    def status(self):
        item = self.app.current
        n = self.engine.number
        return {
            "shown": item.fmt.describe(n),
            "number": n,
            "mode": "manual" if self.app.manual is not None else
                    ("exercise" if self.exercise_start is not None else
                     ("hold" if self.app.hold else "playlist")),
            "source": item.label,
            "value": item.value,
            "error": item.error,
            "playlist": [i.label for i in self.app.items],
            "index": self.app.index,
            "cal_ppm": self.cal_ppm,
            "ref_ppm": None if self.ref_ppb is None else self.ref_ppb / 1000,
            "gate_ms": self.gate_ms,
            "utc_offset_h": self.app.ctx.offset / 3600,
        }
