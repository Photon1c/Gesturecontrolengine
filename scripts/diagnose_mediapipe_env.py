"""Print whether this Windows environment is suitable for MediaPipe Tasks.

Run from the repo root (any Python):
  python scripts/diagnose_mediapipe_env.py

MediaPipe Tasks needs a venv created from python.org CPython, not Anaconda.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    print("Python executable:", sys.executable)
    print("sys.prefix:        ", sys.prefix)
    print("sys.version:       ", sys.version.split()[0])

    cfg = Path(sys.prefix) / "pyvenv.cfg"
    if cfg.is_file():
        print("\npyvenv.cfg:")
        raw = cfg.read_text(encoding="utf-8", errors="replace")
        print(raw.rstrip())
        bad = False
        for line in raw.splitlines():
            ls = line.strip().lower()
            if ls.startswith("home ="):
                home = ls.split("=", 1)[1].strip()
                if any(x in home for x in ("conda", "anaconda", "miniconda")):
                    bad = True
        print(
            "\nVerdict:",
            "NOT OK — venv base (home=) points at Conda. Recreate .venv with python.org Python."
            if bad
            else "pyvenv home does not mention Conda (good sign).",
        )
    else:
        print("\n(No pyvenv.cfg — not a standard venv here.)")
        if any(x in sys.executable.lower() for x in ("conda", "anaconda", "miniconda")):
            print("Verdict: NOT OK — interpreter path looks like Conda.")

    print(
        "\nNext: py -0p\n"
        "Pick the install under ...\\AppData\\Local\\Programs\\Python\\... then:\n"
        r'  & "C:\Users\YOU\AppData\Local\Programs\Python\Python311\python.exe" -m venv .venv'
    )


if __name__ == "__main__":
    main()
