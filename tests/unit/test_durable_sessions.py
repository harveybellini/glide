from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from glide.adapters.fixtures import local_datetime
from glide.adapters.sqlite import SqliteStateStore
from glide.api.demo_store import DemoSessionStore
from glide.api.run_service import build_run_processor
from glide.domain.models import DecisionStatus, Run, RunStatus
from glide.jobs.queue import Job

DAY = date(2026, 9, 9)


def process(store, sessions, user_id: str, run_id: str) -> None:
    # The API persists a queued placeholder before enqueueing; mirror it.
    store.save_run(
        Run(
            id=run_id,
            user_id=user_id,
            trigger="sample",
            status=RunStatus.QUEUED,
            lease_revision=1,
            source_fingerprint="",
            started_at=datetime.now(UTC),
        )
    )
    build_run_processor(sessions, store)(
        Job(id=run_id, user_id=user_id, trigger="sample", run_id=run_id)
    )


def test_snapshot_roundtrips_through_sqlite(tmp_path) -> None:
    store = SqliteStateStore(tmp_path / "glide.db")
    sessions = DemoSessionStore(state_store=store)
    session = sessions.create(day=DAY)

    snapshot = store.get_sample_snapshot(session.settings.user_id)
    assert snapshot is not None
    assert snapshot.session_id == session.id
    assert len(snapshot.source_events) == 3

    restored = store.get_sample_snapshot_by_session(session.id)
    assert restored is not None
    assert restored == snapshot
    store.close()


def test_cold_worker_restores_session_and_continues_without_duplicates(
    tmp_path,
) -> None:
    store = SqliteStateStore(tmp_path / "glide.db")
    warm = DemoSessionStore(state_store=store)
    session = warm.create(day=DAY)
    user_id = session.settings.user_id

    process(store, warm, user_id, "run-1")
    assert len(store.get_blocks(user_id)) == 1

    # The user moves the middle appointment; the route would persist this,
    # so exercise the same persist path directly.
    session.calendar.move(
        "occ_b",
        local_datetime(DAY, 10, 45),
        local_datetime(DAY, 11, 15),
    )
    session.persist()

    # A different Lambda instance sees no in-memory session and must rebuild
    # it from the snapshot before processing the queued job.
    cold = DemoSessionStore(state_store=store)
    restored = cold.get(session.id)
    assert restored.calendar.events() == session.calendar.events()

    process(store, cold, user_id, "run-2")
    assert len(store.get_blocks(user_id)) == 2
    assert len(cold.get_by_user(user_id).calendar.events()) == 3
    store.close()


def test_skipped_journeys_survive_cold_restore(tmp_path) -> None:
    store = SqliteStateStore(tmp_path / "glide.db")
    warm = DemoSessionStore(state_store=store)
    session = warm.create(day=DAY)
    user_id = session.settings.user_id
    process(store, warm, user_id, "run-1")
    decision = store.get_decisions(user_id)[0]
    session.skipped_journeys.add(decision.journey_key)
    store.save_decisions(
        [decision.model_copy(update={"status": DecisionStatus.RESOLVED})]
    )
    session.persist()

    cold = DemoSessionStore(state_store=store)
    process(store, cold, user_id, "run-2")

    assert len(store.get_blocks(user_id)) == 1
    assert store.get_latest_run(user_id).status.value == "completed"
    store.close()


def test_resolved_conflict_closes_the_stale_open_decision(tmp_path) -> None:
    store = SqliteStateStore(tmp_path / "glide.db")
    sessions = DemoSessionStore(state_store=store)
    session = sessions.create(day=DAY)
    user_id = session.settings.user_id
    process(store, sessions, user_id, "run-1")
    open_ids = {
        decision.id
        for decision in store.get_decisions(user_id)
        if decision.status == DecisionStatus.OPEN
    }
    assert len(open_ids) == 1

    session.calendar.move(
        "occ_b",
        local_datetime(DAY, 10, 45),
        local_datetime(DAY, 11, 15),
    )
    session.persist()
    process(store, sessions, user_id, "run-2")

    decisions = store.get_decisions(user_id)
    assert all(decision.status == DecisionStatus.STALE for decision in decisions)
    assert {decision.id for decision in decisions} == open_ids
    store.close()


def test_warm_store_reloads_a_snapshot_changed_by_another_instance(tmp_path) -> None:
    store = SqliteStateStore(tmp_path / "glide.db")
    writer = DemoSessionStore(state_store=store)
    session = writer.create(day=DAY)
    reader = DemoSessionStore(state_store=store)
    reader.get(session.id)

    session.calendar.move(
        "occ_b",
        local_datetime(DAY, 10, 45),
        local_datetime(DAY, 11, 15),
    )
    session.persist()

    refreshed = reader.get(session.id)
    assert refreshed.calendar.events() == session.calendar.events()
    store.close()


def test_expired_snapshot_cannot_restore_a_session(tmp_path) -> None:
    store = SqliteStateStore(tmp_path / "glide.db")
    sessions = DemoSessionStore(state_store=store)
    session = sessions.create(day=DAY)
    snapshot = store.get_sample_snapshot(session.settings.user_id)
    assert snapshot is not None
    store.save_sample_snapshot(
        snapshot.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
    )

    assert store.get_sample_snapshot(session.settings.user_id) is None
    assert store.get_sample_snapshot_by_session(session.id) is None
    store.close()


def test_in_memory_store_serves_cached_sessions_and_delete() -> None:
    sessions = DemoSessionStore()
    created = sessions.create(day=DAY)

    assert created.last_result is None
    assert sessions.get(created.id).id == created.id
    assert sessions.get_by_user(created.settings.user_id).id == created.id

    sessions.delete(created.id)

    with pytest.raises(KeyError):
        sessions.get(created.id)


def test_last_result_returns_most_recent_completed_run() -> None:
    sessions = DemoSessionStore()
    session = sessions.create(day=DAY)
    now = datetime(2026, 9, 9, 8, 0, tzinfo=UTC)

    first = session.run(now=now, run_id="run-a")
    second = session.run(now=now, run_id="run-b")

    assert session.last_result is second
    assert session.last_result is not first
    assert {run_id for run_id in session.results} == {"run-a", "run-b"}


def test_run_during_reset_does_not_repopulate_results() -> None:
    sessions = DemoSessionStore()
    session = sessions.create(day=DAY)
    original_run = session.workflow.run

    def racing_run(*args, **kwargs):
        # A reset lands while this run is executing.
        session.generation += 1
        return original_run(*args, **kwargs)

    session.workflow.run = racing_run

    result = session.run(
        now=datetime(2026, 9, 9, 8, 0, tzinfo=UTC),
        run_id="run-1",
    )

    assert result.run.id == "run-1"
    assert session.results == {}
    assert session.receipts == []
    assert session.last_result is None
