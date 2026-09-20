"""Put `backend` on the import path.

The tests in this suite write `from app.muse import …` directly. Other suites do a
`sys.path.insert` at the top of each file; these do not, so as they stand all
eleven fail at collection (`ModuleNotFoundError: app`).

**Rather than touching the author's files, one entrance is placed here.**
"""
import sys
from pathlib import Path

BACKEND = str(Path(__file__).resolve().parents[2] / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
