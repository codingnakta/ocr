# 필기봇 OCR Studio — single-container image (used by Render, see render.yaml)
#
# One container runs everything:
#   - Next.js (public port $PORT)  ->  proxies /api to
#   - FastAPI on 127.0.0.1:8000 with the embedded OCR worker
#   - SQLite database + uploaded files on the persistent disk mounted at /data
# For a multi-container setup (PostgreSQL, separate worker) use docker-compose.yml.

ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.13-slim

# Also used by the Hugging Face Space workflow (.github/workflows/deploy-hf-space.yml).
# Hugging Face runs the container as an unprivileged user (uid 1000) without a
# persistent disk, so every directory written at runtime is world-writable here.

# ---------- frontend build ----------
FROM ${NODE_IMAGE} AS web-build
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
COPY frontend/scripts ./scripts
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# /api rewrites are fixed at build time: the API runs in the same container.
ENV BACKEND_URL=http://127.0.0.1:8000 NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM ${NODE_IMAGE} AS node-runtime

# ---------- runtime ----------
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
# Node.js runtime for the Next.js standalone server (glibc build, matches this image)
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node

WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install -r requirements.txt \
    && python -c "import cv2, onnxruntime, pymupdf, rapidocr; print('imports ok')" \
    && node --version
COPY backend/app ./app
COPY backend/scripts ./scripts
# Local OCR models (Korean recognizer from RapidOCR's registry when reachable;
# detector/orientation models from PyPI). A failed download does not fail the build.
RUN python scripts/fetch_assets.py models || true

COPY --from=web-build /web/.next/standalone /app/web
COPY --from=web-build /web/.next/static /app/web/.next/static
COPY --from=web-build /web/public /app/web/public
COPY deploy/start.sh /app/start.sh
RUN chmod +x /app/start.sh \
    && mkdir -p /data /app/backend/models_data /app/web/.next/cache \
    && chmod -R a+rwX /data /app/backend/models_data /app/web/.next

# Defaults for an internet deployment: login required, cost caps on. Only
# ANTHROPIC_API_KEY and ADMIN_PASSWORD must be provided (Render / Hugging Face secrets).
ENV APP_ENV=production \
    AUTH_MODE=password \
    ADMIN_USERNAME=admin \
    COOKIE_SECURE=true \
    DAILY_COST_LIMIT_USD=10 \
    DEFAULT_JOB_COST_LIMIT_USD=5 \
    RETENTION_DAYS=14 \
    DATABASE_URL=sqlite:////data/pilgibot.db \
    STORAGE_DIR=/data/files \
    WORK_DIR=/data/work \
    LOCAL_OCR_MODEL_DIR=/app/backend/models_data \
    EMBEDDED_WORKER=true \
    PORT=10000

EXPOSE 10000
CMD ["/app/start.sh"]
