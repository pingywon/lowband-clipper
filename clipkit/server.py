"""The web server: the pages in web/, a small JSON API, and byte-range video.

    GET  /                           tape picker
    GET  /editor.html?tape=ID        the editor
    GET  /api/tapes                  every tape with its clip count
    GET  /api/tape/ID/state          cut list + waveform + transcript
    GET  /api/tape/ID/video          the viewing copy, with Range support
    GET  /api/tape/ID/job            render progress
    POST /api/tape/ID/ACTION         clip | del | edit | order | undo | submit | reset

There is no login. It listens on this computer only unless started with --bind, and it refuses
POSTs that a different website tries to send through somebody's browser.
"""
import gzip
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from . import ROOT, __version__, media, model
from .tapes import VIDEO_TYPES, discover

WEB = ROOT / "web"
STATIC = {"index.html", "editor.html", "editor.css", "editor.js", "model.js", "backend-http.js", "favicon.svg"}
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}
BACKEND = '<script src="backend-http.js?v=__VERSION__"></script>'

JOBS = {}
JOBS_LOCK = threading.Lock()


def page(name):
    text = (WEB / name).read_text(encoding="utf-8")
    return text.replace("__BACKEND__", BACKEND).replace("__VERSION__", __version__)


def job_state(tape):
    j = JOBS.get(str(tape.path))
    return dict(j) if j else {"state": "idle"}


def summary(tape):
    cl = tape.store.load()
    sub = cl.get("submitted") or {}
    job = job_state(tape)["state"]
    return {"id": tape.id, "name": tape.name, "file": tape.path.name,
            "clips": len(cl["clips"]), "total": model.total(cl), "duration": cl.get("duration") or 0,
            "submitted": sub.get("at"), "job": job if job != "idle" else sub.get("job"),
            "low": tape.low.exists()}


def forget_finished(tape):
    """A finished render describes the list as it was. Once the list changes, stop announcing it."""
    with JOBS_LOCK:
        j = JOBS.get(str(tape.path))
        if j and j.get("state") != "running":
            del JOBS[str(tape.path)]


def start_job(tape, cl):
    """Submit = render, in a thread, straight from the cut list as it was when SUBMIT was pressed."""
    key = str(tape.path)
    with JOBS_LOCK:
        cur = JOBS.get(key)
        if cur and cur.get("state") == "running":
            return cur
        job = JOBS[key] = {"state": "running", "done": 0, "total": len(cl["clips"]), "line": "starting",
                           "error": "", "out": str(tape.clips_dir)}
    rows = media.transcript(tape)
    log = tape.state_dir / "render.log"

    def progress(done, total, line):
        job["done"], job["total"], job["line"] = done, total, line

    def run():
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as lf:
                lf.write("\n--- %s ---\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
                media.render(tape, cl, rows, progress=progress,
                             say=lambda s: (lf.write(s + "\n"), lf.flush()))
            job["state"] = "done"
        except Exception as e:                                   # noqa: BLE001
            job["state"], job["error"] = "failed", str(e)[:300]
        with tape.lock:
            now = tape.store.load()
            if now.get("submitted"):
                now["submitted"]["job"] = job["state"]
                tape.store.save(now, undo=False)

    threading.Thread(target=run, daemon=True).start()
    return job


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "lowband-clipper/" + __version__
    folder = None
    body_timeout = 15

    def log_message(self, fmt, *a):
        if "/video" not in str(a[0] if a else ""):
            sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % a))

    # ---- plumbing
    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        gz = len(body) > 1024 and "gzip" in (self.headers.get("Accept-Encoding") or "")
        if gz:
            body = gzip.compress(body, 6)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if gz:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        raw = b""
        if 0 < n <= 1 << 20:
            self.connection.settimeout(self.body_timeout)      # a client that promises a body and stalls
            try:
                raw = self.rfile.read(n)
            except OSError:
                self.close_connection = True
                return None
            finally:
                self.connection.settimeout(None)
        try:
            d = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            return None
        return d if isinstance(d, dict) else None

    def _route(self):
        """-> (tape, tail) for /api/tape/ID/tail, sending the 404 itself when there is no match."""
        m = re.match(r"^/api/tape/([^/]+)/([a-z]+)$", urlsplit(self.path).path)
        tape = discover(self.folder).get(unquote(m.group(1))) if m else None
        if tape is None:
            self._send(404, "not found")
            return None, None
        return tape, m.group(2)

    def _from_this_site(self):
        """Browsers attach Origin to cross-site POSTs. If it names a different host, refuse."""
        origin = self.headers.get("Origin")
        return not origin or urlsplit(origin).netloc == (self.headers.get("Host") or "")

    # ---- GET
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        p = urlsplit(self.path).path
        name = "index.html" if p == "/" else p.lstrip("/")
        if name in STATIC:
            ext = name[name.rfind("."):]
            body = page(name) if ext == ".html" else (WEB / name).read_bytes()
            return self._send(200, body, TYPES[ext])
        if p == "/api/tapes":
            return self._json({"version": __version__,
                               "tapes": [summary(t) for t in discover(self.folder).values()]})
        tape, tail = self._route()
        if tape is None:
            return
        if tail == "video":
            return self._video(tape)
        if tail == "job":
            return self._json(job_state(tape))
        if tail == "state":
            with tape.lock:
                tape.store.snapshot()
                return self._json({"id": tape.id, "name": tape.name, "version": __version__,
                                   "cutlist": tape.store.load(), "envelope": media.envelope(tape),
                                   "lines": media.transcript(tape), "job": job_state(tape),
                                   "low": tape.low.exists(), "file": tape.path.name})
        self._send(404, "not found")

    # ---- POST
    def do_POST(self):
        tape, act = self._route()
        if tape is None:
            return
        if not self._from_this_site():
            return self._json({"error": "cross-site request refused"}, 403)
        if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
            return self._json({"error": "send JSON"}, 415)
        body = self._body()
        if body is None:
            return self._json({"error": "bad JSON"}, 400)
        with tape.lock:
            try:
                cl = self._apply(tape, act, body)
            except (KeyError, ValueError, TypeError, IndexError, OverflowError) as e:
                return self._json({"error": str(e) or "bad request"}, 400)
        if cl is None:
            return self._send(404, "not found")
        if act != "submit":
            forget_finished(tape)
        self._json({"cutlist": cl, "job": job_state(tape)})

    def _apply(self, tape, act, body):
        store = tape.store
        if act in model.ACTIONS:
            return store.save(model.apply(store.load(), act, body))
        if act == "undo":
            return store.undo("fwd" if body.get("dir") == "fwd" else "back")
        if act == "reset":
            return store.save(store.fresh())
        if act == "submit":
            cl = store.load()
            if not cl["clips"]:
                raise ValueError("nothing marked yet")
            cl["submitted"] = {"at": model.stamp(), "clips": len(cl["clips"]),
                               "total": model.total(cl), "job": "running"}
            cl = store.save(cl)
            start_job(tape, json.loads(json.dumps(cl)))
            return cl
        return None

    # ---- video
    def _video(self, tape):
        f = tape.viewing_copy()
        try:
            size = f.stat().st_size
        except OSError:
            return self._send(404, "no video")
        start, length, code = 0, size, 200
        m = re.match(r"bytes=(\d*)-(\d*)$", (self.headers.get("Range") or "").strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start, end = max(0, size - int(m.group(2))), size - 1
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            length, code = end - start + 1, 206
        self.send_response(code)
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{start + length - 1}/{size}")
        self.send_header("Content-Type", VIDEO_TYPES.get(f.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(f, "rb") as fh:
                fh.seek(start)
                left = length
                while left > 0:
                    chunk = fh.read(min(1 << 18, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """Browsers drop video connections all the time while seeking; that is not worth a traceback."""
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            super().handle_error(request, client_address)


def make_server(folder, bind="127.0.0.1", port=8765):
    handler = type("BoundHandler", (Handler,), {"folder": folder})
    return Server((bind, port), handler)
