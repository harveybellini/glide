"""API Gateway + Lambda entrypoint.

Builds the same FastAPI application as local development but with the
DynamoDB state adapter, the SQS FIFO queue, and no in-process worker (the
worker Lambda drains the queue). Requires ``GLIDE_TABLE_NAME`` and
``GLIDE_QUEUE_URL`` environment variables set by the SAM stack.
"""

from __future__ import annotations

import os

import boto3
from mangum import Mangum

from glide.adapters.dynamodb import DynamoDbStateStore
from glide.api.app import create_app
from glide.jobs.sqs_queue import SqsJobQueue


def build_lambda_app():
    table_name = os.environ["GLIDE_TABLE_NAME"]
    queue_url = os.environ["GLIDE_QUEUE_URL"]
    dynamodb = boto3.client("dynamodb")
    sqs = boto3.client("sqs")
    return create_app(
        state_store=DynamoDbStateStore(dynamodb, table_name),
        job_queue=SqsJobQueue(sqs, queue_url),
        run_local_worker=False,
    )


app = build_lambda_app()
handler = Mangum(app)
