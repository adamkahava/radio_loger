#!/usr/bin/env bash

STATION="${STATION:?STATION required}"

DIR="/recordings/${STATION}"

FILE=$(find "$DIR" \
    -type f \
    -name "*.mp3" \
    -mmin -15 \
    -size +10k \
    -print \
    -quit)

if [ -z "$FILE" ]; then
    echo "ERROR: No recent valid recording for $STATION"
    exit 1
fi

echo "OK: $FILE"
exit 0