"""Stand-ins for the Pico's hardware modules, so the code runs under CPython.

The fake rp2.asm_pio records the PIO program instruction by instruction, and
`PioEmulator` runs it cycle by cycle: the real program from nixie_pio.py is
what gets tested, not a copy of it.
"""

import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


# ------------------------------------------------------------------- rp2 --
class Program:
    def __init__(self, options):
        self.options = options
        self.instructions = []  # tuples
        self.labels = {}
        self.wrap_target = 0
        self.wrap = None


def asm_pio(**options):
    def decorator(fn):
        prog = Program(options)
        ins = prog.instructions

        def label(name):
            prog.labels[name] = len(ins)

        def wrap_target():
            prog.wrap_target = len(ins)

        def wrap():
            prog.wrap = len(ins) - 1

        g = {
            "pull": lambda mode="block": ins.append(("pull", mode)),
            "mov": lambda dst, src: ins.append(("mov", dst, src)),
            "set": lambda dst, value: ins.append(("set", dst, value)),
            "jmp": lambda cond, target=None: ins.append(
                ("jmp", cond, target) if target is not None else ("jmp", None, cond)),
            "label": label, "wrap_target": wrap_target, "wrap": wrap,
        }
        for name in ("x", "y", "osr", "isr", "pins", "block", "noblock",
                     "x_dec", "y_dec", "not_x", "not_y"):
            g[name] = name
        types.FunctionType(fn.__code__, g)()
        if prog.wrap is None:
            prog.wrap = len(ins) - 1
        return prog
    return decorator


class FakeSM:
    def __init__(self, sm_id):
        self.id = sm_id
        self.log = []

    def init(self, prog, **kw):
        self.log.append(("init", prog, kw))

    def active(self, on=None):
        self.log.append(("active", on))

    def exec(self, instr):
        self.log.append(("exec", instr))


class FakeDMA:
    def __init__(self):
        self.log = []

    def pack_ctrl(self, **kw):
        return kw

    def config(self, **kw):
        self.log.append(("config", kw))

    def active(self, on=None):
        self.log.append(("active", on))


rp2 = types.ModuleType("rp2")
rp2.asm_pio = asm_pio
rp2.PIO = types.SimpleNamespace(OUT_LOW=0, OUT_HIGH=1, JOIN_TX=1, JOIN_RX=2)
rp2.StateMachine = FakeSM
rp2.DMA = FakeDMA
sys.modules["rp2"] = rp2

machine = types.ModuleType("machine")
machine.freq = lambda: 125000000


class FakePin:
    OUT, IN, IRQ_RISING = 1, 0, 8

    def __init__(self, *a, **kw):
        self.args = a

    def irq(self, *a, **kw):
        pass


machine.Pin = FakePin
machine.disable_irq = lambda: 0
machine.enable_irq = lambda state: None
sys.modules["machine"] = machine

uctypes = types.ModuleType("uctypes")
uctypes.addressof = lambda obj: 0x20001008  # deliberately not 16-byte aligned
sys.modules["uctypes"] = uctypes


# -------------------------------------------------------------- emulator --
class PioEmulator:
    """Runs a recorded program. One instruction per cycle; the TX FIFO is
    always topped up from `words` in a ring, as the DMA does."""

    def __init__(self, prog, words):
        self.prog = prog
        self.words = list(words)
        self.k = 0
        self.reg = {"x": 0, "y": 0, "osr": 0, "isr": 0}
        self.pin = 0
        self.pc = 0
        self.cycle = 0
        self.rising = []  # cycle at which the pin went high

    def step(self):
        op = self.prog.instructions[self.pc]
        jumped = False
        if op[0] == "pull":
            self.reg["osr"] = self.words[self.k % len(self.words)]
            self.k += 1
        elif op[0] == "mov":
            self.reg[op[1]] = self.reg[op[2]]
        elif op[0] == "set":
            assert op[1] == "pins"
            if op[2] and not self.pin:
                self.rising.append(self.cycle)
            self.pin = op[2]
        elif op[0] == "jmp":
            cond, target = op[1], op[2]
            if cond in ("x_dec", "y_dec"):
                r = cond[0]
                take = self.reg[r] != 0
                self.reg[r] = (self.reg[r] - 1) & 0xFFFFFFFF
            else:
                take = cond is None
            if take:
                self.pc = self.prog.labels[target]
                jumped = True
        else:
            raise AssertionError("unexpected instruction %r" % (op,))
        if not jumped:
            self.pc = self.prog.wrap_target if self.pc == self.prog.wrap else self.pc + 1
        self.cycle += 1

    def run(self, cycles):
        end = self.cycle + cycles
        while self.cycle < end:
            self.step()


@pytest.fixture
def emulator():
    return PioEmulator
