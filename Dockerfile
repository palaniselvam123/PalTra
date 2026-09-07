# Production image for the ORB FastAPI backend (bot, scanner, paper engine).
# Bind 0.0.0.0:3000 so Fly.io's internal_port matches this process.
#
# Secrets stay out of the image. Set them on the machine, not in a baked .env:
#   fly secrets set ENCRYPTION_KEY=... FRONTEND_ORIGIN=https://<app>.fly.dev
#
# SQLite lives on /data so a Fly volume can keep the ledger across deploys.

FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=3000 \
    DATABASE_URL=sqlite+aiosqlite:////data/trading.db \
    DEFAULT_MODE=paper

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend/ /app/

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/api/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3000"]
