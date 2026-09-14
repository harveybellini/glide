"""Local scheduled-run dispatcher mirroring the deployed EventBridge rule.

Every tick it enqueues a check for each watching sample tenant that is due,
through the same queue path as manual and decision-triggered work, so
background updates keep working while the browser is closed. The deployed
equivalent scans persisted settings; locally the in-memory tenant registry is
the source. Both share ``is_due`` so the interval ceiling cannot drift between
the two.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from glide.adapters.interfaces import StateStore
from glide.api.demo_store import DemoSessionStore
from glide.domain.models import Run, RunStatus
from glide.jobs.queue import JobQueue
from glide.jobs.schedule_state import (
    InMemoryScheduleStateStore,
    ScheduleStateStore,
    is_due,
    next_bucket,
)


@dataclass
class LocalDispatcher:
    demo_store: DemoSessionStore
    state_store: StateStore
    queue: JobQueue
    poll_interval_seconds: float = 300.0
    schedule_store: ScheduleStateStore | None = None

    def __post_init__(self) -> None:
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        if self.schedule_store is None:
            self.schedule_store = InMemoryScheduleStateStore()

    def tick(self, now: datetime | None = None) -> int:
        tick = now or datetime.now(UTC)
        enqueued = 0
        for session in self.demo_store.enabled_sessions():
            settings = session.settings
            if not settings.background_check:
                continue
            previous = self.schedule_store.get(settings.user_id)
            if not is_due(
                previous,
                interval_minutes=settings.background_interval_minutes,
                now=tick,
            ):
                continue
            self.schedule_store.save(
                settings.user_id,
                next_bucket(
                    previous,
                    last_viewed_at=settings.last_viewed_at,
                    now=tick,
                ),
            )
            run_id = f"run-{uuid.uuid4().hex}"
            self.state_store.save_run(
                Run(
                    id=run_id,
                    user_id=settings.user_id,
                    trigger="schedule",
                    status=RunStatus.QUEUED,
                    lease_revision=1,
                    source_fingerprint="",
                    started_at=tick,
                )
            )
            self.queue.enqueue(settings.user_id, "schedule", run_id=run_id)
            enqueued += 1
        return enqueued

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
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - a failed tick retries next round
                continue
