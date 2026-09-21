from datetime import datetime, timedelta
import os
from pathlib import Path
import sys
import json
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "recorder"))
import worker
from retention import cleanup, TZ


def test_retention_only_closed_old_recordings(tmp_path):
    root = tmp_path / "recordings"
    folder = root / "radio-one" / "2025" / "01" / "01"
    folder.mkdir(parents=True)
    old = folder / "radio-one_2025-01-01_00-00-00.mp3"
    old.write_bytes(b"old recording")
    old.with_suffix(".json").write_text('{}')
    recent_restore = folder / "radio-one_2025-01-01_00-10-00.mp3"
    recent_restore.write_bytes(b"recent restore")
    old_time = datetime(2025, 1, 1, tzinfo=TZ).timestamp()
    os.utime(old, (old_time, old_time))
    staging = root / "radio-one" / ".work" / "run"
    staging.mkdir(parents=True)
    active = staging / old.name
    active.write_bytes(b"active")
    os.utime(active, (old_time, old_time))
    unrelated = folder / "unrelated.mp3"
    unrelated.write_bytes(b"do not delete")
    now = datetime.now(TZ)
    assert cleanup(root, 153, dry_run=True, now=now) == 1
    assert old.exists()
    assert cleanup(root, 153, now=now) == 1
    assert not old.exists() and not old.with_suffix('.json').exists()
    assert active.exists() and recent_restore.exists() and unrelated.exists()


def test_retention_symlinks_untouched(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    target = outside / "radio-one_2025-01-01_00-00-00.mp3"
    target.write_bytes(b"protected")
    try:
        (root / "radio-one").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not permit symlinks")
    assert cleanup(root, 1) == 0
    assert target.exists()


def test_clock_segmentation_and_codec_options(monkeypatch, tmp_path):
    monkeypatch.setenv("STATION", "radio-one")
    cmd = worker.command("https://example.test/stream", tmp_path, "transcode")
    assert cmd[cmd.index("-segment_time") + 1] == "600"
    assert cmd[cmd.index("-segment_atclocktime") + 1] == "1"
    assert cmd[cmd.index("-reservoir") + 1] == "0"
    assert "-reconnect_on_network_error" in cmd
    assert "copy" in worker.command("https://example.test/stream", tmp_path, "copy")


def test_publish_date_metadata_collision_and_recovery(tmp_path, monkeypatch):
    base = tmp_path / "radio-one"
    staging = base / ".work" / "run"
    staging.mkdir(parents=True)
    source = staging / "radio-one_2026-09-21_10-34-00.mp3"
    source.write_bytes(b"validated audio")
    monkeypatch.setattr(worker, "probe", lambda p: 360.0)
    name = worker.publish(base, source, recovered=True)
    destination = base / name
    assert destination.relative_to(base).as_posix() == "2026/09/21/" + source.name
    assert json.loads(destination.with_suffix('.json').read_text())["recovered"]
    source.write_bytes(b"another recording")
    with pytest.raises(FileExistsError):
        worker.publish(base, source)
    assert source.exists() and destination.read_bytes() == b"validated audio"


def test_health_requires_process_growth_and_validation(tmp_path, monkeypatch):
    now = time.time()
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    status = {"supervisor_pid": 1, "ffmpeg_pid": 101, "running": True, "heartbeat": now,
              "last_activity": now, "last_valid_audio": now}
    path = tmp_path / ".status.json"
    path.write_text(json.dumps(status))
    assert worker.health(tmp_path)
    status["last_valid_audio"] -= 1000
    path.write_text(json.dumps(status))
    assert not worker.health(tmp_path)
