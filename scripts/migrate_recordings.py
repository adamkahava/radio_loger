"""Offline, explicit legacy flat-layout migration; default is dry-run."""
import argparse
from datetime import datetime
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "recorder"))
if Path("/app/worker.py").is_file():
    sys.path.insert(0, "/app")
from worker import publish, STATIONS, TZ


def main():
    parser = argparse.ArgumentParser(description="STOP old recorders before --apply. No files are overwritten.")
    parser.add_argument("root", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = args.root.absolute()
    if root.is_symlink() or not root.is_dir():
        raise SystemExit("Invalid recording root")
    failures = 0
    for station in sorted(STATIONS):
        base = root / station
        if not base.is_dir() or base.is_symlink():
            continue
        for path in base.glob("*.mp3"):
            if path.is_symlink() or not path.is_file():
                continue
            if not re.fullmatch(re.escape(station) + r"_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.mp3", path.name):
                print("SKIP malformed:", path.name)
                continue
            try:
                start = datetime.strptime(path.name[len(station) + 1:-4], "%Y-%m-%d_%H-%M-%S").replace(tzinfo=TZ)
                print("MOVE" if args.apply else "WOULD MOVE", path, "->", base / start.strftime("%Y/%m/%d") / path.name)
                if args.apply:
                    publish(base, path, recovered=True)
            except Exception as error:
                failures += 1
                print("PRESERVED:", path.name, type(error).__name__, file=sys.stderr)
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
