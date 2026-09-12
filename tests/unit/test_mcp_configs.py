"""S7: MCP tooling pins its supply chain and keeps credentials scoped."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MCP = ROOT / "tools" / "mcp"

IMAGE_REFERENCE = re.compile(r"(?:ghcr\.io|public\.ecr\.aws)/[^\s\"')]+")
EXACT_VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?")
PROXY_PIN = re.compile(r"mcp-proxy-for-aws-cli@(\d+\.\d+\.\d+)")
AWS_MCP_ENDPOINT = "https://aws-mcp.eu-central-1.api.aws/mcp"

CONFIG_FILES = (
    MCP / "codex-config.toml",
    MCP / "aws" / "mcp-section.toml",
    MCP / "playwright" / "mcp-section.toml",
    MCP / "github" / "mcp-section.toml",
    MCP / "google-calendar" / "mcp-section.toml",
)


def _version(value: str) -> tuple[int, ...]:
    """Compare the release part of a pinned version, ignoring any suffix."""

    return tuple(int(part) for part in value.split("-")[0].split(".")[:3])


def _manifests() -> list[Path]:
    return sorted(MCP.glob("*/package.json"))


def test_npm_specs_are_exact_versions() -> None:
    manifests = _manifests()

    assert manifests
    for manifest in manifests:
        dependencies = json.loads(manifest.read_text(encoding="utf-8"))["dependencies"]
        assert dependencies
        for name, spec in dependencies.items():
            assert spec != "latest", f"{manifest}: {name}"
            assert EXACT_VERSION.fullmatch(spec), f"{manifest}: {name}@{spec}"


def test_lockfiles_pin_the_same_versions() -> None:
    for manifest in _manifests():
        dependencies = json.loads(manifest.read_text(encoding="utf-8"))["dependencies"]
        lockfile = manifest.parent / "package-lock.json"
        assert lockfile.exists(), lockfile
        packages = json.loads(lockfile.read_text(encoding="utf-8"))["packages"]
        for name, spec in dependencies.items():
            assert packages[""]["dependencies"][name] == spec
            assert packages[f"node_modules/{name}"]["version"] == spec


def test_github_mcp_takes_the_patched_sdk() -> None:
    """GHSA-w48q-cv73-mx4w: DNS rebinding protection arrived in sdk 1.24.0.

    server-github@2025.4.8 depends on `@modelcontextprotocol/sdk` 1.0.1
    exactly, and the package is archived, so an override is the only way to
    take the fix. This guard fails if the override is dropped and the
    vulnerable version comes back onto the dependency graph.
    """

    patched = (1, 24, 0)
    manifest = json.loads((MCP / "github" / "package.json").read_text(encoding="utf-8"))
    override = manifest["overrides"]["@modelcontextprotocol/sdk"]
    assert _version(override) >= patched, override

    lockfile = MCP / "github" / "package-lock.json"
    packages = json.loads(lockfile.read_text(encoding="utf-8"))["packages"]
    resolved = packages["node_modules/@modelcontextprotocol/sdk"]["version"]
    assert _version(resolved) >= patched, resolved


def test_aws_mcp_server_is_pinned_and_uses_the_managed_endpoint() -> None:
    """The AWS server is the managed Agent Toolkit one, through the pinned proxy."""
    for path in (MCP / "codex-config.toml", MCP / "aws" / "mcp-section.toml"):
        text = path.read_text(encoding="utf-8")
        assert PROXY_PIN.search(text), path
        assert AWS_MCP_ENDPOINT in text, path
        assert not IMAGE_REFERENCE.findall(text), path
        server = tomllib.loads(text)["mcp_servers"]["aws-mcp"]
        assert server["command"] == "uvx"
        assert server["args"][0].startswith("mcp-proxy-for-aws-cli@")
        assert server["args"][1] == AWS_MCP_ENDPOINT
        assert server["default_tools_approval_mode"] == "writes"


def test_aws_region_is_not_passed_to_the_proxy() -> None:
    """--region would override the endpoint's SigV4 signing region."""
    for path in (MCP / "codex-config.toml", MCP / "aws" / "mcp-section.toml"):
        args = tomllib.loads(path.read_text(encoding="utf-8"))["mcp_servers"]["aws-mcp"]["args"]
        assert "--region" not in args, path


def test_no_config_pins_a_mutable_tag() -> None:
    for path in CONFIG_FILES:
        text = path.read_text(encoding="utf-8")
        assert ":latest" not in text, path
        assert '"latest"' not in text, path


def test_playwright_approval_mode_is_writes_everywhere() -> None:
    entries = (
        MCP / "codex-config.toml",
        MCP / "playwright" / "mcp-section.toml",
    )

    for path in entries:
        server = tomllib.loads(path.read_text(encoding="utf-8"))["mcp_servers"][
            "playwright"
        ]
        assert server["default_tools_approval_mode"] == "writes", path
        for tool in (
            "browser_navigate",
            "browser_evaluate",
            "browser_run_code_unsafe",
        ):
            assert server["tools"][tool]["approval_mode"] == "approve", path


def test_launchers_reference_the_pinned_packages() -> None:
    expected = {
        "playwright": "@playwright/mcp",
        "github": "@modelcontextprotocol/server-github",
        "google-calendar": "@cocal/google-calendar-mcp",
    }

    for folder, package in expected.items():
        launcher = (MCP / folder / "launch.mjs").read_text(encoding="utf-8")
        assert f"node_modules/{package}/" in launcher


def test_setup_docs_state_the_minimum_scopes() -> None:
    aws = (MCP / "aws" / "SETUP.md").read_text(encoding="utf-8")
    github = (MCP / "github" / "SETUP.md").read_text(encoding="utf-8")
    calendar = (MCP / "google-calendar" / "SETUP.md").read_text(encoding="utf-8")

    assert "glide-readonly" in aws
    assert "read-only" in aws
    assert "AWS_ACCESS_KEY_ID" not in aws
    for scope in ("Contents: read", "Issues: read", "Pull requests: read"):
        assert scope in github
    assert "credentials.json" in calendar


def test_aws_servers_do_not_forward_long_lived_keys() -> None:
    for path in (MCP / "codex-config.toml", MCP / "aws" / "mcp-section.toml"):
        text = path.read_text(encoding="utf-8")
        assert "AWS_ACCESS_KEY_ID" not in text, path
        assert "AWS_SECRET_ACCESS_KEY" not in text, path
        assert "AWS_SESSION_TOKEN" not in text, path
        assert 'env_vars = ["AWS_PROFILE", "AWS_REGION"]' in text, path
