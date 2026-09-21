"""Delete only old, finalized, named recordings. Never descend into staging or symlinks."""
import argparse
from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path
import re
import stat
import time
from zoneinfo import ZoneInfo

STATIONS = ("radio-one", "capital-radio", "east-africa-radio")
TZ = ZoneInfo("Africa/Dar_es_Salaam")
LOG = logging.getLogger("retention")


def walk_station(base):
    """Linux deletion is relative to pinned directory descriptors, including through renames."""
    if hasattr(os, "fwalk"):
        descriptor = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for directory, dirs, files, fd in os.fwalk(".", follow_symlinks=False, dir_fd=descriptor):
                yield base / directory, dirs, files, fd
        finally:
            os.close(descriptor)
    else:
        # Portable test/preview fallback. Production uses Linux fwalk and dir_fd operations.
        for directory, dirs, files in os.walk(base, followlinks=False):
            yield Path(directory), dirs, files, None


def cleanup(root, days, dry_run=False, now=None):
    if days < 1 or root.is_symlink() or not root.is_dir():
        raise ValueError("invalid retention root or period")
    cutoff = (now or datetime.now(TZ)) - timedelta(days=days)
    count = 0
    for station in STATIONS:
        base = root / station
        if base.is_symlink() or not base.is_dir():
            continue
        # os.walk does not follow directory symlinks. Prune everything except YYYY/MM/DD.
        for directory, dirs, files, fd in walk_station(base):
            folder = Path(directory)
            depth = len(folder.relative_to(base).parts)
            dirs[:] = [d for d in dirs if depth < 3 and re.fullmatch(r"\d{4}" if depth == 0 else r"\d{2}", d)
                       and not (folder / d).is_symlink()]
            if depth != 3:
                continue
            for name in files:
                match = re.fullmatch(re.escape(station) + r"_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})\.mp3", name)
                path = folder / name
                if not match or path.is_symlink() or not path.is_file():
                    continue
                try:
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False) if fd is not None else path.lstat()
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    start = datetime.strptime(" ".join(match.groups()), "%Y-%m-%d %H-%M-%S").replace(tzinfo=TZ)
                    if folder.relative_to(base).as_posix() != start.strftime("%Y/%m/%d"):
                        continue
                    # Both filename time and mtime must be old. Recent restores are protected.
                    if start >= cutoff or info.st_mtime >= cutoff.timestamp():
                        continue
                    if root.resolve() not in path.resolve().parents:
                        raise ValueError("escaped storage root")
                    LOG.info("event=%s file=%s", "would_delete" if dry_run else "delete", path.relative_to(root))
                    if not dry_run:
                        if fd is not None:
                            os.unlink(name, dir_fd=fd)
                        else:
                            path.unlink()
                        meta = path.with_suffix(".json")
                        try:
                            meta_info = os.stat(meta.name, dir_fd=fd, follow_symlinks=False) if fd is not None else meta.lstat()
                            if stat.S_ISREG(meta_info.st_mode):
                                if fd is not None:
                                    os.unlink(meta.name, dir_fd=fd)
                                else:
                                    meta.unlink()
                        except FileNotFoundError:
                            pass
                    count += 1
                except (ValueError, FileNotFoundError):
                    continue
    LOG.info("event=cleanup_complete count=%s dry_run=%s", count, dry_run)
    return count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()
    marker = Path("/tmp/retention-health.json")
    if args.health:
        try:
            return 0 if time.time() - json.loads(marker.read_text())["success"] < 90000 else 1
        except (OSError, ValueError, KeyError):
            return 1
    os.environ["TZ"] = "Africa/Dar_es_Salaam"
    if hasattr(time, "tzset"):
        time.tzset()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s service=retention %(message)s")
    while True:
        try:
            cleanup(Path(os.getenv("RECORDINGS_ROOT", "/recordings")), int(os.getenv("RETENTION_DAYS", "153")), args.dry_run)
            marker.write_text(json.dumps({"success": time.time()}))
        except Exception:
            LOG.exception("event=cleanup_failed")
            if args.once or args.dry_run:
                return 1
        if args.once or args.dry_run:
            return 0
        time.sleep(43200)


if __name__ == "__main__":
    raise SystemExit(main())
