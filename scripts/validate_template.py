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

    worker_environment = resources["WorkerFunction"]["Properties"]["Environment"][
        "Variables"
    ]
    if "AWS_REGION" in worker_environment:
        print("WorkerFunction sets the Lambda-reserved AWS_REGION variable")
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
