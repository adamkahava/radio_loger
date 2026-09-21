FROM alpine:3.22

RUN apk add --no-cache \
    ffmpeg \
    bash \
    tzdata \
    findutils \
    coreutils

COPY recorder.sh /usr/local/bin/recorder.sh
COPY healthcheck.sh /usr/local/bin/healthcheck.sh

RUN chmod +x \
    /usr/local/bin/recorder.sh \
    /usr/local/bin/healthcheck.sh

ENTRYPOINT ["/usr/local/bin/recorder.sh"]