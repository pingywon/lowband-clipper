#!/usr/bin/env python3
"""Build the project page (GitHub Pages serves docs/ as it is).

    python3 tools/build_site.py           write docs/
    python3 tools/build_site.py --check   write nothing; fail if docs/ is out of date

The live demo is the real editor from web/, with the server swapped for web/backend-demo.js.
The version shown on every page, and in README.md, comes from the VERSION file and nowhere else.

Not built here, on purpose: docs/media/, docs/img/ and docs/demo/state.json. Those come from
tools/make_demo.py and tools/shots.py and are committed as they are.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB, SITE, DOCS = ROOT / "web", ROOT / "site", ROOT / "docs"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
DEMO_BACKEND = ('<script src="model.js?v=__VERSION__"></script>\n'
                '<script src="backend-demo.js?v=__VERSION__"></script>')


def text(path, backend=""):
    return (path.read_text(encoding="utf-8").replace("__BACKEND__", backend)
            .replace("__VERSION__", VERSION).encode("utf-8"))


def plan():
    """{path under docs/: bytes}"""
    out = {".nojekyll": b"",
           "index.html": text(SITE / "index.html"),
           "site.css": text(SITE / "site.css"),
           "site.js": text(SITE / "site.js"),
           "favicon.svg": (WEB / "favicon.svg").read_bytes(),
           "demo/index.html": text(WEB / "editor.html", DEMO_BACKEND)}
    for name in ("editor.css", "editor.js", "model.js", "backend-demo.js", "favicon.svg"):
        out["demo/" + name] = (WEB / name).read_bytes()
    return out


def readme():
    """README.md with the release number between its two markers brought up to date."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    return re.sub(r"(<!-- version -->).*?(<!-- /version -->)", r"\1**v%s**\2" % VERSION, text)


def main():
    check = "--check" in sys.argv[1:]
    stale = []
    if (ROOT / "README.md").read_text(encoding="utf-8") != readme():
        stale.append("../README.md")
        if not check:
            (ROOT / "README.md").write_text(readme(), encoding="utf-8")
    for rel, data in plan().items():
        dst = DOCS / rel
        if dst.exists() and dst.read_bytes() == data:
            continue
        stale.append(rel)
        if not check:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
    missing = [p for p in ("media/demo-v1.low.mp4", "img/poster.jpg", "demo/state.json") if not (DOCS / p).exists()]
    if check and stale:
        sys.exit("docs/ is out of date (run tools/build_site.py): " + ", ".join(stale))
    if missing:
        sys.exit("missing from docs/ (run tools/make_demo.py): " + ", ".join(missing))
    print(f"docs/ {'is up to date' if check else 'built'} for v{VERSION}" + ("" if check else f" ({len(stale)} files changed)"))


if __name__ == "__main__":
    main()
