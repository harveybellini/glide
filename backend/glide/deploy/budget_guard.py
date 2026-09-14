"""Credit-aware circuit breaker for the deployed Glide stack.

AWS billing data is not real time, so this function deliberately shuts the
application down while promotional credit remains.  It runs hourly and is
also subscribed to the account-level AWS Budget notification topic.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)


@dataclass(frozen=True)
class Settings:
    account_id: str
    total_credit_usd: Decimal
    reserve_usd: Decimal
    expiry_buffer_hours: int
    api_function: str
    worker_function: str
    dispatcher_function: str
    queue_arn: str
    api_id: str
    distribution_id: str
    dispatcher_schedule_name: str
    alarm_topic_arn: str
    budget_name: str

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            account_id=os.environ["GLIDE_AWS_ACCOUNT_ID"],
            total_credit_usd=Decimal(os.environ.get("GLIDE_TOTAL_CREDIT_USD", "180")),
            reserve_usd=Decimal(os.environ.get("GLIDE_CREDIT_RESERVE_USD", "30")),
            expiry_buffer_hours=int(os.environ.get("GLIDE_CREDIT_EXPIRY_BUFFER_HOURS", "24")),
            api_function=os.environ["GLIDE_API_FUNCTION"],
            worker_function=os.environ["GLIDE_WORKER_FUNCTION"],
            dispatcher_function=os.environ["GLIDE_DISPATCHER_FUNCTION"],
            queue_arn=os.environ["GLIDE_QUEUE_ARN"],
            api_id=os.environ["GLIDE_API_ID"],
            distribution_id=os.environ["GLIDE_DISTRIBUTION_ID"],
            dispatcher_schedule_name=os.environ["GLIDE_DISPATCHER_SCHEDULE_NAME"],
            alarm_topic_arn=os.environ.get("GLIDE_ALARM_TOPIC_ARN", ""),
            budget_name=os.environ["GLIDE_BUDGET_NAME"],
        )


@dataclass(frozen=True)
class Clients:
    billing: Any
    budgets: Any
    lambda_: Any
    apigateway: Any
    cloudfront: Any
    scheduler: Any
    sns: Any


def _clients() -> Clients:
    return Clients(
        # The Billing API has its endpoint in us-east-1 even though Glide runs
        # in eu-west-1.
        billing=boto3.client("billing", region_name="us-east-1"),
        budgets=boto3.client("budgets", region_name="us-east-1"),
        lambda_=boto3.client("lambda"),
        apigateway=boto3.client("apigatewayv2"),
        cloudfront=boto3.client("cloudfront"),
        scheduler=boto3.client("scheduler"),
        sns=boto3.client("sns"),
    )


def _amount(credit: dict[str, Any]) -> Decimal:
    amount = credit.get("estimatedAmount") or credit.get("remainingAmount") or {}
    if amount.get("currencyCode") != "USD":
        return Decimal("0")
    return Decimal(str(amount.get("currencyAmount", "0")))


def credit_snapshot(
    billing: Any,
    settings: Settings,
    *,
    budgets: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return credit that will remain usable beyond the safety window."""

    now = now or datetime.now(UTC)
    try:
        response = billing.get_credits(
            accountId=settings.account_id,
            startDate=now - timedelta(days=365),
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "AccessDeniedException":
            raise
        if budgets is None:
            raise
        # Some accounts have the older account-wide "IAM access to Billing"
        # switch disabled. AWS Budgets still exposes the gross spend already
        # maintained for this guard, so use it without broadening account
        # access just for one Lambda role.
        LOGGER.warning("Billing GetCredits denied; using the gross-spend budget")
        budget = budgets.describe_budget(
            AccountId=settings.account_id,
            BudgetName=settings.budget_name,
        )["Budget"]
        actual = budget.get("CalculatedSpend", {}).get("ActualSpend", {})
        if actual.get("Unit") != "USD":
            raise RuntimeError("Credit guard budget did not return USD spend") from exc
        gross_spend = Decimal(str(actual.get("Amount", "0")))
        usable = max(Decimal("0"), settings.total_credit_usd - gross_spend)
        return {
            "usable_credit_usd": str(usable.quantize(Decimal("0.01"))),
            "reserve_usd": str(settings.reserve_usd.quantize(Decimal("0.01"))),
            "gross_spend_usd": str(gross_spend.quantize(Decimal("0.01"))),
            "source": "aws-budget-fallback",
            "checked_at": now.isoformat(),
        }
    safe_until = now + timedelta(hours=settings.expiry_buffer_hours)
    usable = Decimal("0")
    enabled_count = 0
    expiring_count = 0

    for credit in response.get("credits", []):
        if credit.get("creditStatus") != "ENABLED":
            continue
        enabled_count += 1
        end_date = credit.get("endDate")
        if end_date is not None and end_date <= safe_until:
            expiring_count += 1
            continue
        usable += _amount(credit)

    return {
        "usable_credit_usd": str(usable.quantize(Decimal("0.01"))),
        "reserve_usd": str(settings.reserve_usd.quantize(Decimal("0.01"))),
        "enabled_credit_count": enabled_count,
        "expiring_credit_count": expiring_count,
        "source": "billing-credit-estimate",
        "checked_at": now.isoformat(),
    }


def _record_step(
    name: str,
    operation: Callable[[], bool],
    changed: list[str],
    errors: list[str],
) -> None:
    try:
        if operation():
            changed.append(name)
    except Exception as exc:  # Continue so one failure cannot leave costly work running.
        LOGGER.exception("Budget guard step failed: %s", name)
        errors.append(f"{name}: {type(exc).__name__}: {exc}")


def _set_function_state(lambda_client: Any, name: str, enabled: bool) -> bool:
    current = lambda_client.get_function_concurrency(FunctionName=name)
    reserved = current.get("ReservedConcurrentExecutions")
    if enabled:
        if reserved is None:
            return False
        lambda_client.delete_function_concurrency(FunctionName=name)
        return True
    if reserved == 0:
        return False
    lambda_client.put_function_concurrency(
        FunctionName=name,
        ReservedConcurrentExecutions=0,
    )
    return True


def _set_queue_consumer_state(
    lambda_client: Any,
    settings: Settings,
    enabled: bool,
) -> bool:
    response = lambda_client.list_event_source_mappings(
        FunctionName=settings.worker_function,
        EventSourceArn=settings.queue_arn,
    )
    changed = False
    for mapping in response.get("EventSourceMappings", []):
        current = mapping.get("State") in {"Enabled", "Enabling"}
        if current == enabled:
            continue
        lambda_client.update_event_source_mapping(
            UUID=mapping["UUID"],
            Enabled=enabled,
        )
        changed = True
    return changed


def _set_api_state(apigateway: Any, api_id: str, enabled: bool) -> bool:
    current = apigateway.get_api(ApiId=api_id)
    disabled = bool(current.get("DisableExecuteApiEndpoint", False))
    desired_disabled = not enabled
    if disabled == desired_disabled:
        return False
    apigateway.update_api(
        ApiId=api_id,
        DisableExecuteApiEndpoint=desired_disabled,
    )
    return True


def _set_distribution_state(cloudfront: Any, distribution_id: str, enabled: bool) -> bool:
    current = cloudfront.get_distribution_config(Id=distribution_id)
    config = current["DistributionConfig"]
    if bool(config.get("Enabled")) == enabled:
        return False
    config["Enabled"] = enabled
    cloudfront.update_distribution(
        Id=distribution_id,
        IfMatch=current["ETag"],
        DistributionConfig=config,
    )
    return True


def _set_schedule_state(scheduler: Any, schedule_name: str, enabled: bool) -> bool:
    current = scheduler.get_schedule(Name=schedule_name)
    desired = "ENABLED" if enabled else "DISABLED"
    if current.get("State") == desired:
        return False

    allowed = (
        "ActionAfterCompletion",
        "Description",
        "EndDate",
        "FlexibleTimeWindow",
        "GroupName",
        "KmsKeyArn",
        "ScheduleExpression",
        "ScheduleExpressionTimezone",
        "StartDate",
        "Target",
    )
    request = {key: current[key] for key in allowed if key in current}
    request.update(Name=schedule_name, State=desired)
    scheduler.update_schedule(**request)
    return True


def set_application_state(
    clients: Clients,
    settings: Settings,
    *,
    enabled: bool,
    reason: str,
) -> dict[str, Any]:
    """Enable or disable every entry point that can create material spend."""

    changed: list[str] = []
    errors: list[str] = []
    # Stop the expensive background path first. Existing invocations may run
    # until their 200-second application deadline, but no new work can start.
    steps: list[tuple[str, Callable[[], bool]]] = [
        (
            "worker-function",
            lambda: _set_function_state(clients.lambda_, settings.worker_function, enabled),
        ),
        (
            "queue-consumer",
            lambda: _set_queue_consumer_state(clients.lambda_, settings, enabled),
        ),
        (
            "dispatcher-schedule",
            lambda: _set_schedule_state(
                clients.scheduler, settings.dispatcher_schedule_name, enabled
            ),
        ),
        (
            "dispatcher-function",
            lambda: _set_function_state(clients.lambda_, settings.dispatcher_function, enabled),
        ),
        (
            "api-function",
            lambda: _set_function_state(clients.lambda_, settings.api_function, enabled),
        ),
        (
            "api-endpoint",
            lambda: _set_api_state(clients.apigateway, settings.api_id, enabled),
        ),
        (
            "cloudfront-distribution",
            lambda: _set_distribution_state(clients.cloudfront, settings.distribution_id, enabled),
        ),
    ]
    for name, operation in steps:
        _record_step(name, operation, changed, errors)

    state = "enabled" if enabled else "disabled"
    result = {"state": state, "reason": reason, "changed": changed, "errors": errors}
    LOGGER.info("Budget guard application state: %s", result)

    if changed and settings.alarm_topic_arn:
        clients.sns.publish(
            TopicArn=settings.alarm_topic_arn,
            Subject=f"Glide credit guard: application {state}",
            Message=(f"Glide was {state}. Reason: {reason}. Changed: {', '.join(changed)}."),
        )
    if errors:
        raise RuntimeError("; ".join(errors))
    return result


def application_status(clients: Clients, settings: Settings) -> dict[str, Any]:
    function_concurrency = {}
    for name in (
        settings.api_function,
        settings.worker_function,
        settings.dispatcher_function,
    ):
        response = clients.lambda_.get_function_concurrency(FunctionName=name)
        function_concurrency[name] = response.get("ReservedConcurrentExecutions")
    mappings = clients.lambda_.list_event_source_mappings(
        FunctionName=settings.worker_function,
        EventSourceArn=settings.queue_arn,
    )
    api = clients.apigateway.get_api(ApiId=settings.api_id)
    distribution = clients.cloudfront.get_distribution_config(Id=settings.distribution_id)[
        "DistributionConfig"
    ]
    schedule = clients.scheduler.get_schedule(Name=settings.dispatcher_schedule_name)
    return {
        "function_reserved_concurrency": function_concurrency,
        "queue_consumer_states": [
            item.get("State") for item in mappings.get("EventSourceMappings", [])
        ],
        "api_enabled": not bool(api.get("DisableExecuteApiEndpoint", False)),
        "cloudfront_enabled": bool(distribution.get("Enabled")),
        "dispatcher_schedule_state": schedule.get("State"),
    }


def _is_budget_notification(event: dict[str, Any]) -> bool:
    return any(record.get("EventSource") == "aws:sns" for record in event.get("Records", []))


def handle(
    event: dict[str, Any],
    clients: Clients,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if _is_budget_notification(event):
        return set_application_state(
            clients,
            settings,
            enabled=False,
            reason="AWS Budget reported that the gross-spend limit was exceeded",
        )

    action = str(event.get("action", "check")).lower()
    if action == "status":
        return {
            "credit": credit_snapshot(clients.billing, settings, budgets=clients.budgets, now=now),
            "application": application_status(clients, settings),
        }
    if action == "shutdown":
        return set_application_state(
            clients,
            settings,
            enabled=False,
            reason="manual credit-guard shutdown",
        )
    if action == "restore":
        snapshot = credit_snapshot(clients.billing, settings, budgets=clients.budgets, now=now)
        balance = Decimal(snapshot["usable_credit_usd"])
        if balance <= settings.reserve_usd and not event.get("force"):
            raise RuntimeError(
                "Refusing to restore Glide while usable credit is at or below "
                f"the ${settings.reserve_usd} reserve"
            )
        return set_application_state(
            clients,
            settings,
            enabled=True,
            reason="manual credit-guard restore",
        )
    if action != "check":
        raise ValueError(f"Unsupported budget guard action: {action}")

    snapshot = credit_snapshot(clients.billing, settings, budgets=clients.budgets, now=now)
    balance = Decimal(snapshot["usable_credit_usd"])
    if balance <= settings.reserve_usd:
        state = set_application_state(
            clients,
            settings,
            enabled=False,
            reason=(
                f"usable promotional credit ${balance} reached the ${settings.reserve_usd} reserve"
            ),
        )
        return {"credit": snapshot, "application": state}
    LOGGER.info("Credit guard check passed: %s", snapshot)
    return {"credit": snapshot, "application": {"state": "unchanged"}}


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    return handle(event, _clients(), Settings.from_environment())
