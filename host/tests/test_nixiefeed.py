"""Tests for the host side. No network and no hardware: web fetches are patched."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest

from nixiefeed import cli
from nixiefeed.display import ConsoleCounter, Format
from nixiefeed.runner import Item, Runner
from nixiefeed.sources import SourceError, available, make
from nixiefeed.sources import base as base_mod
from nixiefeed.sources import live as live_mod

UTC = timezone.utc


# ---------------------------------------------------------------- Format --
def test_format_basic_and_rounding():
    f = Format()
    assert f.to_counter(0) == 0
    assert f.to_counter(12344.5) == 12345
    assert f.to_counter(-3) == 0


def test_format_scale_offset_kelvin():
    # 21.37 C shown in kelvin to 0.01 K
    f = Format(scale=100, offset=27315)
    assert f.to_counter(21.37) == 29452
    assert f.describe(29452) == "29452"


@pytest.mark.parametrize("mode,expected,shown", [
    ("clip", 99999, "99999"),
    ("wrap", 23456, "23456"),
])
def test_format_overflow(mode, expected, shown):
    f = Format(overflow=mode)
    n = f.to_counter(123456)
    assert n == expected
    assert f.describe(n) == shown


def test_format_rejects_bad_mode():
    with pytest.raises(ValueError):
        Format(overflow="explode")


# --------------------------------------------------------------- models --
def test_registry_has_presets():
    names = available()
    for name in ("us_births_today", "us_traffic_deaths_ytd", "quakes", "weather",
                 "json", "clock", "countdown", "rate"):
        assert name in names


def test_rate_counter_even():
    src = make("rate", per_year=365.2425 * 86400, reset="day", timezone="UTC")  # 1 per s
    now = datetime(2026, 11, 26, 1, 2, 3, tzinfo=UTC)
    assert src.read(now) == 3723


def test_rate_counter_resets_each_year():
    src = make("rate", per_year=1000, reset="year", timezone="UTC")
    assert src.read(datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)) == 0
    assert src.read(datetime(2026, 7, 2, 12, tzinfo=UTC)) == pytest.approx(500, abs=1)


def test_traffic_deaths_mid_year():
    src = make("us_traffic_deaths_ytd", timezone="UTC", poisson="false")
    v = src.read(datetime(2026, 7, 2, 12, tzinfo=UTC))
    assert 19_500 <= v <= 19_800  # half of 39,345


def test_poisson_is_monotonic_reproducible_and_right_on_average():
    src = make("us_births_today", timezone="UTC")
    t0 = datetime(2026, 3, 1, tzinfo=UTC)
    values = [src.read(t0 + timedelta(minutes=10 * k)) for k in range(144)]  # one day
    assert values == sorted(values)
    again = make("us_births_today", timezone="UTC")
    assert again.read(t0 + timedelta(hours=12)) == values[72]
    end_of_day = src.read(t0 + timedelta(hours=23, minutes=59, seconds=59))
    expected_day = 3_628_934 / 365.2425
    assert abs(end_of_day - expected_day) < 5 * expected_day ** 0.5  # 5 sigma
    assert src.read(t0 + timedelta(days=1)) == 0  # resets at midnight


def test_clock_and_countdown():
    now = datetime(2026, 11, 26, 13, 45, 30, tzinfo=UTC)
    assert make("clock", timezone="UTC").read(now) == 13 * 3600 + 45 * 60 + 30
    assert make("clock", style="hhmm", timezone="UTC").read(now) == 1345
    cd = make("countdown", target="2027-01-01", timezone="UTC")
    assert cd.read(now) == 36  # 35 days 10 h -> "36" (rounded up)


def test_unknown_param_and_source():
    with pytest.raises(SourceError):
        make("rate", per_yer=3)
    with pytest.raises(SourceError):
        make("no_such_thing")


# ----------------------------------------------------------------- live --
def _patch_fetch(monkeypatch, payload):
    calls = []

    def fake(url, params=None, timeout=20.0):
        calls.append((url, params))
        return payload

    monkeypatch.setattr(live_mod, "fetch_json", fake)
    return calls


def test_quakes(monkeypatch):
    feats = [{"properties": {"mag": m, "time": t}} for m, t in [(1.2, 5), (4.6, 1), (2.0, 9)]]
    calls = _patch_fetch(monkeypatch, {"features": feats})
    now = datetime.now(UTC)
    assert make("quakes").read(now) == 3
    assert make("quakes", stat="max_mag10").read(now) == pytest.approx(46)
    assert make("quakes", stat="latest_mag10").read(now) == pytest.approx(20)
    assert calls[0][0].endswith("/all_day.geojson")


def test_weather(monkeypatch):
    calls = _patch_fetch(monkeypatch, {"current": {"surface_pressure": 1013.2}})
    v = make("weather", latitude="51.5", longitude="-0.1",
             variable="surface_pressure").read(datetime.now(UTC))
    assert v == pytest.approx(1013.2)
    assert calls[0][1]["current"] == "surface_pressure"
    with pytest.raises(SourceError):
        make("weather").read(datetime.now(UTC))  # no location set


def test_kp_both_formats(monkeypatch):
    _patch_fetch(monkeypatch, [{"Kp": 2.0}, {"Kp": 4.33}])
    assert make("kp_index").read(datetime.now(UTC)) == pytest.approx(4.33)
    _patch_fetch(monkeypatch, [["time_tag", "Kp"], ["2026-11-26 00:00", "5.67"]])
    assert make("kp_index").read(datetime.now(UTC)) == pytest.approx(5.67)


def test_river(monkeypatch):
    payload = {"value": {"timeSeries": [{"values": [{"value": [
        {"value": "1500"}, {"value": "1560"}]}]}]}}
    _patch_fetch(monkeypatch, payload)
    assert make("river", site="01646500").read(datetime.now(UTC)) == 1560


def test_json_source(monkeypatch):
    _patch_fetch(monkeypatch, {"a": {"b": [1, 2, {"c": "42.5"}]}})
    now = datetime.now(UTC)
    assert make("json", url="https://x", path="a.b.-1.c").read(now) == 42.5
    assert make("json", url="https://x", path="a.b", count="true").read(now) == 3
    with pytest.raises(SourceError):
        make("json", url="https://x", path="a.zzz").read(now)


def test_dig_errors():
    with pytest.raises(SourceError):
        base_mod.dig([1, 2], "5")
    with pytest.raises(SourceError):
        base_mod.dig(3, "x")


# --------------------------------------------------------------- runner --
class FakeDevice:
    def __init__(self):
        self.shown, self.commands = [], []

    def show(self, n):
        self.shown.append(n)

    def command(self, line):
        self.commands.append(line)
        return "OK"

    def close(self):
        pass


class Steps:
    """A source that returns a scripted sequence, or raises."""

    refresh_s = 10.0
    default_scale = 1.0
    default_offset = 0.0
    default_overflow = "clip"

    def __init__(self, values):
        self.values = list(values)

    def read(self, now):
        v = self.values.pop(0)
        if isinstance(v, Exception):
            raise v
        return v

    def label(self):
        return "steps"


def _runner(items, **kw):
    clock = {"t": 0.0}
    dev = FakeDevice()
    r = Runner(items, dev, monotonic=lambda: clock["t"], sleep=lambda s: None, **kw)
    return r, dev, clock


def test_runner_sends_on_change_and_keeps_value_on_error():
    src = Steps([5, SourceError("down"), 7])
    r, dev, clock = _runner([Item(src, Format())], exercise_every_s=None)
    r.step()
    assert dev.shown == [5]
    clock["t"] = 10.0
    r.step()               # fetch fails: keep showing 5, nothing new sent
    assert dev.shown == [5]
    clock["t"] = 25.0      # retry after backoff (min(refresh, 20 s))
    r.step()
    assert dev.shown == [5, 7]


def test_runner_playlist_and_exercise():
    a, b = Steps([1] * 10), Steps([2] * 10)
    r, dev, clock = _runner([Item(a, Format(), dwell_s=30), Item(b, Format(), dwell_s=30)],
                            exercise_every_s=100)
    r.step()
    clock["t"] = 31.0
    r.step()
    assert dev.shown == [1, 2]
    clock["t"] = 101.0
    r.step()
    assert dev.commands == ["T 2"]


# ------------------------------------------------------- console and CLI --
def test_console_counter_draws_tubes():
    out = io.StringIO()
    c = ConsoleCounter(stream=out, color=False)
    c.show(123)
    c.show(100042)
    lines = out.getvalue().splitlines()
    assert "0 0 1 2 3" in lines[0]
    assert "0 0 0 4 2" in lines[1] and "OVR" in lines[1]


def test_cli_list_and_run_once(capsys):
    assert cli.main(["list"]) == 0
    assert "us_births_today" in capsys.readouterr().out
    assert cli.main(["run", "clock", "-p", "style=hhmm", "--once",
                     "--exercise-minutes", "0"]) == 0


def test_config_file(tmp_path):
    cfg = tmp_path / "p.toml"
    cfg.write_text("""
[device]
port = "console"

[format]
overflow = "wrap"

[[show]]
source = "clock"
seconds = 30
params = { style = "hhmm", timezone = "UTC" }

[[show]]
source = "weather"
minutes = 2
params = { latitude = 51.5, longitude = -0.1, temperature_unit = "celsius" }
format = { scale = 100, offset = 27315 }
""")
    device, items = cli.load_config(str(cfg))
    assert device["port"] == "console"
    assert [i.dwell_s for i in items] == [30, 120]
    assert items[0].fmt.overflow == "wrap"
    assert items[1].fmt.offset == 27315 and items[1].fmt.overflow == "wrap"


def test_format_channel_digit():
    f = Format(channel=3)
    assert f.to_counter(142) == 30142
    assert f.to_counter(123456) == 39999  # clip to four digits
    assert Format(channel=3, overflow="wrap").to_counter(12345) == 32345
    with pytest.raises(ValueError):
        Format(channel=12)
