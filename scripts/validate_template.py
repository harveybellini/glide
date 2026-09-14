"""Offline structural check for ``infra/template.yaml``.

SAM CLI is not required locally. This parses the template with CloudFormation
tags preserved as plain values, checks the resources the application depends
on, and confirms each referenced Lambda handler module exists as a file. It
does not replace ``sam validate`` before a real deployment.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml


class _Loader(yaml.SafeLoader):
    pass


def _construct_unknown(loader, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return None


for _tag in (
    "!Ref",
    "!Sub",
    "!GetAtt",
    "!Join",
    "!Select",
    "!If",
    "!FindInMap",
    "!GetAZs",
    "!ImportValue",
    "!Split",
    "!Base64",
    "!Cidr",
    "!And",
    "!Equals",
    "!Not",
    "!Or",
):
    _Loader.add_constructor(_tag, _construct_unknown)


def main(root: Path | None = None) -> int:
    if root is None:
        root = Path(__file__).resolve().parents[1]
    template_path = root / "infra" / "template.yaml"
    data = yaml.load(template_path.read_text(encoding="utf-8"), Loader=_Loader)
    resources = data["Resources"]

    required = {
        "UiBucket",
        "CloudFrontDistribution",
        "StateTable",
        "JobQueue",
        "DeadLetterQueue",
        "HttpApi",
        "ApiFunction",
        "WorkerFunction",
        "DispatcherFunction",
        "BudgetGuardFunction",
        "BudgetGuardTopic",
        "BudgetGuardTopicPolicy",
        "CreditGuardBudget",
        "SessionSecret",
    }
    missing = required - set(resources)
    if missing:
        print(f"missing resources: {sorted(missing)}")
        return 1

    handlers = {
        resources["ApiFunction"]["Properties"]["Handler"],
        resources["WorkerFunction"]["Properties"]["Handler"],
        resources["DispatcherFunction"]["Properties"]["Handler"],
        resources["BudgetGuardFunction"]["Properties"]["Handler"],
    }
    for handler in sorted(handlers):
        module_path = root / "backend" / Path(*handler.split(".")[:-1]).with_suffix(".py")
        if not module_path.exists():
            print(f"handler module not found: {module_path}")
            return 1

    table = resources["StateTable"]["Properties"]
    index_names = [index["IndexName"] for index in table["GlobalSecondaryIndexes"]]
    if "user-index" not in index_names:
        print("StateTable is missing the user-index GSI")
        return 1
    if table.get("TimeToLiveSpecification", {}).get("AttributeName") != "ttl":
        print("StateTable TTL is not enabled on the ttl attribute")
        return 1
    if not (
        table.get("PointInTimeRecoverySpecification", {})
        .get("PointInTimeRecoveryEnabled")
    ):
        print("StateTable point-in-time recovery is not enabled")
        return 1

    queue = resources["JobQueue"]["Properties"]
    if not queue.get("FifoQueue"):
        print("JobQueue is not FIFO")
        return 1

    http_api = resources["HttpApi"]["Properties"]
    access_logs = http_api.get("AccessLogSettings")
    if not access_logs:
        print("HttpApi has no AccessLogSettings")
        return 1
    if "ApiAccessLogGroup" not in resources:
        print("HttpApi access logs have no dedicated log group")
        return 1
    if access_logs.get("DestinationArn") != "ApiAccessLogGroup.Arn":
        print("HttpApi access logging must target the stack's log group")
        return 1
    log_format = str(access_logs.get("Format", ""))
    for field in ("$context.requestId", "$context.status", "$context.path"):
        if field not in log_format:
            print(f"HttpApi access log format is missing {field}")
            return 1
    default_settings = http_api.get("DefaultRouteSettings", {})
    if not default_settings.get("ThrottlingRateLimit"):
        print("HttpApi has no default route throttling")
        return 1
    if not default_settings.get("ThrottlingBurstLimit"):
        print("HttpApi has no default burst throttling")
        return 1
    # The API is a single ANY /{proxy+} route, so per-endpoint RouteSettings can
    # never match a real route key: the stage update fails and the stack drops
    # into UPDATE_ROLLBACK_FAILED (11 September incident). Guard against any
    # endpoint-keyed RouteSettings returning; default throttling covers every
    # route.
    route_settings = http_api.get("RouteSettings") or {}
    endpoint_keys = [key for key in route_settings if "/" in str(key)]
    if endpoint_keys:
        print(f"HttpApi RouteSettings must not key on endpoint paths: {endpoint_keys}")
        return 1

    if resources.get("AlarmTopic", {}).get("Type") != "AWS::SNS::Topic":
        print("no alarm topic is defined")
        return 1
    alarms = {
        name: resource["Properties"]
        for name, resource in resources.items()
        if resource.get("Type") == "AWS::CloudWatch::Alarm"
    }
    for name in (
        "DlqDepthAlarm",
        "WorkerErrorsAlarm",
        "Api5xxAlarm",
        "ApiThrottleAlarm",
    ):
        if name not in alarms:
            print(f"missing alarm: {name}")
            return 1
        if alarms[name].get("AlarmActions") != ["AlarmTopic"]:
            print(f"{name} does not notify the alarm topic")
            return 1
    dlq_dimension = alarms["DlqDepthAlarm"]["Dimensions"][0]
    if dlq_dimension.get("Value") != "DeadLetterQueue.QueueName":
        print("DlqDepthAlarm must watch the dead-letter queue")
        return 1
    worker_dimension = alarms["WorkerErrorsAlarm"]["Dimensions"][0]
    if worker_dimension.get("Value") != "WorkerFunction":
        print("WorkerErrorsAlarm must watch the worker function")
        return 1
    if alarms["Api5xxAlarm"].get("MetricName") != "5xx":
        print("Api5xxAlarm must watch the API 5xx metric")
        return 1
    metric_filter = resources.get("ApiThrottleMetricFilter", {})
    if metric_filter.get("Type") != "AWS::Logs::MetricFilter":
        print("no metric filter backs the API throttle alarm")
        return 1
    if metric_filter["Properties"].get("LogGroupName") != "ApiAccessLogGroup":
        print("the throttle metric filter must read the API access log group")
        return 1

    worker_environment = resources["WorkerFunction"]["Properties"]["Environment"][
        "Variables"
    ]
    if "AWS_REGION" in worker_environment:
        print("WorkerFunction sets the Lambda-reserved AWS_REGION variable")
        return 1
    for forbidden in ("GLIDE_SESSION_SECRET", "GOOGLE_CLIENT_SECRET"):
        if forbidden in worker_environment:
            print(f"WorkerFunction injects the secret value {forbidden}")
            return 1
    if "GOOGLE_CLIENT_SECRET_ARN" not in worker_environment:
        print("WorkerFunction is missing GOOGLE_CLIENT_SECRET_ARN")
        return 1
    if "resolve:secretsmanager" in str(worker_environment):
        print("WorkerFunction resolves a secret into the environment")
        return 1

    # The worker refreshes the Google grant during a run and persists the new
    # access token. On 12 September 2026 its role could read the per-user
    # secret but not write it, so every run that needed a refresh died with
    # AccessDenied on PutSecretValue and the calendar work never ran.
    token_actions: set[str] = set()
    for policy in resources["WorkerFunction"]["Properties"].get("Policies") or []:
        for statement in policy.get("Statement") or []:
            # A statement may itself be an Fn::If (a two-element list) rather
            # than a mapping; only inspect the literal ones.
            if not isinstance(statement, dict):
                continue
            if "glide/tokens/" not in str(statement.get("Resource", "")):
                continue
            actions = statement.get("Action")
            if isinstance(actions, str):
                token_actions.add(actions)
            else:
                token_actions.update(actions or [])
    missing_actions = {
        "secretsmanager:GetSecretValue",
        "secretsmanager:PutSecretValue",
        "secretsmanager:CreateSecret",
    } - token_actions
    if missing_actions:
        print(
            "WorkerFunction cannot persist refreshed Google tokens; missing "
            + ", ".join(sorted(missing_actions))
        )
        return 1

    api_environment = resources["ApiFunction"]["Properties"]["Environment"][
        "Variables"
    ]
    if "GOOGLE_REDIRECT_URI" not in api_environment:
        print("ApiFunction is missing GOOGLE_REDIRECT_URI")
        return 1
    if api_environment.get("GLIDE_ENV") != "production":
        print("ApiFunction must set GLIDE_ENV=production to skip local SQLite init")
        return 1
    for name, value in api_environment.items():
        if "resolve:secretsmanager" in str(value):
            print(f"ApiFunction resolves a secret into the environment: {name}")
            return 1
    for forbidden in ("GLIDE_SESSION_SECRET", "GOOGLE_CLIENT_SECRET"):
        if forbidden in api_environment:
            print(f"ApiFunction injects the secret value {forbidden}")
            return 1
    if "GLIDE_SESSION_SECRET_ARN" not in api_environment:
        print("ApiFunction is missing GLIDE_SESSION_SECRET_ARN")
        return 1
    if "GOOGLE_CLIENT_SECRET_ARN" not in api_environment:
        print("ApiFunction is missing GOOGLE_CLIENT_SECRET_ARN")
        return 1

    # Uncapped worker concurrency starved the API on 11 September 2026: the
    # account allows 10 concurrent Lambda executions, the worker filled them
    # with 120-220 s Bedrock runs, and every other route returned 503. The cap
    # lived only on the deployed event source mapping, so it was lost on the
    # next deploy until it was written here.
    worker_events = resources["WorkerFunction"]["Properties"].get("Events") or {}
    queue_event = (worker_events.get("Queue") or {}).get("Properties") or {}
    scaling = queue_event.get("ScalingConfig") or {}
    if scaling.get("MaximumConcurrency") != 2:
        print(
            "WorkerFunction must cap queue concurrency with "
            "ScalingConfig.MaximumConcurrency=2"
        )
        return 1

    # The guard must use gross account spend and an independent hourly credit
    # check. Including credits in the budget would leave its spend at zero
    # until the protection was already exhausted.
    guard = resources["BudgetGuardFunction"]["Properties"]
    guard_environment = guard["Environment"]["Variables"]
    for variable in (
        "GLIDE_CREDIT_RESERVE_USD",
        "GLIDE_TOTAL_CREDIT_USD",
        "GLIDE_API_FUNCTION",
        "GLIDE_WORKER_FUNCTION",
        "GLIDE_DISPATCHER_FUNCTION",
        "GLIDE_QUEUE_ARN",
        "GLIDE_API_ID",
        "GLIDE_DISTRIBUTION_ID",
        "GLIDE_DISPATCHER_SCHEDULE_NAME",
        "GLIDE_BUDGET_NAME",
    ):
        if variable not in guard_environment:
            print(f"BudgetGuardFunction is missing {variable}")
            return 1
    guard_events = guard.get("Events") or {}
    hourly = (guard_events.get("HourlyCreditCheck") or {}).get("Properties") or {}
    if hourly.get("ScheduleExpression") != "rate(1 hour)":
        print("BudgetGuardFunction must check the credit balance every hour")
        return 1
    if (guard_events.get("BudgetLimitReached") or {}).get("Type") != "SNS":
        print("BudgetGuardFunction must subscribe to the budget SNS topic")
        return 1
    budget = resources["CreditGuardBudget"]["Properties"]["Budget"]
    if budget.get("TimeUnit") != "CUSTOM":
        print("CreditGuardBudget must span the promotional-credit period")
        return 1
    cost_types = budget.get("CostTypes") or {}
    if cost_types.get("IncludeCredit") is not False:
        print("CreditGuardBudget must exclude credits so it measures gross spend")
        return 1
    if cost_types.get("IncludeRefund") is not False:
        print("CreditGuardBudget must exclude refunds so they cannot hide gross spend")
        return 1
    notifications = resources["CreditGuardBudget"]["Properties"].get(
        "NotificationsWithSubscribers"
    ) or []
    actual_notifications = [
        item
        for item in notifications
        if isinstance(item, dict)
        and (item.get("Notification") or {}).get("NotificationType") == "ACTUAL"
    ]
    if not actual_notifications:
        print("CreditGuardBudget has no actual-spend notification")
        return 1
    subscribers = actual_notifications[0].get("Subscribers") or []
    if not any(
        isinstance(item, dict) and item.get("SubscriptionType") == "SNS"
        for item in subscribers
    ):
        print("CreditGuardBudget does not notify the shutdown topic")
        return 1
    topic_policy = resources["BudgetGuardTopicPolicy"]["Properties"].get(
        "PolicyDocument", {}
    )
    if "budgets.amazonaws.com" not in str(topic_policy):
        print("BudgetGuardTopicPolicy does not allow AWS Budgets to publish")
        return 1

    # A deployed GLIDE_AGENT_TURNS overrides the runner's code default, so a
    # stale value here silently un-does an agent-loop turn-budget change.
    template_turns = str(worker_environment.get("GLIDE_AGENT_TURNS", "")).strip()
    runner_path = root / "backend" / "glide" / "agent" / "strands_runner.py"
    match = re.search(
        r'DEFAULT_LIMITS[^=]*=\s*\{\s*"turns":\s*(\d+)',
        runner_path.read_text(encoding="utf-8"),
    )
    if match is None:
        print("could not read DEFAULT_LIMITS turns from strands_runner.py")
        return 1
    if template_turns and template_turns != match.group(1):
        print(
            f"WorkerFunction GLIDE_AGENT_TURNS={template_turns} does not match "
            f"the runner default ({match.group(1)}); the deployed value wins"
        )
        return 1

    behaviors = resources["CloudFrontDistribution"]["Properties"][
        "DistributionConfig"
    ].get("CacheBehaviors")
    by_pattern = {
        behavior.get("PathPattern"): behavior for behavior in (behaviors or [])
    }
    api_behavior = by_pattern.get("/api/*")
    if api_behavior is None:
        print("CloudFrontDistribution must keep an /api/* cache behavior")
        return 1
    if api_behavior.get("OriginRequestPolicyId") != (
        "b689b0a8-53d0-40ab-baf2-68738e2966ac"
    ):
        print("API origin must use AllViewerExceptHostHeader")
        return 1

    # A distribution-wide 404/403 -> /index.html mapping answered every API
    # 404 with 200 text/html, so a missing run or decision looked like a
    # successful read. The SPA fallback must live on the UI behavior only.
    dist_config = resources["CloudFrontDistribution"]["Properties"][
        "DistributionConfig"
    ]
    if dist_config.get("CustomErrorResponses"):
        print(
            "CloudFrontDistribution must not map errors globally: it hides "
            "API 404s behind 200 text/html; use SpaRewriteFunction instead"
        )
        return 1
    associations = dist_config["DefaultCacheBehavior"].get("FunctionAssociations") or []
    ui_rewrites = [
        item
        for item in associations
        if item.get("EventType") == "viewer-request"
        # ``!GetAtt SpaRewriteFunction.FunctionArn`` parses as the plain string
        # "SpaRewriteFunction.FunctionArn", so match on the resource name.
        and "SpaRewriteFunction" in str(item.get("FunctionARN", ""))
    ]
    if not ui_rewrites:
        print("UI behavior must rewrite app paths with SpaRewriteFunction")
        return 1
    # CloudFront::Function exposes ``FunctionARN``; the mixed-case spelling
    # passes review but fails sam lint and leaves the association unresolved.
    for item in ui_rewrites:
        if "FunctionArn" in str(item.get("FunctionARN", "")):
            print("CloudFront function associations must use !GetAtt ...FunctionARN")
            return 1

    # index.html must be revalidated; only content-hashed assets may be cached
    # for a long time. A long-cached shell survives a deploy and points at
    # asset hashes that no longer exist.
    default_behavior = dist_config["DefaultCacheBehavior"]
    if "UiHtmlCachePolicy" not in str(default_behavior.get("CachePolicyId", "")):
        print(
            "DefaultCacheBehavior must use UiHtmlCachePolicy so index.html is "
            "revalidated after a deploy"
        )
        return 1
    assets_behavior = by_pattern.get("/assets/*")
    if assets_behavior is None:
        print("CloudFrontDistribution must cache /assets/* with a long TTL")
        return 1
    if assets_behavior.get("CachePolicyId") != (
        "658327ea-f89d-4fab-a63d-7e88639e58f6"
    ):
        print("/assets/* must use the managed CachingOptimized policy")
        return 1
    if "UiSecurityHeadersPolicy" not in str(
        default_behavior.get("ResponseHeadersPolicyId", "")
    ):
        print("DefaultCacheBehavior must attach UiSecurityHeadersPolicy")
        return 1

    # CloudFront rejects Enabl*/Brotli on a policy whose TTLs disable caching;
    # the first deploy attempt failed on exactly this.
    html_policy = resources.get("UiHtmlCachePolicy", {}).get("Properties", {}).get(
        "CachePolicyConfig", {}
    )
    if (
        html_policy.get("MinTTL") == 0
        and html_policy.get("DefaultTTL") == 0
        and html_policy.get("MaxTTL") == 0
        and (
            html_policy.get("ParametersInCacheKeyAndForwardedToOrigin", {}).get(
                "EnableAcceptEncodingGzip"
            )
            or html_policy.get("ParametersInCacheKeyAndForwardedToOrigin", {}).get(
                "EnableAcceptEncodingBrotli"
            )
        )
    ):
        print(
            "UiHtmlCachePolicy disables caching, so compression flags must be false"
        )
        return 1
    security_policy = resources.get("UiSecurityHeadersPolicy", {}).get(
        "Properties", {}
    ).get("ResponseHeadersPolicyConfig", {})
    security_headers = security_policy.get("SecurityHeadersConfig", {})
    for required in (
        "ContentSecurityPolicy",
        "ContentTypeOptions",
        "FrameOptions",
        "ReferrerPolicy",
        "StrictTransportSecurity",
    ):
        if required not in security_headers:
            print(f"UiSecurityHeadersPolicy must set {required}")
            return 1
    function_code = (
        resources.get("SpaRewriteFunction", {})
        .get("Properties", {})
        .get("FunctionCode", "")
    )
    if "/api/" not in function_code:
        print("SpaRewriteFunction must leave /api/* requests untouched")
        return 1

    print("infra/template.yaml: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
