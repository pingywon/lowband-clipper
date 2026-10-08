#!/usr/bin/env python3
"""Take the pictures used in the README and on the project page.

    python3 tools/shots.py             everything
    python3 tools/shots.py slider      only the calculator picture (or: app)

Runs the real app on a free local port, with the stand-in tape from docs/media/ as its one tape,
drives it in headless Chromium with real mouse input, and writes into docs/img/:

    editor.png  marking.png  reorder.png  render.png     the whole window, for the README
    step-mark.png  step-reorder.png  step-submit.png     close-ups, for the project page
    marking.gif                                          marking one clip, as a short loop
    slider.png                                           the connection-speed calculator

Needs Chromium (or Chrome) and ffmpeg.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import preview  # noqa: E402
from cdp import Browser  # noqa: E402
from clipkit.server import make_server  # noqa: E402

IMG = ROOT / "docs" / "img"
TAPE = "NASA interview"
W, H = 1440, 860
SEED = [(37.9, 51.2), (142.8, 173.5), (269.9, 291.0)]
CLOSE = {"mark": (0, 385, 760, 475), "reorder": (680, 46, 760, 475), "submit": (0, 385, 760, 475)}


def serve(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d" % server.server_address[1]


def post(base, act, body):
    req = urllib.request.Request(f"{base}/api/tape/{quote(TAPE)}/{act}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def settle(b, t=None):
    """Jump to a time (if given) and wait until the picture for it is on screen."""
    if t is not None:
        b.js("window.__clipper.seek(%r)" % t)
    b.wait("(function(){var v=document.getElementById('v');return v.readyState>=2&&!v.seeking;})()", what="a video frame")
    time.sleep(0.25)


def open_editor(b, base):
    b.goto(f"{base}/editor.html?tape={quote(TAPE)}")
    b.wait("window.__clipper && document.getElementById('v').readyState >= 1", what="the editor")


def app_shots():
    tmp = Path(tempfile.mkdtemp(prefix="clipper-shots-"))
    try:
        tapes = tmp / "tapes"
        tapes.mkdir()
        shutil.copy(ROOT / "docs" / "media" / "demo-v1.low.mp4", tapes / (TAPE + ".mp4"))
        shutil.copy(ROOT / "docs" / "media" / "demo-v1.low.mp4", tapes / (TAPE + ".low.mp4"))
        shutil.copy(ROOT / "site" / "demo-transcript.json", tapes / (TAPE + ".json"))
        app = make_server(tapes, "127.0.0.1", 0)
        app.RequestHandlerClass.log_message = lambda *a: None
        base = serve(app)
        post(base, "clip", {"in": SEED[0][0], "out": SEED[0][1]})

        with Browser(W, H) as b:
            # --- a short loop of marking one clip
            open_editor(b, base)
            frames = tmp / "frames"
            frames.mkdir()
            n = 0

            def frame(times=1):
                nonlocal n
                b.shot(frames / ("%03d.png" % n))
                for k in range(1, times):
                    shutil.copy(frames / ("%03d.png" % n), frames / ("%03d.png" % (n + k)))
                n += times

            settle(b, 142.8)
            frame(3)
            b.click("#mark")
            time.sleep(0.2)
            frame(2)
            t = 142.8
            while t < 173.5:
                t = min(173.5, t + 2.4)
                settle(b, t)
                frame()
            b.click("#mark")
            b.wait("window.__clipper.state().cutlist.clips.length === 2", what="the new clip")
            time.sleep(0.3)
            frame(6)
            subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-framerate", "4", "-i", str(frames / "%03d.png"),
                            "-vf", "scale=800:-2:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
                            "-loop", "0", str(IMG / "marking.gif")], check=True)

            post(base, "reset", {})
            for a, c in SEED:
                post(base, "clip", {"in": a, "out": c})

            # --- the whole editor, three clips cut
            open_editor(b, base)
            settle(b, 45.0)
            b.shot(IMG / "editor.png")

            # --- marking a fourth clip: the button is red and counting
            settle(b, 85.6)
            b.click("#mark")
            settle(b, 96.0)
            b.shot(IMG / "marking.png")
            b.shot(IMG / "step-mark.png", CLOSE["mark"])
            settle(b, 140.4)
            b.click("#mark")
            b.wait("window.__clipper.state().cutlist.clips.length === 4", what="the fourth clip")

            # --- dragging clip 004 up to second place
            x0, y0 = b.centre('.clip[data-n="4"] s')
            x1, y1 = b.centre('.clip[data-n="2"] s', fy=0.15)
            b.drag(x0, y0, x1, y1, steps=14, hold=lambda i: (b.shot(IMG / "reorder.png"),
                                                             b.shot(IMG / "step-reorder.png", CLOSE["reorder"])))
            b.wait("window.__clipper.state().cutlist.clips[1]['in'] === 85.6", what="the new order")

            # --- submit, and catch the render part-way
            settle(b, 150.0)
            b.click("#submit")
            b.wait("document.getElementById('job').classList.contains('ask')", what="the question")
            b.click("#go")
            b.wait("/ [1-3] \\/ 4/.test(document.getElementById('jt').textContent)", timeout=60, what="render progress")
            b.shot(IMG / "render.png")
            b.shot(IMG / "step-submit.png", CLOSE["submit"])
            b.wait("document.getElementById('job').classList.contains('done')", timeout=120, what="the render to finish")
            if b.errors:
                sys.exit("the page logged errors: " + "; ".join(b.errors))

        app.shutdown()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def slider_shot():
    """The calculator on the project page."""
    site = preview.make_server("127.0.0.1", 0)
    with Browser(1280, 1300) as b:
        b.goto(serve(site) + preview.PREFIX)
        time.sleep(0.6)
        r = b.rect("#meter")
        b.shot(IMG / "slider.png", (r["x"] - 12, r["y"] - 12, r["w"] + 24, r["h"] + 24))
    site.shutdown()
def main():
    IMG.mkdir(parents=True, exist_ok=True)
    which = sys.argv[1:] or ["app", "slider"]
    if "app" in which:
        app_shots()
    if "slider" in which:
        slider_shot()
    for f in sorted(IMG.iterdir()):
        print("%-18s %7.0f KB" % (f.name, f.stat().st_size / 1e3))


if __name__ == "__main__":
    main()
