FROM alpine:3.22
RUN apk add --no-cache ffmpeg bash tzdata python3 ca-certificates \
    && addgroup -g 10001 radio && adduser -D -H -u 10001 -G radio radio
WORKDIR /app
COPY *.py /app/
COPY recorder.sh healthcheck.sh /usr/local/bin/
RUN chmod 755 /usr/local/bin/recorder.sh /usr/local/bin/healthcheck.sh
ENV TZ=Africa/Dar_es_Salaam PYTHONUNBUFFERED=1
USER 10001:10001
ENTRYPOINT ["/usr/local/bin/recorder.sh"]
