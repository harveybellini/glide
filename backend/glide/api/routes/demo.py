from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from datetime import time as datetime_time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError

from glide.adapters.interfaces import StateStore
from glide.api.demo_store import DemoSession, DemoSessionStore
from glide.api.deps import (
    LiveUser,
    Principal,
    get_demo_session,
    get_demo_store,
    get_principal,
    get_queue,
    get_state_store,
)
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
    StoragePolicyStatus,
    UserSettings,
)
from glide.jobs.queue import JobQueue

router = APIRouter(prefix="/api", tags=["sample"])

MAX_PLACE_LABEL_CHARS = 200


def _user_id(principal: Principal) -> str:
    return principal.settings.user_id


def _validated_place(
    payload: dict[str, object] | None,
    *,
    detail: str,
) -> PlaceRef:
    """Validate a wire place and bound the text that reaches the prompt."""

    try:
        place = PlaceRef.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        ) from exc
    if len(place.label) > MAX_PLACE_LABEL_CHARS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The selected place name is too long.",
        )
    return place


def _queued_run(user_id: str, trigger: str) -> Run:
    return Run(
        id=f"run-{uuid.uuid4().hex}",
        user_id=user_id,
        trigger=trigger,
        status=RunStatus.QUEUED,
        lease_revision=1,
        source_fingerprint="",
        started_at=datetime.now(UTC),
    )


@router.get("/me", response_model=UserSettings)
def get_me(
    principal: Annotated[Principal, Depends(get_principal)],
) -> UserSettings:
    return principal.settings


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
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    state_store: Annotated[StateStore, Depends(get_state_store)] = None,
    requested_date: date | None = None,
) -> DayResponse:
    user_id = _user_id(principal)
    if isinstance(principal, DemoSession):
        if requested_date is not None and requested_date != principal.day:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The sample session only covers its configured sample date.",
            )
        return DayResponse(
            date=principal.day,
            source_events=principal.calendar.events(),
            travel_blocks=state_store.get_blocks(user_id),
            decisions=[
                decision
                for decision in state_store.get_decisions(user_id)
                if decision.status == DecisionStatus.OPEN
            ],
            last_run=state_store.get_latest_run(user_id),
            label="Sample calendar - simulated routes",
        )

    settings = principal.settings
    now = request.app.state.clock()
    window_start = now - timedelta(hours=1)
    window_end = now + timedelta(hours=48)
    calendar = request.app.state.calendar_factory(settings)
    source_events = calendar.list_events(
        calendar_id=settings.source_calendar_id,
        window_start=window_start,
        window_end=window_end,
    )
    travel_blocks = calendar.list_blocks(
        calendar_id="primary",
        window_start=window_start,
        window_end=window_end,
    )
    return DayResponse(
        date=now.date(),
        source_events=source_events,
        travel_blocks=[
            block for block in travel_blocks if block.user_id in {"", user_id}
        ],
        decisions=[
            decision
            for decision in state_store.get_decisions(user_id)
            if decision.status == DecisionStatus.OPEN
        ],
        last_run=state_store.get_latest_run(user_id),
        label="Your calendar - real routes",
    )


@router.post("/runs", response_model=RunQueuedResponse, status_code=202)
def queue_run(
    principal: Annotated[Principal, Depends(get_principal)],
    body: RunRequest | None = None,
    queue: Annotated[JobQueue, Depends(get_queue)] = None,
    state_store: Annotated[StateStore, Depends(get_state_store)] = None,
) -> RunQueuedResponse:
    user_id = _user_id(principal)
    if isinstance(principal, LiveUser):
        trigger = "live"
    else:
        trigger = body.trigger if body is not None else "sample"
    queued = _queued_run(user_id, trigger)
    state_store.save_run(queued)
    queue.enqueue(user_id, trigger, run_id=queued.id)
    return RunQueuedResponse(run_id=queued.id, status=queued.status.value)


@router.get("/runs/{run_id}", response_model=RunResultResponse)
def get_run(
    run_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    state_store: Annotated[StateStore, Depends(get_state_store)],
) -> RunResultResponse:
    user_id = _user_id(principal)
    run = state_store.get_run(run_id)
    if run is None or run.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found for this account.",
        )
    decisions = [
        decision
        for decision in state_store.get_decisions(user_id)
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
        travel_blocks=state_store.get_blocks(user_id),
        receipts=state_store.get_receipts(run_id),
    )


@router.get("/activity", response_model=ActivityResponse)
def get_activity(
    principal: Annotated[Principal, Depends(get_principal)],
    state_store: Annotated[StateStore, Depends(get_state_store)] = None,
) -> ActivityResponse:
    return ActivityResponse(
        receipts=state_store.get_user_receipts(_user_id(principal))
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
    principal: Annotated[Principal, Depends(get_principal)],
    state_store: Annotated[StateStore, Depends(get_state_store)] = None,
) -> DecisionListResponse:
    decisions = sorted(
        state_store.get_decisions(_user_id(principal)),
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
    principal: Annotated[Principal, Depends(get_principal)],
    state_store: Annotated[StateStore, Depends(get_state_store)] = None,
    queue: Annotated[JobQueue, Depends(get_queue)] = None,
) -> ResolveDecisionResponse:
    user_id = _user_id(principal)
    decision = next(
        (
            candidate
            for candidate in state_store.get_decisions(user_id)
            if candidate.id == decision_id
        ),
        None,
    )
    if decision is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Decision not found for this account.",
        )
    if decision.status != DecisionStatus.OPEN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This decision has already been resolved or superseded.",
        )
    if body.action not in decision.allowed_actions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Action '{body.action}' is not available for this decision.",
        )
    if isinstance(principal, DemoSession) and body.action != "skip_journey":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This sample only supports skip_journey. Correct a location or edit "
            "an appointment in the source calendar and run a new check.",
        )
    if body.action == "correct_location":
        if not isinstance(principal, LiveUser) or body.place is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Choose a stored place candidate to correct this location.",
            )
        place = _validated_place(body.place, detail="The selected place is invalid.")
        if place.storage_policy_status != StoragePolicyStatus.STORAGE_ALLOWED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The selected place was not retrieved for persistent storage.",
            )
        place = place.model_copy(update={"confirmed": True})
        if decision.reason == "unknown_start":
            setting_updates: dict[str, object] = {"start_place": place}
        else:
            setting_updates = {
                "location_overrides": {
                    **principal.settings.location_overrides,
                    decision.occurrence_id: place,
                }
            }
        corrected_settings = principal.settings.model_copy(
            update={
                **setting_updates,
                "revision": principal.settings.revision + 1,
            }
        )
        state_store.save_settings(corrected_settings)
    updated = decision.model_copy(
        update={"status": DecisionStatus.RESOLVED, "resolution": body.action}
    )
    state_store.save_decisions([updated])

    if isinstance(principal, DemoSession):
        principal.skipped_journeys.add(decision.journey_key)
        principal.persist()

    # An answer triggers a fresh bounded run against current events so the
    # persisted choice is applied without waiting for the next dispatch.
    queued = _queued_run(user_id, "decision")
    state_store.save_run(queued)
    queue.enqueue(user_id, "decision", run_id=queued.id)
    return ResolveDecisionResponse(decision=updated, run_id=queued.id)


@router.patch("/settings", response_model=UserSettings)
def patch_settings(
    body: SettingsPatch,
    principal: Annotated[Principal, Depends(get_principal)],
    state_store: Annotated[StateStore, Depends(get_state_store)] = None,
) -> UserSettings:
    updates: dict[str, object] = {}
    if body.padding_minutes is not None:
        updates["padding_minutes"] = body.padding_minutes
    if body.enabled is not None:
        updates["enabled"] = body.enabled
    if body.time_zone is not None:
        updates["time_zone"] = body.time_zone
    if "earliest_departure" in body.model_fields_set:
        # An explicit null clears the field; omitting it leaves the stored value
        # alone. The frontend sends null when the owner empties the input, which
        # is the only way back to "no earliest departure" once one is set.
        if body.earliest_departure is None:
            updates["earliest_departure"] = None
        else:
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
        if body.start_place is None:
            updates["start_place"] = None
        else:
            start_place = _validated_place(
                body.start_place,
                detail="The selected start place is invalid.",
            )
            if (
                isinstance(principal, LiveUser)
                and start_place.storage_policy_status
                != StoragePolicyStatus.STORAGE_ALLOWED
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="The selected start place cannot be stored.",
                )
            updates["start_place"] = start_place.model_copy(
                update={"confirmed": True}
            )

    notification_change = (
        "notification_email" in body.model_fields_set
        or body.notify_on_decisions is not None
    )
    if notification_change and not isinstance(principal, LiveUser):
        # Anonymous sample sessions have no address to contact and no calendar
        # to act on; decision emails are a signed-in feature by design.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connect Google Calendar before enabling decision emails.",
        )
    if "notification_email" in body.model_fields_set:
        updates["notification_email"] = body.notification_email or None
    if body.notify_on_decisions is not None:
        updates["notify_on_decisions"] = body.notify_on_decisions

    if isinstance(principal, LiveUser):
        updated = principal.settings.model_copy(
            update={**updates, "revision": principal.settings.revision + 1}
        )
        state_store.save_settings(updated)
        return updated

    updated = principal.settings.model_copy(update=updates)
    principal.settings = updated
    principal.workflow.settings = updated
    state_store.save_settings(updated)
    return updated


@router.post("/pause", response_model=UserSettings)
def pause_automation(
    principal: Annotated[Principal, Depends(get_principal)],
    state_store: Annotated[StateStore, Depends(get_state_store)] = None,
) -> UserSettings:
    if isinstance(principal, LiveUser):
        updated = principal.settings.model_copy(
            update={
                "enabled": False,
                "revision": principal.settings.revision + 1,
            }
        )
        state_store.save_settings(updated)
        return updated
    updated = principal.settings.model_copy(update={"enabled": False})
    principal.settings = updated
    principal.workflow.settings = updated
    state_store.save_settings(updated)
    return updated


@router.post("/resume", response_model=UserSettings)
def resume_automation(
    principal: Annotated[Principal, Depends(get_principal)],
    state_store: Annotated[StateStore, Depends(get_state_store)] = None,
) -> UserSettings:
    if isinstance(principal, LiveUser):
        updated = principal.settings.model_copy(
            update={
                "enabled": True,
                "revision": principal.settings.revision + 1,
            }
        )
        state_store.save_settings(updated)
        return updated
    updated = principal.settings.model_copy(update={"enabled": True})
    principal.settings = updated
    principal.workflow.settings = updated
    state_store.save_settings(updated)
    return updated
