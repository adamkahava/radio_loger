"""Operator/admin browser acceptance using real synthetic MP3s and disposable data."""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import imageio_ffmpeg
from playwright.sync_api import sync_playwright
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server

os.environ["RADIO_FACTORY_ONLY"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "archive"))
from app import create_app
from catalog import TZ, STATIONS


def wait(page, expression):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if page.evaluate("() => " + expression):
            return
        time.sleep(0.1)
    raise AssertionError("Browser condition timed out: " + expression)


def choose(page, day, hour="10", minute="35", station="radio-one"):
    page.locator("#finder").evaluate("el => {el.open = true}")
    page.locator(f'input[name="station"][value="{station}"]').check()
    page.locator("#custom-date").evaluate("el => {el.hidden = false}")
    page.fill("#date", day)
    page.select_option("#hour", hour)
    page.select_option("#minute", minute)


def find(page, fragment, offset=None):
    page.click("#go")
    wait(page, f"document.getElementById('audio').src.includes('{fragment}') && document.getElementById('audio').readyState >= 1")
    if offset is not None:
        wait(page, f"document.getElementById('audio').currentTime >= {offset} && document.getElementById('audio').currentTime < {offset + 5}")


def finish_file(page, next_fragment):
    page.evaluate("() => {const a=document.getElementById('audio'); a.currentTime=a.duration-0.15; a.play();}")
    wait(page, f"document.getElementById('audio').src.includes('{next_fragment}') && document.getElementById('audio').readyState >= 1")


def main():
    output = Path("test-results/operator-browser")
    output.mkdir(parents=True, exist_ok=True)
    today = datetime.now(TZ).date()
    yesterday = today - timedelta(days=1)
    custom = today - timedelta(days=3)
    checks = []
    with tempfile.TemporaryDirectory(prefix="radio-operator-") as temporary:
        root = Path(temporary)
        sample = root / "sample.mp3"
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                        "-t", "600", "-c:a", "libmp3lame", "-reservoir", "0", "-b:a", "64k", str(sample)], check=True)
        def recording(station, day, clock):
            directory = root / "recordings" / station / day.strftime("%Y/%m/%d")
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{station}_{day}_{clock}.mp3"
            try:
                os.link(sample, path)
            except OSError:
                shutil.copyfile(sample, path)
            path.with_suffix(".json").write_text(json.dumps({"duration": 600}))
        for clock in ("10-20-00", "10-30-00", "10-40-00", "10-50-00", "11-00-00", "11-10-00", "11-20-00", "23-50-00"):
            recording("radio-one", yesterday, clock)
        for clock in ("00-00-00", "10-20-00", "10-40-00"):
            recording("radio-one", today, clock)
        recording("radio-one", custom, "10-30-00")
        recording("capital-radio", yesterday, "10-30-00")
        recording("east-africa-radio", yesterday, "10-30-00")
        for station, age in (("radio-one", 0), ("capital-radio", 120), ("east-africa-radio", 1000)):
            base = root / "recordings" / station
            (base / ".work/run").mkdir(parents=True)
            (base / ".work/run/active.mp3").write_bytes(b"audio" * 10000)
            now = time.time()
            (base / ".status.json").write_text(json.dumps({"running": True, "heartbeat": now, "last_activity": now - age,
                "last_valid_audio": now - age, "active_file": "active.mp3", "active_path": ".work/run/active.mp3"}))
        app = create_app({"TESTING": True, "SECRET_KEY": "operator-browser-secret" * 3,
            "ADMIN_USERNAME": "admin-test", "ADMIN_PASSWORD_HASH": generate_password_hash("admin-test-password"),
            "OPERATOR_USERNAME": "operator-test", "OPERATOR_PASSWORD_HASH": generate_password_hash("operator-test-password"),
            "RECORDINGS_ROOT": str(root / "recordings"), "STATE_DIR": str(root / "state"), "SESSION_COOKIE_SECURE": False})
        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                page = context.new_page()
                errors, ranges = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("response", lambda response: ranges.append(response.status) if "/audio/" in response.url else None)
                base_url = f"http://127.0.0.1:{server.server_port}"
                page.goto(base_url)
                assert page.url.endswith("next=/") or "/login" in page.url
                page.fill("#username", "operator-test")
                page.fill("#password", "wrong")
                page.press("#password", "Enter")
                assert "Username or password is incorrect." in page.locator("[role=alert]").inner_text()
                page.fill("#username", "operator-test")
                page.fill("#password", "operator-test-password")
                page.press("#password", "Enter")
                page.wait_for_selector("#search")
                assert page.locator('a[href="/admin"]').count() == 0
                wait(page, "document.querySelectorAll('.station-status').length === 3")
                statuses = page.locator("#stations").inner_text()
                assert "Recording normally" in statuses and "Recording problem" in statuses and "Offline" in statuses
                assert "active.mp3" not in page.locator("#stations").inner_text()
                assert not page.locator("#player").is_visible()
                assert page.locator("#date").input_value() == str(today)
                page.screenshot(path=str(output / "home-desktop.png"), full_page=True)
                checks.append("login errors, Enter sign-in, role navigation, three station statuses, Today default, no automatic audio")

                # First-time workflow: station, Yesterday, hour, minute, Find & Play.
                page.locator('input[value="radio-one"]').check()
                page.click("#yesterday")
                assert page.locator("#date").input_value() == str(yesterday)
                page.select_option("#hour", "10")
                page.select_option("#minute", "35")
                find(page, "10-30-00.mp3", 300)
                wait(page, "!document.getElementById('audio').paused")
                assert page.locator("#finder").get_attribute("open") is None
                assert page.locator("#broadcast-time").inner_text() == "10:35"
                assert page.locator("#file-detail").count() == 0
                assert "time=10%3A35" in page.url
                checks.append("Yesterday at 10:35 selects 10:30 and seeks 300 seconds, playing view and bookmark URL")

                page.click("#play-pause")
                wait(page, "document.getElementById('audio').paused")
                for delta in (-30, -10, 10, 30):
                    before = page.evaluate("document.getElementById('audio').currentTime")
                    page.click(f'[data-skip="{delta}"]')
                    after = page.evaluate("document.getElementById('audio').currentTime")
                    assert abs(after - before - delta) < 0.3, (before, after, delta)
                page.locator("#seek").fill("400")
                assert abs(page.evaluate("document.getElementById('audio').currentTime") - 400) < 1
                page.locator("#volume").fill("0.5")
                assert page.evaluate("document.getElementById('audio').volume") == 0.5
                with page.expect_download() as download:
                    page.click("#player-download")
                assert "10-30-00.mp3" in download.value.suggested_filename
                page.click("#play-pause")
                wait(page, "!document.getElementById('audio').paused")
                page.click("#next")
                wait(page, "document.getElementById('audio').src.includes('10-40-00')")
                page.click("#previous")
                wait(page, "document.getElementById('audio').src.includes('10-30-00') && document.getElementById('audio').readyState >= 1")
                checks.append("Play/Pause, all four jumps, seek, volume, download, previous/next, Range responses")

                finish_file(page, "10-40-00")
                finish_file(page, "10-50-00")
                finish_file(page, "11-00-00")
                finish_file(page, "11-10-00")
                checks.append("automatic successive playback through an hour boundary")
                page.click("#another-time")
                assert page.locator("#hour").evaluate("el => document.activeElement === el")
                choose(page, str(yesterday), "23", "55")
                find(page, "23-50-00", 300)
                finish_file(page, f"{today}_00-00-00")
                assert page.locator("#broadcast-time").inner_text() == "00:00"
                checks.append("Go to another time and continuous playback across midnight")

                choose(page, str(today))
                page.click("#go")
                page.get_by_role("heading", name="Recording Not Available").wait_for()
                nearby = page.locator("#message button")
                assert nearby.count() == 2
                assert "10:20" in nearby.nth(0).inner_text() and "10:40" in nearby.nth(1).inner_text()
                nearby.nth(1).click()
                wait(page, "document.getElementById('audio').src.includes('10-40-00')")
                page.click("#next")
                wait(page, "document.getElementById('player-message').textContent === 'Recording unavailable for this time.'")
                checks.append("missing time, two nearest suggestions, suggestion playback and unavailable Next")

                page.locator("#finder").evaluate("el => {el.open = true}")
                page.click("#choose-date")
                page.keyboard.press("Escape")
                page.fill("#date", str(custom))
                page.select_option("#hour", "10")
                page.select_option("#minute", "35")
                find(page, str(custom), 300)
                page.click("#another-time")
                page.locator('input[value="capital-radio"]').focus()
                page.keyboard.press("Space")
                assert page.locator('input[value="capital-radio"]').is_checked()
                assert "radio-one" in page.locator("#audio").get_attribute("src")
                page.locator("#yesterday").focus()
                page.keyboard.press("Enter")
                page.select_option("#hour", "10")
                page.select_option("#minute", "35")
                page.locator("#go").focus()
                assert page.locator("#go").evaluate("el => getComputedStyle(el).outlineStyle") != "none"
                page.keyboard.press("Enter")
                wait(page, "document.getElementById('audio').src.includes('capital-radio') && document.getElementById('audio').currentTime >= 300")
                page.reload()
                page.wait_for_selector("#search")
                assert page.locator("#hour").input_value() == "10" and page.locator("#minute").input_value() == "35"
                assert page.locator("#audio").get_attribute("src") is None
                page.go_back()
                assert page.locator("#audio").get_attribute("src") is None
                page.goto(base_url)
                assert page.locator('input[value="capital-radio"]').is_checked()
                assert "password" not in page.evaluate("JSON.stringify(localStorage)").lower()
                checks.append("calendar date, keyboard selection and action, visible focus, station changes, preference, refresh and Back without autoplay")

                page.click('[data-today-station="radio-one"]')
                page.locator("#timeline-title").wait_for()
                assert "radio-one_" not in page.locator("#timeline").inner_text()
                page.get_by_role("button", name="Play Radio One at 10:20", exact=True).click()
                wait(page, "document.getElementById('audio').src.includes('10-20-00')")
                page.locator("#more-options").evaluate("el => {el.open=true}")
                page.select_option("#range-station", "radio-one")
                page.fill("#range-date", str(yesterday))
                page.fill("#range-start", "10:35")
                page.fill("#range-end", "11:15")
                page.locator("#range-search button").click()
                page.get_by_role("button", name="PLAY THIS TIME RANGE").click()
                wait(page, "document.getElementById('audio').src.includes('10-30-00') && document.getElementById('audio').currentTime >= 300")
                for fragment in ("10-40-00", "10-50-00", "11-00-00", "11-10-00"):
                    finish_file(page, fragment)
                page.evaluate("document.getElementById('audio').currentTime=300.5")
                wait(page, "document.getElementById('audio').paused && document.getElementById('player-message').textContent.includes('end of your chosen time range')")
                assert "11-10-00" in page.locator("#audio").get_attribute("src")
                checks.append("today timeline, range search, automatic 40-minute range transitions, precise range stop")

                choose(page, str(yesterday))
                page.evaluate("() => {HTMLMediaElement.prototype.play = function(){return Promise.reject(new DOMException('blocked','NotAllowedError'))};}")
                find(page, "10-30-00", 300)
                wait(page, "document.getElementById('player-message').textContent.includes('large PLAY button')")
                assert page.locator("#play-pause").is_visible()
                page.screenshot(path=str(output / "player-desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(output / "mobile.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.locator("#play-pause").bounding_box()["height"] >= 48
                assert page.evaluate("[...document.querySelectorAll('main *')].filter(e => e.clientWidth > 0 && e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX === 'auto').length") == 0
                page.set_viewport_size({"width": 768, "height": 1024})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                checks.append("autoplay rejection with large Play, phone/tablet layouts without horizontal scrolling")

                response = page.goto(base_url + "/archive/unknown/2026-02-30?time=invalid")
                assert response.status == 400 and page.locator("#search").is_visible()
                assert "link is not valid" in page.locator(".notice.error").inner_text()
                response = page.goto(base_url + "/admin")
                assert response.status == 403
                page.goto(base_url)
                page.locator('form[action="/logout"] button').click()
                page.wait_for_url("**/login")
                assert context.request.get(base_url + "/api/status").status == 401
                checks.append("invalid URL recovery, operator admin denial, logout and API protection")

                admin_page = browser.new_page(viewport={"width": 1440, "height": 1000})
                admin_page.goto(base_url + "/login")
                admin_page.fill("#username", "admin-test")
                admin_page.fill("#password", "admin-test-password")
                admin_page.press("#password", "Enter")
                admin_page.get_by_role("link", name="System health").click()
                admin_page.wait_for_selector("#admin-system p")
                assert "153 days" in admin_page.locator("#admin-system").inner_text()
                assert "Read only" in admin_page.locator("#admin-system").inner_text()
                assert "Usage" in admin_page.locator("#admin-storage").inner_text()
                assert admin_page.locator("#admin-stations article").count() == 3
                admin_page.fill("#gap-date", str(today))
                admin_page.locator("#gap-search button").click()
                wait(admin_page, "document.getElementById('gap-results').textContent.includes('Missing')")
                admin_page.screenshot(path=str(output / "admin.png"), full_page=True)
                checks.append("administrator login, separate health/storage/retention dashboard and date gap inspection")
                assert 206 in ranges
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
    (output / "results.json").write_text(json.dumps(checks, indent=2))
    for check in checks:
        print("PASS:", check)
    print("Screenshots:", output)


if __name__ == "__main__":
    main()
