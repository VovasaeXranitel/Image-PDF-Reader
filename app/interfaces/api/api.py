from __future__ import annotations
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from ...infrastructure.ocr.simple_text import ocr_pdf_bytes_to_text, OcrSimpleOptions
from ...infrastructure.deps.deps import assert_ready, dependency_report_dict, ensure_dependencies
import threading
import time
import os
import uuid
import logging

# ЖЁСТКАЯ КОНФИГУРАЦИЯ OCR (меняется только в коде) — whitelist убран
_OCR_CONFIG = OcrSimpleOptions(
    languages="rus+eng",
    psm=6,
    oem=1,
    oversample=350,
    optimize=0,
    # whitelist удалён намеренно: вызывал ошибки read_params_file
    preserve_spaces=True,
)

# Ограничение размера (можно переопределить переменной среды OCR_MAX_PDF_MB)
_MAX_MB = int(os.environ.get("OCR_MAX_PDF_MB", "32"))
_MAX_BYTES = _MAX_MB * 1024 * 1024

_DEP_CHECK_LOCK = threading.Lock()
_DEPS_OK = False
_DEPS_REPORT: dict | None = None

# Настройка базового логирования (если не настроено извне)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
api_logger = logging.getLogger("api")

class OcrTextResponse(BaseModel):
    success: bool
    text: str
    chars: int
    languages: str

app = FastAPI(
    title="Image PDF Reader OCR API",
    version="1.0.1",
    description=(
        "Простой статичный OCR сервис: один маршрут /ocr, принимает PDF (application/pdf) байтами и возвращает распознанный текст.\n"
        "Настройки Tesseract жёстко заданы. Автопроверка зависимостей Tesseract/Ghostscript + языков rus/eng.\n"
        f"Лимит размера PDF: <= {_MAX_MB} MB (настраивается OCR_MAX_PDF_MB)."
    ),
    docs_url="/swagger",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

@app.on_event("startup")
async def _startup_check():
    global _DEPS_OK, _DEPS_REPORT
    with _DEP_CHECK_LOCK:
        try:
            ensure_dependencies()
            assert_ready()
            _DEPS_OK = True
        except Exception:
            _DEPS_OK = False
        _DEPS_REPORT = dependency_report_dict()

# Синхронная функция (FastAPI выполнит её в thread pool) чтобы не блокировать event loop.
@app.post("/ocr", response_model=OcrTextResponse, summary="Распознать PDF и вернуть текст")
def ocr_endpoint(data: bytes = Body(..., media_type="application/pdf", description="Сырые байты PDF")):
    req_id = uuid.uuid4().hex[:8]
    start = time.time()
    api_logger.info(f"[{req_id}] /ocr request received size={len(data) if data else 0} bytes")
    if not data or len(data) < 10:
        api_logger.warning(f"[{req_id}] invalid or empty payload")
        raise HTTPException(status_code=400, detail="Пустые или повреждённые данные PDF")
    if len(data) > _MAX_BYTES:
        api_logger.warning(f"[{req_id}] payload too large: {len(data)} > {_MAX_BYTES}")
        raise HTTPException(status_code=413, detail=f"Размер PDF превышает лимит {_MAX_MB} MB")

    global _DEPS_OK, _DEPS_REPORT
    if not _DEPS_OK:
        api_logger.info(f"[{req_id}] dependencies not ready, retrying detection...")
        with _DEP_CHECK_LOCK:
            if not _DEPS_OK:
                try:
                    ensure_dependencies(); assert_ready(); _DEPS_OK = True; _DEPS_REPORT = dependency_report_dict()
                    api_logger.info(f"[{req_id}] dependencies OK after retry")
                except Exception:
                    _DEPS_REPORT = dependency_report_dict()
                    api_logger.error(f"[{req_id}] dependencies still missing: {_DEPS_REPORT}")
    if not _DEPS_OK:
        raise HTTPException(status_code=503, detail={
            "error": "OCR dependencies not ready",
            "dependencies": _DEPS_REPORT,
        })

    api_logger.info(f"[{req_id}] starting OCR pipeline ...")
    try:
        text = ocr_pdf_bytes_to_text(data, _OCR_CONFIG, req_id=req_id)
    except Exception as e:  # крайний непойманный случай
        api_logger.exception(f"[{req_id}] unhandled OCR exception")
        raise HTTPException(status_code=500, detail=f"Internal OCR failure: {e}")
    duration = time.time() - start
    api_logger.info(f"[{req_id}] OCR finished chars={len(text)} time={duration:.2f}s")
    return OcrTextResponse(success=True, text=text, chars=len(text), languages=_OCR_CONFIG.languages)
