from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional, Dict
from .entities import OcrTaskResult, OcrTask


class OcrTaskRepository(ABC):
    @abstractmethod
    def add_task(self, task: OcrTask) -> None: ...

    @abstractmethod
    def get_task(self, task_id: str) -> Optional[OcrTask]: ...

    @abstractmethod
    def set_result(self, result: OcrTaskResult) -> None: ...

    @abstractmethod
    def get_result(self, task_id: str) -> Optional[OcrTaskResult]: ...


class InMemoryOcrTaskRepository(OcrTaskRepository):
    def __init__(self):
        self._tasks: Dict[str, OcrTask] = {}
        self._results: Dict[str, OcrTaskResult] = {}

    def add_task(self, task: OcrTask) -> None:
        self._tasks[task.task_id] = task

    def get_task(self, task_id: str) -> Optional[OcrTask]:
        return self._tasks.get(task_id)

    def set_result(self, result: OcrTaskResult) -> None:
        self._results[result.task_id] = result

    def get_result(self, task_id: str) -> Optional[OcrTaskResult]:
        return self._results.get(task_id)
