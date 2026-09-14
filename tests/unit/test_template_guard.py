"""The offline template checks must be able to fail.

``scripts/validate_template.py`` runs in CI in place of ``sam validate``, so
the two checks it gained from real incidents (the worker concurrency cap and
the agent turn budget) are only useful if a broken template actually fails
them. Each case copies the shipped template into a temporary tree, breaks one
value, and asserts the validator reports it.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _validator():
    path = ROOT / "scripts" / "validate_template.py"
    spec = importlib.util.spec_from_file_location("validate_template_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """A copy of the pieces the validator reads, so it can be mutated safely."""
    (tmp_path / "infra").mkdir()
    shutil.copy(
        ROOT / "infra" / "template.yaml", tmp_path / "infra" / "template.yaml"
    )
    for relative in (
        "backend/glide/deploy/api.py",
        "backend/glide/deploy/worker.py",
        "backend/glide/deploy/dispatcher.py",
        "backend/glide/deploy/budget_guard.py",
        "backend/glide/agent/strands_runner.py",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, target)
    return tmp_path


def _rewrite(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"template no longer contains {old!r}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_shipped_template_passes(tree: Path, capsys: pytest.CaptureFixture) -> None:
    assert _validator().main(tree) == 0
    assert "OK" in capsys.readouterr().out


def test_stale_turn_budget_is_rejected(
    tree: Path, capsys: pytest.CaptureFixture
) -> None:
    _rewrite(
        tree / "infra" / "template.yaml",
        'GLIDE_AGENT_TURNS: "24"',
        'GLIDE_AGENT_TURNS: "16"',
    )

    assert _validator().main(tree) == 1
    assert "GLIDE_AGENT_TURNS" in capsys.readouterr().out


def test_uncapped_worker_mapping_is_rejected(
    tree: Path, capsys: pytest.CaptureFixture
) -> None:
    _rewrite(
        tree / "infra" / "template.yaml",
        "            ScalingConfig:\n              MaximumConcurrency: 2\n",
        "",
    )

    assert _validator().main(tree) == 1
    assert "MaximumConcurrency" in capsys.readouterr().out


def test_long_cached_shell_is_rejected(
    tree: Path, capsys: pytest.CaptureFixture
) -> None:
    """A shell cached for the managed year-long TTL outlives its asset hashes."""

    _rewrite(
        tree / "infra" / "template.yaml",
        "CachePolicyId: !Ref UiHtmlCachePolicy",
        "CachePolicyId: 658327ea-f89d-4fab-a63d-7e88639e58f6",
    )

    assert _validator().main(tree) == 1
    assert "revalidated" in capsys.readouterr().out


def test_worker_without_token_write_access_is_rejected(
    tree: Path, capsys: pytest.CaptureFixture
) -> None:
    """The worker persists refreshed Google tokens, so it needs write access."""

    _rewrite(
        tree / "infra" / "template.yaml",
        "              Action:\n"
        "                - secretsmanager:GetSecretValue\n"
        "                - secretsmanager:PutSecretValue\n"
        "                - secretsmanager:CreateSecret\n",
        "              Action: secretsmanager:GetSecretValue\n",
    )

    assert _validator().main(tree) == 1
    assert "PutSecretValue" in capsys.readouterr().out


def test_missing_security_headers_are_rejected(
    tree: Path, capsys: pytest.CaptureFixture
) -> None:
    """Dropping a header from the policy must fail the offline gate."""

    _rewrite(
        tree / "infra" / "template.yaml",
        "          ContentTypeOptions:\n            Override: true",
        "          ContentTypeOptionsDisabled:\n            Override: true",
    )

    assert _validator().main(tree) == 1
    assert "ContentTypeOptions" in capsys.readouterr().out
