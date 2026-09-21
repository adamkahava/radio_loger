FROM python:3.13-alpine
RUN apk add --no-cache tzdata \
    && addgroup -g 10001 radio && adduser -D -H -u 10001 -G radio radio \
    && mkdir /state && chown 10001:10001 /state
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV TZ=Africa/Dar_es_Salaam PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--threads", "4", "--timeout", "60", "--worker-tmp-dir", "/tmp", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
