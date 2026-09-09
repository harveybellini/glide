"""Decision lifecycle policy tests."""

from __future__ import annotations

from datetime import UTC, datetime

from glide.domain.decisions import close_stale_decisions
from glide.domain.models import Run, RunStatus, WorkflowResult


def test_close_stale_decisions_skips_nonterminal_runs() -> None:
    class Guard:
        def get_decisions(self, user_id):
            raise AssertionError("stale scan must not run for a queued run")

        def save_decisions(self, decisions):
            raise AssertionError("stale scan must not write for a queued run")

    result = WorkflowResult(
        run=Run(
            id="run-1",
            user_id="user-1",
            trigger="manual",
            status=RunStatus.QUEUED,
            lease_revision=1,
            source_fingerprint="",
            started_at=datetime(2026, 9, 9, tzinfo=UTC),
        ),
        plans=(),
        decisions=(),
        travel_blocks=(),
        receipts=(),
        source_events=(),
    )

    close_stale_decisions(Guard(), result)
