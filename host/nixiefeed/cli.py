"""Command line: `nixiefeed list | run | show | calibrate | raw`.

  nixiefeed list
  nixiefeed run us_births_today                      # terminal preview
  nixiefeed run us_births_today --port /dev/ttyUSB0  # the real counter
  nixiefeed run weather -p latitude=51.48 -p longitude=0 --scale 10
  nixiefeed run --config examples/playlist.toml
  nixiefeed show 31415 --port /dev/ttyUSB0
  nixiefeed calibrate --port /dev/ttyUSB0
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from . import __version__
from .display import DeviceError, Format, open_device
from .runner import Item, Runner
from .sources import SourceError, available, make, params_of

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


def _parse_params(pairs: list[str]) -> dict[str, str]:
    out = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"parameters look like key=value, not {pair!r}")
        out[key.strip()] = value.strip()
    return out


def _format_for(source, overrides: dict[str, Any]) -> Format:
    return Format(
        scale=float(overrides.get("scale") if overrides.get("scale") is not None
                    else source.default_scale),
        offset=float(overrides.get("offset") if overrides.get("offset") is not None
                     else source.default_offset),
        digits=int(overrides.get("digits") or 5),
        overflow=str(overrides.get("overflow") or source.default_overflow),
        channel=None if overrides.get("channel") is None else int(overrides["channel"]),
    )


def load_config(path: str) -> tuple[dict[str, Any], list[Item]]:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    device = cfg.get("device", {})
    defaults = cfg.get("format", {})
    items = []
    for entry in cfg.get("show", []):
        if "source" not in entry:
            raise SystemExit(f"{path}: every [[show]] needs source = '...'")
        src = make(entry["source"], **entry.get("params", {}))
        fmt = _format_for(src, {**defaults, **entry.get("format", {})})
        dwell = float(entry.get("seconds", 0)) + 60 * float(entry.get("minutes", 0))
        items.append(Item(src, fmt, dwell_s=dwell or 60.0))
    if not items:
        raise SystemExit(f"{path}: add at least one [[show]] section")
    return device, items


def cmd_list(_args) -> int:
    for name, summary in available().items():
        print(f"{name:24s} {summary}")
        for f in params_of(name):
            default = f.default if f.default is not None else ""
            print(f"{'':26s}-p {f.name}={default}")
    return 0


def cmd_run(args) -> int:
    if args.config:
        device_cfg, items = load_config(args.config)
    else:
        if not args.source:
            raise SystemExit("give a source name (see `nixiefeed list`) or --config FILE")
        device_cfg = {}
        src = make(args.source, **_parse_params(args.param))
        fmt = _format_for(src, {"scale": args.scale, "offset": args.offset,
                                "digits": args.digits, "overflow": args.overflow,
                                "channel": args.channel})
        items = [Item(src, fmt)]
    port = args.port if args.port is not None else device_cfg.get("port", "console")
    gate_ms = args.gate_ms or device_cfg.get("gate_ms") or 1000
    exercise = args.exercise_minutes
    if exercise is None:
        exercise = device_cfg.get("exercise_every_minutes", 60)
    device = open_device(port, gate_ms=gate_ms, digits=items[0].fmt.digits)
    runner = Runner(items, device, exercise_every_s=exercise * 60 if exercise else None,
                    gate_ms=int(gate_ms))
    try:
        runner.run(once=args.once)
    except KeyboardInterrupt:
        pass
    finally:
        device.close()
    return 0


def cmd_show(args) -> int:
    device = open_device(args.port, gate_ms=args.gate_ms)
    try:
        device.show(args.number)
        if hasattr(device, "_ser"):
            print(device.command("?"))
    finally:
        device.close()
    return 0


def cmd_raw(args) -> int:
    device = open_device(args.port)
    try:
        print(device.command(" ".join(args.line)))
    finally:
        device.close()
    return 0


CAL_HELP = """\
Calibration: make the Arduino's crystal agree with the counter's time base.

The Arduino is now sending {n} Hz. Watch the tubes for a few gates and type:
  a number    what the counter shows (e.g. 99997); a big correction is made
  +  or  -    the last digit flickers up / down: nudge by 0.2 ppm
  ++ or --    nudge by 2 ppm
  q           done (the correction is saved in the Arduino's EEPROM)
"""


def cmd_calibrate(args) -> int:
    if args.port in (None, "", "console"):
        raise SystemExit("calibrate needs the real counter: --port /dev/ttyUSB0 (or COM3)")
    device = open_device(args.port, gate_ms=args.gate_ms)
    n = args.number
    status = device.command("?")
    ppm = float(status.split(" C=")[1].split()[0]) if " C=" in status else 0.0
    device.command(f"N {n}")
    print(CAL_HELP.format(n=n))
    steps = {"+": 0.2, "-": -0.2, "++": 2.0, "--": -2.0}
    try:
        while True:
            answer = input(f"[C = {ppm:+.3f} ppm] > ").strip()
            if answer in ("q", "quit", ""):
                if answer == "":
                    continue
                break
            if answer in steps:
                # Counter reads high -> the Arduino's crystal is fast relative
                # to the counter -> raise the correction.
                ppm += steps[answer]
            else:
                try:
                    shown = int(answer)
                except ValueError:
                    print("type a number, +, -, ++, -- or q")
                    continue
                ppm += (shown - n) / n * 1e6
            device.command(f"C {ppm:.3f}")
    finally:
        device.close()
    print(f"saved: C = {ppm:+.3f} ppm")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nixiefeed", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list sources and their parameters").set_defaults(fn=cmd_list)

    r = sub.add_parser("run", help="show a source (or a playlist) continuously")
    r.add_argument("source", nargs="?")
    r.add_argument("-p", "--param", action="append", default=[], metavar="KEY=VALUE")
    r.add_argument("--config", help="TOML playlist (see examples/)")
    r.add_argument("--port", help="serial port, or 'console' to preview in the terminal")
    r.add_argument("--gate-ms", type=int, help="counter gate time in ms (default 1000)")
    r.add_argument("--scale", type=float)
    r.add_argument("--offset", type=float)
    r.add_argument("--digits", type=int)
    r.add_argument("--overflow", choices=["clip", "wrap"])
    r.add_argument("--channel", type=int, help="use the first tube as a label digit 0-9")
    r.add_argument("--exercise-minutes", type=float,
                   help="run every digit this often to protect the tubes (0 = never)")
    r.add_argument("--once", action="store_true", help="update once and exit")
    r.set_defaults(fn=cmd_run)

    s = sub.add_parser("show", help="show one fixed number")
    s.add_argument("number", type=int)
    s.add_argument("--port", default="console")
    s.add_argument("--gate-ms", type=int)
    s.set_defaults(fn=cmd_show)

    c = sub.add_parser("calibrate", help="interactively match the Arduino to the counter")
    c.add_argument("--port")
    c.add_argument("--gate-ms", type=int)
    c.add_argument("--number", type=int, default=99999,
                   help="number to calibrate at (bigger = finer)")
    c.set_defaults(fn=cmd_calibrate)

    w = sub.add_parser("raw", help="send one protocol command, e.g. '?' or 'T 2'")
    w.add_argument("line", nargs="+")
    w.add_argument("--port", required=True)
    w.set_defaults(fn=cmd_raw)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    try:
        return args.fn(args)
    except (SourceError, DeviceError, ValueError) as e:
        print(f"nixiefeed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
