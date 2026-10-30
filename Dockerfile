# OSINT Hunter Hub — 24/7 hosting ke liye (Northflank / Oracle / VPS / Render)
# Ek command:  docker build -t osint-hub . && docker run -p 8000:8000 --env-file .env osint-hub
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=10000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 10000

HEALTHCHECK --interval=5m --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/ping" || exit 1

CMD ["python", "bot.py"]
