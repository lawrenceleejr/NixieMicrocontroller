# stream.py - live event streams from the internet (Server-Sent Events).
#
# Wikimedia EventStreams pushes every change to every Wikipedia (and its
# sister projects) the moment it is saved: about 20-40 events a second, a few
# of which are edits by people rather than bots. We keep one connection open,
# read it line by line, and count human edits in a rolling window.
#
# Runs as an asyncio task beside the display loop and the web page. It speaks
# HTTP/1.1 (some networks' proxies refuse HTTP/1.0) and undoes the "chunked"
# transfer encoding itself, and it tests each event with two substring checks
# instead of decoding the JSON: at ~50 KB/s that matters on a Pico.

import time

try:
    import asyncio
except ImportError:  # very old MicroPython
    import uasyncio as asyncio


def tls_context():
    """TLS without certificate checks (MicroPython has no CA store), as the
    Pico's `requests` does. Fine for reading public data."""
    import ssl

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if hasattr(ctx, "check_hostname"):
        ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class Window:
    """Events in the last `seconds` seconds, in one-second buckets."""

    def __init__(self, seconds=60):
        self.seconds = seconds
        self.buckets = [0] * seconds
        self.stamp = [0] * seconds  # which second each bucket holds

    def add(self, now, n=1):
        i = now % self.seconds
        if self.stamp[i] != now:
            self.stamp[i] = now
            self.buckets[i] = 0
        self.buckets[i] += n

    def count(self, now):
        oldest = now - self.seconds
        return sum(b for b, s in zip(self.buckets, self.stamp) if oldest < s <= now)


def is_human_edit(line, wiki=None):
    """One SSE line from the recentchange stream -> is it a person editing?"""
    if not line.startswith(b"data:"):
        return False
    if b'"bot":false' not in line:
        return False
    if b'"type":"edit"' not in line and b'"type":"new"' not in line:
        return False
    return wiki is None or (b'"wiki":"' + wiki + b'"') in line


class WikiEditStream:
    HOST = "stream.wikimedia.org"
    PATH = "/v2/stream/recentchange"

    def __init__(self, wiki="", window_s=60, host=None, port=443, use_tls=True,
                 user_agent="nixiefeed-picow/1.0 (Nixie counter display)"):
        self.wiki = wiki.encode() if wiki else None
        self.window = Window(window_s)
        self.host = host or self.HOST
        self.port = port
        self.use_tls = use_tls
        self.user_agent = user_agent
        self.connected = False
        self.connected_at = 0
        self.last_event = 0      # time.time() of the last line received
        self.events = 0          # all lines with data, for the status page
        self.edits = 0           # human edits counted since boot
        self.error = ""

    def feed(self, line, now):
        if line.startswith(b"data:"):
            self.events += 1
            self.last_event = now
            if is_human_edit(line, self.wiki):
                self.edits += 1
                self.window.add(now)

    async def read_once(self):
        """Connect, read until the connection drops (then raise OSError)."""
        ssl = tls_context() if self.use_tls else None
        reader, writer = await asyncio.open_connection(self.host, self.port, ssl=ssl)
        try:
            request = ("GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: %s\r\n"
                       "Accept: text/event-stream\r\nAccept-Encoding: identity\r\n\r\n"
                       % (self.PATH, self.host, self.user_agent))
            writer.write(request.encode())
            await writer.drain()
            status = await reader.readline()
            if b" 200" not in status:
                raise OSError("stream refused: %s" % status.strip())
            chunked = False
            while True:  # headers
                line = await reader.readline()
                if not line:
                    raise OSError("stream closed")
                if line in (b"\r\n", b"\n"):
                    break
                low = line.lower()
                if low.startswith(b"transfer-encoding:") and b"chunked" in low:
                    chunked = True
            self.connected = True
            self.connected_at = time.time()
            self.error = ""
            if chunked:
                await self._read_chunked(reader)
            else:
                while True:
                    line = await reader.readline()
                    if not line:
                        raise OSError("stream closed by server")
                    self.feed(line, int(time.time()))
        finally:
            self.connected = False
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _read_chunked(self, reader):
        """HTTP/1.1 chunked body: <hex size>CRLF <data> CRLF ... A chunk can
        end in the middle of an event line, so lines are rebuilt in a buffer."""
        buf = b""
        while True:
            size_line = await reader.readline()
            if not size_line:
                raise OSError("stream closed by server")
            try:
                size = int(size_line.split(b";")[0].strip(), 16)
            except ValueError:
                raise OSError("bad chunk header %r" % size_line[:20])
            if size == 0:
                raise OSError("stream ended by server")
            buf += await reader.readexactly(size)
            await reader.readline()  # the CRLF after each chunk
            now = int(time.time())
            start = 0
            while True:
                nl = buf.find(b"\n", start)
                if nl < 0:
                    break
                self.feed(buf[start:nl + 1], now)
                start = nl + 1
            buf = buf[start:]
            if len(buf) > 16384:  # a runaway line: drop it rather than run out of RAM
                buf = b""

    async def run(self):
        """Keep the stream open forever, reconnecting with backoff."""
        delay = 5
        while True:
            try:
                await self.read_once()
            except Exception as e:  # network errors, server restarts, Wi-Fi drops
                self.error = str(e) or type(e).__name__
                print("stream:", self.error)
            if time.time() - self.connected_at > 300:
                delay = 5  # it had been running fine: reconnect quickly
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120)
