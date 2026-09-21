"""Bounded daily filesystem catalog. MP3s are authoritative; metadata is optional."""
from datetime import date, datetime, timedelta
import json
import math
import os
from pathlib import Path
import re
import shutil
import time
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Africa/Dar_es_Salaam")
STATIONS = {"radio-one": "Radio One", "capital-radio": "Capital Radio", "east-africa-radio": "East Africa Radio"}
FILE_RE = re.compile(r"^(radio-one|capital-radio|east-africa-radio)_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})\.mp3$")


def parse_day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Use a date in YYYY-MM-DD format.")
    return date.fromisoformat(value)


class Catalog:
    def __init__(self, root):
        self.root = Path(root).absolute()

    def safe(self, *parts):
        path = self.root
        if path.is_symlink():
            raise ValueError("Storage symlinks are not supported.")
        for part in parts:
            for component in Path(part).parts:
                if component in ("..", ".") or Path(component).is_absolute():
                    raise ValueError("Invalid recording path.")
                path /= component
                if path.is_symlink():
                    raise ValueError("Storage symlinks are not supported.")
        return path

    def folder(self, station, day):
        if station not in STATIONS:
            raise ValueError("Unknown station.")
        return self.safe(station, day.strftime("%Y/%m/%d"))

    def path(self, station, filename):
        match = FILE_RE.fullmatch(filename)
        if not match or match[1] != station:
            raise ValueError("Invalid recording name.")
        day = parse_day(match[2])
        datetime.strptime(match[3], "%H-%M-%S")
        return self.safe(station, day.strftime("%Y/%m/%d"), filename)

    def recordings(self, station, day):
        folder = self.folder(station, day)
        if not self.root.is_dir():
            raise OSError("Recording storage is unavailable.")
        if not folder.exists():
            return []
        results = []
        for path in folder.iterdir():
            match = FILE_RE.fullmatch(path.name)
            if path.is_symlink() or not match or match[1] != station or match[2] != day.isoformat() or not path.is_file():
                continue
            try:
                start = datetime.strptime(match[2] + " " + match[3], "%Y-%m-%d %H-%M-%S").replace(tzinfo=TZ)
                size = path.stat().st_size
                if size < 1024:
                    continue
                duration = None
                recovered = False
                meta = path.with_suffix(".json")
                if meta.is_file() and not meta.is_symlink() and meta.stat().st_size < 16384:
                    try:
                        data = json.loads(meta.read_text())
                        candidate = float(data["duration"])
                        if math.isfinite(candidate) and 0 < candidate < 86400:
                            duration = candidate
                            recovered = bool(data.get("recovered"))
                    except (ValueError, KeyError, TypeError):
                        pass
                boundary = (int(start.timestamp()) // 600 + 1) * 600
                # Without metadata, do not invent known audio coverage or duration.
                end = min(start.timestamp() + duration, boundary + 2) if duration else boundary
                results.append({"filename": path.name, "station": station, "station_name": STATIONS[station],
                                "date": day.isoformat(), "start": start.isoformat(), "timestamp": start.timestamp(),
                                "end_timestamp": end, "time": start.strftime("%H:%M:%S"),
                                "end": datetime.fromtimestamp(end, TZ).strftime("%H:%M:%S"),
                                "duration": duration, "size": size, "recovered": recovered,
                                "audio_url": f"/audio/{station}/{path.name}",
                                "download_url": f"/download/{station}/{path.name}"})
            except (ValueError, FileNotFoundError):
                continue
        return sorted(results, key=lambda item: item["timestamp"])

    def summary(self, station, day, now=None, recordings=None):
        now = now or datetime.now(TZ)
        start = datetime.combine(day, datetime.min.time(), TZ)
        expected = max(0, min(144, int((now.timestamp() - start.timestamp()) // 600)))
        recordings = self.recordings(station, day) if recordings is None else recordings
        missing, partial, unknown = [], [], []
        recorded = 0
        for interval in range(expected):
            left = start.timestamp() + interval * 600
            right = left + 600
            label = datetime.fromtimestamp(left, TZ).strftime("%H:%M") + "–" + datetime.fromtimestamp(right, TZ).strftime("%H:%M")
            items = [r for r in recordings if left <= r["timestamp"] < right]
            if not items:
                missing.append(label)
                continue
            recorded += 1
            if any(r["duration"] is None for r in items):
                unknown.append(label)
                continue
            cursor = left
            gap = False
            for item in items:
                if item["timestamp"] > cursor + 2:
                    gap = True
                cursor = max(cursor, item["end_timestamp"])
            if gap or cursor < right - 2 or any(r["recovered"] for r in items):
                partial.append(label)
        return {"expected": expected, "recorded": recorded, "missing": missing, "partial": partial,
                "unverified": unknown, "daily_total": 144}

    def status(self):
        now = time.time()
        today = datetime.now(TZ).date()
        stations = []
        for station, name in STATIONS.items():
            state = {"station": station, "name": name, "status": "Offline", "last_activity": None,
                     "age_seconds": None, "latest_file": None, "error": None}
            try:
                rows = self.recordings(station, today)
                state["today"] = self.summary(station, today, recordings=rows)
                state.update(last_successful=None, last_file_size=rows[-1]["size"] if rows else None)
                status_path = self.safe(station, ".status.json")
                if status_path.exists() and status_path.stat().st_size < 16384:
                    report = json.loads(status_path.read_text())
                    verified = float(report.get("last_valid_audio", 0))
                    state["last_successful"] = datetime.fromtimestamp(verified, TZ).isoformat() if verified > 0 else None
                    activity = float(report.get("last_activity", 0))
                    age = now - activity
                    state.update(last_activity=datetime.fromtimestamp(activity, TZ).isoformat() if activity else None,
                                 age_seconds=max(0, int(age)) if activity else None,
                                 latest_file=report.get("active_file") or report.get("latest_file"))
                    fresh = 0 <= now - float(report.get("heartbeat", 0)) < 60
                    valid = 0 <= now - float(report.get("last_valid_audio", 0)) < 900
                    active = report.get("active_path")
                    active_path = self.safe(station, active) if isinstance(active, str) else None
                    growing = (active_path is not None and active_path.is_file() and active_path.stat().st_size > 8192
                               and 0 <= now - active_path.stat().st_mtime < 90)
                    if active_path is not None and active_path.is_file():
                        state["last_file_size"] = active_path.stat().st_size
                    if report.get("running") and fresh and valid and growing and 0 <= age < 90:
                        state["status"] = "Recording"
                    elif activity and 0 <= age < 900:
                        state["status"] = "Warning"
                if not state["latest_file"] and rows:
                    state["latest_file"] = rows[-1]["filename"]
            except (OSError, ValueError, KeyError, TypeError):
                state["error"] = "Recording storage or recorder status is unavailable."
                state.setdefault("today", {"expected": 0, "recorded": 0, "missing": [], "partial": [], "unverified": []})
            stations.append(state)
        try:
            usage = shutil.disk_usage(self.root)
            percent = round(usage.used / usage.total * 100, 1)
            storage = {"used": usage.used, "free": usage.free, "total": usage.total, "percent": percent,
                       "status": "Critical" if percent >= 90 else "Warning" if percent >= 80 else "Normal"}
        except OSError:
            storage = {"error": "Recording storage is unavailable."}
        return {"stations": stations, "storage": storage, "timezone": str(TZ), "time": datetime.now(TZ).isoformat()}

    def days(self, station):
        if station not in STATIONS:
            raise ValueError("Unknown station.")
        base = self.safe(station)
        days = set()
        if not base.is_dir():
            return []
        for year in base.iterdir():
            if year.is_symlink() or not year.is_dir() or not re.fullmatch(r"\d{4}", year.name):
                continue
            for month in year.iterdir():
                if month.is_symlink() or not month.is_dir() or not re.fullmatch(r"\d{2}", month.name):
                    continue
                for folder in month.iterdir():
                    if folder.is_symlink() or not folder.is_dir():
                        continue
                    try:
                        days.add(parse_day(f"{year.name}-{month.name}-{folder.name}"))
                    except ValueError:
                        pass
        return sorted(days)

    def nearest(self, station, day, target, rows=None):
        rows = self.recordings(station, day) if rows is None else rows
        before = next((row for row in reversed(rows) if row["timestamp"] < target), None)
        after = next((row for row in rows if row["timestamp"] > target), None)
        # Only visit another date if this day has no candidate in that direction.
        if before is None or after is None:
            days = self.days(station)
            for direction in (-1, 1):
                if (before if direction < 0 else after) is not None:
                    continue
                for candidate in sorted(days, reverse=direction < 0):
                    if (candidate - day).days * direction <= 0:
                        continue
                    found = self.recordings(station, candidate)
                    if found:
                        if direction < 0:
                            before = found[-1]
                        else:
                            after = found[0]
                        break
        return [row for row in (before, after) if row is not None]

    def adjacent(self, station, filename, direction):
        current = self.path(station, filename)
        match = FILE_RE.fullmatch(filename)
        day = parse_day(match[2])
        days = set(self.days(station)) | {day}
        for candidate in sorted(days, reverse=direction < 0):
            if (candidate - day).days * direction < 0:
                continue
            rows = self.recordings(station, candidate)
            if direction < 0:
                rows.reverse()
            for row in rows:
                if (row["filename"] > current.name if direction > 0 else row["filename"] < current.name):
                    return row
        return None
