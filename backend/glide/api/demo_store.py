"""Isolated sample sessions with an optional durable snapshot.

Sample tenants use the real deterministic workflow with synthetic calendar and
route adapters. They never have access to live Google tokens or AWS resources.
When a state store is configured, the mutable session state (day, source
events, skipped journeys) is snapshotted so a cold Lambda worker can rebuild
the tenant it is asked to process.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from glide.adapters.fixtures import (
    FixtureCalendar,
    FixtureRouter,
    canonical_settings,
    place_index,
)
from glide.adapters.interfaces import StateStore
from glide.agent.runner import AgentRunner, DeterministicAgentRunner
from glide.domain.engine import InMemoryTravelCalendar, SampleWorkflow
from glide.domain.models import (
    Decision,
    SampleSnapshot,
    UserSettings,
    WorkflowResult,
)


@dataclass
class DemoSession:
    id: str
    day: date
    settings: UserSettings
    calendar: FixtureCalendar
    router: FixtureRouter
    workflow: SampleWorkflow
    runner: AgentRunner
    created_at: datetime
    results: dict[str, WorkflowResult] = field(default_factory=dict)
    receipts: list[object] = field(default_factory=list)
    decisions: dict[str, Decision] = field(default_factory=dict)
    skipped_journeys: set[str] = field(default_factory=set)
    generation: int = 0
    on_change: Callable[[DemoSession], None] | None = field(
        default=None, repr=False
    )

    def run(self, now: datetime, run_id: str | None = None) -> WorkflowResult:
        events = self.calendar.events()
        generation = self.generation
        result = self.workflow.run(
            source_events=events,
            place_index=place_index(events),
            now=now,
            skip_journeys=self.skipped_journeys,
            run_id=run_id,
        )
        if generation != self.generation:
            # The tenant was reset while this run was executing. Its result is
            # stale and must not repopulate the fresh in-memory state.
            return result
        self.results[result.run.id] = result
        self.receipts.extend(result.receipts)
        self.decisions.update({decision.id: decision for decision in result.decisions})
        return result

    def reset(self) -> None:
        self.calendar = FixtureCalendar(day=self.day)
        self.router = FixtureRouter()
        self.workflow = SampleWorkflow(
            settings=self.settings,
            travel_calendar=InMemoryTravelCalendar(),
            router=self.router,
            runner=self.runner,
        )
        self.results.clear()
        self.receipts.clear()
        self.decisions.clear()
        self.skipped_journeys.clear()
        self.generation += 1
        self.persist()

    def snapshot(self) -> SampleSnapshot:
        return SampleSnapshot(
            user_id=self.settings.user_id,
            session_id=self.id,
            day=self.day,
            source_events=tuple(self.calendar.events()),
            skipped_journeys=tuple(sorted(self.skipped_journeys)),
            generation=self.generation,
            expires_at=self.created_at + timedelta(hours=24),
        )

    def persist(self) -> None:
        if self.on_change is not None:
            self.on_change(self)

    @property
    def last_result(self) -> WorkflowResult | None:
        if not self.results:
            return None
        # Results are stored in run order and never replaced, so the most
        # recently completed result is the last inserted value.
        return next(reversed(self.results.values()))


class DemoSessionStore:
    def __init__(
        self,
        agent_runner: AgentRunner | None = None,
        state_store: StateStore | None = None,
    ) -> None:
        self._sessions: dict[str, DemoSession] = {}
        self._by_user: dict[str, DemoSession] = {}
        self._lock = threading.Lock()
        self._agent_runner = agent_runner or DeterministicAgentRunner()
        self._state_store = state_store

    def create(self, day: date | None = None) -> DemoSession:
        sample_day = day or (date.today() + timedelta(days=1))
        settings = UserSettings.model_validate(
            canonical_settings(user_id=f"sample-{uuid.uuid4().hex}")
        )
        router = FixtureRouter()
        calendar = FixtureCalendar(day=sample_day)
        session = DemoSession(
            id=uuid.uuid4().hex,
            day=sample_day,
            settings=settings,
            calendar=calendar,
            router=router,
            workflow=SampleWorkflow(
                settings=settings,
                travel_calendar=InMemoryTravelCalendar(),
                router=router,
                runner=self._agent_runner,
            ),
            runner=self._agent_runner,
            created_at=datetime.now(UTC),
            on_change=self._persist,
        )
        with self._lock:
            self._sessions[session.id] = session
            self._by_user[session.settings.user_id] = session
        self._persist(session)
        return session

    def get(self, session_id: str) -> DemoSession:
        if self._state_store is not None:
            return self._restore_by_session(session_id)
        with self._lock:
            session = self._sessions.get(session_id)
        if session is not None:
            return session
        return self._restore_by_session(session_id)

    def get_by_user(self, user_id: str) -> DemoSession:
        if self._state_store is not None:
            return self._restore_by_user(user_id)
        with self._lock:
            session = self._by_user.get(user_id)
        if session is not None:
            return session
        return self._restore_by_user(user_id)

    def enabled_sessions(self) -> list[DemoSession]:
        with self._lock:
            user_ids = list(self._by_user)
            cached = list(self._by_user.values())
        sessions = (
            [self._restore_by_user(user_id) for user_id in user_ids]
            if self._state_store is not None
            else cached
        )
        return [session for session in sessions if session.settings.enabled]

    def delete(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is not None:
                self._by_user.pop(session.settings.user_id, None)

    def _persist(self, session: DemoSession) -> None:
        if self._state_store is not None:
            self._state_store.save_sample_snapshot(session.snapshot())

    def _restore_by_user(self, user_id: str) -> DemoSession:
        if self._state_store is None:
            raise KeyError(f"unknown sample user {user_id}")
        snapshot = self._state_store.get_sample_snapshot(user_id)
        if snapshot is None:
            raise KeyError(f"unknown sample user {user_id}")
        return self._restore(snapshot)

    def _restore_by_session(self, session_id: str) -> DemoSession:
        if self._state_store is None:
            raise KeyError(f"unknown sample session {session_id}")
        snapshot = self._state_store.get_sample_snapshot_by_session(session_id)
        if snapshot is None:
            raise KeyError(f"unknown sample session {session_id}")
        return self._restore(snapshot)

    def _restore(self, snapshot: SampleSnapshot) -> DemoSession:
        settings = self._state_store.get_settings(snapshot.user_id)
        if settings is None:
            settings = UserSettings.model_validate(
                canonical_settings(user_id=snapshot.user_id)
            )
        calendar = FixtureCalendar.restored(
            snapshot.day,
            list(snapshot.source_events),
        )
        router = FixtureRouter()
        blocks = {
            block.journey_key: block
            for block in self._state_store.get_blocks(snapshot.user_id)
        }
        session = DemoSession(
            id=snapshot.session_id,
            day=snapshot.day,
            settings=settings,
            calendar=calendar,
            router=router,
            workflow=SampleWorkflow(
                settings=settings,
                travel_calendar=InMemoryTravelCalendar(blocks=blocks),
                router=router,
                runner=self._agent_runner,
            ),
            runner=self._agent_runner,
            created_at=snapshot.expires_at - timedelta(hours=24),
            skipped_journeys=set(snapshot.skipped_journeys),
            generation=snapshot.generation,
            on_change=self._persist,
        )
        with self._lock:
            self._sessions[session.id] = session
            self._by_user[snapshot.user_id] = session
        return session
