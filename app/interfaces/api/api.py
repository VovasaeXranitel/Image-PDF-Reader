from __future__ import annotations
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from ...infrastructure.ocr.simple_text import ocr_pdf_bytes_to_text, OcrSimpleOptions
from ...infrastructure.deps.deps import assert_ready, dependency_report_dict, ensure_dependencies
import threading

# ЖЁСТКАЯ КОНФИГУРАЦИЯ OCR (меняется только в коде)
_OCR_CONFIG = OcrSimpleOptions(
    languages="rus+eng",
    psm=6,          # один блок текста
    oem=1,          # LSTM
    oversample=350, # повышение DPI
    optimize=0,     # без оптимизации (устойчивее к битым JPEG)
    whitelist="абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ0123456789.,:-()/%№«»\"\\/",
    preserve_spaces=True,
)

_DEP_CHECK_LOCK = threading.Lock()
_DEPS_OK = False
_DEPS_REPORT: dict | None = None

class OcrTextResponse(BaseModel):
    success: bool
    text: str
    chars: int
    languages: str

app = FastAPI(
    title="Image PDF Reader OCR API",
    version="1.0.0",
    description=(
        "Простой статичный OCR сервис: один маршрут /ocr, принимает PDF (application/pdf) байтами и возвращает распознанный текст.\n"
        "Настройки Tesseract зашиты в коде (_OCR_CONFIG). При старте выполняется авто-проверка зависимостей Tesseract/Ghostscript."
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
            ensure_dependencies()  # кеширование
            assert_ready()
            _DEPS_OK = True
        except Exception:
            _DEPS_OK = False
        _DEPS_REPORT = dependency_report_dict()

@app.post("/ocr", response_model=OcrTextResponse, summary="Распознать PDF и вернуть текст")
async def ocr_endpoint(data: bytes = Body(..., media_type="application/pdf", description="Сырые байты PDF")):
    if not data or len(data) < 10:
        raise HTTPException(status_code=400, detail="Пустые или повреждённые данные PDF")
    if not _DEPS_OK:
        # Возвращаем структурированную информацию о проблеме
        raise HTTPException(status_code=503, detail={
            "error": "OCR dependencies not ready",
            "dependencies": _DEPS_REPORT,
        })
    text = ocr_pdf_bytes_to_text(data, _OCR_CONFIG)
    return OcrTextResponse(success=True, text=text, chars=len(text), languages=_OCR_CONFIG.languages)
