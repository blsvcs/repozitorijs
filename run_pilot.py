from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DB_PATH = Path("pilot/pilot.sqlite")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ensure pilot.sqlite exists, then run the Streamlit pilot.")
    parser.add_argument("--update", action="store_true", help="Download the latest pilot database before starting.")
    parser.add_argument("--no-browser", action="store_true", help="Ask Streamlit not to open a browser automatically.")
    return parser.parse_args()


def ensure_database(update: bool) -> int:
    if DB_PATH.exists() and not update:
        return 0

    args = [sys.executable, "scripts/download_pilot_artifact.py"]
    if update:
        args.append("--force")

    result = subprocess.run(args, check=False)
    return result.returncode


def main() -> int:
    args = parse_args()
    if ensure_database(args.update) != 0:
        return 1

    streamlit_args = [sys.executable, "-m", "streamlit", "run", "streamlit_app.py"]
    if args.no_browser:
        streamlit_args.append("--server.headless=true")
    return subprocess.call(streamlit_args)


if __name__ == "__main__":
    raise SystemExit(main())
