from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import tempfile
import os
import shutil
from typing import Optional, List

from PIL import ImageFile  # type: ignore
ImageFile.LOAD_TRUNCATED_IMAGES = True

@dataclass
class OcrSimpleOptions:
    languages: str = "rus"
    psm: Optional[int] = None
    oem: Optional[int] = None
    oversample: int = 300
    optimize: int = 0
    whitelist: Optional[str] = None
    blacklist: Optional[str] = None
    preserve_spaces: bool = False
    clean: bool = False


def _ensure_ghostscript() -> bool:
    # Re-use environment; try which first
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


def ocr_pdf_bytes_to_text(data: bytes, options: OcrSimpleOptions) -> str:
    import ocrmypdf  # type: ignore
    # Подготовка временных путей
    tmp_dir = Path(tempfile.mkdtemp(prefix="ocr_bytes_"))
    input_path = tmp_dir / "input.pdf"
    output_pdf = tmp_dir / "out.pdf"  # результат нам не нужен, но ocrmypdf требует
    sidecar = tmp_dir / "out.txt"

    input_path.write_bytes(data)

    # Проверка зависимостей (мягко)
    _ensure_ghostscript()
    _ensure_tesseract()

    kwargs = dict(
        input_file=str(input_path),
        output_file=str(output_pdf),
        language=options.languages,
        force_ocr=True,
        rotate_pages=True,
        deskew=True,
        sidecar=str(sidecar),
        progress_bar=False,
        optimize=options.optimize,
        oversample=options.oversample,
        clean=options.clean,
    )
    if options.psm is not None:
        kwargs["tesseract_pagesegmode"] = options.psm
    else:
        kwargs["tesseract_pagesegmode"] = 6
    if options.oem is not None:
        kwargs["tesseract_oem"] = options.oem
    else:
        kwargs["tesseract_oem"] = 1

    tess_cfg: List[str] = []
    if options.whitelist:
        tess_cfg.append(f"tessedit_char_whitelist={options.whitelist}")
    if options.blacklist:
        tess_cfg.append(f"tessedit_char_blacklist={options.blacklist}")
    if options.preserve_spaces:
        tess_cfg.append("preserve_interword_spaces=1")
    if tess_cfg:
        kwargs["tesseract_config"] = " ".join(tess_cfg)

    text_content = ""
    try:
        ocrmypdf.ocr(**kwargs)
        if sidecar.exists():
            text_content = sidecar.read_text(encoding="utf-8", errors="ignore")
    finally:
        # Удаляем временные файлы
        try:
            for p in tmp_dir.iterdir():
                try:
                    p.unlink()
                except Exception:
                    pass
            tmp_dir.rmdir()
        except Exception:
            pass
    return text_content

