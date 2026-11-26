# main.py - runs at power-up on the Pico W.
#
# Boot: tubes show 00000 -> Wi-Fi -> the last part of the IP address for 10 s
# (e.g. 00042 for 192.168.1.42) -> set the clock (NTP) -> playlist.
# Then: http://nixie.local/ (or that IP) to control it from a phone.

import gc
import time

import asyncio
import machine
import network
import ntptime

try:
    import config
except ImportError:
    import config_example as config
    print("config.py not found: using config_example.py (copy it and add your Wi-Fi)")

from app import App, Controller, load_settings
from nixie_pio import Engine, RefLock
from sources import SourceError, http_json
import web


def cfg(name, default):
    return getattr(config, name, default)


def connect_wifi(engine, timeout_s=30):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    try:
        network.hostname(cfg("HOSTNAME", "nixie"))
    except (AttributeError, OSError):
        pass
    wlan.config(pm=0xA11140)  # no power saving: snappier web page
    if not wlan.isconnected():
        wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        for _ in range(timeout_s):
            if wlan.isconnected():
                break
            time.sleep(1)
    if wlan.isconnected():
        ip = wlan.ifconfig()[0]
        print("Wi-Fi connected:", ip)
        return ip
    print("Wi-Fi failed, status", wlan.status())
    return None


def sync_clock():
    for _ in range(5):
        try:
            ntptime.settime()  # sets the RTC to UTC
            return True
        except OSError:
            time.sleep(2)
    return False


def utc_offset_seconds():
    setting = cfg("UTC_OFFSET_HOURS", "auto")
    if setting != "auto":
        return int(float(setting) * 3600)
    lat, lon = cfg("LATITUDE", 0.0), cfg("LONGITUDE", 0.0)
    if not (lat or lon):
        return 0
    try:
        data = http_json("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
                         "&current=temperature_2m&timezone=auto&forecast_days=1" % (lat, lon))
        return int(data["utc_offset_seconds"])
    except (SourceError, KeyError, ValueError):
        return None


async def display_loop(ctl):
    while True:
        try:
            ctl.tick(time.time())
        except Exception as e:  # keep going whatever a source does
            print("tick:", e)
        gc.collect()
        await asyncio.sleep_ms(500)


async def housekeeping(ctl, engine, synced, offset_known):
    last_sync = time.time() if synced else 0
    while True:
        await asyncio.sleep(60)
        if not network.WLAN(network.STA_IF).isconnected():
            connect_wifi(engine, timeout_s=15)
        if time.time() - last_sync > 6 * 3600 or not offset_known:
            if sync_clock():
                last_sync = time.time()
            offset = utc_offset_seconds()  # follows daylight saving changes
            if offset is not None:
                ctl.app.ctx.offset = offset
                offset_known = True


async def reference_loop(ctl, lock):
    while True:
        await asyncio.sleep(1)
        ppb = lock.poll()
        if ppb is not None:
            ctl.set_reference(ppb)


async def main():
    gate_ms = cfg("GATE_MS", 1000)
    settings = load_settings()
    if "cal_ppm" not in settings:
        settings["cal_ppm"] = cfg("CAL_PPM", 0.0)
    engine = Engine(cfg("OUTPUT_PIN", 16), gate_ms=gate_ms)
    engine.show(0)

    ip = connect_wifi(engine)
    if ip and cfg("SHOW_IP_AT_BOOT", True):
        engine.show(int(ip.split(".")[-1]))
        time.sleep(10)
    synced = sync_clock()
    if not synced:
        print("NTP failed: clocks and models will be wrong until the next try")
    offset = utc_offset_seconds()

    app = App(config.PLAYLIST, cfg("LATITUDE", 0.0), cfg("LONGITUDE", 0.0), offset or 0)
    ctl = Controller(app, engine, settings, gate_ms, cfg("EXERCISE_MINUTES", 60))
    ctl.exercise(time.time())  # light every digit once at start-up

    tasks = [display_loop(ctl), housekeeping(ctl, engine, synced, offset is not None)]
    if cfg("REF_PIN", None) is not None and cfg("REF_HZ", 0):
        tasks.append(reference_loop(ctl, RefLock(config.REF_PIN, config.REF_HZ)))
    if ip:
        await asyncio.start_server(web.make_handler(ctl), "0.0.0.0", 80)
        print("control page: http://%s/  or  http://%s.local/" % (ip, cfg("HOSTNAME", "nixie")))
    await asyncio.gather(*tasks)


try:
    asyncio.run(main())
except KeyboardInterrupt:  # Ctrl-C from a USB terminal
    pass
finally:
    machine.Pin(cfg("OUTPUT_PIN", 16), machine.Pin.OUT, value=0)
