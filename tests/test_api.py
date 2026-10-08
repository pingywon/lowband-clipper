"""python3 -m unittest discover tests      The server, end to end, on two small generated tapes.

Needs ffmpeg and ffprobe. The server listens on a free port on this computer only.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from clipkit import __version__, media  # noqa: E402
from clipkit.server import make_server  # noqa: E402
from clipkit.tapes import discover, probe  # noqa: E402

TAPE = "talk one"           # a space in the name, on purpose
SILENT = "silent"
SRT = """﻿1
00:00:01,000 --> 00:00:03,500
>> FIRST LINE
OF THE TALK.

2
00:00:04,000 --> 00:00:06,000
<i>Second</i> line.
"""


def make_tape(path, audio=True, seconds=12):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x180:rate=25:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac", "-shortest"]
    subprocess.run(cmd + ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)], check=True)


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.tmp.name)
        make_tape(cls.folder / (TAPE + ".mp4"))
        make_tape(cls.folder / (SILENT + ".mp4"), audio=False)
        (cls.folder / (TAPE + ".srt")).write_text(SRT, encoding="utf-8")
        (cls.folder / "notes.txt").write_text("not a tape", encoding="utf-8")
        cls.srv = make_server(cls.folder, "127.0.0.1", 0)
        cls.srv.RequestHandlerClass.body_timeout = 1
        cls.srv.RequestHandlerClass.log_message = lambda *a: None
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    # ---- helpers
    def call(self, path, body=None, headers=None, method=None):
        h = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, dict(e.headers), e.read()

    def api(self, tape, tail, body=None, **kw):
        code, _, raw = self.call("/api/tape/%s/%s" % (quote(tape), tail), body, **kw)
        return code, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw)

    def reset(self, tape=TAPE):
        self.assertEqual(self.api(tape, "reset", {})[0], 200)

    # ---- pages
    def test_pages_carry_the_version(self):
        for path in ("/", "/editor.html"):
            code, _, raw = self.call(path)
            text = raw.decode()
            self.assertEqual(code, 200)
            self.assertIn("v" + __version__, text)
            self.assertNotIn("__VERSION__", text)
            self.assertNotIn("__BACKEND__", text)

    def test_only_listed_files_are_served(self):
        for path in ("/editor.js", "/model.js", "/favicon.svg"):
            self.assertEqual(self.call(path)[0], 200, path)
        for path in ("/backend-demo.js", "/../clipper.py", "/%2e%2e/VERSION", "/notes.txt", "/api/nope"):
            self.assertEqual(self.call(path)[0], 404, path)

    # ---- tapes
    def test_tapes_are_listed_and_low_copies_are_not_tapes(self):
        shutil.copy(self.folder / (SILENT + ".mp4"), self.folder / "other.low.mp4")
        try:
            code, _, raw = self.call("/api/tapes")
            d = json.loads(raw)
            self.assertEqual(code, 200)
            self.assertEqual(d["version"], __version__)
            self.assertEqual([t["id"] for t in d["tapes"]], [SILENT, TAPE])
        finally:
            (self.folder / "other.low.mp4").unlink()

    def test_state(self):
        code, st = self.api(TAPE, "state")
        self.assertEqual(code, 200)
        self.assertAlmostEqual(st["cutlist"]["duration"], 12.0, delta=0.2)
        self.assertEqual(st["cutlist"]["fps"], 25.0)
        self.assertAlmostEqual(st["envelope"]["n"], 240, delta=8)
        self.assertGreater(max(st["envelope"]["v"]), 50)
        self.assertEqual([(l["s"], l["e"], l["t"]) for l in st["lines"]],
                         [(1.0, 3.5, "FIRST LINE OF THE TALK."), (4.0, 6.0, "Second line.")])
        self.assertFalse(st["low"])

    def test_a_tape_without_sound_or_transcript(self):
        code, st = self.api(SILENT, "state")
        self.assertEqual(code, 200)
        self.assertEqual(st["envelope"]["v"], [])
        self.assertEqual(st["lines"], [])

    def test_unknown_tape_and_action(self):
        self.assertEqual(self.api("nope", "state")[0], 404)
        self.assertEqual(self.api("../" + TAPE, "state")[0], 404)
        self.assertEqual(self.api(TAPE, "explode", {})[0], 404)

    # ---- editing
    def test_mark_reorder_delete_undo(self):
        self.reset()
        self.api(TAPE, "clip", {"in": 1, "out": 3})
        self.api(TAPE, "clip", {"in": 8, "out": 6})
        code, r = self.api(TAPE, "clip", {"in": 4, "out": 5})
        self.assertEqual(code, 200)
        self.assertEqual([(c["n"], c["in"], c["out"]) for c in r["cutlist"]["clips"]], [(1, 1.0, 3.0), (2, 6.0, 8.0), (3, 4.0, 5.0)])
        _, r = self.api(TAPE, "order", {"order": [3, 1, 2]})
        self.assertEqual([c["in"] for c in r["cutlist"]["clips"]], [4.0, 1.0, 6.0])
        _, r = self.api(TAPE, "del", {"n": 1})
        self.assertEqual([c["in"] for c in r["cutlist"]["clips"]], [1.0, 6.0])
        _, r = self.api(TAPE, "undo", {})
        self.assertEqual([c["in"] for c in r["cutlist"]["clips"]], [4.0, 1.0, 6.0])
        _, r = self.api(TAPE, "undo", {"dir": "fwd"})
        self.assertEqual([c["in"] for c in r["cutlist"]["clips"]], [1.0, 6.0])
        self.assertEqual(self.api(TAPE, "state")[1]["cutlist"]["clips"], r["cutlist"]["clips"])

    def test_bad_requests(self):
        self.reset()
        self.assertEqual(self.api(TAPE, "clip", {"in": "abc", "out": 2})[0], 400)
        self.assertEqual(self.api(TAPE, "clip", {"out": 2})[0], 400)
        self.assertEqual(self.api(TAPE, "submit", {})[0], 400)                       # nothing marked
        code, _, _ = self.call("/api/tape/%s/clip" % quote(TAPE), method="POST",
                               headers={"Content-Type": "application/json", "Content-Length": "9"})
        self.assertEqual(code, 400)                                                   # promised a body, sent none

    def test_another_website_cannot_post(self):
        self.reset()
        self.assertEqual(self.api(TAPE, "clip", {"in": 1, "out": 2}, headers={"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.api(TAPE, "clip", {"in": 1, "out": 2}, headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.api(TAPE, "clip", {"in": 1, "out": 2}, headers={"Origin": self.base})[0], 200)
        self.assertEqual(len(self.api(TAPE, "state")[1]["cutlist"]["clips"]), 1)
        self.assertNotIn("Access-Control-Allow-Origin", self.call("/api/tapes")[1])

    # ---- video
    def test_video_ranges(self):
        url = "/api/tape/%s/video" % quote(TAPE)
        size = (self.folder / (TAPE + ".mp4")).stat().st_size
        code, h, raw = self.call(url, headers={"Range": "bytes=100-299"})
        self.assertEqual((code, len(raw), h["Content-Range"]), (206, 200, "bytes 100-299/%d" % size))
        code, h, raw = self.call(url, headers={"Range": "bytes=-50"})
        self.assertEqual((code, len(raw), h["Content-Range"]), (206, 50, "bytes %d-%d/%d" % (size - 50, size - 1, size)))
        code, h, raw = self.call(url)
        self.assertEqual((code, len(raw), h["Content-Type"]), (200, size, "video/mp4"))
        self.assertEqual(self.call(url, headers={"Range": "bytes=%d-" % (size + 5)})[0], 416)
        code, h, raw = self.call(url, method="HEAD")
        self.assertEqual((code, raw, h["Content-Length"]), (200, b"", str(size)))

    # ---- submit = render
    def wait(self, tape=TAPE):
        for _ in range(240):
            _, j = self.api(tape, "job")
            if j["state"] != "running":
                return j
            time.sleep(0.25)
        self.fail("render did not finish")

    def test_submit_renders_numbered_files(self):
        self.reset()
        self.api(TAPE, "clip", {"in": 6.5, "out": 9.25})
        self.api(TAPE, "clip", {"in": 1, "out": 3})
        clips = self.folder / (TAPE + "-clips")
        code, r = self.api(TAPE, "submit", {})
        self.assertEqual((code, r["cutlist"]["submitted"]["clips"]), (200, 2))
        j = self.wait()
        self.assertEqual((j["state"], j["done"], j["total"]), ("done", 2, 2), j)
        for name, want in (("001_00-00-06.mp4", 2.75), ("002_00-00-01.mp4", 2.0)):
            self.assertAlmostEqual(probe(clips / name)[0], want, delta=0.06, msg=name)
        readme = (clips / "README.txt").read_text(encoding="utf-8")
        self.assertIn("001  0:00:06.50 -> 0:00:09.25", readme)
        self.assertIn("FIRST LINE OF THE TALK.", readme)                              # under clip 002
        self.assertEqual(self.api(TAPE, "state")[1]["cutlist"]["submitted"]["job"], "done")

        # a second, different list: the finished notice is dropped, and the unwanted clip goes away
        code, r = self.api(TAPE, "del", {"n": 1})
        self.assertEqual(r["job"]["state"], "idle")
        self.api(TAPE, "submit", {})
        self.assertEqual(self.wait()["state"], "done")
        self.assertEqual(sorted(p.name for p in clips.glob("*.mp4")), ["001_00-00-01.mp4"])

    def test_render_never_deletes_from_a_folder_it_did_not_make(self):
        clips = self.folder / (SILENT + "-clips")
        clips.mkdir()
        (clips / "123_keep-me.mp4").write_bytes(b"mine")
        self.reset(SILENT)
        self.api(SILENT, "clip", {"in": 2, "out": 4})
        self.api(SILENT, "submit", {})
        self.assertEqual(self.wait(SILENT)["state"], "done")
        self.assertEqual(sorted(p.name for p in clips.glob("*.mp4")), ["001_00-00-02.mp4", "123_keep-me.mp4"])
        self.assertAlmostEqual(probe(clips / "001_00-00-02.mp4")[0], 2.0, delta=0.06)

    # ---- low copy
    def test_low_copy_is_made_and_then_played(self):
        folder = Path(tempfile.mkdtemp(dir=self.folder.parent))
        try:
            make_tape(folder / "long take.mp4", seconds=6)
            tape = discover(folder)["long take"]
            self.assertEqual(tape.viewing_copy(), tape.path)
            media.make_low(tape, say=lambda s: None, stats=False)
            self.assertEqual(list(discover(folder)), ["long take"])                   # the low copy is not a tape
            self.assertEqual(tape.viewing_copy(), tape.low)
            self.assertAlmostEqual(probe(tape.low)[0], 6.0, delta=0.25)
            keys = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
                                   "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(tape.low)],
                                  capture_output=True, text=True).stdout.replace(",", "").split()
            self.assertEqual([round(float(k)) for k in keys], [0, 1, 2, 3, 4, 5])
        finally:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
