"""Local worker loop that processes leased jobs one at a time."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from glide.jobs.queue import InMemoryJobQueue, Job


class JobProcessor(Protocol):
    def __call__(self, job: Job) -> None: ...


@dataclass
class LocalWorker:
    queue: InMemoryJobQueue
    processor: Callable[[Job], None]
    poll_interval_seconds: float = 1.0

    def __post_init__(self) -> None:
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def process_one(self) -> bool:
        job = self.queue.dequeue()
        if job is None:
            return False
        try:
            self.processor(job)
        except Exception as exc:  # noqa: BLE001 - local loop must keep running
            self.queue.fail(job.id, job.lease_revision, type(exc).__name__)
            return True
        self.queue.complete(job.id, job.lease_revision)
        return True

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.wait(self.poll_interval_seconds):
            self.process_one()
