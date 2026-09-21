# Radio Archive — Broadcast Recording System

Continuous, independent internet-radio logging for Radio One Tanzania, Capital Radio Tanzania and East Africa Radio. Operational time is **Africa/Dar_es_Salaam (UTC+3)**. This extends the existing Flask/FFmpeg project.

## Architecture

```text
Radio One stream ───────> radio-one ─────────┐
Capital Radio stream ──> capital-radio ─────┼─> recordings/<station>/YYYY/MM/DD/*.mp3
East Africa stream ────> east-africa-radio ──┘       │                   │
                                                   │ read only         │ finalized old files
                                              radio-archive     radio-retention-cleaner
                                                   │
                                             HTTPS reverse proxy
                                                   │
                                             authenticated browser
```

Each recorder has its own FFmpeg process, supervisor, station-only writable mount, watchdog and restart policy. There are **no Compose service dependencies**. Web, authentication, cleanup and other station failures do not stop a recorder. Shared host, network and disk failures can still affect all stations.

The shell entrypoint execs a small Python supervisor. FFmpeg segments continuously with `-segment_time 600 -segment_atclocktime 1 -strftime 1`; it is not restarted at ten-minute boundaries. Each process run gets a unique staging directory under `<station>/.work/`. FFmpeg's bounded CSV completion list identifies closed segments. The supervisor FFprobes them, writes a small JSON duration sidecar, and atomically moves the MP3 onto the same filesystem into the dated archive. Staging files are never exposed as downloadable recordings or touched by retention. This also avoids creating date directories inside FFmpeg at midnight.

```text
recordings/radio-one/2026/09/21/radio-one_2026-09-21_14-00-00.mp3
recordings/radio-one/2026/09/21/radio-one_2026-09-21_14-00-00.json
recordings/radio-one/.status.json
recordings/radio-one/.work/<unique-run>/...
```

The archive scans only the requested station/day (normally 144 MP3s). Status scans today's three directories and reads three small status files. Previous/Next enumerates date directories and scans days until it finds the adjacent recording. No archive database is needed at this scale. SQLite in the separate `archive-state` volume stores **authentication rate-limit counters only**. Recording files remain the source of truth; JSON metadata adds measured duration and recovery information. Missing sidecars show an unverified duration rather than claiming complete coverage.

## Audit of the original repository

All original project files were read, including the empty `.env`, empty Compose file and three empty `recordings/*/test` placeholders. The original shell loop recorded stream-copy MP3s into a flat station folder, and Flask listed those files and served audio with conditional responses.

Problems found:

- Empty Compose prevented deployment and provided no isolation, healthchecks, mounts or log rotation.
- No authentication, session protection, CSRF checks or login throttling. Audio and archive metadata were public.
- Path handling accepted arbitrary basenames and did not reject symlinks. Filenames were loosely validated.
- Recorder files accumulated in one directory; health and web listing repeatedly scanned all recordings.
- Naive Python datetimes inherited host timezone, while FFmpeg timezone was not configured.
- Hardcoded UI “Recording” indicator, no actual activity tracking or process check, no stuck-process watchdog.
- Unconditional audio copy assumed every source was valid MP3 and ignored cross-segment MP3 reservoir references.
- No retention, shutdown signal forwarding, duplicate-writer lock, collision protection or partial-file recovery.
- No Go To Time, continuous playback, persistent cross-search player, gaps, storage view or useful operational API.
- Dockerfiles ran as root. The archive Dockerfile's multiline JSON `CMD` was not a valid Dockerfile instruction layout.
- Template text contained encoding damage. Errors and malformed dates could cause unhelpful responses.

Dependencies are updated to Flask 3.1.3 and Gunicorn 25.3.0: Flask's [3.1.3 security release](https://github.com/pallets/flask/releases/tag/3.1.3) fixes session-cookie variation behavior, and Gunicorn's [security policy](https://github.com/benoitc/gunicorn/security) lists 25.3.0 as supported while the original 23.0.0 is unsupported. Continue reviewing dependency and base-image updates during maintenance.

## Timing and audio limitations

Intervals are **wall-clock aligned**, with packet/frame granularity (MP3 frames are about 26 ms at 44.1 kHz). An initial start at 10:34 creates a partial 10:34–10:40 recording, followed by 10:40, 10:50, etc. A reconnect can produce another partial file inside an interval. Do not promise mathematically exact 600.000-second encoded files: frame boundaries, startup buffering and network interruptions affect measured audio duration. Filenames describe recorder-local wall-clock capture time, not a timestamp supplied by the broadcaster. Keep the host clock synchronized with NTP. Stream latency means capture time may lag the original broadcast.

`SEGMENT_SECONDS` is deliberately validated as 600. `TZ` is fixed operationally to Africa/Dar_es_Salaam. Do not change these independently of archive interval logic.

**Default audio mode is `transcode`**: libmp3lame, stereo 44.1 kHz, 128 kb/s, one encoding thread, `-reservoir 0`. On the verification host, Capital Radio and East Africa Radio supplied MP3 at 64 kb/s. Copy-mode segmentation succeeded as a container operation, but copied segment audio frames referenced previous reservoir bytes (the initial zero-reservoir Xing header is not an audio independence guarantee). Re-encoding disables those dependencies and makes segment starts independently decodable. Radio One returned HTTP 200 but no audio in the attempted sample; its codec could not be verified. See [verification results](docs/VERIFICATION.md).

Set `AUDIO_MODE=copy` only after verifying a source is MP3 and accepting/testing segment-boundary artifacts; AAC cannot be copied into this MP3 archive. Override mode per station in Compose if necessary. `MP3_BITRATE=64k` is an option to reduce storage; encoding at 128 kb/s does not restore quality missing in a 64 kb/s source. See the official [FFmpeg segment muxer](https://ffmpeg.org/ffmpeg-formats.html#segment_002c-stream_005fsegment_002c-ssegment) and [HTTP reconnect options](https://ffmpeg.org/ffmpeg-protocols.html#http).

FFmpeg reconnects after network errors, EOF, 429 and 5xx responses. The supervisor retries exited processes after five seconds and terminates a process with no file growth for 60 seconds. Invalid audio also triggers a restart. Short outages may be compressed within an MP3; the duration/gap view detects underfilled intervals but is not sample-level evidence of every outage. Recovered files are explicitly marked partial. Silence is valid audio and is not treated as stream failure. External silence/content monitoring is outside this implementation.

## Requirements

- Linux server with Docker Engine and the Docker Compose v2 plugin installed and running.
- A reliable local disk/filesystem with atomic rename and advisory locking; avoid unverified network filesystems.
- Outbound HTTPS/DNS access to the three stations, accurate NTP, adequate disk capacity and backups.
- TLS reverse proxy and restricted operator access for production.
- Suggested starting capacity: 2 CPU cores and 2 GB RAM; measure actual CPU/network/disk use on your host.

The development environment was Windows without Docker. Do not interpret local tests as a Linux container build or production acceptance test.

## Clean installation, configuration and start

Copy or clone this repository into `/opt/radio-logger`, then run these commands there on the Linux host. Do not copy development `.venv`, `test-results` or someone else's `.env`.

```bash
cd /opt/radio-logger
docker version
docker compose version
cp .env.example .env
chmod 600 .env
sudo install -d -o 10001 -g 10001 -m 0750 recordings
sudo install -d -o 10001 -g 10001 -m 0750 \
  recordings/radio-one recordings/capital-radio recordings/east-africa-radio
docker compose config --quiet
docker compose build --pull
docker compose run --rm -it --no-deps \
  --user "$(id -u):$(id -g)" --entrypoint python \
  --volume "$PWD:/setup" --workdir /setup \
  radio-archive scripts/configure.py
docker compose config --quiet
docker compose up -d
docker compose ps
docker compose logs --tail=100 radio-one capital-radio east-africa-radio
```

The interactive setup writes only a scrypt hash, administrator username and cryptographically random session secret to `.env`; it never prints or stores the plaintext password. Use at least 14 characters. Single quotes around hashes protect `$` characters from Compose interpolation. `.env` is ignored by Git and the originally tracked empty file has been removed from tracking. Never paste full `docker compose config` output into tickets: it includes environment secrets. Use `--quiet` for routine validation.

If `RECORDINGS_PATH` is changed, create the three station subdirectories there and grant UID/GID 10001 write access **before** starting Compose. Auto-created bind mounts may otherwise be owned by root. The archive-state named volume is initialized from the archive image's owned `/state` directory. Do not run `docker compose down -v` during normal operations.

Configure `.env` URLs, storage and retention before starting. The `.env` file is injected through explicit Compose environment entries, not mounted into containers. There are no default credentials; the archive refuses to start with missing/insecure configuration. Recorder startup does not depend on those credentials.

## Access and TLS

By default, the archive binds to **127.0.0.1:8096** on the host and sets Secure cookies. Place an existing HTTPS reverse proxy in front of it. Example Nginx server (replace hostname and certificate paths):

```nginx
server {
    listen 443 ssl;
    server_name radio.example.org;
    ssl_certificate /etc/letsencrypt/live/radio.example.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/radio.example.org/privkey.pem;
    location / {
        proxy_pass http://127.0.0.1:8096;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 60s;
        proxy_buffering off;
    }
}
```

Preserve HTTP Range and Content-Range headers; do not put unauthenticated caching in front of audio. Redirect public HTTP to HTTPS in your existing proxy. Visit `https://radio.example.org` and sign in.

For an **SSH-tunnel-only HTTP check**, set `COOKIE_SECURE=false` in `.env`, recreate only the archive, then tunnel from your workstation:

```bash
# Server, after editing .env:
docker compose up -d --no-deps --force-recreate radio-archive
# Workstation:
ssh -L 8096:127.0.0.1:8096 operator@YOUR_SERVER
```

Open `http://127.0.0.1:8096`. Restore `COOKIE_SECURE=true` before production TLS access. Secure cookies intentionally do not support ordinary HTTP sign-in. Keep `ARCHIVE_BIND=127.0.0.1` unless you have a specific protected network design.

## Operator workflow

- Station + Date + Time → **Go To Time** selects actual available coverage. 10:34 in a 10:30 segment seeks to 240 seconds. A startup at 10:34 seeks relative to that actual start; an earlier missing time is not silently substituted.
- Autoplay restrictions show “Press Play” after loading and seeking. Browsing another station/date keeps the current player running.
- Find Recordings lists chronological intervals, measured duration, file size and Play/Download.
- Native controls provide play/pause, volume and seeking. Additional controls provide ±10s, ±30s, speed, Previous/Next, current position and download.
- Continuous Play defaults on, crosses midnight, and stops at gaps greater than two seconds or unverified/recovered coverage. Previous/Next can explicitly cross gaps.
- Only published files are available, normally within seconds after a ten-minute interval ends. Current incomplete segments remain private in staging.
- Today counts **completed intervals so far**, not all 144 future intervals. Multiple partial files in one interval count once. Missing, partial and unverified intervals are distinct. A brief publication delay at a boundary can transiently show a gap.
- Storage reports filesystem used/free/total (including other data on that filesystem), not a recursively computed MP3-only total. Warning starts at 80%, Critical at 90%.

## Health, logs and APIs

```bash
docker compose ps
docker compose exec radio-one /usr/local/bin/healthcheck.sh
docker compose exec capital-radio /usr/local/bin/healthcheck.sh
docker compose exec east-africa-radio /usr/local/bin/healthcheck.sh
docker compose logs -f --tail=100 radio-one
docker compose logs --since=1h capital-radio east-africa-radio
docker compose logs --since=24h radio-retention-cleaner
docker compose logs --tail=100 radio-archive
curl --fail http://127.0.0.1:8096/healthz
```

Recorder health requires supervisor and FFmpeg PIDs alive, a fresh supervisor heartbeat, growth in the last 15 minutes, and recently FFprobe-validated MP3 audio. The web dashboard is stricter for “Recording”: fresh heartbeat, reported growth, recent validation and an actual nonempty recent staging file. Activity up to 15 minutes old is Warning; older/missing activity is Offline. It never infers recorder health from web uptime. Docker alone does not restart an unhealthy container: the recorder watchdog supplies recovery.

`/healthz` is public and checks web liveness only. Authenticated endpoints:

```text
GET /api/stations
GET /api/status                 # station activity, completed intervals, filesystem storage
GET /api/health                 # compatibility alias of /api/status
GET /api/recordings?station=radio-one&date=2026-09-21
GET /api/resolve?station=radio-one&date=2026-09-21&time=10:34
GET /api/adjacent?station=radio-one&filename=radio-one_2026-09-21_10-30-00.mp3&direction=next
GET /audio/<station>/<filename>
GET /download/<station>/<filename>
POST /logout                   # CSRF token required
```

Use the authenticated browser's developer console for API inspection, for example `fetch('/api/status').then(r => r.json()).then(console.log)`. Audio supports conditional and byte Range requests. Bad dates/stations/times return 400, missing audio 404, unauthorized API/audio 401, temporarily unavailable storage 503. No endpoint accepts a filesystem path.

Logs use timestamp, service/station and event fields. FFmpeg diagnostics retain its own format; Docker prefixes them with the station service. Supervisor and cleaner timestamps use EAT; API/UI timestamps include `+03:00`. Docker's own log timestamps may be UTC. Docker rotates each service's logs at 10 MB × 5 files. Do not put credentials in stream URL query strings: FFmpeg can include a failed URL in diagnostics.

## Stop and update

```bash
# Stop gracefully (interrupts capture; finalized current segments are retained):
docker compose stop
# Resume:
docker compose up -d
# Remove containers/networks, preserve recordings and auth state:
docker compose down
```

For an existing deployment, back up its configuration and recordings first (below). Update the checkout using your normal Git workflow; do not overwrite a customized `.env` with the example. Compare new example keys and merge them manually. For a first upgrade from the old flat-layout recorder:

```bash
cd /opt/radio-logger
# After backing up and updating the checkout:
docker compose config --quiet
docker compose build --pull
# Stop existing recorders and cleaner before migrating legacy files.
# If the old deployment uses different service names, stop those writers too.
docker compose stop radio-one capital-radio east-africa-radio radio-retention-cleaner
sudo chown -R 10001:10001 recordings
# Dry run first; this command uses the new recorder image but does not start a recorder:
docker compose run --rm --no-deps --entrypoint python3 \
  --volume "$PWD/scripts:/scripts:ro" radio-retention-cleaner \
  /scripts/migrate_recordings.py /recordings
# Apply only after reviewing the dry-run output:
docker compose run --rm --no-deps --entrypoint python3 \
  --volume "$PWD/scripts:/scripts:ro" radio-retention-cleaner \
  /scripts/migrate_recordings.py /recordings --apply
# Create/reset credentials if this is the first authenticated version:
docker compose run --rm -it --no-deps \
  --user "$(id -u):$(id -g)" --entrypoint python \
  --volume "$PWD:/setup" --workdir /setup \
  radio-archive scripts/configure.py
docker compose up -d
docker compose ps
```

The migration reads only recognized flat MP3 filenames, verifies audio, refuses collisions, and preserves malformed/unreadable originals. It assumes legacy filenames were EAT: **verify the original host's timezone before migration**. It does not guess or relabel incorrectly timestamped recordings. Existing flat files are otherwise ignored by the new browser and retention. Recovered/imported files are marked for operator review. The migration can be repeated safely after correcting errors.

For later updates with the dated layout already in place:

```bash
cd /opt/radio-logger
git pull --ff-only
docker compose config --quiet
docker compose build --pull
# Web-only changes require no recorder interruption:
docker compose up -d --no-deps radio-archive
# When the recorder image/configuration changes, update one station at a time:
docker compose up -d --no-deps radio-one
docker compose exec radio-one /usr/local/bin/healthcheck.sh
docker compose up -d --no-deps capital-radio
docker compose exec capital-radio /usr/local/bin/healthcheck.sh
docker compose up -d --no-deps east-africa-radio
docker compose exec east-africa-radio /usr/local/bin/healthcheck.sh
docker compose up -d --no-deps radio-retention-cleaner
```

Wait for each station to produce audio before updating the next. Updates/restarts create a short interruption; this single-host design cannot promise gap-free upgrades. Explicitly stop old differently named containers so there is one writer per station. Keep the previous release and image IDs for rollback; recorder data remains outside images.

## Reset administrator credentials

Run the same `scripts/configure.py` Compose command from installation and then:

```bash
docker compose up -d --no-deps --force-recreate radio-archive
```

It rotates the session secret as well as the password hash, invalidating existing sessions. Manual hash changes also invalidate sessions via the credential fingerprint. There is no unauthenticated reset endpoint. Sessions expire after eight hours of inactivity. CSRF applies to login and logout. Login attempts are limited to ten per source IP and 100 globally per 15-minute window, across workers and restarts. Behind a reverse proxy, all callers may share the proxy's source IP and therefore its quota; configure additional per-client throttling at your trusted proxy if needed. Spoofable forwarded IP headers are deliberately ignored.

## Retention, backup and restore

`RETENTION_DAYS=153`. The independent cleaner runs immediately and every 12 hours. It deletes only recognized finalized MP3s and their sidecars inside the exact dated station layout when **both filename time and modification time** are older than the cutoff. Recent restores are protected. It skips `.work`, hidden folders, malformed names and symlinks. Failed cleanup logs an error and retries on its next schedule; recorders continue. Invalid crash remnants in staging are preserved for investigation and require administrator review, not automatic deletion. Empty date directories are harmless and retained.

```bash
# Non-destructive retention preview:
docker compose run --rm --no-deps radio-retention-cleaner --dry-run
# Run a normal production cleanup once (deletes expired finalized recordings):
docker compose run --rm --no-deps radio-retention-cleaner --once
```

Back up to a **different disk/server**, preserving permissions and modification times. A live incremental backup copies closed archive files safely; exclude staging/status, and run it again after ongoing segments close. Example with a separately mounted backup disk:

```bash
sudo install -d -m 0700 /mnt/radio-backup/config /mnt/radio-backup/recordings
sudo rsync -a --exclude='.work/' --exclude='.status.json' --exclude='.metadata-*' \
  recordings/ /mnt/radio-backup/recordings/
sudo cp .env docker-compose.yml /mnt/radio-backup/config/
sudo chmod 600 /mnt/radio-backup/config/.env
git rev-parse HEAD | sudo tee /mnt/radio-backup/config/revision.txt
```

Keep the source/release and image digests too. Encrypt backups containing `.env` or sensitive broadcast material. Auth rate counters need not be backed up; they can reset on disaster recovery.

Restore onto a stopped deployment:

```bash
docker compose stop
sudo rsync -a /mnt/radio-backup/recordings/ recordings/
sudo cp /mnt/radio-backup/config/.env .env
sudo chmod 600 .env
sudo chown -R 10001:10001 recordings
docker compose config --quiet
docker compose up -d
```

Restore the appropriate application release before starting. Review expired restored files with cleaner dry-run before enabling cleanup if you need to retain them longer. Do not overwrite current recordings from an older backup without reviewing collisions. Test restoration periodically.

## Storage planning

At 128 kb/s, one station uses about 1.3824 GB/day; three use 4.1472 GB/day. For 153 days, allow **about 634.5 GB (591 GiB)** of MP3 audio plus metadata, filesystem overhead, logs, operating system, backups and free-space headroom. At 64 kb/s the audio estimate halves. There are approximately 66,096 full-interval MP3s plus sidecars; reconnects increase file count. Provision at least 20–30% extra and monitor actual growth. A 1 TB dedicated recording volume is a practical starting point for the default bitrate, subject to filesystem overhead and workload. Disk warnings do not shorten retention automatically or delete recordings early.

## Troubleshooting and catalog rebuild

**Stream failure:** check station logs, DNS, outbound firewall, URL changes and upstream availability. HTTP 200 alone does not prove audio is arriving. Probe from the recorder image:

```bash
docker compose exec radio-one sh -c \
  'ffprobe -v error -rw_timeout 15000000 -analyzeduration 1000000 -probesize 32768 -show_entries stream=codec_name,sample_rate,bit_rate -of json "$STREAM_URL"'
```

If a station repeatedly exits, also check writable mount ownership, free disk space, MP3 encoder availability (`docker compose exec radio-one ffmpeg -encoders`) and corrupt staging files. Do not fix permissions by running privileged containers. If a stale instance holds the writer lock, stop that instance rather than removing the lock file while it is running.

**Missing recordings:** verify selected EAT date/station, use gap reporting, examine supervisor logs and station directory, inspect `.work` for interrupted runs, and allow the current interval to close. Old flat files require the migration. A service cannot recover broadcasts missed while the upstream or host was unavailable. Never rename partial files to imply complete coverage.

**No playback:** confirm authentication, browser support, network response status, Range responses, and that the proxy passes cookies/Range headers. Native playback may require a user click. A file removed by retention can disappear between listing and playback; browse again. Inspect duration sidecars when Go To Time reports partial coverage.

**Rebuild index:** there is no persistent archive index to rebuild. Every search reads that day's source files. Restore/migrate valid dated MP3s and refresh the browser. Missing JSON sidecars remain visible with unverified duration. A malformed sidecar does not hide its MP3. Do not delete `archive-state` to “reindex”; it contains login throttling state, not recordings.

## Production acceptance checklist

- [ ] `docker compose config --quiet`, image builds and service startup succeed on the Linux deployment host.
- [ ] All three stations produce growing valid MP3 audio; healthchecks become healthy.
- [ ] Observe **two complete ten-minute boundaries**, including a partial startup interval, and confirm EAT filenames and decoded durations.
- [ ] Exercise a station interruption/restart; other stations continue and the failed one recovers.
- [ ] Stop/restart archive and cleaner individually while confirming recorder output continues.
- [ ] Verify midnight date folders and Next/continuous playback across dates.
- [ ] Sign in over HTTPS; unsigned page/API/audio/download requests cannot access recordings; logout and credential rotation invalidate sessions.
- [ ] Confirm Go To Time selects the right file/offset, seek returns 206, playback/download work, and autoplay fallback is clear.
- [ ] Confirm Warning/Offline on a stopped recorder and a meaningful gap/partial interval afterward.
- [ ] Inspect archive mount read-only state: `docker inspect "$(docker compose ps -q radio-archive)" --format '{{json .Mounts}}'`; `/recordings` must have `RW:false`.
- [ ] Run cleaner `--dry-run`; use only disposable data for destructive tests. Never test retention by aging production recordings.
- [ ] Review invalid/traversal/symlink tests on Linux, storage warning thresholds, backup and restore.
- [ ] Configure external alerts for health failures, no recent files, full disk, cleanup failures and host availability. Dashboard polling alone is not alert delivery.
- [ ] Check NTP, firewall, TLS renewals, dependency/image security updates and disk/inode monitoring.

See [verification results and remaining checks](docs/VERIFICATION.md). This is a deployable implementation, not a claim of completed production soak testing.
