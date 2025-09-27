from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import tempfile
import shutil
from typing import Optional, List, Tuple
import subprocess
import logging
import re
import time
import hashlib
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from multiprocessing import cpu_count
import string
from PIL import Image, ImageEnhance  # type: ignore
from PIL import ImageFile  # type: ignore
from PIL import ImageOps  # type: ignore
from PIL import Image as PILImage  # type: ignore
ImageFile.LOAD_TRUNCATED_IMAGES = True
# Avoid DecompressionBombWarning for large scanned pages (adjust as appropriate)
# Setting to None disables the safety check; we set a high limit instead to be safer.
try:
    # Set a large but finite limit to avoid DecompressionBombWarning while keeping some safety
    PILImage.MAX_IMAGE_PIXELS = 200_000_000
except Exception:
    pass

try:
    from PyPDF2 import PdfReader  # type: ignore
except Exception:  # noqa
    PdfReader = None  # type: ignore

# ================== CONFIG CONSTANTS ==================
FAST_TEXT_ONLY: bool = True
RAW_RASTER_DPI: int = 400
MIN_IMG_W: int = 40
MIN_IMG_H: int = 40
UPSCALE_TINY: float = 3.0
ENABLE_MULTI_PSM: bool = True
PARALLEL_PAGE_WORKERS: int = max(2, min(8, cpu_count()))
PAGE_SCORE_THRESHOLD: float = 0.08
HIGH_DPI: int = 500
RE_RASTER_MAX_PAGES: int = 6
MIN_SECOND_PASS_IMPROVEMENT: float = 0.01
POSTFILTER_ENABLED: bool = True
CACHE_MAX: int = 10
DESKEW_MAX_DIM: int = 600

# ================== LRU CACHE ==================
_CACHE: "OrderedDict[str, Tuple[str, float]]" = OrderedDict()

def _cache_get(h: str) -> Tuple[str, float] | None:
    if h in _CACHE:
        _CACHE.move_to_end(h)
        return _CACHE[h]
    return None

def _cache_put(h: str, text: str, score: float) -> None:
    _CACHE[h] = (text, score)
    _CACHE.move_to_end(h)
    while len(_CACHE) > CACHE_MAX:
        _CACHE.popitem(last=False)

# ================== SCORING & EXTRACTION ==================
_cyrillic_re = re.compile(r"[А-Яа-яЁё]")
_latin_re = re.compile(r"[A-Za-z]")
_digit_re = re.compile(r"[0-9]")
_bad_re = re.compile(r"[�]")
_CLEAN_CONTROL_RE = re.compile(r"[\x00-\x09\x0B-\x0C\x0E-\x1F\x7F]+")
_CLEAN_DISALLOWED_RE = re.compile(r"[^0-9A-Za-zА-Яа-яЁё ]+")

def _score_text(text: str) -> float:
    if not text:
        return 0.0
    length = len(text)
    cyr = len(_cyrillic_re.findall(text))
    lat = len(_latin_re.findall(text))
    dig = len(_digit_re.findall(text))
    bad = len(_bad_re.findall(text))
    score = (cyr * 2 + dig * 0.5 + lat * 0.3) - bad * 5
    return score / max(50, length)

def _try_extract_pdf_text(pdf_path: Path, logger: logging.Logger, req_id: str | None) -> str:
    if PdfReader is None:
        return ""
    try:
        reader = PdfReader(str(pdf_path))
        parts: List[str] = []
        for page in reader.pages:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            if t:
                parts.append(t)
        extracted = "\n".join(parts)
        if req_id:
            logger.info(f"[{req_id}] Pre-extracted existing text length={len(extracted)}")
        if len(extracted) > 500 and _score_text(extracted) > 0.05:
            if req_id:
                logger.info(f"[{req_id}] Using embedded text layer (skip OCR)")
            return extracted
        return ""
    except Exception as e:  # noqa
        if req_id:
            logger.info(f"[{req_id}] Embedded text extraction failed: {e}")
        return ""

# ================== DATA CLASS ==================
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
    # clean=True включает агрессивную финальную очистку: удаление спецсимволов и переводов строк
    clean: bool = True
    rotate_pages: bool = True
    deskew: bool = True

# ================== DEP HELPERS ==================

def _gs_path() -> str | None:
    for name in ("gswin64c", "gswin32c", "gs"):
        p = shutil.which(name)
        if p:
            return p
    return None

def _ensure_ghostscript() -> bool:
    return _gs_path() is not None

def _ensure_tesseract() -> bool:
    return shutil.which("tesseract") is not None

# ================== RASTER & PREPROCESS ==================

def _rasterize_pdf(pdf_path: Path, out_dir: Path, dpi: int, req_id: str | None, logger: logging.Logger) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    gs = _gs_path()
    if not gs:
        if req_id:
            logger.warning(f"[{req_id}] Ghostscript not found for raster pipeline")
        return []
    pattern = out_dir / "page_%04d.png"
    cmd = [gs, "-dNOPAUSE", "-dBATCH", "-sDEVICE=png16m", f"-r{dpi}", f"-sOutputFile={pattern}", str(pdf_path)]
    try:
        subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=600)
    except Exception as e:  # noqa
        if req_id:
            logger.warning(f"[{req_id}] Rasterization failed: {e}")
        return []
    pages = sorted(out_dir.glob("page_*.png"))
    if req_id:
        logger.info(f"[{req_id}] Rasterized {len(pages)} pages @ {dpi} DPI")
    return pages

try:
    _NEAREST = Image.Resampling.NEAREST  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    # Fallback: numeric value 0 corresponds to NEAREST resampling in Pillow
    _NEAREST = 0

def _detect_orientation_tesseract(img_path: Path, req_id: str | None) -> int:
    """Use tesseract OSD to detect rotation in degrees (0/90/180/270). Returns degrees to rotate clockwise to correct orientation."""
    tess = shutil.which("tesseract")
    if not tess:
        return 0
    cmd = [tess, str(img_path), "stdout", "-l", "osd", "--psm", "0"]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        out = proc.stdout.decode('utf-8', errors='ignore')
        # Look for lines like 'Orientation in degrees: 270' or 'Rotate: 270'
        m = re.search(r"Orientation in degrees:\s*(\d+)", out, re.IGNORECASE)
        if not m:
            m = re.search(r"Rotate:\s*(\d+)", out, re.IGNORECASE)
        if m:
            deg = int(m.group(1)) % 360
            if req_id:
                logging.getLogger('ocr_pipeline').info(f"[{req_id}] tesseract OSD detected rotation={deg}")
            return deg
        return 0
    except Exception:
        return 0

def _estimate_skew_angle(img: Image.Image, req_id: str | None = None) -> float:
    """Estimate skew angle (degrees). Positive means image is rotated CCW. We'll search coarse then refine.
    Uses horizontal projection variance maximization. Works on grayscale/binary PIL images.
    """
    try:
        # Work on a smaller copy to speed up
        w, h = img.size
        scale = 1.0
        max_dim = DESKEW_MAX_DIM
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            img_small = img.resize((int(w*scale), int(h*scale)), Image.Resampling.BILINEAR)
        else:
            img_small = img.copy()
        # Convert to binary
        gray = img_small.convert('L')
        bw = gray.point(lambda x: 0 if x < 200 else 255, '1')

        def score_angle(a: float) -> float:
            # rotate -a (PIL rotates counter-clockwise) to deskew
            r = bw.rotate(a, resample=Image.Resampling.BILINEAR, expand=True)
            arr = r.convert('L')
            hist = arr.histogram()
            # horizontal projection variance: sum of pixel values per row
            px = list(arr.getdata())  # type: ignore
            rw, rh = arr.size
            rowsums = [0]*rh
            for y in range(rh):
                rowstart = y*rw
                rowsums[y] = sum(255 - v for v in px[rowstart:rowstart+rw])
            # compute variance
            mean = sum(rowsums)/len(rowsums) if rowsums else 0
            var = sum((x-mean)**2 for x in rowsums)
            return var

        # coarse search (narrower range to speed up; most skews are small)
        best_a = 0.0
        best_s = -1.0
        for a in [i for i in range(-3, 4)]:
            s = score_angle(a)
            if s > best_s:
                best_s = s
                best_a = a
        # refine around best_a
        best_a_f = best_a
        best_s_f = best_s
        # finer-grained search around best_a with larger step to reduce rotations
        for a in [best_a - 1 + i*0.25 for i in range(9)]:
            s = score_angle(a)
            if s > best_s_f:
                best_s_f = s
                best_a_f = a
        if req_id:
            logging.getLogger('ocr_pipeline').info(f"[{req_id}] estimated skew angle={best_a_f:.2f}")
        return best_a_f
    except Exception:
        return 0.0

def _preprocess_image(img_path: Path, options: OcrSimpleOptions, req_id: str | None, logger: logging.Logger) -> Path:
    try:
        img = Image.open(img_path)
        w, h = img.size
        # If requested, detect coarse rotation (OSD) and apply
        if options.rotate_pages:
            try:
                deg = _detect_orientation_tesseract(img_path, req_id)
                if deg and deg % 360 != 0:
                    # PIL.rotate rotates CCW, so rotate -deg to rotate clockwise
                    img = img.rotate(-deg, expand=True)
                    if req_id:
                        logger.info(f"[{req_id}] Applied OSD rotation {-deg} deg")
            except Exception:
                pass
        # Estimate and correct small skew if requested
        if options.deskew:
            try:
                ang = _estimate_skew_angle(img, req_id)
                if abs(ang) > 0.3:
                    img = img.rotate(-ang, expand=True)
                    if req_id:
                        logger.info(f"[{req_id}] Applied deskew rotation {-ang:.2f} deg")
            except Exception:
                pass
        tiny = w < MIN_IMG_W or h < MIN_IMG_H
        # Slight upscale for very small images
        if tiny:
            new_w = max(MIN_IMG_W, int(w * UPSCALE_TINY))
            new_h = max(MIN_IMG_H, int(h * UPSCALE_TINY))
            img = img.resize((new_w, new_h), _NEAREST)
            if req_id:
                logger.info(f"[{req_id}] Upscaled tiny image {w}x{h} -> {new_w}x{new_h}")
        else:
            # For somewhat small images, a light upscale can help OCR without big cost
            if w < 800:
                new_w = int(w * 1.4)
                new_h = int(h * 1.4)
                img = img.resize((new_w, new_h), _NEAREST)
                if req_id:
                    logger.info(f"[{req_id}] Light-upscaled image {w}x{h} -> {new_w}x{new_h}")
        g = img.convert("L")
        # Improve contrast and apply slight autocontrast + sharpen to help OCR
        g = ImageEnhance.Contrast(g).enhance(1.6)
        try:
            g = ImageOps.autocontrast(g)
        except Exception:
            pass
        try:
            g = ImageEnhance.Sharpness(g).enhance(1.4)
        except Exception:
            pass
        hist = g.histogram()
        total = sum(hist)
        sumB = 0.0
        wB = 0.0
        maximum = 0.0
        sum1 = sum(i * hist[i] for i in range(256))
        level = 0
        for i in range(256):
            wB += hist[i]
            if wB == 0:
                continue
            wF = total - wB
            if wF == 0:
                break
            sumB += i * hist[i]
            mB = sumB / wB
            mF = (sum1 - sumB) / wF
            between = wB * wF * (mB - mF) ** 2
            if between > maximum:
                level = i
                maximum = between
        bw = g.point(lambda x: 255 if x > level else 0, "L")
        out_path = img_path.parent / (img_path.stem + "_prep.png")
        bw.save(out_path)
        return out_path
    except Exception:
        return img_path

# ================== PAGE OCR ==================

def _tesseract_page(img_path: Path, languages: str, psm: int, oem: int, timeout_s: int = 120) -> str:
    tess = shutil.which("tesseract")
    if not tess:
        return ""
    cmd = [tess, str(img_path), "stdout", "-l", languages, "--oem", str(oem), "--psm", str(psm)]
    try:
        # Используем run чтобы разделить stdout и stderr и не смешивать сообщения об ошибках
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout_s)
        out = proc.stdout.decode("utf-8", errors="ignore")
        if proc.returncode != 0:
            # Логируем stderr, но не добавляем его в возвращаемый текст
            try:
                err = proc.stderr.decode("utf-8", errors="ignore").strip()
                if err:
                    logging.getLogger("ocr_pipeline").warning(f"tesseract stderr: {err}")
            except Exception:
                pass
        # Фильтруем типичные диагностические/информационные строки tesseract из stdout
        try:
            out = _filter_tesseract_noise(out)
        except Exception:
            pass
        return out
    except Exception:
        return ""

def _filter_tesseract_noise(text: str) -> str:
    if not text:
        return text
    noise_patterns = [
        r"read_params_file", r"warning", r"error", r"tesseract (?:open|version|lib)" , r"no text detected",
        r"failed to", r"segmentation fault", r"can't open", r"unable to", r"load.*data"
    ]
    # join patterns with | to form a combined regex (avoid unnecessary nested groups)
    combined = re.compile("|".join(noise_patterns), re.IGNORECASE)
    lines = text.splitlines()
    kept: List[str] = []
    for L in lines:
        s = L.strip()
        if not s:
            kept.append("")
            continue
        if combined.search(s):
            # пропускаем диагностические сообщения
            continue
        # также пропускаем односимвольные строки из служебных символов
        if len(s) <= 2 and re.fullmatch(r"[^\w\d]+", s):
            continue
        kept.append(s)
    # Возвращаем с сохранением переводов строк
    return "\n".join(kept)

def _rasterize_single_page(pdf_path: Path, page_index: int, dpi: int, req_id: str | None, logger: logging.Logger) -> Path | None:
    gs = _gs_path()
    if not gs:
        return None
    out_dir = pdf_path.parent / "_raster_single"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"page_{page_index:04d}_{dpi}.png"
    cmd = [gs, f"-dFirstPage={page_index}", f"-dLastPage={page_index}", "-dNOPAUSE", "-dBATCH", "-sDEVICE=png16m", f"-r{dpi}", f"-sOutputFile={out_file}", str(pdf_path)]
    try:
        subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=300)
        if req_id:
            logger.info(f"[{req_id}] Re-rasterized page {page_index} @ {dpi} DPI")
        return out_file if out_file.exists() else None
    except Exception as e:  # noqa
        if req_id:
            logger.warning(f"[{req_id}] Re-raster page {page_index} failed: {e}")
        return None

def _process_page(page_png: Path, languages: str, options: OcrSimpleOptions, req_id: str | None, logger: logging.Logger) -> Tuple[str, float, int]:
    target = _preprocess_image(page_png, options, req_id, logger)
    try:
        w, h = Image.open(target).size
    except Exception:
        w, h = (1000, 1000)
    candidate_psms = [6]
    if ENABLE_MULTI_PSM and (w < h * 0.35 or w < 450):
        candidate_psms = [6, 11, 4]
    best_txt = ""
    best_score = -1.0
    best_psm = candidate_psms[0]
    for test_psm in candidate_psms:
        txt = _tesseract_page(target, languages, test_psm, 1)
        sc = _score_text(txt)
        if sc > best_score:
            best_txt = txt
            best_score = sc
            best_psm = test_psm
    return best_txt, best_score, best_psm

# ================== POST FILTER ==================

def _post_filter_text(text: str) -> str:
    if not POSTFILTER_ENABLED or not text:
        return text or ""
    lines = text.replace('\r','').split('\n')
    filtered: List[str] = []
    blank = False
    for raw in lines:
        line = raw.strip()
        if len(line) <= 2 and all(ch in string.punctuation for ch in line):
            continue
        if not line:
            if blank:
                continue
            blank = True
            filtered.append("")
        else:
            blank = False
            norm = re.sub(r"\s+", " ", line)
            filtered.append(norm)
    while filtered and not filtered[0]:
        filtered.pop(0)
    while filtered and not filtered[-1]:
        filtered.pop()
    return "\n".join(filtered)

def _remove_gibberish(text: str) -> str:
    """Remove tokens that look like OCR garbage.
    Rules:
    - Collapse runs of the same character (>3 -> 2)
    - Keep token if ratio of (cyrillic|latin|digits) chars to length >= 0.6
    - Keep token if length >=2 or it contains a digit
    """
    if not text:
        return text
    def collapse_repeats(tok: str) -> str:
        return re.sub(r"(.)\1{2,}", r"\1\1", tok)

    tokens = re.split(r"(\s+)", text)
    out_parts: List[str] = []
    for tok in tokens:
        if tok.isspace():
            out_parts.append(tok)
            continue
        t = collapse_repeats(tok)
        if not t:
            continue
        # compute ratio of allowed chars
        allowed = len(_cyrillic_re.findall(t)) + len(_latin_re.findall(t)) + len(_digit_re.findall(t))
        ratio = allowed / max(1, len(t))
        if ratio >= 0.6 and (len(t) >= 2 or any(ch.isdigit() for ch in t)):
            out_parts.append(t)
        else:
            # drop token (replace with single space to avoid word concatenation)
            out_parts.append(' ')
    # join and normalize spaces
    out = ''.join(out_parts)
    out = re.sub(r" +", " ", out)
    # trim line-leading/trailing spaces per line
    lines = [ln.strip() for ln in out.split('\n')]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)

def _final_clean_text(text: str, aggressive: bool = True) -> str:
    """Final text normalization.
    If aggressive=True: remove newlines and all characters except letters (rus/lat), digits and spaces.
    If aggressive=False: soft normalization: remove control chars, normalize spaces, preserve punctuation and newlines.
    """
    if not text:
        return ""
    if aggressive:
        # replace newlines with spaces and collapse whitespace
        t = text.replace('\r', ' ').replace('\n', ' ')
        t = re.sub(r"\s+", ' ', t)
        t = _CLEAN_DISALLOWED_RE.sub('', t)
        t = re.sub(r' +', ' ', t)
        return t.strip()

    # soft mode
    t = text.replace('\r', '')
    t = _CLEAN_CONTROL_RE.sub('', t)
    t = re.sub(r"[ \t]+", ' ', t)
    t = re.sub(r"\n{3,}", '\n\n', t)
    lines = [ln.strip() for ln in t.split('\n')]
    # trim empty lines
    while lines and lines[0] == '':
        lines.pop(0)
    while lines and lines[-1] == '':
        lines.pop()
    return '\n'.join(lines)

def _apply_manual_fixes(text: str) -> str:
    """Apply targeted regex-based corrections for common OCR misspellings seen in scans.
    This list is conservative — add more patterns as we encounter real-world errors.
    """
    if not text:
        return text
    fixes = [
        (r"(?i)\bМоскав[а-я]*\b", "Москва"),
        (r"(?i)\bМоска[вя]\b", "Москва"),
        (r"(?i)\bАлмир[а-я]+\b", "Адмирала"),
        (r"(?i)\bАлмиряля\b", "Адмирала"),
        (r"(?i)\bАдмнраля\b", "Адмирала"),
        (r"(?i)\bПутевой\s+проез[лд]\b", "Путевой проезд"),
        (r"(?i)\bПутев(ой|ев)\s+проез[лд]\b", "Путевой проезд"),
        (r"(?i)\bпом\.?\s*этаж\b", "пом этаж"),
        (r"(?i)\bДОКУМЕНТ\s+О\s*КAЧЕСТВ[ЕЕ]\b", "ДОКУМЕНТ О КАЧЕСТВЕ"),
    ]
    for pat, rep in fixes:
        try:
            text = re.sub(pat, rep, text)
        except Exception:
            pass
    return text

# ================== PIPELINE ==================

def _raw_raster_tesseract_pipeline(pdf_path: Path, languages: str, options: OcrSimpleOptions, req_id: str | None, logger: logging.Logger) -> Tuple[str, float]:
    raster_dir = pdf_path.parent / "_raster"
    pages = _rasterize_pdf(pdf_path, raster_dir, RAW_RASTER_DPI, req_id, logger)
    if not pages:
        return "", 0.0
    results_per_page: List[Tuple[str, float, int]] = [("", -1.0, 6)] * len(pages)

    # Create a fast initial options copy with deskew and rotate disabled to speed up the first pass
    try:
        opts_init_kwargs = dict(vars(options))
        opts_init_kwargs['deskew'] = False
        opts_init_kwargs['rotate_pages'] = False
        options_initial = OcrSimpleOptions(**opts_init_kwargs)
    except Exception:
        # Fallback
        options_initial = OcrSimpleOptions(languages=options.languages, clean=options.clean, rotate_pages=False, deskew=False)

    def task(i_page: int, p: Path, options_local: OcrSimpleOptions):
        txt, sc, psm_sel = _process_page(p, languages, options_local, req_id, logger)
        return i_page, txt, sc, psm_sel

    max_workers = min(PARALLEL_PAGE_WORKERS, len(pages))
    if req_id:
        logger.info(f"[{req_id}] Parallel page OCR workers={max_workers}")
    # First quick pass: skip expensive rotate/deskew
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(task, idx, p, options_initial) for idx, p in enumerate(pages)]
        for f in as_completed(futures):
            idx, txt, sc, psm_sel = f.result()
            results_per_page[idx] = (txt, sc, psm_sel)
            if req_id:
                logger.info(f"[{req_id}] Page {idx+1}/{len(pages)} score={sc:.4f} psm={psm_sel} chars={len(txt)}")

    weak_indices = [i for i, (_, sc, _) in enumerate(results_per_page) if sc < PAGE_SCORE_THRESHOLD]
    if weak_indices and req_id:
        logger.info(f"[{req_id}] Weak pages detected: {len(weak_indices)} (limit {RE_RASTER_MAX_PAGES})")
    if weak_indices:
        weak_indices = weak_indices[:RE_RASTER_MAX_PAGES]
        for i in weak_indices:
            single_png = _rasterize_single_page(pdf_path, i + 1, HIGH_DPI, req_id, logger)
            if not single_png:
                continue
            txt2, sc2, psm2 = _process_page(single_png, languages, options, req_id, logger)
            old_txt, old_sc, old_psm = results_per_page[i]
            if sc2 > old_sc + MIN_SECOND_PASS_IMPROVEMENT:
                results_per_page[i] = (txt2, sc2, psm2)
                if req_id:
                    logger.info(f"[{req_id}] Page {i+1} improved {old_sc:.4f}->{sc2:.4f} (psm {old_psm}->{psm2})")
            else:
                if req_id:
                    logger.info(f"[{req_id}] Page {i+1} re-raster no gain (old {old_sc:.4f} new {sc2:.4f})")

    full = "\n".join(part[0] for part in results_per_page)
    score = _score_text(full)
    try:
        for f in raster_dir.glob("*"):
            f.unlink()
        raster_dir.rmdir()
    except Exception:
        pass
    return full, score

# ================== PUBLIC ENTRY ==================

def ocr_pdf_bytes_to_text(data: bytes, options: OcrSimpleOptions, req_id: str | None = None) -> str:
    logger = logging.getLogger("ocr_pipeline")
    start_time = time.time()
    h = hashlib.sha256(data).hexdigest()
    cached = _cache_get(h)
    if cached:
        if req_id:
            logger.info(f"[{req_id}] Cache hit hash={h[:8]} score={cached[1]:.4f}")
        return cached[0]

    tmp_dir = Path(tempfile.mkdtemp(prefix="ocr_fast_"))
    if req_id:
        logger.info(f"[{req_id}] tmp_dir={tmp_dir}")
    input_path = tmp_dir / "input.pdf"
    input_path.write_bytes(data)

    existing = _try_extract_pdf_text(input_path, logger, req_id)
    if existing:
        existing_clean = _final_clean_text(existing, aggressive=options.clean)
        sc = _score_text(existing_clean)
        _cache_put(h, existing_clean, sc)
        if req_id:
            logger.info(f"[{req_id}] Using embedded text score={sc:.4f} chars={len(existing_clean)} (raw_embedded={len(existing)})")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return existing_clean

    _ensure_ghostscript(); _ensure_tesseract()
    if req_id:
        logger.info(f"[{req_id}] Running FAST_TEXT_ONLY raster+tesseract pipeline (dpi={RAW_RASTER_DPI})")
    # Pass options into pipeline so preprocessing (rotate/deskew) is applied
    raw_text, raw_score = _raw_raster_tesseract_pipeline(input_path, options.languages, options, req_id, logger)

    if POSTFILTER_ENABLED:
        filtered = _post_filter_text(raw_text)
        if filtered and len(filtered) >= 0.6 * len(raw_text):
            raw_text = filtered
            raw_score = _score_text(raw_text)
            if req_id:
                logger.info(f"[{req_id}] Post-filter applied new_score={raw_score:.4f}")

    # Additional gibberish removal to cut OCR noise (repeated chars, non-alpha blobs)
    try:
        raw_text = _remove_gibberish(raw_text)
        raw_score = _score_text(raw_text)
    except Exception:
        pass

    # Final mandatory cleaning: remove newlines and all special characters
    cleaned_text = _final_clean_text(raw_text, aggressive=options.clean)
    # Apply manual regex-based fixes for common OCR errors
    try:
        cleaned_text = _apply_manual_fixes(cleaned_text)
    except Exception:
        pass
    final_score = _score_text(cleaned_text)
    if req_id:
        logger.info(f"[{req_id}] Final clean applied score={final_score:.4f} chars={len(cleaned_text)} (pre_clean_chars={len(raw_text)})")

    duration = time.time() - start_time
    if req_id:
        logger.info(f"[{req_id}] FAST pipeline done (raw_score={raw_score:.4f} final_score={final_score:.4f}) time={duration:.2f}s")

    _cache_put(h, cleaned_text, final_score)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return cleaned_text
