"""Real-time FFmpeg output test using captured samples; writes only test-results.

Run with the optional imageio-ffmpeg test dependency. Windows uses CRT TZ syntax;
production containers use the IANA timezone with Alpine tzdata.
"""
import json
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
import time

import imageio_ffmpeg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "recorder"))
from worker import command


def main():
    root = Path("test-results") / ("rollover-" + str(int(time.time())))
    root.mkdir(parents=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 625
    env = os.environ.copy()
    env["TZ"] = "EAT-3" if os.name == "nt" else "Africa/Dar_es_Salaam"
    processes = []
    for station in ("capital-radio", "east-africa-radio"):
        sample = Path("test-results") / (station + ".sample")
        if not sample.exists() or sample.stat().st_size < 1024:
            raise SystemExit(f"Missing usable sample: {sample}")
        folder = root / station
        folder.mkdir()
        os.environ["STATION"] = station
        os.environ["FFMPEG"] = ffmpeg
        cmd = command(str(sample), folder, "transcode")
        output = cmd[cmd.index("-map"):]
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin", "-re", "-stream_loop", "-1",
               "-i", str(sample), "-t", str(seconds), *output]
        log = (folder / "ffmpeg.log").open("w")
        process = subprocess.Popen(cmd, env=env, stdout=log, stderr=log)
        processes.append((station, folder, process, log))
    for station, folder, process, log in processes:
        code = process.wait(timeout=seconds + 60)
        log.close()
        files = sorted(folder.glob("*.mp3"))
        for path in files[1:]:
            stamp = datetime.strptime(path.stem[len(station) + 1:], "%Y-%m-%d_%H-%M-%S")
            assert stamp.minute % 10 == 0 and stamp.second <= 1, path.name
        report = {"station": station, "exit": code, "files": []}
        for path in files:
            decoded = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True)
            report["files"].append({"name": path.name, "size": path.stat().st_size,
                                    "decode_exit": decoded.returncode, "errors": decoded.stderr.decode(errors="replace")})
        (folder / "report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)
        if code or len(files) < 2 or any(item["decode_exit"] for item in report["files"]):
            raise SystemExit(1)
    print("Output:", root, flush=True)


if __name__ == "__main__":
    main()
