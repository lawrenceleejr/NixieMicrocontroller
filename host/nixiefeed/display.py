"""Turning values into counter readings, and the devices that show them.

A frequency counter can only show a non-negative integer with a fixed number
of digits (five on the IB-1100, plus an "overrange" lamp). `Format` maps any
real-world value onto that; the device classes send it to the Arduino (or to
the terminal, for trying things out without hardware).
"""

from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass
from typing import Protocol


class DeviceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Format:
    """How a value becomes the integer shown on the tubes.

    shown = round(value * scale + offset), then fitted to `digits`:

    overflow = "clip"       values above 99999 show 99999
             = "wrap"       show only the last five digits (an odometer)
    Negative results show 0.

    channel = 0..9 reserves the leading tube as a label, so a playlist can
    say which source is showing: channel=3 and value 142 show "30142".


    Examples: temperature in kelvin to 0.01 K -> scale=100, offset=27315
    (with a Celsius source); Kp index 4.33 -> scale=100 shows 433.
    """

    scale: float = 1.0
    offset: float = 0.0
    digits: int = 5
    overflow: str = "clip"
    channel: int | None = None

    def __post_init__(self) -> None:
        if self.overflow not in ("clip", "wrap"):
            raise ValueError(f"overflow must be clip or wrap, not {self.overflow!r}")
        if not 1 <= self.digits <= 6:
            raise ValueError("digits must be 1..6")
        if self.channel is not None and not (0 <= self.channel <= 9 and self.digits >= 2):
            raise ValueError("channel must be 0..9 (and needs at least 2 digits)")

    @property
    def limit(self) -> int:
        return 10**self.digits

    def to_counter(self, value: float) -> int:
        """The number to send to the device."""
        if value is None or math.isnan(value):
            raise ValueError("no value")
        n = max(0, int(math.floor(value * self.scale + self.offset + 0.5)))
        value_limit = self.limit if self.channel is None else self.limit // 10
        if n >= value_limit:
            n = value_limit - 1 if self.overflow == "clip" else n % value_limit
        if self.channel is not None:
            n += self.channel * value_limit
        return n

    def describe(self, n: int) -> str:
        """What the tubes will look like, e.g. '04213' ('+' if the overrange lamp is lit)."""
        lamp = n >= self.limit
        return f"{n % self.limit:0{self.digits}d}" + ("+" if lamp else "")


class Device(Protocol):
    def show(self, n: int) -> None: ...
    def command(self, line: str) -> str: ...
    def close(self) -> None: ...


class SerialCounter:
    """The NixieCounter Arduino firmware on a serial port."""

    def __init__(self, port: str, baud: int = 115200, gate_ms: int | None = None,
                 timeout: float = 2.0) -> None:
        try:
            import serial  # pyserial
        except ImportError as e:  # pragma: no cover - depends on install
            raise DeviceError("pyserial is not installed: pip install pyserial") from e
        self._ser = serial.Serial(port, baud, timeout=timeout)
        # Opening the port resets most Arduinos; wait for the banner.
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            line = self._ser.readline().decode("ascii", "replace").strip()
            if "READY" in line:
                break
        self._ser.reset_input_buffer()
        if gate_ms is not None:
            self.command(f"G {int(gate_ms)}")

    def command(self, line: str) -> str:
        self._ser.write((line.strip() + "\n").encode("ascii"))
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            reply = self._ser.readline().decode("ascii", "replace").strip()
            if reply.startswith("OK") or reply.startswith("ERR"):
                if reply.startswith("ERR"):
                    raise DeviceError(f"{line!r}: {reply}")
                return reply
        raise DeviceError(f"no reply to {line!r}")

    def show(self, n: int) -> None:
        self.command(f"N {int(n)}")

    def close(self) -> None:
        self._ser.close()


class ConsoleCounter:
    """Draws the tubes in the terminal; no hardware needed."""

    _ORANGE = "\x1b[38;5;208m"
    _DIM = "\x1b[2m"
    _RESET = "\x1b[0m"

    def __init__(self, digits: int = 5, stream=None, color: bool | None = None) -> None:
        self.digits = digits
        self.stream = stream or sys.stdout
        self.color = self.stream.isatty() if color is None else color
        self.label = ""
        self.last: int | None = None

    def command(self, line: str) -> str:
        line = line.strip()
        if line[:1].upper() == "N":
            self.show(int(line[1:].strip(" =")))
        return "OK console"

    def show(self, n: int) -> None:
        self.last = n
        limit = 10**self.digits
        tubes = " ".join(f"{n % limit:0{self.digits}d}")
        lamp = "OVR" if n >= limit else "   "
        if self.color:
            tubes = f"{self._ORANGE}{tubes}{self._RESET}"
            lamp = f"{self._ORANGE}{lamp}{self._RESET}"
            label = f"{self._DIM}{self.label}{self._RESET}"
        else:
            label = self.label
        self.stream.write(f"\r  [ {tubes} ] {lamp}  {label}\x1b[K" if self.color
                          else f"[ {tubes} ] {lamp}  {label}\n")
        self.stream.flush()

    def close(self) -> None:
        if self.color:
            self.stream.write("\n")


def open_device(port: str, gate_ms: int | None = None, digits: int = 5) -> Device:
    """'console' (or empty) gives the terminal simulator; anything else is a serial port."""
    if not port or port == "console":
        return ConsoleCounter(digits=digits)
    return SerialCounter(port, gate_ms=gate_ms)
