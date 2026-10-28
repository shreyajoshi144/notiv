# UNVERIFIED: not built in the authoring sandbox (no Docker daemon). Build: docker build -t notiv .
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg poppler-utils libreoffice-impress tesseract-ocr tesseract-ocr-eng fonts-liberation fonts-dejavu-core fonts-noto-core && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 notiv
WORKDIR /app
COPY requirements-prod.txt .
RUN pip install --no-cache-dir -r requirements-prod.txt
COPY . .
ENV PYTHONPATH=/app:/app/core:/app/backend PYTHONUNBUFFERED=1 NOTIV_ENV=production NOTIV_INPROCESS_WORKER=0 NOTIV_RATELIMIT_BACKEND=db
USER notiv
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
# worker:  docker run ... notiv python -m core.lecture.worker
# migrate: docker run ... notiv python -m core.lecture.migrations upgrade
