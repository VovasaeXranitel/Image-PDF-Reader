from __future__ import annotations
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Dict, Optional
from datetime import datetime
import threading

from ..domain.entities import OcrTask, OcrTaskOptions, OcrTaskResult
from ..domain.repositories import OcrTaskRepository
from ..infrastructure.ocr.engine import run_ocr_task


_TASK_EXECUTOR = ThreadPoolExecutor(max_workers=2)
_LOCK = threading.Lock()


class OcrService:
    def __init__(self, repo: OcrTaskRepository, base_output: Path):
        self.repo = repo
        self.base_output = base_output
        self._futures: Dict[str, Future] = {}

    def submit_file(self, input_file: Path, options: OcrTaskOptions) -> str:
        input_file = input_file.resolve()
        output_dir = self.base_output.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        task = OcrTask(input_file=input_file, output_dir=output_dir, options=options)
        self.repo.add_task(task)

        def _run():
            result: OcrTaskResult = run_ocr_task(task)
            self.repo.set_result(result)
            return result

        fut = _TASK_EXECUTOR.submit(_run)
        with _LOCK:
            self._futures[task.task_id] = fut
        return task.task_id

    def submit_bytes(self, data: bytes, options: OcrTaskOptions, filename: Optional[str] = None) -> str:
        output_dir = self.base_output.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        task = OcrTask(input_file=None, output_dir=output_dir, options=options, raw_bytes=data, original_name=filename)
        self.repo.add_task(task)

        def _run():
            result: OcrTaskResult = run_ocr_task(task)
            self.repo.set_result(result)
            return result

        fut = _TASK_EXECUTOR.submit(_run)
        with _LOCK:
            self._futures[task.task_id] = fut
        return task.task_id

    def get_task_result(self, task_id: str) -> Optional[OcrTaskResult]:
        return self.repo.get_result(task_id)

    def get_task_state(self, task_id: str) -> Dict:
        task = self.repo.get_task(task_id)
        if not task:
            return {"exists": False}
        res = self.repo.get_result(task_id)
        with _LOCK:
            fut = self._futures.get(task_id)
        state = "pending"
        if fut is not None:
            if fut.done():
                state = "finished"
            elif fut.running():
                state = "running"
            else:
                state = "queued"
        if res:
            state = "finished"
        return {
            "exists": True,
            "task_id": task_id,
            "state": state,
            "input_file": str(task.input_file) if task.input_file else None,
            "original_name": task.original_name,
            "output_pdf": str(task.output_pdf_path()),
            "output_txt": str(task.output_txt_path()),
            "options": task.options.__dict__,
            "result": _serialize_result(res) if res else None,
        }


def _serialize_result(res: Optional[OcrTaskResult]):
    if not res:
        return None
    return {
        "task_id": res.task_id,
        "success": res.success,
        "error": res.error,
        "input_file": str(res.input_file) if res.input_file else None,
        "output_pdf": str(res.output_pdf),
        "output_txt": str(res.output_txt) if res.output_txt else None,
        "started_at": res.started_at.isoformat() if res.started_at else None,
        "finished_at": res.finished_at.isoformat() if res.finished_at else None,
        "pages": [
            {
                "page_index": p.page_index,
                "pdf_path": str(p.pdf_path),
                "text_path": str(p.text_path) if p.text_path else None,
                "success": p.success,
                "error": p.error,
                "duration_sec": p.duration_sec,
            }
            for p in res.pages
        ],
    }
