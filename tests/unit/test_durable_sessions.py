from __future__ import annotations

from datetime import UTC, date, datetime

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
