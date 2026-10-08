"""A small headless-Chromium driver for the UI tests and the README screenshots.

Standard library only: it starts Chromium with remote debugging on and speaks the DevTools
protocol over a hand-rolled WebSocket. Input goes in as real mouse and touch events, not as
calls into the page's own functions.
"""
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit

CHROMIUM = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WebSocket:
    def __init__(self, url):
        u = urlsplit(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=60)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += self._read(1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise RuntimeError("websocket handshake refused: " + head[:80].decode(errors="replace"))

    def _read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("browser closed the connection")
            buf += chunk
        return buf

    def send(self, text, opcode=1):
        data = text.encode() if isinstance(text, str) else text
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x80 | opcode]) + (bytes([0x80 | n]) if n < 126 else
                                         bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else
                                         bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        out = b""
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            data = self._read(n)
            op = b0 & 0x0F
            if op == 9:
                self.send(data, 10)
            elif op == 8:
                raise ConnectionError("browser closed the connection")
            elif op in (0, 1, 2):
                out += data
                if b0 & 0x80:
                    return out.decode("utf-8", errors="replace")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Browser:
    """with Browser(1280, 800) as b: b.goto(url); b.click("#mark"); b.shot("out.png")"""

    def __init__(self, width=1280, height=800, touch=False, scale=1):
        exe = next((p for p in map(shutil.which, CHROMIUM) if p), None)
        if not exe:
            raise RuntimeError("no Chromium or Chrome found on the PATH")
        self.width, self.height, self.touch = width, height, touch
        self.profile = tempfile.mkdtemp(prefix="clipper-chromium-")
        port = free_port()
        self.proc = subprocess.Popen(
            [exe, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--mute-audio",
             "--autoplay-policy=no-user-gesture-required", "--no-first-run", "--disable-extensions",
             f"--remote-debugging-port={port}", f"--user-data-dir={self.profile}",
             f"--window-size={width},{height}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        target = None
        for _ in range(100):
            try:
                pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2))
                target = next(p for p in pages if p.get("type") == "page")
                break
            except (OSError, ValueError, StopIteration):
                time.sleep(0.1)
        if target is None:
            self.close()
            raise RuntimeError("Chromium did not start")
        self.ws = WebSocket(target["webSocketDebuggerUrl"])
        self.id = 0
        self.errors, self.requests = [], []
        for domain in ("Page", "Runtime", "Network", "Log"):
            self.send(domain + ".enable")
        self.send("Emulation.setDeviceMetricsOverride",
                  {"width": width, "height": height, "deviceScaleFactor": scale, "mobile": touch})
        if touch:
            self.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if getattr(self, "ws", None):
            self.ws.close()
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)

    # ---- protocol
    def _event(self, msg):
        m, p = msg.get("method"), msg.get("params", {})
        if m == "Runtime.exceptionThrown":
            d = p["exceptionDetails"]
            self.errors.append((d.get("exception") or {}).get("description") or d.get("text", "exception"))
        elif m == "Log.entryAdded" and p["entry"].get("level") == "error":
            self.errors.append(p["entry"].get("text", "") + " " + p["entry"].get("url", ""))
        elif m == "Network.requestWillBeSent":
            self.requests.append(p["request"]["url"])

    def send(self, method, params=None):
        self.id += 1
        want = self.id
        self.ws.send(json.dumps({"id": want, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == want:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error'].get('message')}")
                return msg.get("result", {})
            self._event(msg)

    def js(self, expression):
        r = self.send("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            raise RuntimeError("page script failed: " + str(r["exceptionDetails"].get("exception", {}).get("description")))
        return r["result"].get("value")

    def wait(self, expression, timeout=20, what=None):
        end = time.time() + timeout
        while time.time() < end:
            if self.js("!!(" + expression + ")"):
                return
            time.sleep(0.1)
        raise TimeoutError("timed out waiting for " + (what or expression))

    def goto(self, url):
        self.send("Page.navigate", {"url": url})
        self.wait("document.readyState === 'complete'", what="the page to load")

    # ---- looking
    def rect(self, selector):
        r = self.js("(function(){var e=document.querySelector(%s);if(!e)return null;var r=e.getBoundingClientRect();"
                    "return {x:r.x,y:r.y,w:r.width,h:r.height};})()" % json.dumps(selector))
        if not r:
            raise RuntimeError("nothing matches " + selector)
        return r

    def centre(self, selector, fx=0.5, fy=0.5):
        r = self.rect(selector)
        return r["x"] + r["w"] * fx, r["y"] + r["h"] * fy

    def shot(self, path, clip=None):
        """The viewport, or the rectangle clip=(x, y, width, height) measured from the top of the page."""
        params = {"format": "png"}
        if clip:
            params["clip"] = dict(zip(("x", "y", "width", "height"), clip), scale=1)
            params["captureBeyondViewport"] = True
        data = self.send("Page.captureScreenshot", params)["data"]
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path

    # ---- input
    def _mouse(self, kind, x, y, buttons=0, **extra):
        held = "left" if (kind != "mouseMoved" or buttons) else "none"       # a move with the button down is a drag
        self.send("Input.dispatchMouseEvent", dict({"type": kind, "x": x, "y": y, "button": held, "buttons": buttons,
                                                    "clickCount": 1 if kind != "mouseMoved" else 0}, **extra))

    def _touch(self, kind, x=None, y=None):
        pts = [] if x is None else [{"x": round(x), "y": round(y)}]
        self.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": pts})

    def click(self, selector, fx=0.5, fy=0.5):
        x, y = self.centre(selector, fx, fy)
        if self.touch:
            self._touch("touchStart", x, y)
            self._touch("touchEnd")
        else:
            self._mouse("mouseMoved", x, y)
            self._mouse("mousePressed", x, y, 1)
            self._mouse("mouseReleased", x, y)

    def drag(self, x0, y0, x1, y1, steps=12, hold=None):
        """Press at one point, move to another in small steps, release. `hold(i)` is called
        part-way so a caller can take a picture mid-drag."""
        down, move, up = ((lambda x, y: self._touch("touchStart", x, y), lambda x, y: self._touch("touchMove", x, y),
                           lambda x, y: self._touch("touchEnd")) if self.touch else
                          (lambda x, y: (self._mouse("mouseMoved", x, y), self._mouse("mousePressed", x, y, 1)),
                           lambda x, y: self._mouse("mouseMoved", x, y, 1), lambda x, y: self._mouse("mouseReleased", x, y)))
        down(x0, y0)
        for i in range(1, steps + 1):
            move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
            time.sleep(0.02)
            if hold and i == steps:
                hold(i)
        up(x1, y1)

    def key(self, key, code=None, vk=0, modifiers=0, text=None):
        base = {"key": key, "code": code or key, "windowsVirtualKeyCode": vk, "modifiers": modifiers}
        self.send("Input.dispatchKeyEvent", dict(base, type="keyDown", **({"text": text} if text else {})))
        self.send("Input.dispatchKeyEvent", dict(base, type="keyUp"))
