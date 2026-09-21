# Verification record

Executed on **21 September 2026**, in the supplied Windows/PowerShell workspace. Original recordings contained only three empty `test` placeholders. They were not modified or deleted. Tests use temporary directories or ignored `test-results/`.

## Checks actually run

| Check | Result and scope |
| --- | --- |
| Repository audit | Read every original application, Docker, shell, template, CSS and environment file, plus recording placeholders; Compose and `.env` were empty. |
| Python syntax | `python -m compileall -q archive recorder scripts tests` passed. |
| Dependency update | Final application and browser tests passed on Flask 3.1.3; Gunicorn 25.3.0 was installed but cannot run its Unix worker model on this Windows host. |
| Shell syntax | Git Bash `bash -n recorder/recorder.sh recorder/healthcheck.sh` passed. |
| JavaScript syntax | `node --check archive/static/app.js` passed. |
| Automated application/recorder/retention checks | 23 passed, 2 skipped with pytest. Includes authentication, CSRF, persistent rate limiting, cookie/CSP headers, invalid station/date/time/path requests, HTTP Range payload correctness, downloads, 10:34 → 240-second lookup, partial intervals, midnight adjacency, status, collision protection and retention using disposable data. |
| Symlink tests | Two skipped: this Windows session cannot create symlinks. Run on Linux before production acceptance. |
| Compose structure | YAML parsed with PyYAML; asserted five services, non-root users, read-only root filesystems, capability dropping, independent startup, and read-only archive recording mount. This does **not** replace `docker compose config`. |
| Dockerfiles | Parsed both files with `dockerfile-parse`; inspected instructions, users, entrypoints and build contexts. This does **not** prove image builds. |
| Docker CLI/runtime | Attempted `docker compose config`; `docker` is not installed. An attempt to fetch the official standalone Compose CLI returned HTTP 504. Image builds, container startup, container logs, running mount inspection and Linux supervisor execution were unavailable. |
| Live East Africa Radio | HTTPS 200, `audio/mpeg`, 256,000-byte sample. FFmpeg identified stereo 44.1 kHz MP3, 64 kb/s; sample decoded successfully. |
| Live Capital Radio | First request timed out. Retry returned HTTPS 200, `audio/mpeg`, 65,536-byte sample. FFmpeg identified stereo 44.1 kHz MP3, 64 kb/s; sample decoded successfully. |
| Live Radio One | First request timed out. Retry returned HTTPS 200, `audio/mpeg`, but zero audio bytes after approximately 30 seconds. No valid codec or recording was verified for this stream. |
| Copy-mode investigation | Segmented the two valid source samples with `-c:a copy`. Segment audio frames had nonzero MP3 `main_data_begin` reservoir references after the zero-reference Xing header. Capital's second copied segment began with audio references 348/370 bytes; East Africa's with 11/10 bytes. This supports disabling the reservoir through encoding for independent segment decoding. |
| Real-time clock rollover | Ran Windows FFmpeg 7.1 for 625 seconds simultaneously on looped captured Capital/East Africa samples, using the production output options with 600-second clock intervals. Both created initial `17-34-58` and next `17-40-00` files on 2026-09-21. All four files fully decoded without reported errors. This observed one clock boundary; it was not a live network/container soak or a full uninterrupted second 600-second segment. |
| Timezone in local FFmpeg test | Used Windows CRT `TZ=EAT-3` to match the operational UTC+3 clock. Production uses Alpine tzdata with `TZ=Africa/Dar_es_Salaam`; that container timezone still needs runtime verification. Python API tests explicitly verified `+03:00` timestamps. |
| Browser integration | Headless Chromium with real generated ten-minute MP3 audio: login, date results, Go To Time at 240 seconds, audio playing, +30-second seek, playback speed, download, previous/next, continuous transition, deliberately rejected autoplay fallback, responsive overflow, HTTP 206 and logout passed. No browser JavaScript errors. CSP was kept enabled. |
| Visual inspection | Desktop screenshot reviewed; compact station status, storage, search, list and persistent player. Browser screenshots are local, ignored artifacts in `test-results/browser/`. |
| Retention safety | Deleted only artificially old finalized test recordings in temporary directories; verified newer restore, staging and malformed-name protection. Production Linux descriptor-relative deletion still needs Linux testing. |

The first browser test attempts exposed test-harness issues with CSP-aware predicate evaluation and a mock returning a rejected promise. Those were corrected without weakening application CSP. Subsequent browser checks passed.

## Reproduce local tests

Application tests use dummy credentials and temporary files, never `.env` credentials:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r archive/requirements.txt pytest tzdata PyYAML dockerfile-parse
python -m compileall -q archive recorder scripts tests
bash -n recorder/recorder.sh recorder/healthcheck.sh
node --check archive/static/app.js
python -m pytest -q tests
```

Optional browser test (downloads Chromium and uses only local synthetic audio):

```bash
pip install playwright imageio-ffmpeg
python -m playwright install chromium
python tests/browser_smoke.py
```

`tests/audio_smoke.py 625` expects local captured `test-results/capital-radio.sample` and `test-results/east-africa-radio.sample`. It does not fetch streams or touch master recordings. It loops those samples in real time and writes a unique test folder. For at least one full 600-second segment regardless of launch time, use **1225 seconds**. An absent sample fails the manual smoke script; its output must be reviewed, not treated as proof of all stations.

## Required deployment-host checks

1. Run `docker compose config --quiet`, `docker compose build --pull`, then start the five-service stack.
2. Verify the installed FFmpeg/FFprobe build and options, permissions, writer lock, stop/restart behavior and health in Linux.
3. Resolve Radio One's no-audio response from the production network and verify **all three** streams record.
4. Observe at least two full ten-minute boundaries, preferably also midnight and a forced temporary stream interruption. Decode representative MP3s and compare filenames with EAT time.
5. Confirm `/recordings` is read-only in the running archive container and no recorder depends on archive/cleaner uptime.
6. Run symlink/retention tests on disposable Linux data. Confirm actual scheduled cleaner logs and health marker.
7. Test the real TLS proxy, browser policy, authenticated Range delivery, credential rotation and external monitoring.
8. Perform a multi-day soak, capacity assessment and backup/restore exercise before treating the deployment as production accepted.

## Remaining operational risks

- Radio One was not producing usable audio in the performed network checks; upstream or network investigation is required.
- No Linux/Docker execution was possible here. Static configuration checks do not establish runtime compatibility.
- MP3 frames, input buffering and upstream delay prevent sample-exact correspondence between capture clock and original broadcast. Short reconnects can compress missing audio inside a file; gap detection identifies interval coverage, not every sub-second content loss.
- One host/disk/network remains a shared failure domain. Monitor externally; there is no automatic alert delivery or host redundancy.
- Failed/corrupt crash remnants are preserved in `.work` for review and are not automatically pruned. Monitor staging size as well as total free space.
- Retention is based on local capture filename and mtime, not a compliance hold system. Protect required evidence through a separate backup/hold process.
- Transcoding costs CPU and changes encoded audio. Measure CPU on the target host; the default favors independent MP3 segment playback over bit-for-bit preservation.
