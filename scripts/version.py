"""Keep one Glide version across the backend, the web app, and the changelog.

Glide deploys as two halves -- a static bundle on CloudFront and a Lambda API --
and the page reports both, so a version that drifts between them is a
user-visible bug rather than a cosmetic one. ``VERSION`` is the number every
declaration has to match, and ``CHANGELOG.md`` records what each number means.

Usage::

    uv run python scripts/version.py check [--base <ref>] [--head <ref>]
    uv run python scripts/version.py bump patch|minor|major
    uv run python scripts/version.py show

``check`` is wired into three gates: ``scripts/hooks/pre-push`` blocks a push
whose change set skips the changelog, CI runs the same command, and
``tests/unit/test_version_system.py`` asserts the declarations stay in sync.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = "CHANGELOG.md"
UNRELEASED = "## [Unreleased]"

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
BACKEND_VERSION = re.compile(r'^__version__ = "(?P<version>[^"]+)"', re.MULTILINE)
PROJECT_VERSION = re.compile(r'^version = "(?P<version>[^"]+)"', re.MULTILINE)
LOCK_VERSION = re.compile(
    r'^name = "glide-calendar-agent"\nversion = "(?P<version>[^"]+)"', re.MULTILINE
)
JSON_VERSION = re.compile(r'(?m)^(\s*"version": ")[^"]+(")')
RELEASE_HEADING = re.compile(r"^## \[\d+\.\d+\.\d+\] - \d{4}-\d{2}-\d{2}$")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


class VersionError(RuntimeError):
    """A version declaration or the changelog cannot be used."""


def read_version(root: Path = ROOT) -> str:
    """Return the version from ``VERSION``, or explain why it cannot be read."""

    path = Path(root) / "VERSION"
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise VersionError(f"cannot read {path}: {error}") from error
    if not SEMVER.match(value):
        raise VersionError(
            f"{path} must hold a plain major.minor.patch version, found {value!r}"
        )
    return value


def declarations(root: Path = ROOT) -> dict[str, str]:
    """Every place the version is written, as repo-relative name -> value."""

    root = Path(root)
    values: dict[str, str] = {}
    values["VERSION"] = _text(root / "VERSION").strip() or "<empty>"
    values["backend/glide/__init__.py"] = _search(
        BACKEND_VERSION, _text(root / "backend" / "glide" / "__init__.py")
    )
    values["pyproject.toml"] = _search(
        PROJECT_VERSION, _text(root / "pyproject.toml")
    )
    # uv.lock pins this project's own version; a stale entry makes
    # `uv sync --frozen` fail before any test runs.
    values["uv.lock"] = _search(LOCK_VERSION, _text(root / "uv.lock"))
    package = _json(root / "frontend" / "package.json")
    values["frontend/package.json"] = str(package.get("version", "<missing>"))
    lock = _json(root / "frontend" / "package-lock.json")
    values["frontend/package-lock.json"] = str(lock.get("version", "<missing>"))
    packages = lock.get("packages")
    root_package = packages.get("", {}) if isinstance(packages, dict) else {}
    values['frontend/package-lock.json (packages[""])'] = str(
        root_package.get("version", "<missing>")
    )
    return values


def check_sync(root: Path = ROOT) -> list[str]:
    """Report every declaration that disagrees with ``VERSION``."""

    try:
        expected = read_version(root)
    except VersionError as error:
        return [str(error)]
    return [
        f"{name} declares {value!r}; VERSION says {expected!r}"
        for name, value in declarations(root).items()
        if value != expected
    ]


def check_changelog(root: Path = ROOT) -> list[str]:
    """Report a changelog that cannot explain the version the page reports."""

    root = Path(root)
    try:
        text = (root / CHANGELOG).read_text(encoding="utf-8")
    except OSError:
        return [f"{CHANGELOG} is missing; it is the release history the page links to."]

    headings = _headings(text)
    problems: list[str] = []
    if UNRELEASED not in headings:
        problems.append(
            f"{CHANGELOG} has no '{UNRELEASED}' section; keep one at the top and "
            "add an entry under it for every push."
        )
    try:
        expected = read_version(root)
    except VersionError as error:
        return problems + [str(error)]

    heading = next(
        (line for line in headings if line.startswith(f"## [{expected}]")), None
    )
    if heading is None:
        problems.append(
            f"{CHANGELOG} has no '## [{expected}] - YYYY-MM-DD' section for the "
            "current version; release the Unreleased entries with: "
            "uv run python scripts/version.py bump patch"
        )
    elif not RELEASE_HEADING.match(heading):
        problems.append(
            f"{CHANGELOG}: '{heading}' must read '## [{expected}] - YYYY-MM-DD'."
        )
    return problems


def check_changelog_updated(
    root: Path = ROOT, base: str | None = None, head: str = "HEAD"
) -> list[str]:
    """Report a push whose changed files leave the changelog untouched."""

    changed = changed_files(root, base, head)
    if changed is None or not changed or CHANGELOG in changed:
        return []
    return [
        f"{CHANGELOG} is not part of this change set.",
        "Add a line under '## [Unreleased]' that says what changed, then push again.",
        "Emergency bypass: git push --no-verify.",
    ]


def check(
    root: Path = ROOT, base: str | None = None, head: str = "HEAD"
) -> list[str]:
    """Every gate: declarations in sync, changelog usable, changelog touched."""

    problems = check_sync(root) + check_changelog(root)
    if base is not None:
        problems.extend(check_changelog_updated(root, base, head))
    return problems


def changed_files(
    root: Path = ROOT, base: str | None = None, head: str = "HEAD"
) -> set[str] | None:
    """Files touched between two commits, or None when git cannot answer.

    A missing object (shallow clone, unknown ref) returns None so the gate
    degrades to the non-git checks instead of blocking a push against a
    checkout that simply lacks the history.
    """

    root = Path(root)
    base = (base or "").strip()
    if not base or not set(base) - {"0"}:
        # A brand-new branch: everything on it is part of the push.
        result = _git(root, "ls-tree", "-r", "--name-only", head)
        return _lines(result) if result is not None else None
    for spec in (f"{base}...{head}", f"{base}..{head}"):
        result = _git(root, "diff", "--name-only", spec)
        if result is not None:
            return _lines(result)
    return None


def bump(root: Path = ROOT, part: str = "patch", today: str | None = None) -> str:
    """Release the Unreleased entries as a new version and return it."""

    root = Path(root)
    current = read_version(root)
    problems = check_sync(root)
    if problems:
        raise VersionError(
            "refusing to bump while the declarations disagree:\n  - "
            + "\n  - ".join(problems)
        )

    text = (root / CHANGELOG).read_text(encoding="utf-8")
    spans = _section_spans(text)
    unreleased = next((span for span in spans if span[0] == UNRELEASED), None)
    if unreleased is None:
        raise VersionError(f"{CHANGELOG} has no '{UNRELEASED}' section to release.")
    start = unreleased[1]
    end = next((span[1] for span in spans if span[1] > start), len(text))
    body = COMMENT.sub("", text[start + len(UNRELEASED) : end]).strip()
    if not body:
        raise VersionError(
            f"nothing to release: add an entry under '{UNRELEASED}' in {CHANGELOG} first."
        )

    released = next_version(current, part)
    released_on = today or date.today().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", released_on):
        raise VersionError(f"release date must be YYYY-MM-DD, found {released_on!r}")

    placeholder = (
        "<!-- Add a line here before you push. One sentence, in the user's words. -->"
    )
    updated = (
        text[:start]
        + f"{UNRELEASED}\n\n{placeholder}\n\n"
        + f"## [{released}] - {released_on}\n\n{body}\n\n"
        + text[end:]
    )
    _write_declarations(root, released)
    (root / CHANGELOG).write_text(updated, encoding="utf-8")
    return released


def next_version(current: str, part: str) -> str:
    major, minor, patch = (int(piece) for piece in current.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise VersionError(f"unknown bump part {part!r}; expected major, minor, or patch")


def _write_declarations(root: Path, version: str) -> None:
    """Rewrite every declaration, keeping its existing formatting."""

    version_path = root / "VERSION"
    if not version_path.exists():
        raise VersionError(f"cannot update {version_path}: it is missing")
    version_path.write_text(f"{version}\n", encoding="utf-8")
    _substitute(root / "backend" / "glide" / "__init__.py", BACKEND_VERSION, version)
    _substitute(root / "pyproject.toml", PROJECT_VERSION, version)
    _substitute(root / "uv.lock", LOCK_VERSION, version)
    _substitute(root / "frontend" / "package.json", JSON_VERSION, version, count=1)
    # The lockfile repeats the root version twice (top level and packages[""]);
    # both come before any dependency entry, so two substitutions are exact.
    _substitute(
        root / "frontend" / "package-lock.json", JSON_VERSION, version, count=2
    )

    problems = check_sync(root)
    if problems:
        raise VersionError(
            "wrote the new version but the declarations still disagree:\n  - "
            + "\n  - ".join(problems)
        )


def _substitute(
    path: Path, pattern: re.Pattern[str], value: str, count: int = 0
) -> None:
    text = _text(path)
    if not text:
        raise VersionError(f"cannot update {path}: it is missing or empty")

    def replace(match: re.Match[str]) -> str:
        if "version" in match.groupdict():
            return match.group(0).replace(match.group("version"), value)
        return f"{match.group(1)}{value}{match.group(2)}"

    updated, replacements = pattern.subn(replace, text, count=count)
    if replacements == 0:
        raise VersionError(f"cannot find the version in {path}")
    path.write_text(updated, encoding="utf-8")


def _section_spans(text: str) -> list[tuple[str, int, int]]:
    """(heading, start, end) for every '## ' section, in file order."""

    matches = [match for match in re.finditer(r"(?m)^## .*$", text)]
    spans: list[tuple[str, int, int]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        spans.append((match.group(0).strip(), match.start(), end))
    return spans


def _headings(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.startswith("## ")]


def _search(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group("version") if match else "<missing>"


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    return result if result.returncode == 0 else None


def _lines(result: subprocess.CompletedProcess[str]) -> set[str]:
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="version.py", description=__doc__.splitlines()[0]
    )
    parser.add_argument(
        "--root", default=str(ROOT), help="repository root (defaults to this checkout)"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check_command = commands.add_parser(
        "check", help="verify every declaration and the changelog"
    )
    check_command.add_argument(
        "--base",
        default=None,
        help="commit the push starts from; enables the changelog-updated rule",
    )
    check_command.add_argument("--head", default="HEAD", help="commit being pushed")
    bump_command = commands.add_parser(
        "bump", help="release the Unreleased section as a new version"
    )
    bump_command.add_argument("part", choices=("major", "minor", "patch"))
    bump_command.add_argument(
        "--date", default=None, help="release date (YYYY-MM-DD); defaults to today"
    )
    commands.add_parser("show", help="print the current version")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if args.command == "show":
        try:
            print(read_version(root))
        except VersionError as error:
            print(error, file=sys.stderr)
            return 1
        return 0
    if args.command == "bump":
        try:
            released = bump(root, args.part, args.date)
        except VersionError as error:
            print(f"version bump refused: {error}", file=sys.stderr)
            return 1
        print(f"Released {released}; every declaration and {CHANGELOG} now agree.")
        print("Commit the bump with the changelog entry before pushing.")
        return 0

    problems = check(root, base=args.base, head=args.head)
    if problems:
        print("Version/changelog gate failed:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    version = read_version(root)
    print(f"version {version} is in sync across {len(declarations(root))} declarations")
    print(f"{CHANGELOG} has {UNRELEASED} and a dated [{version}] section")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
