# Syntax docker/dockerfile:1
# Минимальный образ с предустановленными зависимостями OCR (multi-stage build)

# Stage 1: build wheels with build-time dependencies
FROM python:3.12-slim AS build

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
        make \
        pkg-config \
        libffi-dev \
        libssl-dev \
        libxml2-dev \
        libxslt1-dev \
        zlib1g-dev \
        libjpeg-dev \
        libpng-dev \
        libtiff5-dev \
        libopenjp2-7-dev \
        poppler-utils \
        qpdf \
        ghostscript \
        tesseract-ocr \
        tesseract-ocr-rus \
        tesseract-ocr-eng \
        pngquant \
        libmagic1 \
    ; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
# Build wheels for all requirements to avoid compiling in the runtime image
RUN python -m pip install --upgrade pip setuptools wheel && \
    python -m pip wheel --no-deps --wheel-dir /wheels -r requirements.txt

# Stage 2: runtime image with only runtime packages
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# Install only runtime system packages required by ocrmypdf and runtime
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        qpdf \
        ghostscript \
        tesseract-ocr \
        tesseract-ocr-rus \
        tesseract-ocr-eng \
        pngquant \
        poppler-utils \
        libmagic1 \
    ; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Copy prebuilt wheels from the build stage and install them
COPY --from=build /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*

# Copy application code
COPY app ./app
COPY run_api.py ./

# Optional early dependency check (keeps original behavior)
RUN python - <<'PY'
from app.infrastructure.deps.deps import ensure_dependencies, assert_ready
r = ensure_dependencies()
try:
    assert_ready()
    print('Dependencies OK:', r)
except Exception as e:
    raise SystemExit('DEPENDENCY CHECK FAILED: '+str(e))
PY

EXPOSE 8000

# Запуск uvicorn
CMD ["uvicorn", "run_api:app", "--host", "0.0.0.0", "--port", "8000"]
