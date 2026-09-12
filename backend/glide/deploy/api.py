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

from glide.adapters.amazon_location import AmazonLocationPlaces
from glide.adapters.dynamodb import DynamoDbStateStore
from glide.api.app import create_app
from glide.api.auth import GoogleOAuthConfig
from glide.deploy.credentials import SecretsCredentialStore
from glide.deploy.secrets import resolve_secret_key, resolve_secret_string
from glide.jobs.sqs_queue import SqsJobQueue


def build_lambda_app():
    table_name = os.environ["GLIDE_TABLE_NAME"]
    queue_url = os.environ["GLIDE_QUEUE_URL"]
    dynamodb = boto3.client("dynamodb")
    sqs = boto3.client("sqs")
    secretsmanager = boto3.client("secretsmanager")
    session_secret = resolve_secret_key(
        secretsmanager,
        os.environ["GLIDE_SESSION_SECRET_ARN"],
        "GLIDE_SESSION_SECRET",
    )
    google_client_id = os.environ["GOOGLE_CLIENT_ID"]
    google_client_secret = resolve_secret_string(
        secretsmanager,
        os.environ["GOOGLE_CLIENT_SECRET_ARN"],
    )
    credential_store = SecretsCredentialStore(
        client=secretsmanager,
        client_id=google_client_id,
        client_secret=google_client_secret,
    )
    oauth_config = GoogleOAuthConfig(
        client_id=google_client_id,
        client_secret=google_client_secret,
        redirect_uri=os.environ["GOOGLE_REDIRECT_URI"],
        secure_cookies=os.environ.get("GLIDE_SECURE_COOKIES", "").strip().lower()
        in {"1", "true"},
    )
    region = os.environ.get("AWS_REGION")
    places = AmazonLocationPlaces(boto3.client("geo-places", region_name=region))
    return create_app(
        state_store=DynamoDbStateStore(dynamodb, table_name),
        job_queue=SqsJobQueue(sqs, queue_url),
        run_local_worker=False,
        credential_store=credential_store,
        place_search=places,
        session_secret=session_secret,
        oauth_config=oauth_config,
    )


app = build_lambda_app()
handler = Mangum(
    app,
    api_gateway_base_path=f"/{os.environ.get('GLIDE_API_STAGE', 'prod')}",
)
