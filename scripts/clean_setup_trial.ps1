<#
.SYNOPSIS
Reproduce the release setup gates from a clean copy of the repository.

.DESCRIPTION
Copies the tree (excluding build artifacts, caches, credentials, and
private.md) into a fresh temp directory and runs the frozen dependency
install, backend tests, lint, and frontend install/typecheck/build. This is
the local equivalent of the "clean checkout" release gate before the
repository is pushed.
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Trial = Join-Path ([System.IO.Path]::GetTempPath()) ("glide-clean-trial-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $Trial | Out-Null

$excluded = @(
    ".venv",
    "node_modules",
    "dist",
    ".pytest_cache",
    ".ruff_cache",
    "test-results",
    "playwright-report",
    ".git",
    ".env",
    "glide-local.db",
    "private.md"
)
Get-ChildItem -Path $Root -Force |
    Where-Object { $excluded -notcontains $_.Name } |
    Copy-Item -Destination $Trial -Recurse -Force
Get-ChildItem -Path $Trial -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force

Write-Host "Trial directory: $Trial"
Push-Location $Trial
try {
    uv sync --frozen
    uv run pytest -q
    uv run ruff check .
    Push-Location frontend
    npm ci
    npm run typecheck
    npm run build
    Pop-Location
}
finally {
    Pop-Location
}

Write-Host "CLEAN SETUP TRIAL: PASS ($Trial)"
