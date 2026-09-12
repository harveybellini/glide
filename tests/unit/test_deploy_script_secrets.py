"""S8: the deploy script must never put a secret value on a command line."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "scripts" / "deploy.ps1"


def _script() -> str:
    return DEPLOY.read_text(encoding="utf-8")


def test_sam_overrides_only_receive_the_secret_arn() -> None:
    overrides = re.findall(r'"(\w+)=\$(\w+)"', _script())
    names = {name for name, _ in overrides}

    assert names == {
        "Stage",
        "BedrockModelId",
        "GoogleClientId",
        "GoogleClientSecretArn",
        "FrontendOrigin",
        "NotificationFromEmail",
        "AlarmEmail",
    }
    assert not any(name == "GoogleClientSecret" for name, _ in overrides)


def test_secret_value_never_becomes_an_aws_cli_argument() -> None:
    script = _script()

    # The value may only reach the CLI through a file:// path.
    assert '--secret-string "file://$fileName"' in script
    assert "file://$secretValue" not in script
    assert not re.search(r"--secret-string\s+[\"']?\$secretValue", script)


def test_secret_value_is_not_echoed_or_persisted() -> None:
    script = _script()

    assert "GOOGLE_CLIENT_SECRET" in script
    assert not re.search(r"Write-(Host|Output|Verbose|Debug)[^\n]*\$secretValue", script)
    assert "Remove-Item -LiteralPath $filePath" in script


def test_non_interactive_mode_fails_instead_of_prompting() -> None:
    """An unattended caller must never block on Read-Host."""

    script = _script()

    assert "[switch]$NonInteractive" in script
    guard = re.search(
        r"if \(-not \$secretValue -and \$NonInteractive\) \{\s*throw",
        script,
    )
    assert guard is not None
    assert script.index("$NonInteractive)") < script.index("Read-Host")
