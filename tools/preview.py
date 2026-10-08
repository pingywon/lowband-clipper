#!/usr/bin/env python3
"""Preview the project page the way GitHub Pages serves it: under /lowband-clipper/, with byte
ranges so the demo video can be scrubbed.

    python3 tools/preview.py [--port 8766] [--bind 127.0.0.1]

Then open http://127.0.0.1:8766/lowband-clipper/
"""
import argparse
import mimetypes
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

DOCS = Path(__file__).resolve().parent.parent / "docs"
PREFIX = "/lowband-clipper/"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        pass

    def _plain(self, code, text="", location=None):
        body = text.encode()
        self.send_response(code)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path in ("/", PREFIX.rstrip("/")):
            return self._plain(302, location=PREFIX)
        if not path.startswith(PREFIX):
            return self._plain(404, "not found")
        f = (DOCS / path[len(PREFIX):]).resolve()
        if DOCS.resolve() not in (f, *f.parents):
            return self._plain(404, "not found")
        if f.is_dir():
            if not path.endswith("/"):
                return self._plain(302, location=path + "/")
            f = f / "index.html"
        if not f.is_file():
            return self._plain(404, "not found")
        size = f.stat().st_size
        start, length, code = 0, size, 200
        m = re.match(r"bytes=(\d*)-(\d*)$", (self.headers.get("Range") or "").strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start, end = int(m.group(1)), int(m.group(2)) if m.group(2) else size - 1
            else:
                start, end = max(0, size - int(m.group(2))), size - 1
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                return self.end_headers()
            length, code = end - start + 1, 206
        self.send_response(code)
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{start + length - 1}/{size}")
        self.send_header("Content-Type", mimetypes.guess_type(f.name)[0] or "application/octet-stream")
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
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            super().handle_error(request, client_address)


def make_server(bind="127.0.0.1", port=8766):
    return Server((bind, port), Handler)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--bind", default="127.0.0.1")
    a = ap.parse_args()
    srv = make_server(a.bind, a.port)
    print(f"http://{a.bind}:{srv.server_address[1]}{PREFIX}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print()
