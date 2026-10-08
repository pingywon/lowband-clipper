"""Everything that touches ffmpeg, plus transcript reading.

- envelope():   a 20-per-second loudness strip for the timeline
- transcript(): rows from a .json / .srt / .vtt file next to the tape
- make_low():   the small viewing copy that makes scrubbing bearable on a thin connection
- render():     cut the marked clips out of the full-quality tape
"""
import json
import math
import operator
import re
import subprocess
import sys
import time
from array import array

from . import model

ENVELOPE_HZ = 20
ENVELOPE_RATE = 8000
FADE = 0.02                     # seconds of audio fade at each end of a clip, to avoid clicks
MARKER = ".lowband-clipper"     # dropped into a clips folder this tool created

try:
    _sumprod = math.sumprod                                  # Python 3.12+
except AttributeError:                                       # pragma: no cover
    def _sumprod(a, b):
        return sum(map(operator.mul, a, b))


# --------------------------------------------------------------------------- waveform
def compute_envelope(path, hz=ENVELOPE_HZ, rate=ENVELOPE_RATE):
    """Decode the first audio track to 8 kHz mono and take one loudness value per 1/hz second.
    About five seconds per hour of tape. A tape with no audio gives an empty strip."""
    hop = rate // hz
    need = hop * 2
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-vn", "-map", "0:a:0",
           "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    rms, buf = [], b""
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return {"hz": hz, "n": 0, "v": []}
    with p:
        while True:
            chunk = p.stdout.read(need * 512)
            if not chunk:
                break
            buf += chunk
            n = len(buf) // need
            a = array("h")
            a.frombytes(buf[:n * need])
            buf = buf[n * need:]
            if sys.byteorder == "big":
                a.byteswap()
            for i in range(n):
                c = a[i * hop:(i + 1) * hop]
                rms.append(math.sqrt(_sumprod(c, c) / hop) / 32768.0)
    if p.returncode != 0 or not rms:
        return {"hz": hz, "n": 0, "v": []}
    ref = sorted(rms)[min(len(rms) - 1, int(len(rms) * 0.995))] or 1.0
    v = [int(round(math.sqrt(min(1.0, x / ref)) * 100)) for x in rms]
    return {"hz": hz, "n": len(v), "v": v}


def envelope(tape):
    """Cached next to the cut list; recomputed when the tape file changes."""
    cache = tape.state_dir / "envelope.json"
    try:
        mtime = int(tape.path.stat().st_mtime)
    except OSError:
        return {"hz": ENVELOPE_HZ, "n": 0, "v": []}
    if cache.exists():
        try:
            d = json.loads(cache.read_text(encoding="utf-8"))
            if d.get("mtime") == mtime:
                return {"hz": d["hz"], "n": d["n"], "v": d["v"]}
        except (ValueError, KeyError):
            pass
    env = compute_envelope(tape.path)
    model.atomic_write(cache, json.dumps(dict(env, mtime=mtime), separators=(",", ":")))
    return env


# --------------------------------------------------------------------------- transcript
_CUE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")


def _secs(h, m, s, ms):
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_cues(text):
    """SubRip and WebVTT share enough that one loop reads both."""
    rows, cur = [], None
    for raw in text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        m = _CUE.search(raw)
        if m:
            g = m.groups()
            cur = {"s": _secs(*g[0:4]), "e": _secs(*g[4:8]), "t": "", "r": ""}
            rows.append(cur)
            continue
        line = raw.strip()
        if not line:
            cur = None
        elif cur is not None:
            line = re.sub(r"<[^>]+>", "", line).lstrip(">").strip()
            cur["t"] = (cur["t"] + " " + line).strip()
    return rows


def parse_json(text):
    """A list of rows, or {"lines": [...]}, or speech-to-text output shaped {"segments": [...]}.
    Each row needs a start, an end and some text; "role": "q" marks an interviewer's line."""
    d = json.loads(text)
    if isinstance(d, dict):
        d = d.get("lines") or d.get("segments") or []
    rows = []
    for x in d:
        if not isinstance(x, dict):
            continue
        rows.append({"s": float(x.get("start", x.get("s", 0)) or 0),
                     "e": float(x.get("end", x.get("e", 0)) or 0),
                     "t": str(x.get("text", x.get("t", "")) or "").strip(),
                     "r": str(x.get("role", x.get("r", "")) or "")})
    return rows


def transcript(tape):
    f = tape.transcript_file()
    if f is None:
        return []
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
        rows = parse_json(text) if f.suffix.lower() == ".json" else parse_cues(text)
    except (OSError, ValueError, TypeError):
        return []
    rows = [{"s": round(r["s"], 2), "e": round(max(r["e"], r["s"]), 2), "t": r["t"], "r": r["r"]}
            for r in rows if r["t"]]
    return sorted(rows, key=lambda r: r["s"])


# --------------------------------------------------------------------------- low copy
def low_cmd(src, dst, stats=True):
    """480p, about 0.55 Mbps, and a keyframe every second. The keyframes matter most: a browser
    has to download everything back to the previous keyframe before it can show a picture, so
    one a second keeps every jump on the timeline small."""
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", *(["-stats"] if stats else []), "-y", "-i", str(src),
            "-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "-1", "-map_chapters", "-1",
            "-vf", "scale=-2:'min(480,trunc(ih/2)*2)'", "-c:v", "libx264", "-preset", "veryfast",
            "-b:v", "450k", "-maxrate", "550k", "-bufsize", "900k", "-pix_fmt", "yuv420p",
            "-force_key_frames", "expr:gte(t,n_forced*1)", "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(dst)]


def make_low(tape, say=print, stats=True):
    """Encode next to the tape as a hidden part-file, check its length, then move it into place."""
    from .tapes import probe
    part = tape.folder / ("." + tape.path.stem + ".low.part.mp4")
    say(f"encoding {tape.path.name} -> {tape.low.name}")
    p = subprocess.run(low_cmd(tape.path, part, stats))
    if p.returncode != 0:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg could not encode {tape.path.name}")
    want, got = tape.probe()[0], probe(part)[0]
    if want and abs(want - got) > 0.25:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"low copy is {got:.2f}s long but the tape is {want:.2f}s; not keeping it")
    part.replace(tape.low)
    say(f"done: {tape.low}  ({tape.low.stat().st_size / 1e6:.1f} MB)")
    return tape.low


# --------------------------------------------------------------------------- render
def hms(s):
    s = max(0, int(s))
    return "%02d-%02d-%02d" % (s // 3600, s % 3600 // 60, s % 60)


def clock(s):
    s = max(0.0, float(s))
    return "%d:%02d:%05.2f" % (s // 3600, s % 3600 // 60, s % 60)


def clip_name(c):
    return "%03d_%s.mp4" % (c["n"], hms(c["in"]))


def clip_cmd(src, c, dst):
    dur = c["out"] - c["in"]
    af = f"afade=t=in:st=0:d={FADE},afade=t=out:st={max(0.0, dur - FADE):.3f}:d={FADE}"
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-ss", f"{c['in']:.3f}", "-i", str(src), "-t", f"{dur:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-af", af, "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dst)]


def first_line(rows, a, b):
    for r in rows:
        if r["e"] > a and r["s"] < b and r["t"]:
            return r["t"]
    return ""


def render(tape, cl, rows=(), only=None, dry=False, progress=None, say=print):
    """Cut every clip in the list out of the full-quality tape, as numbered files plus a
    README.txt that can be read without opening anything. `progress(done, total, line)` is
    called after each clip."""
    clips = cl["clips"]
    todo = [c for c in clips if only is None or c["n"] in only]
    out = tape.clips_dir
    plan = [(c, out / clip_name(c)) for c in todo]
    say(f"{tape.name}: {len(plan)} clips, {clock(sum(c['out'] - c['in'] for c in todo))} total, from {tape.path.name}")
    if dry:
        for c, dst in plan:
            say(" ".join(clip_cmd(tape.path, c, dst)))
        return []

    ours = (out / MARKER).exists() or not out.exists()
    out.mkdir(parents=True, exist_ok=True)
    if ours:
        (out / MARKER).write_text("made by lowband-clipper\n", encoding="utf-8")
        if only is None:        # clips left over from an earlier, different list would mislead
            keep = {d for _, d in plan}
            for old in out.glob("[0-9][0-9][0-9]_*.mp4"):
                if old not in keep:
                    old.unlink()
                    say(f"  removed stale {old.name}")

    t0 = time.time()
    for i, (c, dst) in enumerate(plan, 1):
        s = time.time()
        p = subprocess.run(clip_cmd(tape.path, c, dst), capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"ffmpeg failed on clip {c['n']}: {p.stderr.strip()[-400:]}")
        line = f"[{i}/{len(plan)}] {dst.name}  {clock(c['out'] - c['in'])}  ({time.time() - s:.1f}s)"
        say("  " + line)
        if progress:
            progress(i, len(plan), line)
    say(f"done in {time.time() - t0:.1f}s")

    text = [f"{tape.name} - clips",
            f"rendered {time.strftime('%Y-%m-%d %H:%M')} from {tape.path.name}",
            f"tape {clock(cl.get('duration') or 0)}   {len(clips)} clips   {clock(model.total(cl))} kept", ""]
    for c in clips:
        text.append("%03d  %s -> %s  (%s)%s" % (c["n"], clock(c["in"]), clock(c["out"]),
                                                clock(c["out"] - c["in"]),
                                                "   " + c["note"] if c.get("note") else ""))
        t = first_line(rows, c["in"], c["out"])
        if t:
            text.append("     " + (t[:96] + "..." if len(t) > 96 else t))
    text += ["", "Numbered in the order they were marked while watching."]
    model.atomic_write(out / "README.txt", "\n".join(text) + "\n")
    return [d for _, d in plan]
