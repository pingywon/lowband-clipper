"""The cut list: a plain dict, a few pure rules, and a small on-disk store.

    {"version": 2, "tape": "interview.mp4", "duration": 3765.9, "fps": 24.0,
     "updated": "...", "submitted": None | {...},
     "clips": [{"n": 1, "in": 12.5, "out": 31.0, "note": ""}, ...]}

Clips are numbered in the order they were marked. They may overlap and may be any length.
web/model.js mirrors normalise() and apply(); tests/vectors.json keeps the two honest.
"""
import json
import math
import time

UNDO_MAX = 100
BACKUP_KEEP = 30
NOTE_MAX = 200
ACTIONS = ("clip", "del", "edit", "order")


def r3(x):
    """Round to the millisecond the same way in Python and JavaScript."""
    return math.floor(float(x) * 1000 + 0.5) / 1000


def fresh(tape, duration, fps):
    return {"version": 2, "tape": tape, "duration": duration, "fps": fps,
            "updated": "", "submitted": None, "clips": []}


def normalise(cl):
    dur = cl.get("duration") or 0
    frame = 1.0 / (cl.get("fps") or 24)
    out = []
    for c in cl.get("clips", []):
        a, b = r3(c["in"]), r3(c["out"])
        a, b = max(0.0, a), max(0.0, b)
        if dur > 0:
            a, b = min(a, dur), min(b, dur)
        if b < a:
            a, b = b, a
        if b - a < frame:
            continue
        note = c.get("note")
        out.append({"n": len(out) + 1, "in": a, "out": b, "note": ("" if note is None else str(note))[:NOTE_MAX]})
    cl["clips"] = out
    return cl


def apply(cl, act, body):
    """One edit to the cut list. Returns the list, or None for an action it does not know."""
    clips = cl["clips"]
    if act == "clip":
        clips.append({"in": float(body["in"]), "out": float(body["out"]), "note": body.get("note", "")})
    elif act == "del":
        n = int(body["n"])
        cl["clips"] = [c for c in clips if c.get("n") != n]
    elif act == "edit":
        n = int(body["n"])
        for c in clips:
            if c.get("n") == n:
                if "in" in body:
                    c["in"] = float(body["in"])
                if "out" in body:
                    c["out"] = float(body["out"])
                if "note" in body:
                    c["note"] = body["note"]
    elif act == "order":
        by = {c["n"]: c for c in clips}
        picked, seen = [], set()
        for x in body.get("order") or []:
            n = int(x)
            if n in by and n not in seen:
                seen.add(n)
                picked.append(by[n])
        cl["clips"] = picked + [c for c in clips if c["n"] not in seen]
    else:
        return None
    return normalise(cl)


def total(cl):
    return r3(sum(c["out"] - c["in"] for c in cl["clips"]))


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def stamp():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class Store:
    """cutlist.json, its undo history and a few dated backups, all inside one folder."""

    def __init__(self, folder, tape_name, probe):
        self.dir = folder
        self.tape_name = tape_name
        self.probe = probe                      # () -> (duration, fps)
        self.file = folder / "cutlist.json"
        self.undo_file = folder / "undo.json"
        self.backups = folder / "backups"

    def fresh(self):
        duration, fps = self.probe()
        return fresh(self.tape_name, duration, fps)

    def load(self):
        if self.file.exists():
            try:
                cl = json.loads(self.file.read_text(encoding="utf-8"))
                if not cl.get("duration"):
                    cl["duration"], cl["fps"] = self.probe()
                return normalise(cl)
            except (ValueError, KeyError, TypeError, AttributeError):
                pass
        return self.fresh()

    def save(self, cl, undo=True):
        if undo and self.file.exists():
            self._push(self.file.read_text(encoding="utf-8"))
        cl["updated"] = stamp()
        atomic_write(self.file, json.dumps(cl, ensure_ascii=False, indent=1))
        return cl

    def snapshot(self):
        """One dated copy per minute at most, newest BACKUP_KEEP kept."""
        if not self.file.exists():
            return
        self.backups.mkdir(parents=True, exist_ok=True)
        dst = self.backups / ("cutlist_" + time.strftime("%Y%m%d-%H%M") + ".json")
        if not dst.exists():
            dst.write_text(self.file.read_text(encoding="utf-8"), encoding="utf-8")
        for old in sorted(self.backups.glob("cutlist_*.json"))[:-BACKUP_KEEP]:
            old.unlink()

    def _history(self):
        if self.undo_file.exists():
            try:
                d = json.loads(self.undo_file.read_text(encoding="utf-8"))
                if isinstance(d, dict) and isinstance(d.get("back"), list) and isinstance(d.get("fwd"), list):
                    return d
            except ValueError:
                pass
        return {"back": [], "fwd": []}

    def _push(self, text):
        d = self._history()
        d["back"] = (d["back"] + [text])[-UNDO_MAX:]
        d["fwd"] = []
        atomic_write(self.undo_file, json.dumps(d))

    def undo(self, direction="back"):
        d = self._history()
        src, dst = ("back", "fwd") if direction == "back" else ("fwd", "back")
        if not d[src]:
            return self.load()
        text = d[src].pop()
        if self.file.exists():
            d[dst] = (d[dst] + [self.file.read_text(encoding="utf-8")])[-UNDO_MAX:]
        atomic_write(self.undo_file, json.dumps(d))
        atomic_write(self.file, text)
        return self.load()
