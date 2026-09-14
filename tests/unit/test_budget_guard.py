from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError
from glide.deploy.budget_guard import Clients, Settings, credit_snapshot, handle

NOW = datetime(2026, 9, 14, 18, tzinfo=UTC)


def settings(*, reserve: str = "30") -> Settings:
    return Settings(
        account_id="123456789012",
        total_credit_usd=Decimal("180"),
        reserve_usd=Decimal(reserve),
        expiry_buffer_hours=24,
        api_function="api",
        worker_function="worker",
        dispatcher_function="dispatcher",
        queue_arn="arn:aws:sqs:eu-west-1:123456789012:jobs.fifo",
        api_id="api-id",
        distribution_id="distribution-id",
        dispatcher_schedule_name="dispatcher-schedule",
        alarm_topic_arn="arn:aws:sns:eu-west-1:123456789012:alarms",
        budget_name="glide-credit-guard",
    )


class FakeBilling:
    def __init__(self, credits: list[dict], *, denied: bool = False) -> None:
        self.credits = credits
        self.denied = denied
        self.requests: list[dict] = []

    def get_credits(self, **kwargs):
        self.requests.append(kwargs)
        if self.denied:
            raise ClientError(
                {
                    "Error": {
                        "Code": "AccessDeniedException",
                        "Message": "IAM user access not activated",
                    }
                },
                "GetCredits",
            )
        return {"credits": self.credits}


class FakeBudgets:
    def __init__(self, actual_spend: str = "13.23") -> None:
        self.actual_spend = actual_spend

    def describe_budget(self, *, AccountId: str, BudgetName: str):
        assert AccountId == "123456789012"
        assert BudgetName == "glide-credit-guard"
        return {
            "Budget": {
                "CalculatedSpend": {"ActualSpend": {"Amount": self.actual_spend, "Unit": "USD"}}
            }
        }


class FakeLambda:
    def __init__(self) -> None:
        self.concurrency: dict[str, int] = {}
        self.mapping_state = "Enabled"

    def get_function_concurrency(self, *, FunctionName: str):
        if FunctionName not in self.concurrency:
            return {}
        return {"ReservedConcurrentExecutions": self.concurrency[FunctionName]}

    def put_function_concurrency(self, *, FunctionName: str, ReservedConcurrentExecutions: int):
        self.concurrency[FunctionName] = ReservedConcurrentExecutions

    def delete_function_concurrency(self, *, FunctionName: str):
        self.concurrency.pop(FunctionName, None)

    def list_event_source_mappings(self, **_kwargs):
        return {"EventSourceMappings": [{"UUID": "mapping", "State": self.mapping_state}]}

    def update_event_source_mapping(self, *, UUID: str, Enabled: bool):
        assert UUID == "mapping"
        self.mapping_state = "Enabled" if Enabled else "Disabled"


class FakeApiGateway:
    def __init__(self) -> None:
        self.disabled = False

    def get_api(self, *, ApiId: str):
        assert ApiId == "api-id"
        return {"DisableExecuteApiEndpoint": self.disabled}

    def update_api(self, *, ApiId: str, DisableExecuteApiEndpoint: bool):
        assert ApiId == "api-id"
        self.disabled = DisableExecuteApiEndpoint


class FakeCloudFront:
    def __init__(self) -> None:
        self.enabled = True

    def get_distribution_config(self, *, Id: str):
        assert Id == "distribution-id"
        return {"ETag": "etag", "DistributionConfig": {"Enabled": self.enabled}}

    def update_distribution(self, *, Id: str, IfMatch: str, DistributionConfig: dict):
        assert Id == "distribution-id"
        assert IfMatch == "etag"
        self.enabled = DistributionConfig["Enabled"]


class FakeScheduler:
    def __init__(self) -> None:
        self.state = "ENABLED"

    def get_schedule(self, *, Name: str):
        assert Name == "dispatcher-schedule"
        return {
            "Name": Name,
            "State": self.state,
            "FlexibleTimeWindow": {"Mode": "OFF"},
            "ScheduleExpression": "rate(5 minutes)",
            "Target": {"Arn": "target", "RoleArn": "role"},
        }

    def update_schedule(self, **kwargs):
        self.state = kwargs["State"]


class FakeSns:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def publish(self, **kwargs):
        self.messages.append(kwargs)


def credit(amount: str, *, end: datetime | None = None, status: str = "ENABLED"):
    return {
        "creditStatus": status,
        "estimatedAmount": {"currencyCode": "USD", "currencyAmount": amount},
        "endDate": end or NOW + timedelta(days=30),
    }


def clients(amount: str) -> Clients:
    return Clients(
        billing=FakeBilling([credit(amount)]),
        budgets=FakeBudgets(),
        lambda_=FakeLambda(),
        apigateway=FakeApiGateway(),
        cloudfront=FakeCloudFront(),
        scheduler=FakeScheduler(),
        sns=FakeSns(),
    )


def test_credit_snapshot_uses_estimated_usable_credit() -> None:
    billing = FakeBilling(
        [
            credit("40"),
            credit("10", end=NOW + timedelta(hours=12)),
            credit("99", status="DISABLED"),
        ]
    )

    result = credit_snapshot(billing, settings(), now=NOW)

    assert result["usable_credit_usd"] == "40.00"
    assert result["enabled_credit_count"] == 2
    assert result["expiring_credit_count"] == 1
    assert billing.requests[0]["accountId"] == "123456789012"


def test_credit_snapshot_falls_back_to_the_gross_spend_budget() -> None:
    result = credit_snapshot(
        FakeBilling([], denied=True),
        settings(),
        budgets=FakeBudgets("13.23"),
        now=NOW,
    )

    assert result["usable_credit_usd"] == "166.77"
    assert result["gross_spend_usd"] == "13.23"
    assert result["source"] == "aws-budget-fallback"


def test_hourly_check_shuts_every_entry_point_at_reserve() -> None:
    fake = clients("30")

    result = handle({}, fake, settings(), now=NOW)

    assert result["application"]["state"] == "disabled"
    assert fake.lambda_.concurrency == {"api": 0, "worker": 0, "dispatcher": 0}
    assert fake.lambda_.mapping_state == "Disabled"
    assert fake.apigateway.disabled is True
    assert fake.cloudfront.enabled is False
    assert fake.scheduler.state == "DISABLED"
    assert len(fake.sns.messages) == 1


def test_check_above_reserve_is_read_only_and_restore_is_guarded() -> None:
    fake = clients("166.77")

    result = handle({}, fake, settings(), now=NOW)

    assert result["application"] == {"state": "unchanged"}
    assert fake.lambda_.concurrency == {}
    assert fake.apigateway.disabled is False
    assert fake.cloudfront.enabled is True

    fake.billing.credits = [credit("20")]
    with pytest.raises(RuntimeError, match="Refusing to restore"):
        handle({"action": "restore"}, fake, settings(), now=NOW)


def test_budget_sns_notification_shuts_down_without_waiting_for_credit_read() -> None:
    fake = clients("166.77")
    event = {"Records": [{"EventSource": "aws:sns", "Sns": {"Message": "budget"}}]}

    result = handle(event, fake, settings(), now=NOW)

    assert result["state"] == "disabled"
    assert fake.billing.requests == []
