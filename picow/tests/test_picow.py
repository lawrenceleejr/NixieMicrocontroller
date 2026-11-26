"""Tests for the Pico W code, run on a PC: `python -m pytest picow/tests`."""

import bisect
import json
import random
import time

import pytest

import nixie_pio
import sources
import web
from app import App, Controller, calibrate_from_reading
from display import Format
from nixie_pio import (Engine, clock_error_ppb, frame_length, frame_ticks, plan,
                       pulse_period)
from sources import Context, SourceError, epoch, make, parse_iso


# ------------------------------------------------------------- PIO program --
@pytest.mark.parametrize("n,frame", [(1, 1000), (7, 1000), (13, 1000), (99, 1000),
                                     (141, 1000), (37, 10007), (250, 10007)])
def test_pio_frames_are_exact(emulator, n, frame):
    """Emulate the real program: every frame lasts exactly `frame` cycles and
    every window of that length holds exactly n rising edges."""
    words = plan(n, frame, pulse_period(frame, 141 if frame == 1000 else 1428, 20))
    assert frame_length(words) == frame
    emu = emulator(nixie_pio.nixie_frame, words)
    emu.run(6 * frame)
    edges = emu.rising
    assert len(edges) >= 5 * n
    # the pattern repeats with period `frame`
    for i in range(len(edges) - n):
        assert edges[i + n] - edges[i] == frame
    # slide a gate across it, including awkward starting points
    rng = random.Random(1)
    starts = [edges[0] + k for k in range(frame)] if frame <= 1000 else \
        [rng.randrange(edges[0], edges[-1] - frame) for _ in range(2000)]
    for t0 in starts:
        if t0 + frame > edges[-1]:
            continue
        count = bisect.bisect_left(edges, t0 + frame) - bisect.bisect_left(edges, t0)
        assert count == n


def test_pio_waveform_is_roughly_square(emulator):
    words = plan(4, 4000, 500)
    emu = emulator(nixie_pio.nixie_frame, words)
    highs = []
    for _ in range(2000):  # the four pulses at the start of the frame
        before = emu.pin
        emu.step()
        highs.append(before)
    duty = sum(highs) / len(highs)
    assert 0.45 < duty < 0.55


@pytest.mark.parametrize("n", [1, 2, 3, 7, 59, 997, 12345, 31415, 54321, 99999,
                               100000, 123456, 199999, 999999])
def test_plan_at_real_clock(n):
    frame = frame_ticks(125000000, 1000, 0)
    assert frame == 125000000
    period = pulse_period(frame, 199999, 125)
    assert period == 125  # 1 us spacing
    assert frame_length(plan(n, frame, period)) == frame


def test_plan_limits():
    with pytest.raises(ValueError):
        plan(1000000, 125000000, 125)  # 1,000,000 us of pulses don't fit in 1 s
    assert plan(0, 125000000, 125) is None
    assert pulse_period(12500000, 199999, 125) == 62  # 0.1 s gate: tighter spacing
    with pytest.raises(ValueError):
        pulse_period(125000000, 20000000, 125)


def _window_counts(edges, length, starts):
    for t0 in starts:
        yield t0, bisect.bisect_left(edges, t0 + length) - bisect.bisect_left(edges, t0)


def test_changing_number_only_ever_reads_old_or_new(emulator):
    """A running total: the number goes up at random moments while the PIO
    runs, with the DMA prefetching. Every gate must read a value the counter
    really had around that time, so an odometer never steps backwards."""
    frame, period = 3000, 10
    rng = random.Random(7)
    n = 40
    emu = emulator(nixie_pio.nixie_frame, plan(n, frame, period))
    history = [(0, n)]  # (cycle, number written to memory)
    while emu.cycle < 120 * frame:
        emu.run(rng.randrange(frame // 3, 3 * frame))
        n += rng.choice([1, 1, 1, 2, 5])  # occasional clusters
        words = plan(n, frame, period)
        # the Engine's in-place update: pause word, then count word
        emu.memory[3] = words[3]
        if rng.random() < 0.5:
            emu.run(rng.randrange(0, 40))  # let the PIO run between the two stores
        emu.memory[0] = words[0]
        history.append((emu.cycle, n))

    def value_at(t):
        return [v for c, v in history if c <= t][-1]

    edges = emu.rising
    starts = range(3 * frame, emu.cycle - frame, 7)
    for t0, count in _window_counts(edges, frame, starts):
        # The new number reaches the output within two frames (FIFO prefetch).
        assert value_at(t0 - 3 * frame) <= count <= value_at(t0 + frame), t0

    # Counter gates in sequence (gate + 0.3 s display): never step backwards.
    readings = [c for _, c in _window_counts(edges, frame,
                                             range(3 * frame, emu.cycle - frame, 1300 + frame))]
    assert readings == sorted(readings)


def test_frame_ticks_calibration():
    assert frame_ticks(125000000, 1000, 1000) == 125000125      # +1 ppm
    assert frame_ticks(125000000, 1000, -2500) == 124999688     # -2.5 ppm (rounded)
    assert frame_ticks(125000000, 100, 0) == 12500000           # 0.1 s gate


def test_clock_error():
    assert clock_error_ppb(10000, 1000, 10000100) == 10000
    assert clock_error_ppb(10, 1, 10000000) == 0


# ----------------------------------------------------------------- engine --
def test_engine_restart_and_glitch_free_calibration():
    eng = Engine(16)
    assert eng._addr % 16 == 0
    eng.show(12345)
    assert eng.words == plan(12345, 125000000, 125)
    assert list(eng._raw[eng._first:eng._first + 4]) == list(eng.words)
    cfg = eng._dma.log[-1][1]
    assert cfg["ctrl"]["ring_size"] == 4 and cfg["ctrl"]["treq_sel"] == 0
    assert cfg["write"] == 0x50200010 and cfg["read"] == eng._addr
    inits = sum(1 for e in eng._sm.log if e[0] == "init")

    eng.set_ppb(30)  # tiny correction: only the pause word changes
    assert sum(1 for e in eng._sm.log if e[0] == "init") == inits
    assert eng._raw[eng._first + 3] == eng.words[3]

    eng.show(54321)  # new number: rewritten in place, no restart
    assert sum(1 for e in eng._sm.log if e[0] == "init") == inits
    assert list(eng._raw[eng._first:eng._first + 4]) == list(eng.words)

    eng.set_gate(100)  # new pulse spacing: restart
    assert sum(1 for e in eng._sm.log if e[0] == "init") == inits + 1
    assert eng.period == 62

    eng.show(0)
    assert eng._sm.log[-1] == ("exec", "set(pins, 0)")
    assert eng.number == 0
    eng.show(1)  # back from 0 needs a restart
    assert sum(1 for e in eng._sm.log if e[0] == "init") == inits + 2


# ----------------------------------------------------------------- format --
def test_format():
    assert Format().to_counter(12344.5) == 12345
    assert Format().to_counter(-4) == 0
    assert Format().to_counter(123456) == 99999
    assert Format(overflow="wrap").to_counter(123456) == 23456
    n = Format(overflow="overrange").to_counter(123456)
    assert n == 123456 and Format().describe(n) == "23456+"
    assert Format(channel=3).to_counter(142) == 30142
    assert Format(scale=100, offset=27315).to_counter(21.37) == 29452


# ---------------------------------------------------------------- sources --
def ctx_at(y, mo, d, h=0, mi=0, s=0, offset=0, lat=0.0, lon=0.0):
    return Context(epoch(y, mo, d, h, mi, s) - offset, offset, lat, lon)


def test_epoch_matches_python():
    import calendar
    for args in [(1970, 1, 1), (2000, 2, 29), (2026, 11, 26, 13, 45, 30), (2038, 1, 19, 3, 14, 8)]:
        assert epoch(*args) == calendar.timegm(args + (0,) * (6 - len(args)))
    assert parse_iso("2027-03-14T15:09") == epoch(2027, 3, 14, 15, 9)


def test_rate_presets_and_local_midnight():
    births = make("us_births_today", {"poisson": False})
    # 06:00 local in a UTC-5 zone: a quarter of a day
    v = births.read(ctx_at(2026, 11, 26, 6, offset=-5 * 3600))
    assert v == pytest.approx(3628934 / 365.2425 / 4, abs=1)
    deaths = make("us_traffic_deaths_ytd", {"poisson": False})
    assert 19500 <= deaths.read(ctx_at(2026, 7, 2, 12)) <= 19800


def test_poisson_counter():
    src = make("us_births_today")
    values = [src.read(ctx_at(2026, 3, 1, h)) for h in range(24)]
    assert values == sorted(values)
    again = make("us_births_today")
    assert again.read(ctx_at(2026, 3, 1, 12)) == values[12]  # same after a "reboot"
    day = src.read(ctx_at(2026, 3, 1, 23, 59, 59))
    expected = 3628934 / 365.2425
    assert abs(day - expected) < 5 * expected ** 0.5
    assert src.read(ctx_at(2026, 3, 2)) == 0
    ytd = make("us_traffic_deaths_ytd")  # streams: no list of arrivals kept
    assert 34000 < ytd.read(ctx_at(2026, 11, 26)) < 37000


def test_clock_countdown():
    c = ctx_at(2026, 11, 26, 13, 45, 30)
    assert make("clock").read(c) == 49530
    assert make("clock", {"style": "hhmm"}).read(c) == 1345
    assert make("clock", {"style": "hhmmss"}).read(c) == 134530
    assert make("countdown", {"target": "2027-01-01"}).read(c) == 36


def test_unknown_things():
    with pytest.raises(SourceError):
        make("nope")
    with pytest.raises(SourceError):
        make("rate", {"per_yer": 4})


@pytest.fixture
def fake_http(monkeypatch):
    calls = []
    responses = {}

    def fake(url):
        calls.append(url)
        for key, value in responses.items():
            if key in url:
                return value
        raise SourceError("no fake for " + url)

    monkeypatch.setattr(sources, "http_json", fake)
    return calls, responses


def test_quakes(fake_http):
    calls, responses = fake_http
    responses["count?"] = {"count": 199, "maxAllowed": 20000}
    responses["query?"] = {"features": [{"properties": {"mag": 5.3}}]}
    c = ctx_at(2026, 11, 26, 12)
    assert make("quakes").read(c) == 199
    assert "starttime=2026-11-25T12:00:00" in calls[0]
    assert make("quakes", {"stat": "max_mag10", "min_magnitude": 2.5}).read(c) == \
        pytest.approx(53)
    assert "minmagnitude=2.5" in calls[1] and "orderby=magnitude" in calls[1]


def test_weather_uses_config_location(fake_http):
    calls, responses = fake_http
    responses["open-meteo"] = {"current": {"temperature_2m": 21.37}}
    v = make("weather", {"temperature_unit": "celsius"}).read(
        ctx_at(2026, 1, 1, lat=51.5, lon=-0.1))
    assert v == pytest.approx(21.37)
    assert "latitude=51.5" in calls[0] and "temperature_unit=celsius" in calls[0]
    with pytest.raises(SourceError):
        make("weather").read(ctx_at(2026, 1, 1))


def test_space_river_json(fake_http):
    _, responses = fake_http
    responses["solar-wind"] = [{"proton_speed": 342}]
    responses["k-index"] = [{"Kp": 1.0}, {"Kp": 4.33}]
    responses["waterservices"] = {"value": {"timeSeries": [{"values": [{"value": [
        {"value": "1500"}, {"value": "1560"}]}]}]}}
    responses["example.org"] = {"a": [1, {"b": "42.5"}]}
    c = ctx_at(2026, 1, 1)
    assert make("solar_wind").read(c) == 342
    assert make("kp_index").read(c) == pytest.approx(4.33)
    assert make("river", {"site": "01646500"}).read(c) == 1560
    assert make("json", {"url": "https://example.org/x", "path": "a.-1.b"}).read(c) == 42.5
    assert make("json", {"url": "https://example.org/x", "path": "a", "count": True}).read(c) == 2


# ------------------------------------------------------ app and controller --
class FakeEngine:
    def __init__(self):
        self.number = 0
        self.ppb = 0
        self.shown = []

    def show(self, n):
        self.number = n
        self.shown.append(n)

    def set_ppb(self, ppb):
        self.ppb = ppb


def make_ctl(playlist, **kw):
    app = App(playlist)
    eng = FakeEngine()
    ctl = Controller(app, eng, {}, **kw)
    ctl.save = lambda settings: None
    return ctl, eng


def test_controller_playlist_and_exercise():
    playlist = [{"source": "clock", "seconds": 30},
                {"source": "clock", "seconds": 30, "params": {"style": "hhmm"}}]
    ctl, eng = make_ctl(playlist, exercise_minutes=10)
    t = epoch(2026, 11, 26, 13, 45, 0)
    ctl.tick(t)
    assert eng.number == 49500
    ctl.tick(t + 31)
    assert eng.number == 1345
    eng.shown.clear()
    for s in range(600, 640):
        ctl.tick(t + s)
    assert eng.shown[:10] == [d * 11111 for d in range(10)]
    assert ctl.status()["mode"] == "playlist"


def test_controller_manual_and_calibration():
    ctl, eng = make_ctl([{"source": "clock"}], exercise_minutes=0)
    ctl.show_manual(99999)
    ctl.tick(1000)
    assert eng.number == 99999
    ctl.calibrate_reading(100002)  # counter reads 3 high: our clock is fast
    assert ctl.cal_ppm == pytest.approx(30.0003, abs=1e-3)
    assert eng.ppb == int(ctl.cal_ppm * 1000)
    ctl.set_reference(-1500)
    assert eng.ppb == int(ctl.cal_ppm * 1000) - 1500
    ctl.resume()
    assert ctl.app.manual is None
    assert calibrate_from_reading(0.0, 50000, 49999) == pytest.approx(-20)


def test_item_backoff_keeps_last_value(fake_http):
    _, responses = fake_http
    responses["solar-wind"] = [{"proton_speed": 400}]
    ctl, eng = make_ctl([{"source": "solar_wind"}], exercise_minutes=0)
    ctl.tick(1000)
    assert eng.number == 400
    responses.clear()
    ctl.tick(1061)
    assert eng.number == 400 and "no fake" in ctl.app.current.error
    assert ctl.app.current.next_fetch == 1061 + 20


# -------------------------------------------------------------------- web --
def test_web_routes():
    ctl, eng = make_ctl([{"source": "clock"}], exercise_minutes=0)
    ctl.tick(epoch(2026, 1, 1, 0, 0, 5))
    status, ctype, body = web.route(ctl, "/", {}, 0)
    assert status.startswith("200") and "00005" in body and ctype == "text/html"
    assert web.route(ctl, "/show", {"n": "31415"}, 0)[0].startswith("303")
    ctl.tick(epoch(2026, 1, 1, 0, 0, 6))
    assert eng.number == 31415
    web.route(ctl, "/cal", {"reading": "31416"}, 0)
    assert ctl.cal_ppm > 0
    st = json.loads(web.route(ctl, "/api", {}, 0)[2])
    assert st["mode"] == "manual" and st["number"] == 31415
    assert web.route(ctl, "/show", {"n": "x"}, 0)[0].startswith("400")
    assert web.route(ctl, "/nope", {}, 0)[0].startswith("404")
    assert web.parse_query("n=1%2B2&x=a+b") == {"n": "1+2", "x": "a b"}


# --------------------------------------------------------------- detector --
class FakeCounter:
    def __init__(self):
        self.n = 0

    def total(self):
        return self.n


@pytest.fixture
def detector(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    counter = FakeCounter()
    monkeypatch.setitem(sources.HARDWARE, "detector", counter)
    return counter


def test_muon_tally_counts_resets_and_survives_reboot(detector):
    detector.n = 12  # pulses counted while Wi-Fi was connecting
    src = make("muons_today")
    assert src.read(ctx_at(2026, 11, 26, 9)) == 12
    detector.n = 400
    assert src.read(ctx_at(2026, 11, 26, 9, 4)) == 400
    detector.n = 900
    assert src.read(ctx_at(2026, 11, 26, 9, 10)) == 900  # saved (5 min passed)

    # Power cut: a fresh source and counter carry on from the saved tally.
    detector.n = 3
    again = make("muons_today")
    assert again.read(ctx_at(2026, 11, 26, 9, 12)) == 903

    # Midnight: back to zero, even with the counter still running.
    detector.n = 5000
    assert again.read(ctx_at(2026, 11, 26, 23, 59, 59)) == 5900
    detector.n = 5003
    assert again.read(ctx_at(2026, 11, 27, 0, 0, 1)) == 0
    detector.n = 5010
    assert again.read(ctx_at(2026, 11, 27, 0, 0, 30)) == 7


def test_muon_tally_handles_counter_wrap(detector):
    src = make("muons_today")
    detector.n = 0x3FFFFFF0
    first = src.read(ctx_at(2026, 1, 1, 1))
    detector.n = 0x10  # the hardware counter wrapped past 2**30
    assert src.read(ctx_at(2026, 1, 1, 1, 0, 2)) - first == 0x20


def test_muon_tally_needs_a_detector(monkeypatch):
    monkeypatch.delitem(sources.HARDWARE, "detector", raising=False)
    with pytest.raises(SourceError):
        make("muons_today").read(ctx_at(2026, 1, 1))


def test_muon_display_lights_overrange_past_99999(detector):
    app = App([{"source": "muons_today"}])
    detector.n = 123456
    assert app.number(epoch(2026, 1, 1, 23)) == 123456  # lamp + "23456"


@pytest.fixture
def fake_ticks(monkeypatch):
    clock = {"us": 1000}
    monkeypatch.setattr(time, "ticks_us", lambda: clock["us"], raising=False)
    monkeypatch.setattr(time, "ticks_diff", lambda a, b: a - b, raising=False)
    return clock


def test_pulse_counter_single_with_dead_time(fake_ticks):
    import machine
    from detector import PulseCounter

    pc = PulseCounter(18, dead_us=50)
    fire = machine.Pin.handlers[18]
    for t in (2000, 2010, 2100, 5000):  # 2010 is ringing on the same pulse
        fake_ticks["us"] = t
        fire(None)
    assert pc.total() == 3


def test_pulse_counter_coincidence(fake_ticks):
    import machine
    from detector import PulseCounter

    pc = PulseCounter(18, pin_b=19, window_us=100)
    a, b = machine.Pin.handlers[18], machine.Pin.handlers[19]
    events = [(10000, a), (10030, b),     # muon through both: counts
              (20000, a),                 # gamma in A only
              (30000, b),                 # noise in B only
              (40000, b), (40080, a),     # muon, B first: counts
              (50000, a), (50500, b)]     # too far apart: accidental, no count
    for t, fire in events:
        fake_ticks["us"] = t
        fire(None)
    assert pc.total() == 2
    assert pc.singles == 4  # pulses on A


def test_serial_counter_counts_cosmicwatch_lines():
    from detector import SerialEventCounter

    sc = SerialEventCounter.__new__(SerialEventCounter)  # skip the UART
    sc.count, sc._partial = 0, b""
    # CosmicWatch v2 style: '###' header lines, then one numbered line per event
    sc.feed(b"##########\r\n### Event Ardn_time[ms] ADC[0-1023] SiPM[mV] Deadtime[ms] Temp[C]\r\n")
    sc.feed(b"Desk-1\r\n1 1532 455 61.20 3 22.10\r\n2 2911 6")
    assert sc.count == 1  # the second line is not finished yet
    sc.feed(b"02 88.41 5 22.10\r\n\r\n")
    assert sc.count == 2


# ------------------------------------------------- live science (fixtures) --
# Real responses saved on 2026-11-26 around 11:27 UTC.
DATA = __import__("os").path.join(__import__("os").path.dirname(__file__), "data")


def fixture(name):
    with open(__import__("os").path.join(DATA, name)) as f:
        return f.read()


@pytest.fixture
def fake_text(monkeypatch):
    responses, calls = {}, []

    def fake(url, tail=None):
        calls.append((url, tail))
        for key, value in responses.items():
            if key in url:
                return value[-tail:] if tail else value
        raise SourceError("no fake for " + url)

    monkeypatch.setattr(sources, "http_text", fake)
    return responses, calls


def test_cosmic_rays_from_nmdb(fake_text):
    responses, calls = fake_text
    responses["nmdb.eu"] = fixture("nmdb_thul.html")
    src = make("cosmic_rays", {"station": "thul"})
    v = src.read(Context(epoch(2026, 11, 26, 11, 26)))
    assert v == pytest.approx(116.2 * 60)  # 11:23 reading, counts/s -> per minute
    assert "stations%5B%5D=THUL" in calls[0][0]
    assert "start_hour=11&start_min=6" in calls[0][0] and "end_min=26" in calls[0][0]
    assert "116.20 counts/s at 11:23" in src.detail
    with pytest.raises(SourceError):  # an hour later the same data is stale
        src.read(Context(epoch(2026, 11, 26, 12, 30)))


def test_sun_xrays_from_goes_tail(fake_text):
    responses, calls = fake_text
    responses["goes"] = fixture("goes_xrays_tail.json")
    src = make("sun_xrays")
    v = src.read(Context(epoch(2026, 11, 26, 11, 27)))
    assert v == pytest.approx(170.84, abs=0.01)   # B1.7 flare class
    assert calls[0][1] == 3000                    # only the tail is kept
    assert src.detail.startswith("class B1.7")
    app = App([{"source": "sun_xrays"}])
    assert app.items[0].fmt.to_counter(v) == 171
    assert app.items[0].fmt.to_counter(2.3e-4 * 1e9) == 130000  # X2.3: lamp + 30000


def test_flare_class():
    assert sources.flare_class(1.708e-7) == "B1.7"
    assert sources.flare_class(5.2e-5) == "M5.2"
    assert sources.flare_class(1.2e-4) == "X1.2"


def test_deep_space_network(fake_text):
    responses, _ = fake_text
    responses["dsn.xml"] = fixture("dsn.xml")
    c = Context(epoch(2026, 11, 26, 11, 27))
    far = make("deep_space")
    assert far.read(c) == 72500             # Voyager 2: 145,000 s round trip
    assert far.detail == "Voyager 2, 21.7 billion km"
    kbps = make("deep_space", {"stat": "kbps"})
    assert kbps.read(c) == pytest.approx(30167.81)
    n = make("deep_space", {"stat": "spacecraft"})
    assert n.read(c) == 7 and "Webb" in n.detail


def test_xml_tags():
    tags = sources.xml_tags('<a><t name="x" v="1"/><t  name="y" v=""/></a>', "t")
    assert tags == [{"name": "x", "v": "1"}, {"name": "y", "v": ""}]


def test_iss_distance(fake_http):
    _, responses = fake_http
    responses["wheretheiss"] = json.loads(fixture("iss.json"))
    iss = responses["wheretheiss"]
    # straight overhead -> just the altitude
    assert sources.iss_distance_km(10, 20, 10, 20, 420) == pytest.approx(420)
    # opposite side of the Earth -> through the planet
    assert sources.iss_distance_km(0, 0, 0, 180, 420) == pytest.approx(2 * 6371 + 420)
    v = make("iss_distance").read(Context(0, 0, iss["latitude"] + 1.0, iss["longitude"]))
    assert 420 < v < 450 + 112  # one degree of latitude away ~ 111 km


class FakeResponse:
    def __init__(self, body, status=200):
        self.status_code = status
        self.content = body
        self._pos = 0
        self.raw = self

    def read(self, n):
        chunk = self.content[self._pos:self._pos + n]
        self._pos += n
        return chunk

    def close(self):
        pass


def test_http_text_tail_streams(monkeypatch):
    body = b"x" * 50000 + b'{"end": 1}]'
    fake = __import__("types").SimpleNamespace(get=lambda url, **kw: FakeResponse(body))
    monkeypatch.setattr(sources, "requests", fake)
    assert sources.http_text("https://e/x", tail=20) == ("x" * 9 + '{"end": 1}]')
    assert len(sources.http_text("https://e/x")) == len(body)
    fake404 = __import__("types").SimpleNamespace(get=lambda url, **kw: FakeResponse(b"", 404))
    monkeypatch.setattr(sources, "requests", fake404)
    with pytest.raises(SourceError):
        sources.http_text("https://e/x")


# ------------------------------------------------------ Wikipedia stream --
import asyncio

import stream as stream_mod
from stream import Window, WikiEditStream, is_human_edit


def stream_fixture():
    with open(__import__("os").path.join(DATA, "wikimedia_stream.txt"), "rb") as f:
        return f.read().splitlines(keepends=True)


def test_is_human_edit_on_real_events():
    lines = stream_fixture()
    data = [l for l in lines if l.startswith(b"data:")]
    assert len(data) == 25
    human = [l for l in data if is_human_edit(l)]
    assert len(human) == 9
    # the fast substring test agrees with decoding the JSON
    for l in data:
        e = json.loads(l[5:])
        assert is_human_edit(l) == (e["type"] in ("edit", "new") and e.get("bot") is False)
    assert sum(is_human_edit(l, b"enwiki") for l in data) == 2
    assert not is_human_edit(b"event: message\n")


def test_window_rolls():
    w = Window(60)
    for t in (100, 100, 101, 130, 159):
        w.add(t)
    assert w.count(159) == 5
    assert w.count(160) == 3   # the two at t=100 have left the minute
    assert w.count(161) == 2
    w.add(161 + 60)            # reuses the bucket of t=161, resetting it
    assert w.count(161 + 60) == 1


def test_stream_client_against_local_server(monkeypatch):
    events = stream_fixture()

    async def scenario():
        async def serve(reader, writer):
            await reader.readline()  # request line; ignore the rest
            writer.write(b"HTTP/1.0 200 OK\r\ncontent-type: text/event-stream\r\n\r\n")
            for line in events:
                writer.write(line)
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        s = WikiEditStream(host="127.0.0.1", port=port, use_tls=False)
        with pytest.raises(OSError):  # the server hangs up at the end
            await s.read_once()
        server.close()
        return s

    s = asyncio.run(scenario())
    assert s.events == 25 and s.edits == 9
    assert s.window.count(int(time.time())) == 9
    assert not s.connected


def test_wikipedia_source_reports_stream_state():
    src = make("wikipedia_edits", {"wiki": "enwiki"})
    assert src.stream.wiki == b"enwiki"
    now = int(time.time())
    with pytest.raises(SourceError):          # not connected yet
        src.read(Context(now))
    for l in stream_fixture():
        src.stream.feed(l, now)
    src.stream.connected = True
    assert src.read(Context(now)) == 2
    assert src.detail.startswith("live; 2 human edits")
    with pytest.raises(SourceError):          # 3 minutes of silence
        src.read(Context(now + 180))
