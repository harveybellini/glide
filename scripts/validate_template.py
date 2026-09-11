"""Offline structural check for ``infra/template.yaml``.

SAM CLI is not required locally. This parses the template with CloudFormation
tags preserved as plain values, checks the resources the application depends
on, and confirms each referenced Lambda handler module exists as a file. It
does not replace ``sam validate`` before a real deployment.
"""

from __future__ import annotations

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


def main() -> int:
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
    demo_settings = http_api.get("RouteSettings", {}).get("POST /api/demo/session")
    if not demo_settings:
        print("HttpApi has no throttling for POST /api/demo/session")
        return 1
    if demo_settings.get("ThrottlingRateLimit", 0) >= default_settings[
        "ThrottlingRateLimit"
    ]:
        print("POST /api/demo/session must be throttled tighter than the default")
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

    api_behavior = resources["CloudFrontDistribution"]["Properties"][
        "DistributionConfig"
    ]["CacheBehaviors"][0]
    if api_behavior.get("OriginRequestPolicyId") != (
        "b689b0a8-53d0-40ab-baf2-68738e2966ac"
    ):
        print("API origin must use AllViewerExceptHostHeader")
        return 1

    print("infra/template.yaml: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
