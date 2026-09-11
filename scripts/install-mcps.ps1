# One-shot installer for every MCP server configured for Glide.
# Prerequisites: Node.js + npm, Docker Desktop (AWS servers), network access to
# registry.npmjs.org and public.ecr.aws. Run from the repository root in a
# terminal that can write to the repo's .codex/ directory.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

foreach ($name in 'playwright', 'github', 'google-calendar') {
    $dir = Join-Path $root "tools/mcp/$name"
    Write-Host "Installing npm dependencies for $name ..."
    Push-Location $dir
    try {
        npm install --no-fund
        if ($LASTEXITCODE -ne 0) { throw "npm install failed for $name" }
    } finally {
        Pop-Location
    }
}

Write-Host 'Installing AWS MCP Docker images ...'
& (Join-Path $root 'tools/mcp/aws/install-aws-mcps.ps1')
if ($LASTEXITCODE -ne 0) { throw 'AWS MCP Docker install failed.' }

$staged = Join-Path $root 'tools/mcp/codex-config.toml'
$target = Join-Path $root '.codex/config.toml'
$rendered = (Get-Content -LiteralPath $staged -Raw) `
    -replace '<REPO_ROOT>', ($root -replace '\\', '/') `
    -replace '<USER_HOME>', ($env:USERPROFILE -replace '\\', '/')
if ($rendered -match '<(REPO_ROOT|USER_HOME)>') {
    throw 'The staged MCP config still contains unreplaced placeholders.'
}
$targetDir = Split-Path -Parent $target
New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
if (Test-Path -LiteralPath $target) {
    if ((Get-Content -LiteralPath $target -Raw) -eq $rendered) {
        Write-Host "Project MCP config already up to date at $target"
    } else {
        # The target is generated from the staged template, so refresh it.
        Set-Content -LiteralPath $target -Value $rendered -NoNewline
        Write-Host "Refreshed project MCP config at $target from the staged template"
    }
} else {
    Set-Content -LiteralPath $target -Value $rendered -NoNewline
    Write-Host "Installed project MCP config at $target"
}

$venvPython = Join-Path $root '.venv/Scripts/python.exe'
if (Test-Path -LiteralPath $venvPython) {
    $python = $venvPython
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $python = 'python'
} else {
    throw 'No Python interpreter found for TOML validation.'
}

$fragments = @(
    'tools/mcp/playwright/mcp-section.toml',
    'tools/mcp/github/mcp-section.toml',
    'tools/mcp/google-calendar/mcp-section.toml',
    'tools/mcp/aws/mcp-section.toml',
    'tools/mcp/codex-config.toml',
    '.codex/config.toml'
)
foreach ($fragment in $fragments) {
    $path = Join-Path $root $fragment
    & $python -c "import sys,tomllib; tomllib.load(open(sys.argv[1],'rb'))" $path
    if ($LASTEXITCODE -ne 0) { throw "TOML validation failed: $fragment" }
}

# Fail fast if a mutable or unpinned reference sneaks back into the config.
$published = @(
    'tools/mcp/codex-config.toml'
    'tools/mcp/aws/mcp-section.toml'
    'tools/mcp/playwright/mcp-section.toml'
    'tools/mcp/github/mcp-section.toml'
    'tools/mcp/google-calendar/mcp-section.toml'
)
foreach ($relative in $published) {
    $path = Join-Path $root $relative
    $text = Get-Content -LiteralPath $path -Raw
    if ($text -match '(:latest|"latest")') {
        throw "Mutable reference found in $relative; pin versions and digests."
    }
    foreach ($match in [regex]::Matches($text, '(ghcr\.io|public\.ecr\.aws)/[^\s"'']+')) {
        if ($match.Value -notmatch '@sha256:[0-9a-f]{64}') {
            throw "Unpinned container reference in ${relative}: $($match.Value)"
        }
    }
}

Write-Host ''
Write-Host 'MCP install complete. Next steps:'
Write-Host '  1. Provide credentials (see docs/mcp-setup.md).'
Write-Host '  2. Restart Codex so the new project config loads.'
Write-Host '  3. Verify with: codex mcp list'
