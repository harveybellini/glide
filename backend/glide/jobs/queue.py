"""In-memory per-user FIFO queue with leases and retries.

The deployed system will use SQS FIFO with one message group per user. This
queue mirrors that batch-size-one, lease-and-fence shape so the worker and
recovery code can be exercised locally.
"""

from __future__ import annotations

import threading
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol


@dataclass
class Job:
    id: str
    user_id: str
    trigger: str
    run_id: str = ""
    status: str = "queued"
    attempts: int = 0
    lease_revision: int = 0
    lease_holder: str | None = None
    enqueued_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None


class JobQueue(Protocol):
    """The enqueue surface shared by the local and SQS-backed queues."""

    def enqueue(self, user_id: str, trigger: str, run_id: str | None = None) -> Job: ...

    def supersede(self, user_id: str) -> list[Job]: ...


class InMemoryJobQueue:
    def __init__(self) -> None:
        self._queues: dict[str, deque[str]] = defaultdict(deque)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()

    def enqueue(self, user_id: str, trigger: str, run_id: str | None = None) -> Job:
        job_id = uuid.uuid4().hex
        job = Job(
            id=job_id,
            user_id=user_id,
            trigger=trigger,
            run_id=run_id or f"run-{job_id}",
        )
        with self._lock:
            self._jobs[job.id] = job
            self._queues[user_id].append(job.id)
        return job

    def supersede(self, user_id: str) -> list[Job]:
        """Mark a user's still-queued jobs as superseded and return them."""

        superseded: list[Job] = []
        with self._lock:
            for job in self._jobs.values():
                if job.user_id == user_id and job.status == "queued":
                    job.status = "superseded"
                    job.completed_at = datetime.now(UTC)
                    superseded.append(job)
        return superseded

    def dequeue(self) -> Job | None:
        with self._lock:
            for user_id in list(self._queues):
                queue = self._queues[user_id]
                while queue:
                    job_id = queue[0]
                    job = self._jobs[job_id]
                    if job.status != "queued":
                        queue.popleft()
                        continue
                    queue.popleft()
                    job.status = "leased"
                    job.attempts += 1
                    job.lease_revision += 1
                    job.started_at = datetime.now(UTC)
                    return job
        return None

    def lease(self, job_id: str, expected_revision: int) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.lease_revision != expected_revision:
                return None
            job.lease_revision += 1
            return job

    def complete(self, job_id: str, lease_revision: int) -> bool:
        return self._finish(job_id, lease_revision, "completed")

    def fail(self, job_id: str, lease_revision: int, error_code: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.lease_revision != lease_revision:
                return False
            if job.attempts >= 3:
                job.status = "dead"
            else:
                job.status = "queued"
                job.error_code = error_code
                job.completed_at = None
                self._queues[job.user_id].append(job.id)
            return True

    def _finish(self, job_id: str, lease_revision: int, status: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.lease_revision != lease_revision:
                return False
            job.status = status
            job.completed_at = datetime.now(UTC)
            return True

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)
