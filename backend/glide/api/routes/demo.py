from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from datetime import time as datetime_time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from glide.adapters.interfaces import StateStore
from glide.api.demo_store import DemoSession, DemoSessionStore
from glide.api.deps import get_demo_session, get_demo_store, get_queue, get_state_store
from glide.api.schemas import (
    ActivityResponse,
    CreateDemoSessionRequest,
    DayResponse,
    DecisionListResponse,
    DemoSessionResponse,
    EventEditRequest,
    ResolveDecisionRequest,
    ResolveDecisionResponse,
    RunQueuedResponse,
    RunRequest,
    RunResultResponse,
    SessionHandle,
    SettingsPatch,
)
from glide.domain.models import (
    CalendarEvent,
    DecisionStatus,
    PlaceRef,
    Run,
    RunStatus,
    UserSettings,
)
from glide.jobs.queue import JobQueue

router = APIRouter(prefix="/api", tags=["sample"])


@router.get("/me", response_model=UserSettings)
def get_me(
    session: Annotated[DemoSession, Depends(get_demo_session)],
) -> UserSettings:
    return session.settings


@router.post("/demo/session", response_model=DemoSessionResponse, status_code=201)
def create_demo_session(
    store: Annotated[DemoSessionStore, Depends(get_demo_store)],
    state_store: Annotated[StateStore, Depends(get_state_store)] = None,
    body: CreateDemoSessionRequest | None = None,
) -> DemoSessionResponse:
    day = body.day if body is not None else None
    session = store.create(day)
    state_store.save_settings(session.settings)
    return DemoSessionResponse(
        session=SessionHandle(session_id=session.id),
        settings=session.settings,
        source_events=session.calendar.events(),
        sample_date=session.day,
        label="Sample calendar - simulated routes",
    )


@router.get("/day", response_model=DayResponse)
def get_day(
    session: Annotated[DemoSession, Depends(get_demo_session)],
    state_store: Annotated[StateStore, Depends(get_state_store)] = None,
    requested_date: date | None = None,
) -> DayResponse:
    if requested_date is not None and requested_date != session.day:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The sample session only covers its configured sample date.",
        )
    user_id = session.settings.user_id
    return DayResponse(
        date=session.day,
        source_events=session.calendar.events(),
        travel_blocks=state_store.get_blocks(user_id),
        decisions=[
            decision
            for decision in state_store.get_decisions(user_id)
            if decision.status == DecisionStatus.OPEN
        ],
        last_run=state_store.get_latest_run(user_id),
        label="Sample calendar - simulated routes",
    )


@router.post("/runs", response_model=RunQueuedResponse, status_code=202)
def queue_run(
    session: Annotated[DemoSession, Depends(get_demo_session)],
    body: RunRequest | None = None,
    queue: Annotated[JobQueue, Depends(get_queue)] = None,
    state_store: Annotated[StateStore, Depends(get_state_store)] = None,
) -> RunQueuedResponse:
    trigger = body.trigger if body is not None else "sample"
    run_id = f"run-{uuid.uuid4().hex}"
    queued = Run(
        id=run_id,
        user_id=session.settings.user_id,
        trigger=trigger,
        status=RunStatus.QUEUED,
        lease_revision=1,
        source_fingerprint="",
        started_at=datetime.now(UTC),
    )
    state_store.save_run(queued)
    queue.enqueue(session.settings.user_id, trigger, run_id=run_id)
    return RunQueuedResponse(run_id=run_id, status=queued.status.value)


@router.get("/runs/{run_id}", response_model=RunResultResponse)
def get_run(
    run_id: str,
    session: Annotated[DemoSession, Depends(get_demo_session)],
    state_store: Annotated[StateStore, Depends(get_state_store)],
) -> RunResultResponse:
    run = state_store.get_run(run_id)
    if run is None or run.user_id != session.settings.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found in this sample session.",
        )
    decisions = [
        decision
        for decision in state_store.get_decisions(session.settings.user_id)
        if decision.source_revision == run.source_fingerprint
        and decision.status == DecisionStatus.OPEN
    ]
    return RunResultResponse(
        run=run,
        plans=[
            plan.model_dump(mode="json")
            for plan in state_store.get_plans(run_id)
        ],
        decisions=decisions,
        travel_blocks=state_store.get_blocks(session.settings.user_id),
        receipts=state_store.get_receipts(run_id),
    )


@router.get("/activity", response_model=ActivityResponse)
def get_activity(
    session: Annotated[DemoSession, Depends(get_demo_session)],
    state_store: Annotated[StateStore, Depends(get_state_store)] = None,
) -> ActivityResponse:
    return ActivityResponse(
        receipts=state_store.get_user_receipts(session.settings.user_id)
    )


@router.patch("/demo/events/{occurrence_id}", response_model=CalendarEvent)
def edit_sample_event(
    occurrence_id: str,
    body: EventEditRequest,
    session: Annotated[DemoSession, Depends(get_demo_session)],
) -> CalendarEvent:
    try:
        updated = session.calendar.move(
            occurrence_id=occurrence_id,
            start=body.start,
            end=body.end,
            new_location=body.location,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sample event not found.",
        ) from exc
    session.persist()
    return updated


@router.post("/demo/reset", response_model=DemoSessionResponse)
def reset_sample(
    session: Annotated[DemoSession, Depends(get_demo_session)],
    queue: Annotated[JobQueue, Depends(get_queue)] = None,
    state_store: Annotated[StateStore, Depends(get_state_store)] = None,
) -> DemoSessionResponse:
    queue.supersede(session.settings.user_id)
    state_store.clear_user(session.settings.user_id)
    session.reset()
    state_store.save_settings(session.settings)
    return DemoSessionResponse(
        session=SessionHandle(session_id=session.id),
        settings=session.settings,
        source_events=session.calendar.events(),
        sample_date=session.day,
        label="Sample calendar - simulated routes",
    )


@router.get("/decisions", response_model=DecisionListResponse)
def list_decisions(
    session: Annotated[DemoSession, Depends(get_demo_session)],
    state_store: Annotated[StateStore, Depends(get_state_store)] = None,
) -> DecisionListResponse:
    decisions = sorted(
        state_store.get_decisions(session.settings.user_id),
        key=lambda decision: decision.id,
    )
    return DecisionListResponse(decisions=decisions)


@router.post(
    "/decisions/{decision_id}/resolve",
    response_model=ResolveDecisionResponse,
)
def resolve_decision(
    decision_id: str,
    body: ResolveDecisionRequest,
    session: Annotated[DemoSession, Depends(get_demo_session)],
    state_store: Annotated[StateStore, Depends(get_state_store)] = None,
    queue: Annotated[JobQueue, Depends(get_queue)] = None,
) -> ResolveDecisionResponse:
    decision = next(
        (
            candidate
            for candidate in state_store.get_decisions(session.settings.user_id)
            if candidate.id == decision_id
        ),
        None,
    )
    if decision is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Decision not found in this sample session.",
        )
    if body.action == "skip_journey":
        session.skipped_journeys.add(decision.journey_key)
        updated = decision.model_copy(update={"status": DecisionStatus.RESOLVED})
        state_store.save_decisions([updated])
        session.persist()
        # An answer triggers a fresh bounded run against current events.
        run_id = f"run-{uuid.uuid4().hex}"
        state_store.save_run(
            Run(
                id=run_id,
                user_id=session.settings.user_id,
                trigger="decision",
                status=RunStatus.QUEUED,
                lease_revision=1,
                source_fingerprint="",
                started_at=datetime.now(UTC),
            )
        )
        queue.enqueue(session.settings.user_id, "decision", run_id=run_id)
        return ResolveDecisionResponse(decision=updated, run_id=run_id)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="This sample only supports skip_journey. Correct a location or edit "
        "an appointment in the source calendar and run a new check.",
    )


@router.patch("/settings", response_model=UserSettings)
def patch_settings(
    body: SettingsPatch,
    session: Annotated[DemoSession, Depends(get_demo_session)],
    state_store: Annotated[StateStore, Depends(get_state_store)] = None,
) -> UserSettings:
    updates: dict[str, object] = {}
    if body.padding_minutes is not None:
        updates["padding_minutes"] = body.padding_minutes
    if body.enabled is not None:
        updates["enabled"] = body.enabled
    if body.earliest_departure is not None:
        try:
            updates["earliest_departure"] = datetime_time.fromisoformat(
                body.earliest_departure
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="earliest_departure must be a 24-hour time like 06:00.",
            ) from exc
    if "start_place" in body.model_fields_set:
        updates["start_place"] = (
            PlaceRef.model_validate(body.start_place)
            if body.start_place is not None
            else None
        )

    updated = session.settings.model_copy(update=updates)
    session.settings = updated
    session.workflow.settings = updated
    state_store.save_settings(updated)
    return updated


@router.post("/pause", response_model=UserSettings)
def pause_automation(
    session: Annotated[DemoSession, Depends(get_demo_session)],
    state_store: Annotated[StateStore, Depends(get_state_store)] = None,
) -> UserSettings:
    updated = session.settings.model_copy(update={"enabled": False})
    session.settings = updated
    session.workflow.settings = updated
    state_store.save_settings(updated)
    return updated


@router.post("/resume", response_model=UserSettings)
def resume_automation(
    session: Annotated[DemoSession, Depends(get_demo_session)],
    state_store: Annotated[StateStore, Depends(get_state_store)] = None,
) -> UserSettings:
    updated = session.settings.model_copy(update={"enabled": True})
    session.settings = updated
    session.workflow.settings = updated
    state_store.save_settings(updated)
    return updated
