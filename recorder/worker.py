"""Supervise one FFmpeg process; publish only closed, validated MP3 segments."""
import csv
import json
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Africa/Dar_es_Salaam")
STATIONS = {"radio-one", "capital-radio", "east-africa-radio"}
STOP = False
LOG = logging.getLogger("recorder")


def atomic_json(path, value):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".metadata-", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def safe_directory(root, relative):
    current = root
    if root.is_symlink():
        raise ValueError("symlink storage root")
    for part in Path(relative).parts:
        if part in ("..", ".") or Path(part).is_absolute():
            raise ValueError("unsafe path")
        current = current / part
        if current.is_symlink():
            raise ValueError("symlink storage directory")
        current.mkdir(exist_ok=True)
    return current


def probe(path):
    result = subprocess.run(
        [os.getenv("FFPROBE", "ffprobe"), "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name:format=duration", "-of", "json", str(path)],
        capture_output=True, timeout=12, check=True,
    )
    data = json.loads(result.stdout)
    if not data.get("streams") or data["streams"][0]["codec_name"] != "mp3":
        raise ValueError("not MP3 audio")
    duration = float(data["format"]["duration"])
    if not 0 < duration < 86400:
        raise ValueError("invalid audio duration")
    return duration


def command(url, run, mode, seconds=600):
    if mode not in ("transcode", "copy"):
        raise ValueError("AUDIO_MODE must be transcode or copy")
    codec = ["-c:a", "copy"] if mode == "copy" else [
        "-c:a", "libmp3lame", "-b:a", os.getenv("MP3_BITRATE", "128k"),
        "-ar", "44100", "-ac", "2", "-reservoir", "0", "-threads", "1",
    ]
    tls = ["-tls_verify", "1", "-ca_file", "/etc/ssl/certs/ca-certificates.crt"] if url.startswith("https://") else []
    return [
        os.getenv("FFMPEG", "ffmpeg"), "-hide_banner", "-loglevel", "warning", "-nostdin",
        "-rw_timeout", "15000000", "-reconnect", "1", "-reconnect_streamed", "1",
        "-reconnect_at_eof", "1", "-reconnect_on_network_error", "1",
        "-reconnect_on_http_error", "429,5xx", "-reconnect_delay_max", "5",
        "-analyzeduration", "1000000", "-probesize", "32768",
        *tls, "-i", url, "-map", "0:a:0", "-vn", "-map_metadata", "-1",
        *codec, "-f", "segment", "-segment_format", "mp3",
        "-segment_time", str(seconds), "-segment_atclocktime", "1",
        "-reset_timestamps", "1", "-strftime", "1", "-flush_packets", "1",
        "-segment_list", str(run / "closed.csv"), "-segment_list_type", "csv",
        "-segment_list_size", "16",
        str(run / (os.environ["STATION"] + "_%Y-%m-%d_%H-%M-%S.mp3")),
    ]


def publish(base, source, recovered=False):
    station = base.name
    match = re.fullmatch(re.escape(station) + r"_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})\.mp3", source.name)
    if source.is_symlink() or not match:
        raise ValueError("unsafe segment")
    start = datetime.strptime(" ".join(match.groups()), "%Y-%m-%d %H-%M-%S").replace(tzinfo=TZ)
    duration = probe(source)
    folder = safe_directory(base, start.strftime("%Y/%m/%d"))
    destination = folder / source.name
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("refusing to overwrite a recording")
    # Metadata appears first, MP3 is the publication commit. Both are on the same filesystem.
    atomic_json(destination.with_suffix(".json"), {
        "start": start.isoformat(), "duration": duration, "recovered": recovered,
        "codec": "mp3", "mode": os.getenv("AUDIO_MODE", "transcode"),
    })
    os.rename(source, destination)
    LOG.info("event=published file=%s duration=%.3f recovered=%s", destination.name, duration, recovered)
    return str(destination.relative_to(base))


def stop_child(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def health(base):
    try:
        status = json.loads((base / ".status.json").read_text())
        now = time.time()
        for pid in (status["supervisor_pid"], status["ffmpeg_pid"]):
            if not isinstance(pid, int) or pid < 1:
                return False
            os.kill(pid, 0)
        return (status["running"] and 0 <= now - status["heartbeat"] < 60
                and 0 <= now - status["last_activity"] < 900
                and 0 <= now - status["last_valid_audio"] < 900)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def main():
    global STOP
    os.environ["TZ"] = "Africa/Dar_es_Salaam"
    if hasattr(time, "tzset"):
        time.tzset()
    station = os.environ["STATION"]
    if station not in STATIONS:
        raise ValueError("invalid station")
    base = Path(os.getenv("RECORDINGS_ROOT", "/recordings")) / station
    if "--health" in sys.argv:
        okay = health(base)
        print("healthy audio activity" if okay else "no recent verified audio activity")
        return 0 if okay else 1
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s station={station} %(message)s")
    if os.getenv("TZ") != "Africa/Dar_es_Salaam" or int(os.getenv("SEGMENT_SECONDS", "600")) != 600:
        raise ValueError("production segments must be 600 seconds in Africa/Dar_es_Salaam")
    base.mkdir(parents=True, exist_ok=True)
    work = safe_directory(base, ".work")
    # A station must have one writer. flock also guards accidental duplicate containers.
    import fcntl
    lock = (work / "writer.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def shutdown(signum, frame):
        global STOP
        STOP = True

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    # Bound startup recovery so a backlog cannot delay new capture indefinitely.
    recovery_deadline = time.monotonic() + 30
    for old in work.iterdir():
        if STOP or time.monotonic() > recovery_deadline:
            LOG.warning("event=recovery_deferred reason=startup_budget")
            break
        if old.is_symlink() or not old.is_dir():
            continue
        for source in old.glob("*.mp3"):
            if STOP or time.monotonic() > recovery_deadline:
                break
            try:
                publish(base, source, recovered=True)
            except Exception as error:
                LOG.warning("event=recovery_failed file=%s error=%s", source.name, type(error).__name__)
        if not any(old.glob("*.mp3")):
            (old / "closed.csv").unlink(missing_ok=True)
            try:
                old.rmdir()
            except OSError:
                pass
    status = {"station": station, "supervisor_pid": os.getpid(), "ffmpeg_pid": 0,
              "running": False, "last_activity": 0, "last_valid_audio": 0, "latest_file": None}
    while not STOP:
        run = safe_directory(work, uuid.uuid4().hex)
        LOG.info("event=connection_attempt mode=%s", os.getenv("AUDIO_MODE", "transcode"))
        child = subprocess.Popen(command(os.environ["STREAM_URL"], run, os.getenv("AUDIO_MODE", "transcode")))
        status.update(ffmpeg_pid=child.pid, running=True)
        seen = {}
        last_growth = time.monotonic()
        started = time.monotonic()
        last_probe = 0
        handled = set()

        def publish_closed(recovered=False):
            listing = run / "closed.csv"
            if not listing.exists():
                return
            rows = list(csv.reader(listing.read_text().splitlines()))
            handled.intersection_update(row[0] for row in rows if row)
            for row in rows:
                if len(row) != 3 or row[0] in handled:
                    continue
                source = run / Path(row[0]).name
                if not source.exists():
                    # FFmpeg rewrites its bounded CSV. A transient empty read must not
                    # make previously published entries trigger repeated probes/logs.
                    handled.add(row[0])
                    continue
                try:
                    status["latest_file"] = publish(base, source, recovered=recovered)
                    status["last_valid_audio"] = time.time()
                    handled.add(row[0])
                except Exception as error:
                    LOG.warning("event=publish_failed file=%s error=%s", source.name, type(error).__name__)

        try:
            while not STOP and child.poll() is None:
                publish_closed()
                sources = list(run.glob("*.mp3"))
                seen = {source.name: seen.get(source.name, 0) for source in sources}
                for source in sources:
                    size = source.stat().st_size
                    if size > seen.get(source.name, 0):
                        seen[source.name] = size
                        status["last_activity"] = time.time()
                        status["active_file"] = source.name
                        status["active_path"] = str(source.relative_to(base))
                        last_growth = time.monotonic()
                        if size > 8192 and time.monotonic() - last_probe > 30:
                            try:
                                probe(source)
                                status["last_valid_audio"] = time.time()
                            except (OSError, ValueError, subprocess.SubprocessError, KeyError):
                                pass
                            last_probe = time.monotonic()
                status["heartbeat"] = time.time()
                atomic_json(base / ".status.json", status)
                # Docker health does not restart unhealthy containers. This watchdog does.
                if time.monotonic() - last_growth > 60:
                    LOG.error("event=connection_failure reason=no_audio_growth restart=true")
                    break
                if time.time() - status["last_valid_audio"] > 900 and time.monotonic() - started > 120:
                    LOG.error("event=connection_failure reason=invalid_audio restart=true")
                    break
                time.sleep(2)
        finally:
            stop_child(child)
            publish_closed(recovered=True)
            # A killed FFmpeg may not have appended its final CSV row.
            for source in run.glob("*.mp3"):
                try:
                    status["latest_file"] = publish(base, source, recovered=True)
                except Exception as error:
                    LOG.warning("event=partial_preserved file=%s error=%s", source.name, type(error).__name__)
            status.update(running=False, heartbeat=time.time())
            atomic_json(base / ".status.json", status)
            LOG.warning("event=ffmpeg_exit code=%s restart=%s", child.returncode, not STOP)
            if not any(run.glob("*.mp3")):
                (run / "closed.csv").unlink(missing_ok=True)
                run.rmdir()
        for _ in range(5):
            if STOP:
                break
            time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
