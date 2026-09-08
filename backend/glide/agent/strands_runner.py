"""Strands-backed ``AgentRunner`` implementation.

The runner owns the Strands agent loop (tools, limits, deadline, and one
schema-repair retry) while ``ToolHost`` owns every reference, budget, and
arithmetic result. The agent factory seam keeps the orchestration testable
without AWS credentials: production builds a real ``strands.Agent`` over
``BedrockModel``, tests substitute a scripted invoker.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from strands import Agent
from strands.hooks.events import AfterToolsEvent
from strands.models.bedrock import BedrockModel

from glide.adapters.interfaces import PlaceLookup
from glide.agent.host import ToolHost
from glide.agent.prompts import (
    SYSTEM_PROMPT,
    build_repair_prompt,
    build_user_prompt,
)
from glide.agent.runner import AgentRunner, DeterministicAgentRunner
from glide.agent.tools import (
    EstimateJourneyInput,
    EstimateJourneyOutput,
    EvaluateCandidateInput,
    EvaluateCandidateOutput,
    LookupPlaceInput,
    LookupPlaceOutput,
    ProposePlanInput,
    ProposePlanOutput,
    ReadScheduleInput,
    ReadScheduleOutput,
    RequestDecisionInput,
    RequestDecisionOutput,
    tool_input_schema,
)
from glide.domain.models import CalendarEvent, JourneyPlan, PlaceRef, UserSettings
from glide.domain.scheduling import RouteEstimator

logger = logging.getLogger("glide.agent")

DEFAULT_LIMITS: dict[str, int] = {"turns": 10}
DEFAULT_DEADLINE_SECONDS = 120.0


class StrandsAgentError(RuntimeError):
    """Base class for safe, run-scoped agent failures."""


class AgentInvocationError(StrandsAgentError):
    """The model loop raised before producing a result."""


class AgentDeadlineExceeded(StrandsAgentError):
    """The 120-second application deadline was reached."""


class AgentProposalMissing(StrandsAgentError):
    """No accepted proposal was produced after one repair retry."""


@dataclass(frozen=True)
class InvocationResult:
    stop_reason: str
    final_text: str = ""
    usage: dict[str, int] = field(default_factory=dict)


class InvokableAgent(Protocol):
    def __call__(
        self,
        prompt: str,
        *,
        limits: dict[str, int] | None = None,
        cancel_signal: threading.Event | None = None,
    ) -> InvocationResult: ...


AgentFactory = Callable[[list[Any], str, ToolHost], InvokableAgent]


def build_tools(host: ToolHost) -> list[Any]:
    """Return the six plan-mandated Strands tools bound to one host."""

    from strands import tool

    @tool(
        name="read_schedule",
        description=(
            "Read the server-bound, minimized schedule for a planning window. "
            "Every journey key, occurrence id, and time in the result is "
            "server-supplied and must not be invented or altered."
        ),
        inputSchema=tool_input_schema(ReadScheduleInput),
    )
    def read_schedule(
        run_id: str,
        window_start: str,
        window_end: str,
    ) -> ReadScheduleOutput:
        return host.read_schedule(
            run_id=run_id,
            window_start=window_start,
            window_end=window_end,
        )

    @tool(
        name="lookup_place",
        description=(
            "Resolve calendar location text to known place references. Returns "
            "up to three candidates or one confirmed alias. Only ids returned "
            "here may be passed to estimate_journey."
        ),
        inputSchema=tool_input_schema(LookupPlaceInput),
    )
    def lookup_place(query: str, region: str | None = None) -> LookupPlaceOutput:
        return host.lookup_place(query=query, region=region)

    @tool(
        name="estimate_journey",
        description=(
            "Request one provider-backed driving route between two known place "
            "references at a specific departure or arrival time. Returns a "
            "typed estimate or a typed unavailable response."
        ),
        inputSchema=tool_input_schema(EstimateJourneyInput),
    )
    def estimate_journey(
        origin_place_id: str,
        destination_place_id: str,
        mode: str,
        timing: str,
        timing_time: str,
    ) -> EstimateJourneyOutput:
        return host.estimate_journey(
            origin_place_id=origin_place_id,
            destination_place_id=destination_place_id,
            mode=mode,
            timing=timing,
            timing_time=timing_time,
        )

    @tool(
        name="evaluate_candidate",
        description=(
            "Deterministic arithmetic and conflict check for one route estimate. "
            "Returns feasibility, proposed times, and quantified shortfall. The "
            "model must copy this result rather than compute its own."
        ),
        inputSchema=tool_input_schema(EvaluateCandidateInput),
    )
    def evaluate_candidate(
        origin_available: str,
        destination_start: str,
        estimate_id: str,
        padding_minutes: int,
    ) -> EvaluateCandidateOutput:
        return host.evaluate_candidate(
            origin_available=origin_available,
            destination_start=destination_start,
            estimate_id=estimate_id,
            padding_minutes=padding_minutes,
        )

    @tool(
        name="request_decision",
        description=(
            "Request a durable, deduplicated user decision for one journey. "
            "Evidence values and allowed actions are validated server-side; "
            "no write happens through this tool."
        ),
        inputSchema=tool_input_schema(RequestDecisionInput),
    )
    def request_decision(
        journey_key: str,
        occurrence_id: str,
        reason_code: str,
        facts: dict[str, Any],
        allowed_actions: list[str],
    ) -> RequestDecisionOutput:
        return host.request_decision(
            journey_key=journey_key,
            occurrence_id=occurrence_id,
            reason_code=reason_code,
            facts=facts,
            allowed_actions=allowed_actions,
        )

    @tool(
        name="propose_plan",
        description=(
            "Submit one evidence-backed proposal covering every supplied "
            "journey pair exactly once. Grants no write authority; the "
            "executor validates and applies accepted proposals afterwards."
        ),
        inputSchema=tool_input_schema(ProposePlanInput),
    )
    def propose_plan(
        run_id: str,
        journeys: list[dict[str, Any]],
        summary: str = "",
    ) -> ProposePlanOutput:
        proposal = ProposePlanInput.model_validate(
            {"run_id": run_id, "journeys": journeys, "summary": summary}
        )
        return host.accept_proposal(proposal)

    return [
        read_schedule,
        lookup_place,
        estimate_journey,
        evaluate_candidate,
        request_decision,
        propose_plan,
    ]


def default_bedrock_model() -> BedrockModel:
    """Build the Bedrock model from environment configuration."""

    model_id = os.getenv("BEDROCK_MODEL_ID", "").strip()
    if not model_id:
        raise ValueError("BEDROCK_MODEL_ID is not set")
    config: dict[str, Any] = {"model_id": model_id}
    region = os.getenv("AWS_REGION", "").strip()
    if region:
        config["region_name"] = region
    raw_max_tokens = os.getenv("BEDROCK_MAX_TOKENS", "").strip()
    if raw_max_tokens:
        config["max_tokens"] = int(raw_max_tokens)
    return BedrockModel(**config)


def build_bedrock_agent_factory(model: Any) -> AgentFactory:
    """Return the production factory around one shared model."""

    def factory(tools: list[Any], system_prompt: str, host: ToolHost) -> InvokableAgent:
        agent = Agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            callback_handler=None,
            agent_id=f"glide-{uuid.uuid4().hex}",
            name="Glide travel planner",
        )

        def stop_after_proposal(event: AfterToolsEvent) -> None:
            if host.proposal is not None:
                event.end_turn = True

        agent.add_hook(stop_after_proposal, AfterToolsEvent)

        def invoke(
            prompt: str,
            *,
            limits: dict[str, int] | None = None,
            cancel_signal: threading.Event | None = None,
        ) -> InvocationResult:
            result = agent(prompt, limits=limits, cancel_signal=cancel_signal)
            return InvocationResult(
                stop_reason=result.stop_reason or "",
                final_text=str(result),
                usage=_usage_dict(result),
            )

        return invoke

    return factory


def _usage_dict(result: Any) -> dict[str, int]:
    try:
        invocation = result.metrics.latest_agent_invocation
        return {key: int(value) for key, value in (invocation.usage or {}).items()}
    except Exception:  # noqa: BLE001 - usage is observational, never fatal
        return {}


class StrandsAgentRunner:
    """Run one bounded Strands planning loop per workflow run."""

    def __init__(
        self,
        *,
        model: Any | None = None,
        agent_factory: AgentFactory | None = None,
        deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
        limits: dict[str, int] | None = None,
        max_events: int = 20,
        max_journeys: int = 10,
        max_routes: int = 30,
        place_search: PlaceLookup | None = None,
    ) -> None:
        if deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        if max_events <= 0 or max_journeys <= 0 or max_routes <= 0:
            raise ValueError("event, journey, and route budgets must be positive")
        self.deadline_seconds = float(deadline_seconds)
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        self.max_events = max_events
        self.max_journeys = max_journeys
        self.max_routes = max_routes
        self.place_search = place_search
        if agent_factory is not None:
            self._agent_factory = agent_factory
        else:
            self._agent_factory = build_bedrock_agent_factory(model or default_bedrock_model())

    def run(
        self,
        *,
        settings: UserSettings,
        events: list[CalendarEvent],
        place_index: dict[str, PlaceRef],
        router: RouteEstimator,
        now: datetime,
    ) -> list[JourneyPlan]:
        host = ToolHost(
            settings=settings,
            events=events,
            place_index=place_index,
            router=router,
            now=now,
            max_events=self.max_events,
            max_journeys=self.max_journeys,
            max_routes=self.max_routes,
            place_search=self.place_search,
        )
        tools = build_tools(host)
        invoker = self._agent_factory(tools, SYSTEM_PROMPT, host)

        cancel_signal = threading.Event()
        deadline = threading.Timer(self.deadline_seconds, cancel_signal.set)
        deadline.daemon = True
        deadline.start()
        started = time.monotonic()
        usage: dict[str, int] = {}
        try:
            prompt = build_user_prompt(
                run_id=host.run_id,
                window_start=host.window_start,
                window_end=host.window_end,
                time_zone=settings.time_zone,
                padding_minutes=settings.padding_minutes,
                earliest_departure=(
                    settings.earliest_departure.isoformat()
                    if settings.earliest_departure is not None
                    else None
                ),
                start_address=(
                    settings.start_place.label if settings.start_place is not None else None
                ),
                now=now,
            )
            usage.update(self._invoke(invoker, prompt, cancel_signal).usage)
            if host.proposal is None:
                self._check_deadline(started)
                usage.update(
                    self._invoke(
                        invoker,
                        build_repair_prompt(host.last_rejection),
                        cancel_signal,
                    ).usage
                )
            if host.proposal is None:
                raise AgentProposalMissing(
                    host.last_rejection or "the model produced no proposal"
                )
            plans = host.materialize_plans()
            self._log_summary(host, plans, usage)
            return plans
        finally:
            deadline.cancel()

    def _invoke(
        self,
        invoker: InvokableAgent,
        prompt: str,
        cancel_signal: threading.Event,
    ) -> InvocationResult:
        try:
            result = invoker(prompt, limits=self.limits, cancel_signal=cancel_signal)
        except StrandsAgentError:
            raise
        except Exception as exc:  # noqa: BLE001 - convert to a safe failure code
            raise AgentInvocationError(
                f"agent invocation failed ({type(exc).__name__}): {exc}"
            ) from exc
        stop_reason = result.stop_reason or ""
        if stop_reason == "cancelled" or cancel_signal.is_set():
            raise AgentDeadlineExceeded(
                "the agent run exceeded its application deadline"
            )
        if stop_reason.startswith("limit_"):
            logger.warning(
                "agent stop_reason=<%s> | budget cap reached before an accepted proposal",
                stop_reason,
            )
        return result

    def _check_deadline(self, started: float) -> None:
        if time.monotonic() - started >= self.deadline_seconds:
            raise AgentDeadlineExceeded(
                "the agent run exceeded its application deadline"
            )

    def _log_summary(
        self,
        host: ToolHost,
        plans: list[JourneyPlan],
        usage: dict[str, int],
    ) -> None:
        logger.info(
            "run_id=<%s> | plans=<%d> route_calls=<%d> tool_calls=<%d> tools=<%s> "
            "usage=<%s>",
            host.run_id,
            len(plans),
            host.route_calls,
            len(host.tool_log),
            ",".join(record.name for record in host.tool_log),
            usage,
        )


def build_agent_runner(environ: Mapping[str, str] | None = None) -> AgentRunner:
    """Select the runner from ``GLIDE_AGENT_MODE``.

    Bedrock mode requires ``BEDROCK_MODEL_ID``; when it is absent the sample
    keeps working through the deterministic runner and the fallback is logged.
    """

    env = dict(os.environ if environ is None else environ)
    mode = env.get("GLIDE_AGENT_MODE", "deterministic").strip().lower()
    if mode not in {"bedrock", "strands"}:
        return DeterministicAgentRunner()
    try:
        model = default_bedrock_model()
    except Exception as exc:  # noqa: BLE001 - configuration problem, fall back
        logger.warning(
            "GLIDE_AGENT_MODE=%s but the Bedrock model could not be built (%s); "
            "falling back to the deterministic runner",
            mode,
            type(exc).__name__,
        )
        return DeterministicAgentRunner()
    return StrandsAgentRunner(model=model)
