from __future__ import annotations

import subprocess
import sys
from pathlib import Path


DB_PATH = Path("pilot/pilot.sqlite")


def ensure_database() -> None:
    if DB_PATH.exists():
        return
    result = subprocess.run(
        [sys.executable, "scripts/download_pilot_artifact.py", "--source", "release", "--force"],
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Neizdevās lejupielādēt pilotdatubāzi: {details}")


ensure_database()

import streamlit_app  # noqa: E402,F401
