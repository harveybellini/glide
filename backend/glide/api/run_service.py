"""Queued run execution for the local sample API.

The deployed system funnels every check into an SQS FIFO queue with one
message group per user and a Lambda worker. This module reproduces that
batch-size-one, lease-and-fence shape locally: an API request only enqueues
work, a background worker reads the live session and persists the completed
``WorkflowResult`` through the state store, and failures are retried by the
queue while reconciliation keeps replay harmless.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from glide.adapters.interfaces import StateStore
from glide.api.demo_store import DemoSessionStore
from glide.domain.decisions import close_stale_decisions
from glide.domain.models import Run, RunStatus
from glide.jobs.queue import Job


def build_run_processor(
    demo_store: DemoSessionStore,
    state_store: StateStore,
) -> Callable[[Job], None]:
    """Return the worker processor that owns a job's full lifecycle."""

    def process(job: Job) -> None:
        try:
            try:
                session = demo_store.get_by_user(job.user_id)
            except KeyError as exc:
                raise RuntimeError(f"sample session for user {job.user_id} is gone") from exc
            result = session.run(now=datetime.now(UTC), run_id=job.run_id)
        except Exception as exc:  # noqa: BLE001 - persist a safe failure code
            persist_failure(state_store, job, type(exc).__name__)
            raise
        if state_store.get_run(job.run_id) is None:
            # The queued placeholder was removed by a reset while this run was
            # executing; the run is superseded and must not be resurrected.
            return
        close_stale_decisions(state_store, result)
        state_store.save_result(result)

    return process


def persist_failure(state_store: StateStore, job: Job, code: str) -> None:
    now = datetime.now(UTC)
    existing = state_store.get_run(job.run_id)
    failed = (existing or _queued_placeholder(job, now)).model_copy(
        update={
            "status": RunStatus.FAILED,
            "ended_at": now,
            "safe_failure_code": code,
        }
    )
    state_store.save_run(failed)


def _queued_placeholder(job: Job, now: datetime) -> Run:
    return Run(
        id=job.run_id,
        user_id=job.user_id,
        trigger=job.trigger,
        status=RunStatus.QUEUED,
        lease_revision=1,
        source_fingerprint="",
        started_at=now,
    )
