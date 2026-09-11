"""SQS FIFO triggered worker entrypoint.

Parses one batch of queue records and routes sample users to the in-process
sample processor and live users to the calendar/routing/Bedrock processor.
Until sample sessions are persisted durably across Lambda instances (see
``infra/README.md``), a worker that cannot see a sample session persists a
safe failed run rather than silently succeeding.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3

from glide.adapters.amazon_location import AmazonLocationPlaces, AmazonLocationRouter
from glide.adapters.dynamodb import DynamoDbStateStore
from glide.adapters.interfaces import StateStore
from glide.agent.runner import DeterministicAgentRunner
from glide.agent.strands_runner import build_agent_runner
from glide.api.demo_store import DemoSessionStore
from glide.api.run_service import build_run_processor, persist_failure
from glide.deploy.credentials import SecretsCredentialStore
from glide.deploy.secrets import resolve_secret_string
from glide.jobs.queue import Job
from glide.live.processor import build_live_processor

logger = logging.getLogger("glide.deploy")


def build_processor():
    state_store = DynamoDbStateStore(
        boto3.client("dynamodb"),
        os.environ["GLIDE_TABLE_NAME"],
    )
    demo_store = build_sample_store(state_store)
    sample_processor = build_run_processor(demo_store, state_store)

    secretsmanager = boto3.client("secretsmanager")
    credential_store = SecretsCredentialStore(
        client=secretsmanager,
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=resolve_secret_string(
            secretsmanager,
            os.environ["GOOGLE_CLIENT_SECRET_ARN"],
        ),
    )

    def calendar_factory(settings):
        from glide.adapters.google_calendar import GoogleCalendarAdapter

        return GoogleCalendarAdapter(credential_store.load(settings.user_id))

    region = os.environ.get("AWS_REGION")
    places_client = boto3.client("geo-places", region_name=region)
    routes_client = boto3.client("geo-routes", region_name=region)
    places = AmazonLocationPlaces(places_client)
    router = AmazonLocationRouter(
        places_client=places_client,
        routes_client=routes_client,
    )
    live_processor = build_live_processor(
        state_store=state_store,
        calendar_factory=calendar_factory,
        router_factory=lambda settings: router,
        place_search=places,
        runner=build_agent_runner(),
    )

    def process(job: Job) -> None:
        try:
            if job.user_id.startswith("sample-"):
                sample_processor(job)
            else:
                live_processor.process(job)
        except Exception as exc:
            persist_failure(state_store, job, type(exc).__name__)
            raise

    return process


def build_sample_store(state_store: StateStore) -> DemoSessionStore:
    """Build the sample-session store used inside the deployed worker.

    Sample tenants are driven by anonymous demo traffic, so they always run
    the deterministic planner. They must never reach Bedrock or Amazon
    Location even though the worker's live path is Bedrock-backed.
    """

    return DemoSessionStore(
        agent_runner=DeterministicAgentRunner(),
        state_store=state_store,
    )


_PROCESSOR = None


def handler(event: dict[str, Any], context: Any = None) -> dict[str, int]:
    del context
    global _PROCESSOR
    if _PROCESSOR is None:
        _PROCESSOR = build_processor()
    for record in event.get("Records", []):
        try:
            body = json.loads(record.get("body", "{}"))
        except json.JSONDecodeError:
            logger.warning("record=<%s> | unreadable queue body, skipped", record)
            continue
        job = Job(
            id=record.get("messageId", ""),
            user_id=body.get("user_id", ""),
            trigger=body.get("trigger", ""),
            run_id=body.get("run_id", ""),
        )
        _PROCESSOR(job)
    return {"statusCode": 200}
