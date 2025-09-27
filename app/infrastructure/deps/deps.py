from __future__ import annotations
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional, List
import os

@dataclass
class DependencyStatus:
    name: str
    found: bool
    path: Optional[str]
    version: Optional[str] = None
    details: Optional[str] = None

@dataclass
class DependenciesReport:
    tesseract: DependencyStatus
    ghostscript: DependencyStatus
    languages_ok: bool
    missing_languages: List[str]

_REQUIRED_LANGS = {"rus", "eng"}

_cached_report: Optional[DependenciesReport] = None


def _run_version(cmd: list[str]) -> Optional[str]:
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=10)
        return out.decode(errors="ignore").strip().splitlines()[0]
    except Exception:
        return None


def _detect_tesseract() -> DependencyStatus:
    path = shutil.which("tesseract")
    if not path:
        # Возможные стандартные пути Windows
        candidates = [
            r"C:\\Program Files\\Tesseract-OCR\\tesseract.exe",
            r"C:\\Program Files (x86)\\Tesseract-OCR\\tesseract.exe",
        ]
        for c in candidates:
            if os.path.isfile(c):
                path = c
                break
        if path:
            os.environ["PATH"] = f"{os.path.dirname(path)}{os.pathsep}" + os.environ.get("PATH", "")
    version = _run_version([path, "--version"]) if path else None  # type: ignore[arg-type]
    langs_ok = False
    missing: List[str] = []
    if path:
        try:
            out = subprocess.check_output([path, "--list-langs"], stderr=subprocess.DEVNULL, timeout=10)  # type: ignore[arg-type]
            langs = {l.strip() for l in out.decode(errors="ignore").splitlines() if l.strip() and not l.startswith("List of")}
            missing = sorted(list(_REQUIRED_LANGS - langs))
            langs_ok = len(missing) == 0
        except Exception:
            missing = sorted(list(_REQUIRED_LANGS))
    return DependencyStatus(name="tesseract", found=bool(path), path=path, version=version,
                             details=("missing languages: " + ", ".join(missing)) if path and missing else None)


def _detect_ghostscript() -> DependencyStatus:
    path = None
    for name in ["gs", "gswin64c", "gswin32c"]:
        p = shutil.which(name)
        if p:
            path = p
            break
    if not path:
        # Windows typical locations
        base_candidates = [
            r"C:\\Program Files\\gs",
            r"C:\\Program Files (x86)\\gs",
        ]
        for base in base_candidates:
            if not os.path.isdir(base):
                continue
            versions = sorted([d for d in os.listdir(base) if d.lower().startswith("gs")], reverse=True)
            for v in versions:
                for exe in ["gswin64c.exe", "gswin32c.exe", "gs.exe"]:
                    cand = os.path.join(base, v, "bin", exe)
                    if os.path.isfile(cand):
                        path = cand
                        break
                if path:
                    break
            if path:
                break
        if path:
            os.environ["PATH"] = f"{os.path.dirname(path)}{os.pathsep}" + os.environ.get("PATH", "")
    version = _run_version([path, "--version"]) if path else None  # type: ignore[arg-type]
    if path:
        # Для ocrmypdf можно выставить переменную
        os.environ.setdefault("OCRMYPDF_GS", path)
    return DependencyStatus(name="ghostscript", found=bool(path), path=path, version=version)


def ensure_dependencies() -> DependenciesReport:
    global _cached_report
    if _cached_report:
        return _cached_report
    tess = _detect_tesseract()
    gs = _detect_ghostscript()
    languages_ok = tess.found and (tess.details is None or not tess.details.startswith("missing languages"))
    missing = []
    if tess.found and tess.details and tess.details.startswith("missing languages"):
        missing = tess.details.split(":", 1)[-1].strip().split(",")
        missing = [m.strip() for m in missing if m.strip()]
    report = DependenciesReport(
        tesseract=tess,
        ghostscript=gs,
        languages_ok=languages_ok,
        missing_languages=missing,
    )
    _cached_report = report
    return report


def assert_ready() -> None:
    r = ensure_dependencies()
    problems = []
    if not r.tesseract.found:
        problems.append("Tesseract not found")
    if not r.ghostscript.found:
        problems.append("Ghostscript not found")
    if r.tesseract.found and not r.languages_ok:
        problems.append("Missing Tesseract languages: " + ", ".join(r.missing_languages))
    if problems:
        raise RuntimeError("; ".join(problems))


def dependency_report_dict() -> dict:
    r = ensure_dependencies()
    return {
        "tesseract": {
            "found": r.tesseract.found,
            "path": r.tesseract.path,
            "version": r.tesseract.version,
            "details": r.tesseract.details,
        },
        "ghostscript": {
            "found": r.ghostscript.found,
            "path": r.ghostscript.path,
            "version": r.ghostscript.version,
        },
        "languages_ok": r.languages_ok,
        "missing_languages": r.missing_languages,
    }

