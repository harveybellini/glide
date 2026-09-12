"""The version gate: declarations stay in sync and the changelog explains them."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _version_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "glide_version", ROOT / "scripts" / "version.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


version = _version_module()

RELEASED = "## [0.1.0] - 2026-01-01\n\n- The first release.\n"


def _fixture(root: Path, changelog: str) -> None:
    (root / "backend" / "glide").mkdir(parents=True, exist_ok=True)
    (root / "frontend").mkdir(parents=True, exist_ok=True)
    (root / "VERSION").write_text("0.1.0\n", encoding="utf-8")
    (root / "backend" / "glide" / "__init__.py").write_text(
        '__version__ = "0.1.0"\n', encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "glide"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (root / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "glide-calendar-agent"\n'
        'version = "0.1.0"\nsource = { editable = "." }\n',
        encoding="utf-8",
    )
    (root / "frontend" / "package.json").write_text(
        '{\n  "name": "glide-frontend",\n  "version": "0.1.0"\n}\n', encoding="utf-8"
    )
    (root / "frontend" / "package-lock.json").write_text(
        '{\n  "name": "glide-frontend",\n  "version": "0.1.0",\n'
        '  "packages": {\n    "": {\n      "name": "glide-frontend",\n'
        '      "version": "0.1.0"\n    }\n  }\n}\n',
        encoding="utf-8",
    )
    (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")


def test_the_repository_version_declarations_agree() -> None:
    assert version.check_sync(ROOT) == []


def test_the_repository_changelog_explains_the_current_version() -> None:
    assert version.check_changelog(ROOT) == []


def test_drift_names_the_offending_declaration(tmp_path: Path) -> None:
    _fixture(tmp_path, f"# Changelog\n\n## [Unreleased]\n\n{RELEASED}")
    assert version.check_sync(tmp_path) == []

    (tmp_path / "backend" / "glide" / "__init__.py").write_text(
        '__version__ = "0.1.1"\n', encoding="utf-8"
    )

    problems = version.check_sync(tmp_path)
    assert len(problems) == 1
    assert "backend/glide/__init__.py" in problems[0]


def test_a_changelog_without_the_released_version_is_rejected(tmp_path: Path) -> None:
    _fixture(tmp_path, "# Changelog\n\n## [Unreleased]\n")

    problems = version.check_changelog(tmp_path)

    assert any("0.1.0" in problem for problem in problems)


def test_a_stale_lockfile_is_reported(tmp_path: Path) -> None:
    _fixture(tmp_path, f"# Changelog\n\n## [Unreleased]\n\n{RELEASED}")
    lock = tmp_path / "uv.lock"
    lock.write_text(
        lock.read_text(encoding="utf-8").replace('version = "0.1.0"', 'version = "0.0.9"'),
        encoding="utf-8",
    )

    problems = version.check_sync(tmp_path)

    assert len(problems) == 1
    assert "uv.lock" in problems[0]


def test_bump_releases_the_unreleased_entries(tmp_path: Path) -> None:
    _fixture(
        tmp_path,
        "# Changelog\n\n"
        "## [Unreleased]\n\n### Added\n\n- A thing.\n\n"
        "## [0.1.0] - 2026-01-01\n\n- The first release.\n",
    )

    released = version.bump(tmp_path, "minor", "2026-09-12")

    assert released == "0.2.0"
    assert version.read_version(tmp_path) == "0.2.0"
    assert version.check_sync(tmp_path) == []
    assert version.check_changelog(tmp_path) == []
    text = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [0.2.0] - 2026-09-12" in text
    assert "A thing." in text


def test_bump_refuses_an_empty_unreleased_section(tmp_path: Path) -> None:
    _fixture(
        tmp_path,
        "# Changelog\n\n## [Unreleased]\n\n<!-- nothing yet -->\n\n" + RELEASED,
    )

    with pytest.raises(version.VersionError):
        version.bump(tmp_path, "patch", "2026-09-12")
