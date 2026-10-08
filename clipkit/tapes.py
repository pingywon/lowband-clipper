"""Find the tapes in a folder.

A tape is any video file in the folder. Next to `interview.mp4` these are picked up if present:

    interview.low.mp4            small viewing copy (made by `clipper.py low`)
    interview.srt | .vtt | .json transcript
    .clipper/interview/          cut list, undo history, backups, cached waveform
    interview-clips/             rendered clips
"""
import json
import subprocess
import threading
from pathlib import Path

from . import model

VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
               ".webm": "video/webm", ".mkv": "video/x-matroska"}
TRANSCRIPT_EXTS = (".json", ".srt", ".vtt")
STATE_DIR = ".clipper"
LOW_SUFFIX = ".low"

_CACHE = {}
_CACHE_LOCK = threading.Lock()


def probe(path):
    """(duration in seconds, frames per second) -- (0.0, 24.0) if ffprobe cannot tell."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=avg_frame_rate,r_frame_rate:format=duration",
             "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
        d = json.loads(out.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0, 24.0
    try:
        duration = round(float(d["format"]["duration"]), 3)
    except (KeyError, ValueError, TypeError):
        duration = 0.0
    fps = 24.0
    for key in ("avg_frame_rate", "r_frame_rate"):
        try:
            num, den = (d["streams"][0][key] + "/1").split("/")[:2]
            if float(num) > 0 and float(den) > 0:
                fps = round(float(num) / float(den), 3)
                break
        except (KeyError, IndexError, ValueError, TypeError):
            continue
    return duration, fps


class Tape:
    def __init__(self, path, tape_id):
        self.path = Path(path)
        self.id = tape_id
        self.name = self.path.stem
        self.folder = self.path.parent
        self.lock = threading.RLock()
        self.low = self.folder / (self.path.stem + LOW_SUFFIX + ".mp4")
        self.state_dir = self.folder / STATE_DIR / self.path.stem
        self.clips_dir = self.folder / (self.path.stem + "-clips")
        self.store = model.Store(self.state_dir, self.path.name, self.probe)
        self._probe = None

    def probe(self):
        if self._probe is None or not self._probe[0]:
            self._probe = probe(self.path)
        return self._probe

    def viewing_copy(self):
        """What the browser plays: the low copy when there is one, else the tape itself."""
        return self.low if self.low.exists() else self.path

    def transcript_file(self):
        for ext in TRANSCRIPT_EXTS:
            f = self.folder / (self.path.stem + ext)
            if f.exists():
                return f
        return None


def is_tape(f):
    return (f.is_file() and not f.name.startswith(".") and f.suffix.lower() in VIDEO_TYPES
            and not f.stem.lower().endswith(LOW_SUFFIX))


def discover(folder):
    """{id: Tape} for every tape in the folder, in name order. Tape objects are reused across
    calls so that each keeps one lock."""
    folder = Path(folder)
    found = {}
    try:
        files = sorted(f for f in folder.iterdir() if is_tape(f))
    except OSError:
        return found
    with _CACHE_LOCK:
        for f in files:
            tape_id = f.stem if f.stem not in found else f.name
            key = (str(f.resolve()), tape_id)
            if key not in _CACHE:
                _CACHE[key] = Tape(f, tape_id)
            found[tape_id] = _CACHE[key]
    return found
