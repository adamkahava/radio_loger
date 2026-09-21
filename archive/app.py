import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
import secrets
import sqlite3
import time

from flask import Flask, abort, g, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash
from catalog import Catalog, STATIONS, TZ, parse_day


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", ""),
        ADMIN_USERNAME=os.getenv("ADMIN_USERNAME", "admin"),
        ADMIN_PASSWORD_HASH=os.getenv("ADMIN_PASSWORD_HASH", ""),
        OPERATOR_USERNAME=os.getenv("OPERATOR_USERNAME", "operator"),
        OPERATOR_PASSWORD_HASH=os.getenv("OPERATOR_PASSWORD_HASH", ""),
        OPERATOR_DOWNLOADS=os.getenv("OPERATOR_DOWNLOADS", "true").lower() == "true",
        RETENTION_DAYS=int(os.getenv("RETENTION_DAYS", "153")),
        STREAM_URLS={"radio-one": os.getenv("RADIO_ONE_URL", "https://radioonetanzania.radioca.st/stream"),
                     "capital-radio": os.getenv("CAPITAL_RADIO_URL", "https://capitalradio.radioca.st/stream"),
                     "east-africa-radio": os.getenv("EAST_AFRICA_URL", "https://eatv.radioca.st/stream")},
        RECORDINGS_ROOT=os.getenv("RECORDINGS_ROOT", "/recordings"),
        STATE_DIR=os.getenv("STATE_DIR", "/state"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "true").lower() == "true",
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=8192,
    )
    if config:
        app.config.update(config)
    password_hash = app.config["ADMIN_PASSWORD_HASH"]
    if (len(app.config["SECRET_KEY"]) < 32 or not password_hash.startswith(("scrypt:", "pbkdf2:sha256:"))
            or len(password_hash.split("$")) != 3):
        raise RuntimeError("Configure SECRET_KEY and a secure ADMIN_PASSWORD_HASH using scripts/configure.py")
    credential_version = hashlib.sha256((app.config["ADMIN_USERNAME"] + password_hash).encode()).hexdigest()
    accounts = {"admin": (app.config["ADMIN_USERNAME"], password_hash, credential_version)}
    operator_hash = app.config["OPERATOR_PASSWORD_HASH"]
    if operator_hash:
        if not operator_hash.startswith(("scrypt:", "pbkdf2:sha256:")) or len(operator_hash.split("$")) != 3:
            raise RuntimeError("Configure a secure OPERATOR_PASSWORD_HASH")
        if app.config["OPERATOR_USERNAME"] == app.config["ADMIN_USERNAME"]:
            raise RuntimeError("Administrator and operator usernames must be different")
        fingerprint = hashlib.sha256((app.config["OPERATOR_USERNAME"] + operator_hash).encode()).hexdigest()
        accounts["operator"] = (app.config["OPERATOR_USERNAME"], operator_hash, fingerprint)
    dummy_hash = generate_password_hash(secrets.token_hex(24))
    catalog = Catalog(app.config["RECORDINGS_ROOT"])
    app.extensions["catalog"] = catalog
    state = Path(app.config["STATE_DIR"])
    state.mkdir(parents=True, exist_ok=True)
    rate_db = state / "auth.sqlite3"
    with sqlite3.connect(rate_db) as db:
        db.execute("CREATE TABLE IF NOT EXISTS attempts (bucket TEXT PRIMARY KEY, started REAL NOT NULL, count INTEGER NOT NULL)")

    def limited():
        # Shared by gunicorn workers and persisted across restarts. Ignore untrusted forwarded IP headers.
        now = time.time()
        bucket = "ip:" + hashlib.sha256((request.remote_addr or "unknown").encode()).hexdigest()
        with sqlite3.connect(rate_db, timeout=5) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM attempts WHERE started < ?", (now - 900,))
            for key, maximum in ((bucket, 10), ("global", 100)):
                row = db.execute("SELECT count FROM attempts WHERE bucket=?", (key,)).fetchone()
                if row and row[0] >= maximum:
                    return True
            for key in (bucket, "global"):
                db.execute("INSERT INTO attempts VALUES (?, ?, 1) ON CONFLICT(bucket) DO UPDATE SET count=count+1", (key, now))
        return False

    @app.before_request
    def protect():
        if request.endpoint in ("healthz", "static"):
            return None
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            token = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
            if not token or not hmac.compare_digest(token, session.get("csrf", "")):
                abort(400, description="Your form expired. Reload the page and try again.")
        if request.endpoint == "login":
            return None
        g.role = next((role for role, account in accounts.items() if session.get("user") == account[2]), None)
        if not g.role:
            if request.path.startswith(("/api/", "/audio/", "/download/")):
                abort(401, description="Sign in to access recordings.")
            destination = request.full_path.rstrip("?") if request.path.startswith("/archive/") else "/"
            return redirect(url_for("login", next=destination))
        if request.endpoint in ("admin", "admin_status") and g.role != "admin":
            abort(403, description="This page is for the system administrator. You can still find and play broadcasts.")

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self'; img-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        response.headers["Cache-Control"] = "private, no-store"
        if app.config["SESSION_COOKIE_SECURE"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.context_processor
    def csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return {"csrf_token": session["csrf"], "is_admin": getattr(g, "role", None) == "admin",
                "can_download": getattr(g, "role", None) == "admin" or app.config["OPERATOR_DOWNLOADS"]}

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        code = 200
        if request.method == "POST":
            if limited():
                error, code = "Too many sign-in attempts. Try again in 15 minutes.", 429
            else:
                username = request.form.get("username", "")
                account = next((account for account in accounts.values() if hmac.compare_digest(username.encode(), account[0].encode())), None)
                good_password = check_password_hash(account[1] if account else dummy_hash, request.form.get("password", ""))
                if account and good_password:
                    session.clear()
                    session.update(user=account[2], csrf=secrets.token_urlsafe(32))
                    session.permanent = True
                    app.logger.info("event=login_success")
                    destination = request.args.get("next", "/")
                    if not destination.startswith("/archive/") or "\\" in destination or any(ord(c) < 32 for c in destination):
                        destination = "/"
                    return redirect(destination)
                app.logger.warning("event=login_failed")
                error, code = "Username or password is incorrect.", 401
        response = app.make_response((render_template("login.html", error=error), code))
        if code == 429:
            response.headers["Retry-After"] = "900"
        return response

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    def index():
        now = datetime.now(TZ)
        return render_template("index.html", stations=STATIONS, today=now.date().isoformat(),
                               yesterday=(now.date() - timedelta(days=1)).isoformat(), initial=None, initial_error=None)

    @app.get("/archive/<station>/<day>")
    def bookmark(station, day):
        now = datetime.now(TZ)
        initial, error = None, None
        try:
            if station not in STATIONS:
                raise ValueError("Choose one of the three stations below.")
            selected_day = parse_day(day)
            value = request.args.get("time", "")
            if value:
                clock_at(selected_day, value)
            initial = {"station": station, "date": day, "time": value}
        except ValueError:
            error = "This broadcast link is not valid. Choose a station, day and time below."
        return render_template("index.html", stations=STATIONS, today=now.date().isoformat(),
                               yesterday=(now.date() - timedelta(days=1)).isoformat(), initial=initial, initial_error=error), 400 if error else 200

    @app.get("/admin")
    def admin():
        return render_template("admin.html", stations=STATIONS, today=datetime.now(TZ).date().isoformat())

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", service="radio-archive")

    @app.get("/api/stations")
    def stations():
        return jsonify(stations=[{"id": key, "name": value} for key, value in STATIONS.items()], timezone=str(TZ))

    @app.get("/api/status")
    def status():
        data = catalog.status()
        simple = [{key: row.get(key) for key in ("station", "name", "status", "last_successful", "age_seconds")} for row in data["stations"]]
        return jsonify(stations=simple, time=data["time"], timezone=data["timezone"])

    @app.get("/api/admin/status")
    @app.get("/api/health")
    def admin_status():
        from urllib.parse import urlsplit, urlunsplit
        data = catalog.status()
        # Never render URL credentials, query-string secrets, or filesystem paths.
        urls = {}
        for station, value in app.config["STREAM_URLS"].items():
            parsed = urlsplit(value)
            urls[station] = urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))
        data["system"] = {"retention_days": app.config["RETENTION_DAYS"], "segment_minutes": 10,
                          "timezone": str(TZ), "stream_urls": urls, "recordings_access": "Read only"}
        return jsonify(data)

    def selection():
        station = request.args.get("station", "radio-one")
        if station not in STATIONS:
            raise ValueError("Unknown station.")
        return station, parse_day(request.args.get("date", datetime.now(TZ).date().isoformat()))

    def clock_at(day, value):
        import re
        if not re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", value):
            raise ValueError("Choose an hour and minute, then try again.")
        try:
            clock = datetime.strptime(value, "%H:%M:%S" if len(value) == 8 else "%H:%M").time()
        except ValueError:
            raise ValueError("Choose a valid hour and minute, then try again.") from None
        return datetime.combine(day, clock, TZ).timestamp()

    @app.get("/api/recordings")
    def recordings():
        station, day = selection()
        rows = catalog.recordings(station, day)
        return jsonify(recordings=rows, summary=catalog.summary(station, day, recordings=rows), timezone=str(TZ))

    @app.get("/api/resolve")
    def resolve():
        station, day = selection()
        value = request.args.get("time", "")
        target = clock_at(day, value)
        rows = catalog.recordings(station, day)
        for item in reversed(rows):
            if item["timestamp"] <= target < item["end_timestamp"]:
                return jsonify(recording=item, offset=target - item["timestamp"], estimated=item["duration"] is None)
        return jsonify(error="Recording not available for this time.", station=STATIONS[station], date=day.isoformat(),
                       time=value, nearest=catalog.nearest(station, day, target, rows)), 404

    @app.get("/api/range")
    def time_range():
        station, day = selection()
        start = clock_at(day, request.args.get("start", ""))
        end = clock_at(day, request.args.get("end", ""))
        if end <= start:
            raise ValueError("The end time must be later than the start time on the same day.")
        rows = [row for row in catalog.recordings(station, day) if row["timestamp"] < end and row["end_timestamp"] > start]
        gaps, cursor = [], start
        for row in rows:
            if row["timestamp"] > cursor + 2:
                gaps.append({"start": cursor, "end": row["timestamp"]})
            cursor = max(cursor, row["end_timestamp"])
        if cursor < end - 2:
            gaps.append({"start": cursor, "end": end})
        return jsonify(recordings=rows, start=start, end=end, gaps=gaps,
                       offset=max(0, start - rows[0]["timestamp"]) if rows else 0)

    @app.get("/api/adjacent")
    def adjacent():
        direction = request.args.get("direction")
        if direction not in ("previous", "next"):
            raise ValueError("Choose previous or next.")
        row = catalog.adjacent(request.args.get("station", ""), request.args.get("filename", ""), -1 if direction == "previous" else 1)
        return jsonify(recording=row)

    def media(station, filename, attachment):
        path = catalog.path(station, filename)
        if not path.is_file() or path.stat().st_size < 1024:
            abort(404, description="This recording is unavailable or incomplete.")
        return send_file(path, mimetype="audio/mpeg", as_attachment=attachment, download_name=filename, conditional=True)

    @app.get("/audio/<station>/<filename>")
    def audio(station, filename):
        return media(station, filename, False)

    @app.get("/download/<station>/<filename>")
    def download(station, filename):
        if g.role != "admin" and not app.config["OPERATOR_DOWNLOADS"]:
            abort(403, description="Downloading is not enabled for this account. You can still listen.")
        return media(station, filename, True)

    @app.errorhandler(ValueError)
    def invalid(error):
        return error_response(str(error), 400)

    @app.errorhandler(OSError)
    @app.errorhandler(sqlite3.Error)
    def unavailable(error):
        app.logger.error("event=resource_unavailable kind=%s", type(error).__name__)
        return error_response("Storage is temporarily unavailable. Contact the administrator.", 503)

    @app.errorhandler(HTTPException)
    def http_error(error):
        return error_response(error.description, error.code)

    @app.errorhandler(Exception)
    def unexpected(error):
        app.logger.exception("event=unexpected_error")
        return error_response("An unexpected error occurred. Contact the administrator.", 500)

    def error_response(message, code):
        if request.path.startswith(("/api/", "/audio/", "/download/")):
            return jsonify(error=message), code
        return render_template("error.html", message=message, code=code), code

    return app


# Tests import the factory without creating a production instance.
if os.getenv("RADIO_FACTORY_ONLY") != "1":
    app = create_app()
