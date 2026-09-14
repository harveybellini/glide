"""Contracts for scripts/deploy-agent.ps1, the unattended deploy entrypoint.

Other agents and CI run this script with no human at the keyboard, so it must
stay non-interactive, keep the Google client secret off every command line and
log, serialise concurrent deploys, and leave a machine-readable result behind.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY_AGENT = ROOT / "scripts" / "deploy-agent.ps1"
DEPLOY = ROOT / "scripts" / "deploy.ps1"


def _agent() -> str:
    return DEPLOY_AGENT.read_text(encoding="utf-8")


def test_entrypoint_never_prompts() -> None:
    script = _agent()

    assert "Read-Host -Prompt" not in script
    # The child deploy shell and the deploy script itself are both told to
    # fail rather than prompt.
    assert re.search(r"\$shell -NoProfile -NonInteractive", script)
    assert re.search(r'"-NonInteractive"', script)


def test_entrypoint_never_puts_the_secret_value_on_a_command_line() -> None:
    script = _agent()

    assert "$secretValue" in script  # read from the environment / .env
    assert not re.search(r"-GoogleClientSecret\b", script)
    assert not re.search(r"Write-Log[^\n]*\$secretValue", script)


def test_entrypoint_serialises_concurrent_deploys() -> None:
    script = _agent()

    assert "deploy.lock" in script
    assert "[System.IO.FileShare]::None" in script
    assert "WaitForLock" in script


def test_entrypoint_writes_machine_readable_output() -> None:
    script = _agent()

    assert "last-deploy.json" in script
    assert 'Save-Summary -Status $finalStatus' in script
    assert 'if ($VerifyOnly) { "verified" } else { "deployed" }' in script
    assert 'Save-Summary -Status "dry-run"' in script
    assert "ConvertTo-Json" in script


def test_entrypoint_can_retry_a_completed_update_rollback() -> None:
    script = _agent()

    assert '$existingStack.StackStatus -in $blockedStackStatuses' in script
    assert '"UPDATE_ROLLBACK_FAILED"' in script
    assert '"UPDATE_ROLLBACK_COMPLETE"' not in script


def test_entrypoint_reuses_deployed_parameters_and_arns() -> None:
    script = _agent()

    # A NoEcho parameter cannot be read back; the script must know that.
    assert "NoEcho" in script
    assert '"-GoogleClientSecretArn"' in script
    assert '"-NotificationFromEmail"' in script
    assert '"-AlarmEmail"' in script


def test_deploy_script_supports_the_flags_the_entrypoint_passes() -> None:
    deploy = DEPLOY.read_text(encoding="utf-8")

    for parameter in (
        "[switch]$NonInteractive",
        "[switch]$SkipFrontendBuild",
        "[switch]$SkipLambdaBuild",
        "[string]$AlarmEmail",
    ):
        assert parameter in deploy
