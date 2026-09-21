import os
import re
from pathlib import Path
from datetime import datetime

from flask import (
    Flask,
    render_template,
    request,
    send_file,
    abort,
    jsonify
)

app = Flask(__name__)

RECORDINGS = Path("/recordings")

STATIONS = {
    "radio-one": "Radio One",
    "capital-radio": "Capital Radio",
    "east-africa-radio": "East Africa Radio"
}

FILE_RE = re.compile(
    r"^(?P<station>.+)_(?P<date>\d{4}-\d{2}-\d{2})_"
    r"(?P<hour>\d{2})-(?P<minute>\d{2})-(?P<second>\d{2})\.mp3$"
)


def safe_station(station):
    if station not in STATIONS:
        abort(404)

    return station


def get_recordings(station, date=None):
    station = safe_station(station)

    directory = RECORDINGS / station

    if not directory.exists():
        return []

    results = []

    for path in directory.glob("*.mp3"):

        match = FILE_RE.match(path.name)

        if not match:
            continue

        data = match.groupdict()

        if date and data["date"] != date:
            continue

        start = datetime.strptime(
            f'{data["date"]} '
            f'{data["hour"]}:{data["minute"]}:{data["second"]}',
            "%Y-%m-%d %H:%M:%S"
        )

        results.append({
            "filename": path.name,
            "station": station,
            "station_name": STATIONS[station],
            "date": data["date"],
            "time": start.strftime("%H:%M"),
            "timestamp": start.timestamp(),
            "size": path.stat().st_size
        })

    results.sort(
        key=lambda x: x["timestamp"],
        reverse=True
    )

    return results


@app.route("/")
def index():

    station = request.args.get(
        "station",
        "radio-one"
    )

    date = request.args.get(
        "date",
        datetime.now().strftime("%Y-%m-%d")
    )

    recordings = get_recordings(
        station,
        date
    )

    return render_template(
        "index.html",
        stations=STATIONS,
        selected_station=station,
        selected_date=date,
        recordings=recordings
    )


@app.route("/audio/<station>/<filename>")
def audio(station, filename):

    station = safe_station(station)

    filename = os.path.basename(filename)

    path = RECORDINGS / station / filename

    if not path.exists():
        abort(404)

    return send_file(
        path,
        mimetype="audio/mpeg",
        conditional=True
    )


@app.route("/download/<station>/<filename>")
def download(station, filename):

    station = safe_station(station)

    filename = os.path.basename(filename)

    path = RECORDINGS / station / filename

    if not path.exists():
        abort(404)

    return send_file(
        path,
        as_attachment=True
    )


@app.route("/api/health")
def health():

    result = {}

    now = datetime.now().timestamp()

    for station, name in STATIONS.items():

        files = get_recordings(station)

        if not files:

            result[station] = {
                "name": name,
                "status": "DOWN",
                "last": None
            }

            continue

        latest = files[0]

        age = now - latest["timestamp"]

        result[station] = {
            "name": name,
            "status": "OK" if age < 900 else "WARNING",
            "last": latest["filename"]
        }

    return jsonify(result)


@app.route("/healthz")
def healthz():
    return "OK", 200


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=8080
    )