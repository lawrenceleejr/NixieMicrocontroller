# detector.py - count particles from a detector wired to the Pico W.
#
# PulseCounter: one logic pulse per particle on a GPIO (Geiger counter kits,
#   scintillator boards). With a second detector on pin_b it counts only
#   coincidences: both firing within window_us. A muon goes straight through
#   two stacked detectors, while most background (radioactivity, noise) hits
#   only one, so coincidences are mostly cosmic-ray muons.
#
# Counts are kept as small integers that wrap at 2**30, because a hard
# interrupt handler must not allocate memory; readers use wrapped differences.

import time

WRAP = 0x3FFFFFFF


class PulseCounter:
    def __init__(self, pin, pin_b=None, window_us=100, dead_us=50, edge="rising",
                 pull=None):
        import machine

        self.window = window_us
        self.dead = dead_us
        self.count = 0      # pulses (or coincidences), wrapping
        self.singles = 0    # all pulses on pin A, for the status page
        self._last_a = time.ticks_us()
        self._last_b = self._last_a
        self._pending_a = False
        self._pending_b = False
        trig = machine.Pin.IRQ_FALLING if edge == "falling" else machine.Pin.IRQ_RISING
        pulls = {"up": machine.Pin.PULL_UP, "down": machine.Pin.PULL_DOWN}
        a = machine.Pin(pin, machine.Pin.IN, pulls[pull]) if pull else \
            machine.Pin(pin, machine.Pin.IN)
        if pin_b is None:
            a.irq(self._single, trig, hard=True)
        else:
            b = machine.Pin(pin_b, machine.Pin.IN, pulls[pull]) if pull else \
                machine.Pin(pin_b, machine.Pin.IN)
            a.irq(self._edge_a, trig, hard=True)
            b.irq(self._edge_b, trig, hard=True)

    # Hard IRQ handlers: no allocation, no floats, nothing slow.
    def _single(self, _pin):
        t = time.ticks_us()
        if time.ticks_diff(t, self._last_a) >= self.dead:
            self.count = (self.count + 1) & WRAP
            self.singles = (self.singles + 1) & WRAP
        self._last_a = t

    def _edge_a(self, _pin):
        t = time.ticks_us()
        if time.ticks_diff(t, self._last_a) < self.dead:
            return
        self._last_a = t
        self.singles = (self.singles + 1) & WRAP
        if self._pending_b and time.ticks_diff(t, self._last_b) <= self.window:
            self.count = (self.count + 1) & WRAP
            self._pending_b = False
        else:
            self._pending_a = True

    def _edge_b(self, _pin):
        t = time.ticks_us()
        if time.ticks_diff(t, self._last_b) < self.dead:
            return
        self._last_b = t
        if self._pending_a and time.ticks_diff(t, self._last_a) <= self.window:
            self.count = (self.count + 1) & WRAP
            self._pending_a = False
        else:
            self._pending_b = True

    def total(self):
        return self.count


class SerialEventCounter:
    """Counts event lines from a detector that reports over a serial line.

    Every line that starts with a number is one event; anything else (headers,
    comments, blank lines) is ignored. Wire the detector's TX to rx_pin.
    """

    def __init__(self, uart_id=0, rx_pin=1, baud=9600):
        import machine

        self.uart = machine.UART(uart_id, baudrate=baud, rx=machine.Pin(rx_pin), rxbuf=2048)
        self.count = 0
        self._partial = b""

    def feed(self, data):
        """Count complete event lines in `data` (bytes)."""
        lines = (self._partial + data).split(b"\n")
        self._partial = lines.pop()[-200:]  # keep an unfinished line
        for line in lines:
            first = line.strip().split(b" ")[0].split(b"\t")[0].split(b",")[0]
            if first and first.isdigit():
                self.count = (self.count + 1) & WRAP

    def total(self):
        n = self.uart.any()
        if n:
            self.feed(self.uart.read(n))
        return self.count
