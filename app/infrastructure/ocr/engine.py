from __future__ import annotations
import os
import platform
import shutil
import time
from pathlib import Path
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor
import tempfile

from PyPDF2 import PdfReader, PdfWriter

from ...domain.entities import (
    OcrTask,
    OcrTaskResult,
    OcrPageResult,
)


def ensure_ghostscript() -> bool:
    for name in ["gswin64c", "gswin32c", "gs"]:
        p = shutil.which(name)
        if p:
            os.environ["OCRMYPDF_GS"] = p
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
                    os.environ["OCRMYPDF_GS"] = str(cand)
                    os.environ["PATH"] = f"{cand.parent}{os.pathsep}" + os.environ.get("PATH", "")
                    return True
    return False


def ensure_tesseract() -> bool:
    if shutil.which("tesseract"):
        return True
    tesseract_dir = Path(os.environ.get("ProgramFiles", r"C:\\Program Files")) / "Tesseract-OCR"
    exe = tesseract_dir / "tesseract.exe"
    if exe.exists():
        os.environ["PATH"] = f"{tesseract_dir}{os.pathsep}" + os.environ.get("PATH", "")
        return shutil.which("tesseract") is not None
    return False


def _split_pdf(input_file: Path) -> List[Path]:
    reader = PdfReader(str(input_file))
    pages: List[Path] = []
    for i, page in enumerate(reader.pages):
        writer = PdfWriter()
        writer.add_page(page)
        temp_path = input_file.parent / f"__page_{i+1}.pdf"
        with open(temp_path, "wb") as f:
            writer.write(f)
        pages.append(temp_path)
    return pages


def _merge_pdfs(page_paths: List[Path], output_path: Path) -> None:
    writer = PdfWriter()
    for p in page_paths:
        r = PdfReader(str(p))
        writer.add_page(r.pages[0])
    with open(output_path, "wb") as f:
        writer.write(f)


def _ocr_single_pdf(
    input_path: Path,
    output_pdf: Path,
    languages: str,
    options: dict,
) -> None:
    from PIL import ImageFile  # type: ignore
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    import ocrmypdf  # type: ignore

    kwargs = dict(
        input_file=str(input_path),
        output_file=str(output_pdf),
        language=languages,
        deskew=True,
        rotate_pages=True,
        force_ocr=True,
        sidecar=None,
        progress_bar=False,
        optimize=options.get("optimize", 0),
        oversample=options.get("oversample", 300),
        output_type=options.get("output_type", "pdf"),
        clean=options.get("clean", False),
    )
    psm = options.get("psm")
    if psm is not None:
        kwargs["tesseract_pagesegmode"] = psm
    oem = options.get("oem")
    if oem is not None:
        kwargs["tesseract_oem"] = oem
    tess_cfg_parts = []
    if options.get("whitelist"):
        tess_cfg_parts.append(f"tessedit_char_whitelist={options['whitelist']}")
    if options.get("blacklist"):
        tess_cfg_parts.append(f"tessedit_char_blacklist={options['blacklist']}")
    if options.get("preserve_spaces"):
        tess_cfg_parts.append("preserve_interword_spaces=1")
    if tess_cfg_parts:
        kwargs["tesseract_config"] = " ".join(tess_cfg_parts)
    if "tesseract_pagesegmode" not in kwargs:
        kwargs["tesseract_pagesegmode"] = 6
    if "tesseract_oem" not in kwargs:
        kwargs["tesseract_oem"] = 1
    ocrmypdf.ocr(**kwargs)


def run_ocr_task(task: OcrTask) -> OcrTaskResult:
    start = time.time()
    pages_results: List[OcrPageResult] = []
    success = True
    error: Optional[str] = None

    output_dir = task.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    final_pdf = task.output_pdf_path()
    sidecar = task.output_txt_path()

    # Если входные данные переданы как bytes — записываем во временный файл
    temp_input_file: Optional[Path] = None
    actual_input: Optional[Path] = task.input_file
    if actual_input is None and task.raw_bytes:
        try:
            # сохраняем именно в output_dir, чтобы потом не потерять при сборе
            temp_input_file = output_dir / f"__input_{task.task_id}.pdf"
            with open(temp_input_file, "wb") as f:
                f.write(task.raw_bytes)
            actual_input = temp_input_file
        except Exception as e:  # noqa
            from datetime import datetime
            return OcrTaskResult(
                task_id=task.task_id,
                input_file=None,
                output_pdf=final_pdf,
                output_txt=sidecar,
                pages=[],
                started_at=None,
                finished_at=None,
                success=False,
                error=f"Failed to materialize input bytes: {e}",
            )

    if platform.system() == "Windows":
        gs_ok = ensure_ghostscript()
        tess_ok = ensure_tesseract()
        if not (gs_ok and tess_ok):
            success = False
            errors = []
            if not gs_ok:
                errors.append("Ghostscript not found")
            if not tess_ok:
                errors.append("Tesseract not found")
            error = "; ".join(errors)
            from datetime import datetime
            return OcrTaskResult(
                task_id=task.task_id,
                input_file=actual_input,
                output_pdf=final_pdf,
                output_txt=sidecar,
                pages=[],
                started_at=datetime.fromtimestamp(start),
                finished_at=datetime.fromtimestamp(time.time()),
                success=False,
                error=error,
            )

    options_dict = {
        "psm": task.options.psm,
        "oem": task.options.oem,
        "oversample": task.options.oversample,
        "optimize": task.options.optimize,
        "output_type": task.options.output_type,
        "whitelist": task.options.whitelist,
        "blacklist": task.options.blacklist,
        "preserve_spaces": task.options.preserve_spaces,
        "clean": task.options.clean,
    }

    page_temp_files: List[Path] = []
    ocr_page_pdfs: List[Path] = []

    try:
        if actual_input is None:
            raise RuntimeError("No input file available for OCR")
        split_pages = _split_pdf(actual_input)
        page_temp_files.extend(split_pages)

        def _proc(idx: int, p: Path) -> OcrPageResult:
            t0 = time.time()
            out_p = p.with_name(p.stem + "__ocr.pdf")
            try:
                _ocr_single_pdf(p, out_p, task.options.languages, options_dict)
                dur = time.time() - t0
                return OcrPageResult(page_index=idx, pdf_path=out_p, text_path=None, success=True, duration_sec=dur)
            except Exception as e:  # noqa
                return OcrPageResult(page_index=idx, pdf_path=out_p, text_path=None, success=False, error=str(e), duration_sec=time.time()-t0)

        max_workers = max(1, min(task.options.max_workers, len(split_pages)))
        if task.options.parallel_pages and len(split_pages) > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_proc, i, p) for i, p in enumerate(split_pages)]
                for f in futures:
                    pages_results.append(f.result())
        else:
            for i, p in enumerate(split_pages):
                pages_results.append(_proc(i, p))

        successful_pages = [r for r in pages_results if r.success]
        if not successful_pages:
            success = False
            error = "All pages failed"
        else:
            ocr_page_pdfs = [r.pdf_path for r in successful_pages]
            _merge_pdfs(ocr_page_pdfs, final_pdf)
            # Извлечение текста
            try:
                reader = PdfReader(str(final_pdf))
                texts: List[str] = []
                for pg in reader.pages:
                    try:
                        texts.append(pg.extract_text() or "")
                    except Exception:
                        texts.append("")
                with open(sidecar, "w", encoding="utf-8") as f:
                    f.write("\n".join(texts))
            except Exception:
                pass

    except Exception as e:  # noqa
        success = False
        error = str(e)
    finally:
        for p in page_temp_files + ocr_page_pdfs:
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass
        # очищаем временный входной файл (оставляем final_pdf/sidecar)
        if temp_input_file and temp_input_file.exists():
            try:
                temp_input_file.unlink()
            except Exception:
                pass

    end = time.time()
    from datetime import datetime
    return OcrTaskResult(
        task_id=task.task_id,
        input_file=None if task.raw_bytes else actual_input,
        output_pdf=final_pdf,
        output_txt=sidecar if sidecar.exists() else None,
        pages=sorted(pages_results, key=lambda r: r.page_index),
        started_at=datetime.fromtimestamp(start),
        finished_at=datetime.fromtimestamp(end),
        success=success and all(r.success for r in pages_results if pages_results),
        error=error,
    )
