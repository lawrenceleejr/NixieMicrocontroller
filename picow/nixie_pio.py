# nixie_pio.py - exact edge counts for a frequency counter, from the RP2040's PIO.
#
# The counter shows how many rising edges arrive during its gate (1 s on the
# IB-1100's Hz range). We output a repeating "frame" that is exactly one gate
# long, measured in system clock ticks, and contains exactly N rising edges:
#
#     N pulses spaced q ticks apart, then a low pause of P ticks:
#     N*q + P + 8 = F  (clock ticks in one gate)
#
# Because the pattern repeats exactly every gate, *any* window one gate long
# holds exactly N edges, wherever the counter's gate happens to start.
#
# The pulse spacing q is fixed (1 us), so the pulses are packed at the start of
# the frame. That matters when the number changes: going from N to N+1 only
# appends one pulse and shortens the pause, and every earlier pulse stays where
# it was. A gate that straddles the change therefore reads N or N+1, never
# anything else, so a running total can only ever step forwards.
#
# The PIO program reads the frame's four numbers from its FIFO. A DMA channel
# in ring mode feeds the same four words forever, so no CPU time is needed:
# Wi-Fi, garbage collection and slow HTTP requests cannot disturb the output.
# A new number is just two words rewritten in memory; there is no restart.

import array

try:
    import rp2
except ImportError:  # imported on a PC without the test stubs
    rp2 = None

PERIOD_OVERHEAD = 7   # instructions per period besides the two delay loops
FRAME_OVERHEAD = 8    # pulls, movs and the pad loop's final pass
MIN_PERIOD = PERIOD_OVERHEAD

PIO0_TXF0 = 0x50200010  # PIO0 TX FIFO for state machine 0 (+4 per machine)
DREQ_PIO0_TX0 = 0       # DMA pacing signal for that FIFO (+1 per machine)


if rp2 is not None:

    @rp2.asm_pio(set_init=rp2.PIO.OUT_LOW, fifo_join=rp2.PIO.JOIN_TX)
    def nixie_frame():
        # Cycle counts are in the comments; every instruction takes one cycle.
        wrap_target()
        pull(block)               # N - 1
        mov(y, osr)
        pull(block)               # H: high-time delay
        mov(isr, osr)             #    (ISR is free storage here)
        pull(block)               # L: low-time delay, kept in OSR
        label("period")           # one period = H + L + 7 cycles
        set(pins, 1)
        mov(x, isr)
        label("hi")
        jmp(x_dec, "hi")          # H + 1 cycles
        set(pins, 0)
        mov(x, osr)
        label("lo")
        jmp(x_dec, "lo")          # L + 1 cycles
        jmp(y_dec, "period")
        pull(block)               # P: pause at the end of the frame
        mov(x, osr)
        label("pad")
        jmp(x_dec, "pad")         # P + 1 cycles
        wrap()


def frame_ticks(sysclk, gate_ms, ppb):
    """Clock ticks in one counter gate.

    ppb is this board's crystal error in parts per billion; positive means it
    runs fast, i.e. there are more ticks in one true second.
    """
    num = sysclk * gate_ms * (1000000000 + ppb)
    den = 1000 * 1000000000
    return (num + den // 2) // den


def pulse_period(frame, max_number, spacing):
    """Ticks between pulses: `spacing`, or less if max_number would not fit."""
    q = min(spacing, (frame - FRAME_OVERHEAD) // max_number)
    if q < MIN_PERIOD:
        raise ValueError("%d per gate is too many for this clock" % max_number)
    return q


def plan(number, frame, period):
    """The four words (N-1, H, L, P) for the PIO program, or None for N = 0."""
    if number <= 0:
        return None
    pad = frame - FRAME_OVERHEAD - number * period
    if pad < 0:
        raise ValueError("%d does not fit in one gate" % number)
    high = (period - PERIOD_OVERHEAD) // 2
    low = period - PERIOD_OVERHEAD - high
    return (number - 1, high, low, pad)


def frame_length(words):
    """Clock ticks the program takes for one frame of `words` (for checking)."""
    n1, high, low, pad = words
    return (n1 + 1) * (high + low + PERIOD_OVERHEAD) + pad + FRAME_OVERHEAD


class Engine:
    """Drives one output pin. show(n) makes the counter read n."""

    def __init__(self, pin, gate_ms=1000, ppb=0, sm_id=0, max_number=199999,
                 spacing_ns=1000):
        import machine
        import uctypes

        if not 0 <= sm_id <= 3:
            raise ValueError("use a PIO0 state machine (0-3); the Wi-Fi chip uses PIO1")
        self.gate_ms = gate_ms
        self.ppb = ppb
        self.sysclk = machine.freq()
        self.max = max_number
        self.spacing = max(MIN_PERIOD, self.sysclk * spacing_ns // 1000000000)
        self.period = pulse_period(frame_ticks(self.sysclk, gate_ms, 0), max_number, self.spacing)
        self.number = 0
        self.words = None
        self._pin = machine.Pin(pin, machine.Pin.OUT, value=0)
        self._sm = rp2.StateMachine(sm_id)
        self._sm_id = sm_id
        self._dma = rp2.DMA()
        # DMA ring mode wraps the read address on a 16-byte boundary, so the
        # four words must sit in an aligned block. MicroPython never moves
        # objects, so the address stays valid.
        self._raw = array.array("I", [0] * 8)
        addr = uctypes.addressof(self._raw)
        self._first = ((16 - addr % 16) % 16) // 4
        self._addr = addr + 4 * self._first

    def max_number(self):
        return (frame_ticks(self.sysclk, self.gate_ms, self.ppb) - FRAME_OVERHEAD) // self.period

    def show(self, number):
        number = int(number)
        if number <= 0:
            self._stop()
            self.number, self.words = 0, None
            return
        words = plan(number, frame_ticks(self.sysclk, self.gate_ms, self.ppb), self.period)
        if self.words is not None and words[1:3] == self.words[1:3]:
            # Same pulse shape: just rewrite the count and the pause. Each is
            # one atomic 32-bit store. If the PIO happens to read the new count
            # with the old pause (or the reverse), that one frame is q ticks
            # (1 us) long or short: a harmless phase shift, no lost edges.
            self._raw[self._first + 3] = words[3]
            self._raw[self._first] = words[0]
        else:
            self._restart(words)
        self.number, self.words = number, words

    def set_ppb(self, ppb):
        self.ppb = int(ppb)
        if self.number:
            self.show(self.number)

    def set_gate(self, gate_ms):
        self.gate_ms = int(gate_ms)
        self.period = pulse_period(frame_ticks(self.sysclk, self.gate_ms, 0), self.max,
                                   self.spacing)
        self.words = None  # force a restart with the new pulse shape
        if self.number:
            self.show(self.number)

    def _stop(self):
        self._dma.active(0)
        self._sm.active(0)
        self._sm.exec("set(pins, 0)")

    def _restart(self, words):
        # Only at start-up, after 0, or after a gate change: the program and
        # the DMA ring start again from word 0 together, so they agree on
        # which word is which.
        self._dma.active(0)
        self._sm.active(0)
        for i in range(4):
            self._raw[self._first + i] = words[i]
        self._sm.init(nixie_frame, set_base=self._pin)  # clears FIFOs, PC to start
        ctrl = self._dma.pack_ctrl(size=2, inc_read=True, inc_write=False,
                                   ring_size=4, ring_sel=False,
                                   treq_sel=DREQ_PIO0_TX0 + self._sm_id)
        self._dma.config(read=self._addr, write=PIO0_TXF0 + 4 * self._sm_id,
                         count=0x7FFFFFFF,  # ~17 years at 4 words per second
                         ctrl=ctrl, trigger=True)
        self._sm.active(1)


def clock_error_ppb(edges, ref_hz, elapsed_us):
    """Crystal error from `edges` of a `ref_hz` reference that took `elapsed_us`
    by our clock. Positive = our clock runs fast."""
    if edges <= 0 or ref_hz <= 0:
        return 0
    return (elapsed_us * ref_hz - edges * 1000000) * 1000 // edges


class RefLock:
    """Measures this board's crystal against a reference clock on a pin.

    Best reference: a TTL stage of the counter's own time base (1-10 kHz), so
    the two crystals are locked together. A GPS 1PPS also works (ref_hz=1).
    """

    WRAP = 0x3FFFFFFF  # keep counters small ints: hard IRQs must not allocate

    def __init__(self, pin, ref_hz, window_s=10):
        import machine

        self.ref_hz = ref_hz
        self.window = max(10, ref_hz * window_s)
        self.edges = 0
        self.last_us = 0
        self.ppb = None
        self._start = None
        machine.Pin(pin, machine.Pin.IN).irq(self._edge, machine.Pin.IRQ_RISING, hard=True)

    def _edge(self, _pin):
        import time
        self.last_us = time.ticks_us()
        self.edges = (self.edges + 1) & self.WRAP

    def poll(self):
        """Call every second or so. Returns a new smoothed ppb, or None."""
        import time
        import machine

        state = machine.disable_irq()
        edges, last = self.edges, self.last_us
        machine.enable_irq(state)
        if self._start is None:
            if edges:
                self._start = (edges, last)
            return None
        n = (edges - self._start[0]) & self.WRAP
        if n < self.window:
            return None
        ppb = clock_error_ppb(n, self.ref_hz, time.ticks_diff(last, self._start[1]))
        self._start = (edges, last)
        if abs(ppb) > 20000000:  # >2 %: wrong ref_hz, or noise on the pin
            return None
        self.ppb = ppb if self.ppb is None else self.ppb + (ppb - self.ppb) // 4
        return self.ppb
