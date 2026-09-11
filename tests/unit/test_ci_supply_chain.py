"""S6: CI actions are pinned to commits and run with least privilege."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PINNED_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _uses_entries(workflow: dict) -> list[str]:
    return [
        step["uses"]
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "uses" in step
    ]


def test_every_action_is_pinned_to_a_full_commit_sha() -> None:
    uses = _uses_entries(_workflow())

    assert uses
    for entry in uses:
        assert PINNED_ACTION.match(entry), entry


def test_workflow_permissions_are_read_only() -> None:
    workflow = _workflow()
    permissions = workflow.get("permissions")

    assert permissions, "the workflow must declare explicit permissions"
    assert "write" not in str(permissions)
    for job in workflow["jobs"].values():
        assert "write" not in str(job.get("permissions", ""))
