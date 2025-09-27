# Syntax docker/dockerfile:1
# Минимальный образ с предустановленными зависимостями OCR
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# Устанавливаем системные зависимости для ocrmypdf и Tesseract
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-rus \
    tesseract-ocr-eng \
    ghostscript \
    qpdf \
    pngquant \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Создаём рабочую директорию
WORKDIR /app

# Копируем зависимости
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Копируем только нужный код (минимизация слоя)
COPY app ./app
COPY run_api.py ./

# Проверка зависимостей на этапе build (необязательно, но полезно для раннего фейла)
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

