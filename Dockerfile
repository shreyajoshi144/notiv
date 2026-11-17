# Build: docker build -t notiv .   Persistent data (SQLite, uploads, Whisper weights) lives in /data: mount a volume there.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg poppler-utils libreoffice-impress tesseract-ocr tesseract-ocr-eng fonts-liberation fonts-dejavu-core fonts-noto-core && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 notiv
WORKDIR /app
COPY requirements-prod.txt .
RUN pip install --no-cache-dir -r requirements-prod.txt
COPY . .
RUN chmod +x /app/docker-entrypoint.sh && mkdir -p /data && chown notiv:notiv /data
ENV PYTHONPATH=/app:/app/core:/app/backend PYTHONUNBUFFERED=1 NOTIV_ENV=production NOTIV_INPROCESS_WORKER=0 NOTIV_RATELIMIT_BACKEND=db \
    NOTIV_LECTURE_DB=/data/lectures.db NOTIV_STORAGE_DIR=/data/objects HF_HOME=/data/hf PORT=8000 \
    FORWARDED_ALLOW_IPS=*
# FORWARDED_ALLOW_IPS: hosting platforms terminate HTTPS at a proxy whose address is not 127.0.0.1. Without trusting its
# X-Forwarded-Proto the app sees http://, the browser sends an https:// Origin, and every write fails the origin check.
# Narrow it to the proxy's address range when you know it. (Client IPs for rate limits are governed by NOTIV_TRUST_PROXY.)
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8000'))"
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["sh", "-c", "exec uvicorn server.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers"]
# worker:  docker run ... notiv python -m core.lecture.worker
# migrate: docker run ... notiv python -m core.lecture.migrations upgrade
