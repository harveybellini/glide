from __future__ import annotations

import time

from glide.jobs.queue import InMemoryJobQueue
from glide.jobs.worker import LocalWorker


def test_queue_leases_and_completes_jobs() -> None:
    queue = InMemoryJobQueue()
    job = queue.enqueue("user-1", "manual")

    leased = queue.dequeue()
    assert leased is not None
    assert leased.id == job.id
    assert leased.status == "leased"
    assert leased.lease_revision == 1

    assert queue.complete(job.id, leased.lease_revision)
    assert queue.get(job.id).status == "completed"


def test_stale_lease_cannot_complete() -> None:
    queue = InMemoryJobQueue()
    job = queue.enqueue("user-1", "manual")
    leased = queue.dequeue()

    assert leased is not None
    assert not queue.complete(job.id, 0)
    assert queue.get(job.id).status == "leased"


def test_worker_retries_then_moves_to_dead_queue() -> None:
    queue = InMemoryJobQueue()
    calls: list[str] = []

    def processor(job: object) -> None:
        calls.append(job.id)
        raise RuntimeError("boom")

    worker = LocalWorker(queue=queue, processor=processor, poll_interval_seconds=0.01)
    job = queue.enqueue("user-1", "scheduled")
    worker.start()

    deadline = time.time() + 2
    while time.time() < deadline and queue.get(job.id).status != "dead":
        time.sleep(0.01)
    worker.stop()

    assert len(calls) == 3
    assert queue.get(job.id).status == "dead"
