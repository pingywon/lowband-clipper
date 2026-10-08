#!/usr/bin/env python3
"""lowband-clipper: watch a long tape in the browser, press one button where a clip starts and
again where it ends, press SUBMIT, and the clips are cut from the full-quality file.

    python3 clipper.py FOLDER                 serve every video in FOLDER on http://127.0.0.1:8765/
    python3 clipper.py FOLDER --bind 0.0.0.0  ... and let other computers on your network reach it
    python3 clipper.py low FILE [FILE ...]    make the small viewing copy for one or more tapes
    python3 clipper.py render FOLDER TAPE     cut a tape's marked clips from the command line

Needs Python 3.9+ and ffmpeg / ffprobe on the PATH. Nothing else.
"""
import argparse
import sys
from pathlib import Path

from clipkit import __version__, media
from clipkit.server import make_server
from clipkit.tapes import Tape, discover, is_tape

COMMANDS = ("serve", "low", "render")


def cmd_serve(a):
    folder = Path(a.folder).expanduser().resolve()
    if not folder.is_dir():
        sys.exit(f"not a folder: {folder}")
    tapes = discover(folder)
    for t in tapes.values():
        print(f"  {t.path.name:40s} {'low copy ready' if t.low.exists() else 'no low copy yet'}", flush=True)
    if not tapes:
        print(f"  no video files in {folder} yet", flush=True)
    srv = make_server(folder, a.bind, a.port)
    shown = "127.0.0.1" if a.bind in ("", "0.0.0.0") else a.bind
    print(f"lowband-clipper {__version__}: http://{shown}:{srv.server_address[1]}/", flush=True)
    if a.bind not in ("127.0.0.1", "localhost", "::1"):
        print("  open to other computers. There is no login, so only do this on a network you trust.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print()


def cmd_low(a):
    for name in a.files:
        f = Path(name).expanduser().resolve()
        if not is_tape(f):
            sys.exit(f"not a tape: {f}")
        media.make_low(Tape(f, f.stem))


def cmd_render(a):
    tapes = discover(Path(a.folder).expanduser().resolve())
    tape = tapes.get(a.tape) or next((t for t in tapes.values() if t.path.name == a.tape), None)
    if tape is None:
        sys.exit(f"no tape '{a.tape}' in {a.folder} (have: {', '.join(tapes) or 'none'})")
    cl = tape.store.load()
    if not cl["clips"]:
        sys.exit(f"{tape.name} has no clips marked yet")
    only = {int(x) for x in a.only.split(",") if x.strip()} if a.only else None
    try:
        media.render(tape, cl, media.transcript(tape), only=only, dry=a.dry)
    except RuntimeError as e:
        sys.exit(str(e))
    if not a.dry:
        print(f"clips are in {tape.clips_dir}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv.insert(0, "serve")
    ap = argparse.ArgumentParser(prog="clipper.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", action="version", version="lowband-clipper " + __version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="serve a folder of tapes (the default)")
    s.add_argument("folder")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--bind", default="127.0.0.1", help="address to listen on (0.0.0.0 = your whole network)")
    s.set_defaults(fn=cmd_serve)

    lo = sub.add_parser("low", help="make the small viewing copy for a tape")
    lo.add_argument("files", nargs="+")
    lo.set_defaults(fn=cmd_low)

    r = sub.add_parser("render", help="cut the marked clips without the browser")
    r.add_argument("folder")
    r.add_argument("tape")
    r.add_argument("--only", default="", help="comma-separated clip numbers")
    r.add_argument("--dry", action="store_true", help="print the ffmpeg lines, render nothing")
    r.set_defaults(fn=cmd_render)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
