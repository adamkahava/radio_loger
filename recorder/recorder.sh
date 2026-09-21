#!/usr/bin/env bash

set -u

STATION="${STATION:?STATION required}"
STREAM_URL="${STREAM_URL:?STREAM_URL required}"
SEGMENT_TIME="${SEGMENT_TIME:-600}"

BASE="/recordings/${STATION}"

mkdir -p "$BASE"

echo "======================================"
echo "Radio Logger"
echo "Station:  $STATION"
echo "Interval: $SEGMENT_TIME seconds"
echo "Started:  $(date)"
echo "======================================"

while true
do

    echo "$(date '+%Y-%m-%d %H:%M:%S') Starting recorder: $STATION"

    ffmpeg \
        -hide_banner \
        -loglevel warning \
        -nostdin \
        -reconnect 1 \
        -reconnect_streamed 1 \
        -reconnect_at_eof 1 \
        -reconnect_delay_max 10 \
        -rw_timeout 30000000 \
        -i "$STREAM_URL" \
        -map 0:a:0 \
        -vn \
        -c:a copy \
        -f segment \
        -segment_time "$SEGMENT_TIME" \
        -segment_atclocktime 1 \
        -reset_timestamps 1 \
        -strftime 1 \
        "${BASE}/${STATION}_%Y-%m-%d_%H-%M-%S.mp3"

    EXIT=$?

    echo "$(date '+%Y-%m-%d %H:%M:%S') FFmpeg stopped."
    echo "Exit code: $EXIT"
    echo "Restarting in 5 seconds..."

    sleep 5

done