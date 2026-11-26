# web.py - a small control page: http://nixie.local/ (or the Pico's IP).
#
#   /                    status, and forms for everything below
#   /api                 status as JSON
#   /show?n=12345        show a fixed number      /resume   back to the playlist
#   /next                next playlist item       /hold     stay on this item
#   /cal?reading=99996   calibrate from what the counter reads
#   /cal?ppm=-12.5       set the correction       /exercise light every digit

import json
import time


def unquote(s):
    s = s.replace("+", " ")
    out, i = "", 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            out += chr(int(s[i + 1:i + 3], 16))
            i += 3
        else:
            out += s[i]
            i += 1
    return out


def parse_query(q):
    params = {}
    for pair in q.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[unquote(k)] = unquote(v)
    return params


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Nixie</title>
<style>
body{font:16px/1.4 Georgia,serif;max-width:34em;margin:1.5em auto;padding:0 1em;color:#222;background:#fffff8}
.tubes{font:600 3.2em/1 'Courier New',monospace;letter-spacing:.15em;color:#c25400}
small,.dim{color:#777}table{border-collapse:collapse}td{padding:.15em .8em .15em 0}
form{margin:.6em 0}input{font:inherit;width:7em}button{font:inherit}
@media(prefers-color-scheme:dark){body{background:#151515;color:#ddd}}
</style></head><body>
<div class="tubes">%(shown)s</div>
<p>%(source)s <span class="dim">(%(mode)s)</span><br><small>%(detail)s %(error)s</small></p>
<table>
<tr><td>playlist</td><td>%(playlist)s</td></tr>
<tr><td>calibration</td><td>%(cal)s ppm %(ref)s</td></tr>
<tr><td>gate, UTC offset</td><td>%(gate)s ms, %(tz)s h</td></tr>
</table>
<form action="/show">Show <input name="n" inputmode="numeric"> <button>show</button></form>
<form action="/cal">Counter reads <input name="reading" inputmode="numeric"> <button>calibrate</button>
<br><small>Show 99999 first, let both warm up, then type what the counter displays.</small></form>
<p><a href="/cal?delta=0.2">+0.2</a> &middot; <a href="/cal?delta=-0.2">&minus;0.2 ppm</a>
&nbsp; <a href="/next">next</a> &middot; <a href="/hold">hold</a> &middot;
<a href="/resume">resume playlist</a> &middot; <a href="/exercise">exercise tubes</a></p>
</body></html>"""


def render(st):
    names = []
    for i, name in enumerate(st["playlist"]):
        names.append("<b>%s</b>" % esc(name) if i == st["index"] else esc(name))
    return PAGE % {
        "shown": esc(st["shown"]),
        "source": esc(st["source"]),
        "mode": esc(st["mode"]),
        "detail": esc(st.get("detail") or ("value %s" % ("" if st["value"] is None
                                                          else st["value"]))),
        "error": ("&mdash; " + esc(st["error"])) if st["error"] else "",
        "playlist": " &middot; ".join(names),
        "cal": "%+.3f" % st["cal_ppm"],
        "ref": "" if st["ref_ppm"] is None else "(reference lock %+.3f)" % st["ref_ppm"],
        "gate": st["gate_ms"],
        "tz": "%+g" % st["utc_offset_h"],
    }


def route(ctl, path, params, now):
    """Returns (status line, content type, body)."""
    try:
        if path == "/api":
            return "200 OK", "application/json", json.dumps(ctl.status())
        if path == "/show":
            ctl.show_manual(int(params["n"]))
        elif path == "/resume":
            ctl.resume()
        elif path == "/hold":
            ctl.hold()
        elif path == "/next":
            ctl.next(now)
        elif path == "/exercise":
            ctl.exercise(now)
        elif path == "/cal":
            if "reading" in params:
                ctl.calibrate_reading(int(params["reading"]))
            elif "ppm" in params:
                ctl.set_cal_ppm(float(params["ppm"]))
            elif "delta" in params:
                ctl.set_cal_ppm(ctl.cal_ppm + float(params["delta"]))
        elif path != "/":
            return "404 Not Found", "text/plain", "not found"
    except (KeyError, ValueError) as e:
        return "400 Bad Request", "text/plain", "bad request: %s" % e
    if path != "/":
        return "303 See Other", "text/plain", ""
    return "200 OK", "text/html", render(ctl.status())


def make_handler(ctl):
    async def handle(reader, writer):
        try:
            request = await reader.readline()
            while True:  # skip headers
                line = await reader.readline()
                if not line or line == b"\r\n":
                    break
            parts = request.decode().split()
            target = parts[1] if len(parts) > 1 else "/"
            path = target.split("?", 1)[0]
            query = target.split("?", 1)[1] if "?" in target else ""
            status, ctype, body = route(ctl, path, parse_query(query), time.time())
            head = "HTTP/1.0 %s\r\nContent-Type: %s; charset=utf-8\r\n" % (status, ctype)
            if status.startswith("303"):
                head += "Location: /\r\n"
            writer.write((head + "Connection: close\r\n\r\n").encode())
            writer.write(body.encode())
            await writer.drain()
        except Exception as e:  # never let one bad request stop the server
            print("web:", e)
        finally:
            writer.close()
            await writer.wait_closed()
    return handle
