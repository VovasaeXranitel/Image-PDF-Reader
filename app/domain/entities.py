from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime
from typing import Optional, List
import uuid


@dataclass
class OcrPageResult:
    page_index: int
    pdf_path: Path
    text_path: Optional[Path]
    success: bool
    error: Optional[str] = None
    duration_sec: float = 0.0


@dataclass
class OcrTaskResult:
    task_id: str
    input_file: Optional[Path]
    output_pdf: Path
    output_txt: Optional[Path]
    pages: List[OcrPageResult]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    success: bool
    error: Optional[str] = None


@dataclass
class OcrTaskOptions:
    languages: str = "rus"
    psm: Optional[int] = None
    oem: Optional[int] = None
    oversample: int = 300
    optimize: int = 0
    output_type: str = "pdf"  # pdf | pdfa
    whitelist: Optional[str] = None
    blacklist: Optional[str] = None
    preserve_spaces: bool = False
    clean: bool = False
    parallel_pages: bool = True
    max_workers: int = 4


@dataclass
class OcrTask:
    # Если файл уже есть на диске
    input_file: Optional[Path]
    output_dir: Path
    options: OcrTaskOptions = field(default_factory=OcrTaskOptions)
    # Альтернатива: "вход" как байтовый массив (например из API)
    raw_bytes: Optional[bytes] = None
    original_name: Optional[str] = None  # имя загруженного файла (для формирования базового имени)
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def base_name(self) -> str:
        if self.original_name:
            return Path(self.original_name).stem
        if self.input_file:
            return self.input_file.stem
        return self.task_id  # fallback

    def output_pdf_path(self) -> Path:
        return self.output_dir / f"{self.base_name}__ocr.pdf"

    def output_txt_path(self) -> Path:
        return self.output_dir / f"{self.base_name}__ocr.txt"
