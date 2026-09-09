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


def test_enqueue_defaults_run_id() -> None:
    queue = InMemoryJobQueue()

    job = queue.enqueue("user-1", "manual")

    assert job.run_id.startswith("run-")


def test_supersede_marks_only_still_queued_jobs() -> None:
    queue = InMemoryJobQueue()
    first = queue.enqueue("user-1", "manual")
    second = queue.enqueue("user-1", "manual")
    leased = queue.dequeue()
    assert leased is not None
    assert leased.id == first.id

    superseded = queue.supersede("user-1")

    assert superseded == [second]
    assert second.status == "superseded"
    assert second.completed_at is not None
    assert first.status == "leased"
    assert first.completed_at is None


def test_dequeue_skips_superseded_job_and_leases_next() -> None:
    queue = InMemoryJobQueue()
    first = queue.enqueue("user-1", "manual")
    second = queue.enqueue("user-1", "manual")
    queue.supersede("user-1")
    fresh = queue.enqueue("user-1", "manual")

    leased = queue.dequeue()

    assert leased is not None
    assert leased.id == fresh.id
    assert leased.status == "leased"
    assert queue.get(first.id).status == "superseded"
    assert queue.get(second.id).status == "superseded"


def test_lease_increments_revision_and_rejects_mismatch() -> None:
    queue = InMemoryJobQueue()
    job = queue.enqueue("user-1", "manual")
    leased = queue.dequeue()
    assert leased is not None
    revision = leased.lease_revision

    renewed = queue.lease(job.id, revision)
    assert renewed is not None
    assert renewed.lease_revision == revision + 1

    assert queue.lease(job.id, 0) is None
    assert queue.lease("missing", 1) is None


def test_fail_requeues_with_error_code_then_marks_dead_after_retries() -> None:
    queue = InMemoryJobQueue()
    job = queue.enqueue("user-1", "manual")

    for expected_attempt in (1, 2):
        leased = queue.dequeue()
        assert leased is not None
        assert leased.attempts == expected_attempt
        assert queue.fail(job.id, leased.lease_revision, "boom")
        requeued = queue.get(job.id)
        assert requeued.status == "queued"
        assert requeued.error_code == "boom"

    final = queue.dequeue()
    assert final is not None
    assert final.attempts == 3
    assert queue.fail(job.id, final.lease_revision, "boom")
    assert queue.get(job.id).status == "dead"


def test_fail_rejects_stale_revision() -> None:
    queue = InMemoryJobQueue()
    job = queue.enqueue("user-1", "manual")
    leased = queue.dequeue()
    assert leased is not None

    assert not queue.fail(job.id, 0, "boom")
    assert queue.get(job.id).status == "leased"
