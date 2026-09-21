import importlib
import json
import os
from pathlib import Path
import sys
from datetime import datetime

import pytest
from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "archive"))
os.environ["RADIO_FACTORY_ONLY"] = "1"
from app import create_app
from catalog import TZ


@pytest.fixture
def application(tmp_path):
    root = tmp_path / "recordings"
    root.mkdir()
    return create_app({"TESTING": True, "SECRET_KEY": "test-secret-" * 4,
                       "ADMIN_USERNAME": "operator", "ADMIN_PASSWORD_HASH": generate_password_hash("test password long"),
                       "RECORDINGS_ROOT": str(root), "STATE_DIR": str(tmp_path / "state"), "SESSION_COOKIE_SECURE": False})


def login(client):
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf"]
    return client.post("/login", data={"username": "operator", "password": "test password long", "csrf_token": csrf})


def recording(application, time="10-30-00", duration=600, day="2026-09-21"):
    root = Path(application.config["RECORDINGS_ROOT"])
    directory = root / "radio-one" / day.replace("-", "/")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"radio-one_{day}_{time}.mp3"
    path.write_bytes(b"ID3" + bytes(range(256)) * 128)
    path.with_suffix(".json").write_text(json.dumps({"duration": duration}))
    return path


def test_auth_csrf_logout_and_protected_routes(application):
    client = application.test_client()
    assert client.get("/").status_code == 302
    for path in ("/api/status", "/api/stations", "/api/recordings", "/audio/radio-one/a.mp3", "/download/radio-one/a.mp3"):
        assert client.get(path).status_code == 401
    assert client.get("/healthz").json["status"] == "ok"
    assert client.post("/login", data={"username": "operator"}).status_code == 400
    assert login(client).status_code == 302
    assert client.get("/").status_code == 200
    assert client.post("/logout").status_code == 400
    with client.session_transaction() as session:
        csrf = session["csrf"]
    assert client.post("/logout", data={"csrf_token": csrf}).status_code == 302
    assert client.get("/api/status").status_code == 401


def test_go_to_time_range_and_download(application):
    path = recording(application)
    client = application.test_client()
    login(client)
    result = client.get("/api/resolve?station=radio-one&date=2026-09-21&time=10:34")
    assert result.status_code == 200
    assert result.json["offset"] == 240
    assert result.json["recording"]["start"] == "2026-09-21T10:30:00+03:00"
    audio = client.get(result.json["recording"]["audio_url"], headers={"Range": "bytes=100-199"})
    assert audio.status_code == 206
    assert audio.headers["Content-Range"] == f"bytes 100-199/{path.stat().st_size}"
    assert audio.data == path.read_bytes()[100:200]
    audio.close()
    download = client.get(result.json["recording"]["download_url"])
    assert "attachment" in download.headers["Content-Disposition"]
    assert download.data == path.read_bytes()
    download.close()


def test_partial_and_missing_intervals(application):
    recording(application, "10-34-00", 360)
    client = application.test_client()
    login(client)
    assert client.get("/api/resolve?station=radio-one&date=2026-09-21&time=10:33").status_code == 404
    assert client.get("/api/resolve?station=radio-one&date=2026-09-21&time=10:35").json["offset"] == 60
    catalog = application.extensions["catalog"]
    day = datetime(2026, 9, 21, tzinfo=TZ)
    summary = catalog.summary("radio-one", day.date(), now=day.replace(hour=10, minute=45))
    assert summary["expected"] == 64  # through 10:30; unfinished 10:40 excluded
    assert summary["recorded"] == 1
    assert "10:30–10:40" in summary["partial"]
    assert "10:40–10:50" not in summary["missing"]


@pytest.mark.parametrize("path", [
    "/api/recordings?station=../&date=2026-09-21", "/api/recordings?date=2026-02-30",
    "/api/recordings?date=21-09-2026", "/api/resolve?time=25:00", "/api/resolve?time=bad",
    "/audio/radio-one/secret.txt", "/audio/radio-one/capital-radio_2026-09-21_10-00-00.mp3",
    "/audio/radio-one/%2e%2e%2f.env", "/audio/radio-one/..%5c.env", "/api/adjacent?direction=bad",
])
def test_invalid_requests(application, path):
    client = application.test_client()
    login(client)
    assert client.get(path).status_code in (400, 404)


def test_adjacent_cross_midnight(application):
    first = recording(application, "23-50-00")
    second = recording(application, "00-00-00", day="2026-09-22")
    catalog = application.extensions["catalog"]
    assert catalog.adjacent("radio-one", first.name, 1)["filename"] == second.name
    assert catalog.adjacent("radio-one", second.name, -1)["filename"] == first.name


def test_status_requires_real_activity(application):
    import time
    catalog = application.extensions["catalog"]
    assert all(station["status"] == "Offline" for station in catalog.status()["stations"])
    base = catalog.root / "radio-one"
    (base / ".work" / "run").mkdir(parents=True)
    active = base / ".work" / "run" / "active.mp3"
    active.write_bytes(b"audio" * 10000)
    now = time.time()
    report = {"running": True, "last_activity": now, "heartbeat": now, "last_valid_audio": now,
              "active_path": ".work/run/active.mp3", "active_file": "active.mp3"}
    status = base / ".status.json"
    status.write_text(json.dumps(report))
    assert catalog.status()["stations"][0]["status"] == "Recording"
    active.unlink()
    assert catalog.status()["stations"][0]["status"] == "Warning"
    report["last_activity"] -= 1000
    status.write_text(json.dumps(report))
    assert catalog.status()["stations"][0]["status"] == "Offline"


def test_rate_limit_shared_between_clients(application):
    for i in range(11):
        client = application.test_client()
        client.get("/login")
        with client.session_transaction() as session:
            csrf = session["csrf"]
        result = client.post("/login", data={"username": "wrong", "password": "wrong", "csrf_token": csrf})
        assert result.status_code == (429 if i == 10 else 401)


def test_symlink_media_refused(application, tmp_path):
    path = recording(application)
    path.unlink()
    outside = tmp_path / "secret.mp3"
    outside.write_bytes(b"secret" * 10000)
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("Host does not permit symlinks")
    client = application.test_client()
    login(client)
    assert client.get(f"/audio/radio-one/{path.name}").status_code == 400
    assert application.extensions["catalog"].recordings("radio-one", datetime(2026, 9, 21).date()) == []


def test_unknown_duration_and_malformed_names(application):
    path = recording(application)
    path.with_suffix(".json").unlink()
    (path.parent / "radio-one_2026-09-21_99-99-99.mp3").write_bytes(b"x" * 4096)
    rows = application.extensions["catalog"].recordings("radio-one", datetime(2026, 9, 21).date())
    assert len(rows) == 1 and rows[0]["duration"] is None


def test_secure_cookie_and_headers(application):
    application.config["SESSION_COOKIE_SECURE"] = True
    response = application.test_client().get("/login", base_url="https://localhost")
    cookie = response.headers["Set-Cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_requires_credentials(tmp_path):
    with pytest.raises(RuntimeError):
        create_app({"SECRET_KEY": "", "ADMIN_PASSWORD_HASH": ""})


def test_operator_cannot_access_admin_and_download_policy(tmp_path):
    root = tmp_path / "recordings"
    root.mkdir()
    app = create_app({"TESTING": True, "SECRET_KEY": "roles-test-" * 4, "ADMIN_USERNAME": "administrator",
                      "ADMIN_PASSWORD_HASH": generate_password_hash("admin test password"),
                      "OPERATOR_USERNAME": "operator", "OPERATOR_PASSWORD_HASH": generate_password_hash("test password long"),
                      "OPERATOR_DOWNLOADS": False, "RECORDINGS_ROOT": str(root),
                      "STATE_DIR": str(tmp_path / "state"), "SESSION_COOKIE_SECURE": False})
    path = recording(app)
    client = app.test_client()
    login(client)
    assert client.get("/").status_code == 200
    assert client.get("/admin").status_code == 403
    assert client.get("/api/admin/status").status_code == 403
    assert client.get("/api/health").status_code == 403
    status = client.get("/api/status").json
    assert "storage" not in status and "latest_file" not in status["stations"][0]
    assert client.get(f"/download/radio-one/{path.name}").status_code == 403
    audio = client.get(f"/audio/radio-one/{path.name}", headers={"Range": "bytes=0-99"})
    assert audio.status_code == 206
    audio.close()
    # Session fingerprints must not become role escalation tokens.
    with client.session_transaction() as session:
        session["role"] = "admin"
    assert client.get("/api/admin/status").status_code == 403


def test_nearest_recordings_and_missing_whole_day(application):
    first = recording(application, "10-20-00")
    second = recording(application, "10-40-00")
    client = application.test_client()
    login(client)
    response = client.get("/api/resolve?station=radio-one&date=2026-09-21&time=10:35")
    assert response.status_code == 404
    assert [row["filename"] for row in response.json["nearest"]] == [first.name, second.name]
    recording(application, "09-00-00", day="2026-09-23")
    response = client.get("/api/resolve?station=radio-one&date=2026-09-22&time=10:35")
    assert [row["date"] for row in response.json["nearest"]] == ["2026-09-21", "2026-09-23"]


def test_range_overlap_offset_end_and_gaps(application):
    for clock in ("10-30-00", "10-40-00", "10-50-00", "11-00-00", "11-10-00", "11-20-00"):
        recording(application, clock)
    client = application.test_client()
    login(client)
    response = client.get("/api/range?station=radio-one&date=2026-09-21&start=10:35&end=11:15")
    assert response.status_code == 200
    assert len(response.json["recordings"]) == 5
    assert response.json["offset"] == 300
    assert not response.json["gaps"]
    assert response.json["end"] - response.json["start"] == 2400
    gap = client.get("/api/range?date=2026-09-21&start=10:20&end=10:40").json
    assert len(gap["gaps"]) == 1
    assert gap["gaps"][0]["end"] - gap["gaps"][0]["start"] == 600
    for query in ("start=11:00&end=10:00", "start=10:00&end=10:00", "start=bad&end=11:00"):
        assert client.get("/api/range?date=2026-09-21&" + query).status_code == 400


def test_bookmarks_auth_return_invalid_and_no_autoplay(application):
    client = application.test_client()
    target = "/archive/radio-one/2026-09-21?time=10:35"
    response = client.get(target)
    assert response.status_code == 302 and "next=" in response.location
    client.get(response.location)
    with client.session_transaction() as session:
        csrf = session["csrf"]
    response = client.post(response.location, data={"username": "operator", "password": "test password long", "csrf_token": csrf})
    assert response.location == target
    assert client.get(target).status_code == 200
    for url in ("/archive/unknown/2026-09-21?time=10:35", "/archive/radio-one/2026-02-30", "/archive/radio-one/2026-09-21?time=25:35"):
        response = client.get(url)
        assert response.status_code == 400
        assert b"Choose a station, day and time below" in response.data
        assert b'id="search"' in response.data


def test_admin_diagnostics_redact_url_secrets(application):
    application.config["STREAM_URLS"]["radio-one"] = "https://user:password@example.com/stream?token=secret"
    client = application.test_client()
    login(client)
    assert client.get("/admin").status_code == 200
    response = client.get("/api/admin/status")
    assert response.status_code == 200
    assert "storage" in response.json
    assert response.json["system"]["retention_days"] == 153
    assert response.json["system"]["stream_urls"]["radio-one"] == "https://example.com/stream"
    assert b"password" not in response.data and b"token=" not in response.data


def test_operator_status_all_stations_and_old_audio(application):
    import time
    catalog = application.extensions["catalog"]
    now = time.time()
    for station, age in (("radio-one", 0), ("capital-radio", 120), ("east-africa-radio", 1000)):
        base = catalog.root / station
        (base / ".work" / "run").mkdir(parents=True)
        (base / ".work" / "run" / "active.mp3").write_bytes(b"audio" * 10000)
        (base / ".status.json").write_text(json.dumps({"running": True, "heartbeat": now,
            "last_activity": now - age, "last_valid_audio": now - age, "active_path": ".work/run/active.mp3"}))
    client = application.test_client()
    login(client)
    statuses = client.get("/api/status").json["stations"]
    assert [row["status"] for row in statuses] == ["Recording", "Warning", "Offline"]
    assert all(row["last_successful"].endswith("+03:00") for row in statuses)
