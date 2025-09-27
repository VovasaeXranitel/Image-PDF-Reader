from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import tempfile
import os
import shutil
from typing import Optional
import subprocess
import logging

from PIL import ImageFile  # type: ignore
ImageFile.LOAD_TRUNCATED_IMAGES = True

@dataclass
class OcrSimpleOptions:
    languages: str = "rus+eng"
    psm: Optional[int] = None  # fallback 6
    oem: Optional[int] = None  # fallback 3 (auto)
    oversample: int = 300
    optimize: int = 0
    # whitelist/blacklist НЕ используются — оставлены для совместимости интерфейса
    whitelist: Optional[str] = None
    blacklist: Optional[str] = None
    preserve_spaces: bool = False  # игнорируется
    clean: bool = False


def _ensure_ghostscript() -> bool:
    for name in ["gswin64c", "gswin32c", "gs"]:
        if shutil.which(name):
            return True
    pf = os.environ.get("ProgramFiles", r"C:\\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\\Program Files (x86)")
    for base in [pf, pf86]:
        gs_root = Path(base) / "gs"
        if not gs_root.exists():
            continue
        versions = sorted(gs_root.glob("gs*"), reverse=True)
        for v in versions:
            for exe in ["gswin64c.exe", "gswin32c.exe", "gs.exe"]:
                cand = v / "bin" / exe
                if cand.exists():
                    os.environ.setdefault("PATH", "")
                    if str(cand.parent) not in os.environ["PATH"]:
                        os.environ["PATH"] = f"{cand.parent}{os.pathsep}" + os.environ["PATH"]
                    return True
    return False


def _ensure_tesseract() -> bool:
    if shutil.which("tesseract"):
        return True
    tdir = Path(os.environ.get("ProgramFiles", r"C:\\Program Files")) / "Tesseract-OCR"
    exe = tdir / "tesseract.exe"
    if exe.exists():
        os.environ.setdefault("PATH", "")
        if str(tdir) not in os.environ["PATH"]:
            os.environ["PATH"] = f"{tdir}{os.pathsep}" + os.environ["PATH"]
        return shutil.which("tesseract") is not None
    return False


def _fallback_tesseract(pdf_path: Path, languages: str, psm: int, oem: int) -> str:
    # Прямой вызов tesseract (он сам разберёт страницы). Менее функционально, но даёт шанс получить текст.
    tess = shutil.which("tesseract")
    if not tess:
        return ""
    cmd = [tess, str(pdf_path), "stdout", "-l", languages, "--oem", str(oem), "--psm", str(psm)]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=300)
        return out.decode("utf-8", errors="ignore")
    except Exception as e:  # noqa
        return f"[OCR_FALLBACK_ERROR] {e}"


def ocr_pdf_bytes_to_text(data: bytes, options: OcrSimpleOptions, req_id: str | None = None) -> str:
    import ocrmypdf  # type: ignore
    from ocrmypdf.exceptions import SubprocessOutputError  # type: ignore
    logger = logging.getLogger("ocr_pipeline")
    start_time = time.time() if 'time' in globals() else __import__('time').time()

    tmp_dir = Path(tempfile.mkdtemp(prefix="ocr_bytes_"))
    if req_id:
        logger.info(f"[{req_id}] tmp_dir={tmp_dir}")
    input_path = tmp_dir / "input.pdf"
    output_pdf = tmp_dir / "out.pdf"
    sidecar = tmp_dir / "out.txt"
    input_path.write_bytes(data)

    if req_id:
        logger.info(f"[{req_id}] Ensuring system dependencies (tesseract/ghostscript)...")
    _ensure_ghostscript()
    _ensure_tesseract()

    psm = options.psm if options.psm is not None else 6
    oem = options.oem if options.oem is not None else 3

    kwargs = dict(
        input_file=str(input_path),
        output_file=str(output_pdf),
        language=options.languages,
        force_ocr=True,
        rotate_pages=False,  # отключаем для устойчивости
        deskew=False,        # отключаем для скорости/устойчивости
        sidecar=str(sidecar),
        progress_bar=False,
        optimize=options.optimize,
        oversample=options.oversample,
        clean=False,         # не дергаем unpaper
        tesseract_pagesegmode=psm,
        tesseract_oem=oem,
    )

    text_content = ""
    primary_error = None
    try:
        if req_id:
            logger.info(f"[{req_id}] Starting primary ocrmypdf run psm={psm} oem={oem} oversample={options.oversample} size={len(data)} bytes")
        try:
            ocrmypdf.ocr(**kwargs)
        except SubprocessOutputError as se:
            primary_error = f"SubprocessOutputError: {se}"; logger.warning(f"[{req_id}] Primary OCR failed: {primary_error}")
        except Exception as e:  # noqa
            primary_error = f"Unexpected OCR error: {e}"; logger.exception(f"[{req_id}] Primary OCR exception")

        if sidecar.exists():
            text_content = sidecar.read_text(encoding="utf-8", errors="ignore")
            if req_id:
                logger.info(f"[{req_id}] Primary OCR success chars={len(text_content)}")
        elif primary_error:
            if req_id:
                logger.info(f"[{req_id}] Running fallback direct tesseract...")
            fallback_text = _fallback_tesseract(input_path, options.languages, psm, oem)
            if fallback_text.strip():
                text_content = fallback_text
                if req_id:
                    logger.info(f"[{req_id}] Fallback success chars={len(text_content)}")
            else:
                text_content = f"[OCR_ERROR] {primary_error}"
                if req_id:
                    logger.error(f"[{req_id}] Fallback failed; returning error marker")
    finally:
        # очистка
        try:
            for p in list(tmp_dir.glob("*")):
                try:
                    p.unlink()
                except Exception:
                    pass
            tmp_dir.rmdir()
        except Exception:
            pass
        try:
            duration = __import__('time').time() - start_time
            if req_id:
                logger.info(f"[{req_id}] Cleanup done; total_time={duration:.2f}s final_chars={len(text_content)}")
        except Exception:
            pass
    return text_content
