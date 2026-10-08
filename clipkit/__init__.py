"""lowband-clipper: cut clips out of long tapes over a thin connection."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
__version__ = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
