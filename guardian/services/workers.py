"""Named background tasks whose completions are drained by the UI thread."""

from __future__ import annotations

import queue
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class TaskResult:
    name: str
    value: Any = None
    error: BaseException | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


Completion = Callable[[TaskResult], None]


class WorkerPool:
    """Small bounded executor with UI-thread completion dispatch."""

    def __init__(self, max_workers: int = 3, thread_name_prefix: str = "guardian") -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=thread_name_prefix,
        )
        self._completed: queue.SimpleQueue[tuple[TaskResult, Completion | None]] = (
            queue.SimpleQueue()
        )
        self._active: set[str] = set()
        self._lock = threading.Lock()
        self._closed = False

    def submit(
        self,
        name: str,
        operation: Callable[[], Any],
        on_complete: Completion | None = None,
        *,
        replace: bool = False,
    ) -> bool:
        """Submit a task, rejecting duplicate names unless ``replace`` is set."""
        with self._lock:
            if self._closed:
                return False
            if name in self._active and not replace:
                return False
            self._active.add(name)

        future = self._executor.submit(operation)
        future.add_done_callback(
            lambda done, task_name=name, callback=on_complete: self._finish(
                task_name, done, callback
            )
        )
        return True

    def _finish(
        self,
        name: str,
        future: Future[Any],
        callback: Completion | None,
    ) -> None:
        try:
            result = TaskResult(name=name, value=future.result())
        except BaseException as exc:  # worker failures must reach the UI
            result = TaskResult(name=name, error=exc)
        with self._lock:
            self._active.discard(name)
        self._completed.put((result, callback))

    def drain(self, limit: int = 100) -> list[TaskResult]:
        """Run completion callbacks in the caller's thread."""
        results: list[TaskResult] = []
        for _ in range(max(0, limit)):
            try:
                result, callback = self._completed.get_nowait()
            except queue.Empty:
                break
            results.append(result)
            if callback is not None:
                callback(result)
        return results

    def is_active(self, name: str) -> bool:
        with self._lock:
            return name in self._active

    def close(self, *, wait: bool = False) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=True)
