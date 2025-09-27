import sys
from pathlib import Path
import argparse
from typing import Tuple
import os
import shutil
import platform
from concurrent.futures import ThreadPoolExecutor
from PyPDF2 import PdfReader, PdfWriter


def _which_any(names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def _add_to_path(p: Path):
    # Добавляет каталог в PATH текущего процесса, если его там ещё нет
    if p and p.is_dir():
        path_entries = os.environ.get("PATH", "").split(os.pathsep)
        dir_str = str(p)
        if dir_str not in path_entries:
            os.environ["PATH"] = os.pathsep.join([dir_str] + path_entries)


def _add_user_hint_to_path(value: str):
    if not value:
        return
    p = Path(value).expanduser()
    if p.is_file():
        target = p.parent
    elif p.is_dir():
        target = p
    else:
        return
    # Добавим в PATH для текущего процесса
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    dir_str = str(target)
    if dir_str not in path_entries:
        os.environ["PATH"] = os.pathsep.join([dir_str] + path_entries)


def ensure_ghostscript() -> bool:
    """Проверяет доступность Ghostscript (gs/gswin64c/gswin32c). Если не найден, пытается
    автоматически добавить типичные пути установки в PATH. Возвращает True, если найден.
    При обнаружении задаёт переменную окружения OCRMYPDF_GS на конкретный exe."""
    # Уже доступен?
    for name in ["gswin64c", "gswin32c", "gs"]:
        p = shutil.which(name)
        if p:
            # Явно укажем ocrmypdf какой двоичный файл использовать
            os.environ["OCRMYPDF_GS"] = p
            return True

    # Попробуем найти в Program Files
    candidates = []
    pf = os.environ.get("ProgramFiles", r"C:\\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\\Program Files (x86)")
    for base in [pf, pf86]:
        gs_base = Path(base) / "gs"
        if gs_base.exists():
            # Ищем последние версии
            versions = sorted(gs_base.glob("gs*"), reverse=True)
            for v in versions:
                candidates.append(v / "bin" / "gswin64c.exe")
                candidates.append(v / "bin" / "gswin32c.exe")
                candidates.append(v / "bin" / "gs.exe")

    for exe in candidates:
        if exe.exists():
            _add_to_path(exe.parent)
            # Явно укажем путь Ghostscript для ocrmypdf
            os.environ["OCRMYPDF_GS"] = str(exe)
            if shutil.which(exe.name) or shutil.which("gs") or shutil.which("gswin64c") or shutil.which("gswin32c"):
                return True

    return False


def ensure_tesseract() -> bool:
    """Проверяет доступность Tesseract. Если не найден, пытается добавить типичный путь.
    Возвращает True, если найден."""
    if shutil.which("tesseract"):
        return True

    # Типичный путь установки на Windows
    tesseract_dir = Path(os.environ.get("ProgramFiles", r"C:\\Program Files")) / "Tesseract-OCR"
    tesseract_exe = tesseract_dir / "tesseract.exe"
    if tesseract_exe.exists():
        _add_to_path(tesseract_dir)
        if shutil.which("tesseract"):
            return True

    return False


def ocr_pdf(
    input_path: str,
    output_pdf: str,
    sidecar_txt: str,
    languages: str = "rus",
    progress: bool = True,
    clean: bool = False,
    optimize: int = 0,
    oversample: int | None = 300,
    tesseract_pagesegmode: int | None = None,
    tesseract_oem: int | None = None,
    output_type: str = "pdf",
    tesseract_config: str | None = None,
):
    """
    Делает OCR над input_path, сохраняет:
      – текст в sidecar_txt,
      – поисковый PDF в output_pdf.
    clean=True включает "очистку" страниц через unpaper (часто недоступен на Windows).
    optimize=0 — отключает оптимизации изображений (устойчивее к битым JPEG в PDF).
    oversample — повышает DPI перед OCR (улучшает качество для низкого разрешения).
    tesseract_pagesegmode / tesseract_oem — тонкая настройка сегментации и движка.
    """
    try:
        import ocrmypdf  # ленивый импорт, чтобы --help работал без установленного пакета
    except ImportError as e:
        raise RuntimeError(
            "Требуется пакет 'ocrmypdf'. Установите его: pip install ocrmypdf.\n"
            "Также убедитесь, что установлены системные зависимости: Tesseract OCR и Ghostscript, и они доступны в PATH."
        ) from e

    # Смягчим обработку битых JPEG внутри ocrmypdf (Pillow)
    try:
        from PIL import ImageFile  # type: ignore
        ImageFile.LOAD_TRUNCATED_IMAGES = True
    except Exception:
        pass

    # Подготовим kwargs с возможностью отката, если версия ocrmypdf не поддерживает часть параметров
    kwargs = dict(
        input_file=input_path,
        output_file=output_pdf,
        language=languages,
        deskew=True,
        rotate_pages=True,
        clean=clean,
        sidecar=sidecar_txt,
        progress_bar=progress,
        force_ocr=True,
        optimize=optimize,
        oversample=oversample,
        output_type=output_type,
    )
    if tesseract_pagesegmode is not None:
        kwargs["tesseract_pagesegmode"] = tesseract_pagesegmode
    if tesseract_oem is not None:
        kwargs["tesseract_oem"] = tesseract_oem
    if tesseract_config:
        kwargs["tesseract_config"] = tesseract_config

    # Установка параметров Tesseract по умолчанию
    if tesseract_pagesegmode is None:
        kwargs["tesseract_pagesegmode"] = 6  # Assume a single uniform block of text
    if tesseract_oem is None:
        kwargs["tesseract_oem"] = 1  # Use LSTM OCR engine

    try:
        ocrmypdf.ocr(**kwargs)
    except TypeError as te:
        # Откат: уберём доп. параметры совместимости и повторим
        unsupported = {"tesseract_pagesegmode", "tesseract_oem", "oversample", "optimize", "output_type", "tesseract_config"}
        for k in list(kwargs.keys()):
            if k in unsupported and k in kwargs:
                kwargs.pop(k, None)
        print("Предупреждение: часть параметров оптимизации недоступна в вашей версии ocrmypdf. Повторяю с упрощёнными настройками.")
        ocrmypdf.ocr(**kwargs)


def _uniquify(path: Path) -> Path:
    """Если файл существует, добавляет -1, -2, ... к имени."""
    if not path.exists():
        return path
    i = 1
    while True:
        candidate = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def _build_output_paths(inp: Path, outdir: Path, overwrite: bool) -> Tuple[Path, Path]:
    stem = inp.stem
    pdf = outdir / f"{stem}__ocr.pdf"
    txt = outdir / f"{stem}__ocr.txt"
    if not overwrite:
        pdf = _uniquify(pdf)
        txt = _uniquify(txt)
    return pdf, txt


def _pick_gs_from_dir(dir_path: Path) -> str | None:
    """Возвращает путь к подходящему exe Ghostscript внутри папки, если найден."""
    for name in ("gswin64c.exe", "gswin32c.exe", "gs.exe"):
        p = dir_path / name
        if p.exists():
            return str(p)
    return None


def split_pdf(input_path):
    """Разделяет PDF на отдельные страницы."""
    reader = PdfReader(input_path)
    pages = []
    for i, page in enumerate(reader.pages):
        writer = PdfWriter()
        writer.add_page(page)
        temp_path = Path(input_path).with_name(f"temp_page_{i + 1}.pdf")
        with open(temp_path, "wb") as temp_file:
            writer.write(temp_file)
        pages.append(temp_path)
    return pages


def merge_pdfs(page_paths, output_path):
    """Объединяет страницы PDF в один файл."""
    writer = PdfWriter()
    for page_path in page_paths:
        reader = PdfReader(page_path)
        writer.add_page(reader.pages[0])
    with open(output_path, "wb") as output_file:
        writer.write(output_file)


def ocr_pdf_pages(input_path, output_pdf, languages="rus", **kwargs):
    """Обрабатывает каждую страницу PDF в отдельном потоке."""
    temp_pages = split_pdf(input_path)
    temp_output_pages = []

    def process_page(page_path):
        temp_output = page_path.with_name(page_path.stem + "_ocr.pdf")
        ocr_pdf(
            input_path=str(page_path),
            output_pdf=str(temp_output),
            sidecar_txt=None,
            languages=languages,
            **kwargs,
        )
        return temp_output

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(process_page, page) for page in temp_pages]
        for future in futures:
            try:
                temp_output_pages.append(future.result())
            except Exception as e:
                print(f"Ошибка при обработке страницы: {e}")

    merge_pdfs(temp_output_pages, output_pdf)

    # Удаляем временные файлы
    for temp_file in temp_pages + temp_output_pages:
        temp_file.unlink()


def ocr_pdf_parallel(tasks, max_workers=4):
    """
    Выполняет OCR для нескольких файлов параллельно.
    tasks: список словарей с параметрами для ocr_pdf.
    max_workers: максимальное количество потоков.
    """
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(ocr_pdf, **task) for task in tasks]
        for future in futures:
            try:
                future.result()  # Ждём завершения задачи
            except Exception as e:
                print(f"Ошибка при обработке задачи: {e}")


def main(argv=None) -> int:
    project_dir = Path(__file__).resolve().parent
    default_outdir = project_dir / "output"

    parser = argparse.ArgumentParser(
        description="OCR PDF/изображений с сохранением результатов в отдельную папку проекта",
    )
    parser.add_argument(
        "input",
        help="Путь к PDF или изображению для распознавания",
    )
    parser.add_argument(
        "-o",
        "--outdir",
        default=str(default_outdir),
        help=f"Папка для сохранения результатов (по умолчанию: {default_outdir})",
    )
    parser.add_argument(
        "-l",
        "--lang",
        default="rus",
        help="Языки Tesseract (например: rus, eng, rus+eng)",
    )
    parser.add_argument(
        "--psm",
        type=int,
        help="Tesseract Page Segmentation Mode (например: 3, 6, 11)",
    )
    parser.add_argument(
        "--oem",
        type=int,
        help="Tesseract OCR Engine Mode (0-3; 1=LSTM-only часто даёт лучший результат)",
    )
    parser.add_argument(
        "--oversample",
        type=int,
        default=300,
        help="Оверсэмплинг (DPI) перед OCR для повышения качества (по умолчанию: 300)",
    )
    parser.add_argument(
        "--optimize",
        type=int,
        choices=[0, 1, 2, 3],
        default=0,
        help="Уровень оптимизации изображений (0=выкл; 1..3 сильнее). 0 устойчивее к битым JPEG",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Отключить прогресс-бар",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Перезаписывать существующие файлы вывода (иначе добавляются -1, -2, …)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Включить очистку страниц (шумы, пыль) через unpaper. На Windows чаще недоступно.",
    )
    parser.add_argument(
        "--gs-dir",
        help="Каталог или полный путь к gswin64c.exe/gs.exe (подсказка для PATH)",
    )
    parser.add_argument(
        "--tesseract-dir",
        help="Каталог или полный путь к tesseract.exe (подсказка для PATH)",
    )
    parser.add_argument(
        "--debug-deps",
        action="store_true",
        help="Вывести диагностику поиска зависимостей (пути к Ghostscript и Tesseract)",
    )
    parser.add_argument(
        "--output-type",
        choices=["pdf", "pdfa"],
        default="pdf",
        help="Тип выходного файла: 'pdf' (обычный PDF, по умолчанию) или 'pdfa' (строгая валидация).",
    )
    parser.add_argument(
        "--whitelist",
        help="Ограничить алфавит (tessedit_char_whitelist), например: АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ0-9.-,",
    )
    parser.add_argument(
        "--blacklist",
        help="Исключить символы (tessedit_char_blacklist)",
    )
    parser.add_argument(
        "--preserve-spaces",
        action="store_true",
        help="Сохранять пробелы как есть (preserve_interword_spaces=1)",
    )

    args = parser.parse_args(argv)

    # Подсказки от пользователя: добавим указанные пути в PATH прямо сейчас
    if args.gs_dir:
        p = Path(args.gs_dir).expanduser()
        if p.is_file():
            # Пользователь передал конкретный exe
            os.environ["OCRMYPDF_GS"] = str(p)
            _add_user_hint_to_path(str(p))
        elif p.is_dir():
            found = _pick_gs_from_dir(p)
            if found:
                os.environ["OCRMYPDF_GS"] = found
            _add_user_hint_to_path(str(p))
    if args.tesseract_dir:
        _add_user_hint_to_path(args.tesseract_dir)

    inp_path = Path(args.input).expanduser()
    if not inp_path.is_file():
        print(f"Ошибка: входной файл не найден: {inp_path}")
        return 2

    outdir = Path(args.outdir).expanduser()
    try:
        outdir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"Ошибка: не удалось создать папку вывода {outdir}: {e}")
        return 3

    # Предварительная проверка зависимостей
    if platform.system() == "Windows":
        gs_ok = ensure_ghostscript()
        tess_ok = ensure_tesseract()

        if args.debug_deps:
            print("[debug] OCRMYPDF_GS=", os.environ.get("OCRMYPDF_GS"))
            print("[debug] which(gswin64c)=", shutil.which("gswin64c"))
            print("[debug] which(gswin32c)=", shutil.which("gswin32c"))
            print("[debug] which(gs)=", shutil.which("gs"))
            print("[debug] which(tesseract)=", shutil.which("tesseract"))

        if not gs_ok:
            print(
                "Ошибка: Ghostscript не найден (gswin64c/gswin32c). Установите его и/или перезапустите терминал.\n"
                "Быстрая установка (PowerShell): winget install --id=ArtifexSoftware.Ghostscript -e\n"
                "Либо укажите путь флагом --gs-dir \"C:\\Program Files (x86)\\gs\\gsXX.XX\\bin\\gswin32c.exe\" или \"C:\\Program Files\\gs\\gsXX.XX\\bin\\gswin64c.exe\"\n"
                "Подсказка для PowerShell: $env:OCRMYPDF_GS=\"C:\\...\\gswin32c.exe\"\n"
                "Затем проверьте: gswin64c --version или gswin32c --version"
            )
            return 5
        if not tess_ok:
            print(
                "Ошибка: Tesseract не найден. Установите его и/или перезапустите терминал.\n"
                "Быстрая установка (PowerShell): winget install --id=UB-Mannheim.TesseractOCR -e\n"
                "Либо укажите путь флагом --tesseract-dir \"C:\\Program Files\\Tesseract-OCR\"\n"
                "Подсказка для PowerShell: $env:PATH=\"C:\\Program Files\\Tesseract-OCR;$env:PATH\"\n"
                "Затем проверьте: tesseract --version"
            )
            return 6

    out_pdf, out_txt = _build_output_paths(inp_path, outdir, overwrite=args.overwrite)

    # Построим tesseract_config из опций CLI
    tess_cfg_parts = []
    if args.whitelist:
        tess_cfg_parts.append(f"tessedit_char_whitelist={args.whitelist}")
    if args.blacklist:
        tess_cfg_parts.append(f"tessedit_char_blacklist={args.blacklist}")
    if args.preserve_spaces:
        tess_cfg_parts.append("preserve_interword_spaces=1")
    tess_cfg = " ".join(tess_cfg_parts) if tess_cfg_parts else None

    # Список задач для многопоточной обработки
    tasks = []
    if inp_path.is_dir():
        # Если входной путь — папка, обработаем все PDF внутри
        for file in inp_path.glob("*.pdf"):
            out_pdf, out_txt = _build_output_paths(file, outdir, overwrite=args.overwrite)
            tasks.append({
                "input_path": str(file),
                "output_pdf": str(out_pdf),
                "sidecar_txt": str(out_txt),
                "languages": args.lang,
                "progress": not args.no_progress,
                "clean": args.clean,
                "optimize": args.optimize,
                "oversample": args.oversample,
                "tesseract_pagesegmode": args.psm,
                "tesseract_oem": args.oem,
                "output_type": args.output_type,
                "tesseract_config": tess_cfg,
            })
    else:
        # Если входной путь — файл, обработаем его
        out_pdf, out_txt = _build_output_paths(inp_path, outdir, overwrite=args.overwrite)
        tasks.append({
            "input_path": str(inp_path),
            "output_pdf": str(out_pdf),
            "sidecar_txt": str(out_txt),
            "languages": args.lang,
            "progress": not args.no_progress,
            "clean": args.clean,
            "optimize": args.optimize,
            "oversample": args.oversample,
            "tesseract_pagesegmode": args.psm,
            "tesseract_oem": args.oem,
            "output_type": args.output_type,
            "tesseract_config": tess_cfg,
        })

    if inp_path.is_file():
        ocr_pdf_pages(
            input_path=str(inp_path),
            output_pdf=str(out_pdf),
            languages=args.lang,
            progress=not args.no_progress,
            clean=args.clean,
            optimize=args.optimize,
            oversample=args.oversample,
            tesseract_pagesegmode=args.psm,
            tesseract_oem=args.oem,
            output_type=args.output_type,
            tesseract_config=tess_cfg,
        )
    else:
        print("Ошибка: входной путь должен быть файлом PDF.")
        return 2

    print("Готово.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
