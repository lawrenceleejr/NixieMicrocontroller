# display.py - fit any value onto five Nixie tubes (plus the overrange lamp).


class Format:
    """shown = round(value * scale + offset), then fitted to the tubes.

    overflow "clip"       values above 99999 show 99999
             "wrap"       last five digits, like an odometer
             "overrange"  last five digits with the counter's overrange lamp
                          lit (sends 100000 + the rest; the lamp is the
                          carry out of the fifth digit)
    channel  0-9 reserves the first tube as a label: channel 3 + 142 -> 30142
    Negative values show 0.
    """

    def __init__(self, scale=1.0, offset=0.0, digits=5, overflow="clip", channel=None):
        if overflow not in ("clip", "wrap", "overrange"):
            raise ValueError("overflow must be clip, wrap or overrange")
        if channel is not None and not 0 <= channel <= 9:
            raise ValueError("channel must be 0-9")
        self.scale = scale
        self.offset = offset
        self.digits = digits
        self.overflow = overflow
        self.channel = channel

    def to_counter(self, value):
        x = value * self.scale + self.offset
        n = int(x + 0.5) if x > 0 else 0
        limit = 10 ** self.digits
        inner = limit // 10 if self.channel is not None else limit
        lamp = False
        if n >= inner:
            if self.overflow == "clip":
                n = inner - 1
            else:
                lamp = self.overflow == "overrange"
                n %= inner
        if self.channel is not None:
            n += self.channel * inner
        return n + limit if lamp else n

    def describe(self, n):
        limit = 10 ** self.digits
        text = str(n % limit)
        return "0" * (self.digits - len(text)) + text + ("+" if n >= limit else "")
