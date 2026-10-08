#!/usr/bin/env python3
"""Prepare the stand-in tape for the live demo on the project page. Run once, by hand:

    python3 tools/make_demo.py /path/to/source.mp4

Writes three things under docs/, and they are committed as they are:

    docs/media/demo-v1.low.mp4   the tape, encoded with the tool's own low-copy settings
    docs/img/poster.jpg          one still, shown before the video loads
    docs/demo/state.json         what the demo starts with: tape facts, waveform, transcript, three clips

tools/build_site.py never touches these, so a rebuild cannot re-encode the video and bloat the
repository history. The transcript comes from site/demo-transcript.json.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from clipkit import media, model  # noqa: E402
from clipkit.tapes import probe  # noqa: E402

DOCS = ROOT / "docs"
VIDEO = DOCS / "media" / "demo-v1.low.mp4"
POSTER = DOCS / "img" / "poster.jpg"
STATE = DOCS / "demo" / "state.json"
NAME = "Stand-in tape: NASA interview"
POSTER_AT = 40.0
SEED_CLIPS = [(37.9, 51.2), (142.8, 173.5), (269.9, 291.0)]      # three answers, to have something to drag


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    src = Path(sys.argv[1]).expanduser().resolve()
    for d in (VIDEO.parent, POSTER.parent, STATE.parent):
        d.mkdir(parents=True, exist_ok=True)

    if VIDEO.exists():
        print(f"keeping the existing {VIDEO.relative_to(ROOT)} (delete it to encode again)")
    else:
        subprocess.run(media.low_cmd(src, VIDEO, stats=False), check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", str(POSTER_AT), "-i", str(VIDEO),
                    "-frames:v", "1", "-q:v", "4", "-map_metadata", "-1", str(POSTER)], check=True)

    duration, fps = probe(VIDEO)
    cl = model.fresh(VIDEO.name, duration, fps)
    for a, b in SEED_CLIPS:
        cl = model.apply(cl, "clip", {"in": a, "out": b})
    lines = [{"s": r["s"], "e": r["e"], "t": r["t"], "r": r.get("r", "")}
             for r in json.loads((ROOT / "site" / "demo-transcript.json").read_text(encoding="utf-8"))]
    state = {"seed": "v1", "id": "demo", "name": NAME,
             "video": "../media/" + VIDEO.name, "poster": "../img/" + POSTER.name,
             "cutlist": cl, "envelope": media.compute_envelope(VIDEO), "lines": lines}
    STATE.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{VIDEO.relative_to(ROOT)}  {VIDEO.stat().st_size / 1e6:.1f} MB  {duration:.1f}s  {fps:g} fps")
    print(f"{STATE.relative_to(ROOT)}  {STATE.stat().st_size / 1e3:.0f} KB  {len(lines)} lines  {state['envelope']['n']} waveform points")


if __name__ == "__main__":
    main()
