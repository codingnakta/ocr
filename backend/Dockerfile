# 필기봇 OCR Studio — backend (API + worker share this image)
ARG PYTHON_IMAGE=python:3.13-slim
FROM ${PYTHON_IMAGE} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# No apt packages needed: the onnxruntime and opencv-python-headless wheels are
# self-contained (verified by importing them in this image during the build).
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt \
    && python -c "import cv2, onnxruntime, pymupdf, rapidocr; print('imports ok', cv2.__version__, onnxruntime.__version__)"

COPY app ./app
COPY scripts ./scripts

# Local OCR models: Korean recognizer from RapidOCR's registry (ModelScope) when
# reachable, plus the PP-OCRv5 detector/orientation models from PyPI (always works).
# A failed Korean download does not fail the build; the app reports it in Settings.
RUN python scripts/fetch_assets.py models || true

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data && chown -R app:app /data /app
USER app

ENV STORAGE_DIR=/data/files \
    WORK_DIR=/data/work \
    LOCAL_OCR_MODEL_DIR=/app/models_data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
