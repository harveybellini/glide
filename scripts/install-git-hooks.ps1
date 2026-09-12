<#
.SYNOPSIS
Install this repository's git hooks.

.DESCRIPTION
Currently installs the pre-push version and changelog gate: before a push
reaches the remote, it runs scripts/version.py to confirm that every version
declaration agrees with VERSION and that the change set updates CHANGELOG.md.

Run this once per clone (the hooks directory is not version controlled):

    pwsh scripts/install-git-hooks.ps1

On macOS or Linux, copy the hook by hand instead:

    cp scripts/hooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git was not found on PATH."
}

$hooksDir = (git -C $Root rev-parse --git-path hooks | Select-Object -First 1).Trim()
if (-not $hooksDir) {
    throw "git did not report a hooks directory; is $Root a git checkout?"
}
if (-not [System.IO.Path]::IsPathRooted($hooksDir)) {
    $hooksDir = Join-Path $Root $hooksDir
}
if (-not (Test-Path -LiteralPath $hooksDir)) {
    New-Item -ItemType Directory -Path $hooksDir | Out-Null
}

$source = Join-Path $PSScriptRoot "hooks/pre-push"
$target = Join-Path $hooksDir "pre-push"
if (-not (Test-Path -LiteralPath $source)) {
    throw "Hook source not found: $source"
}
Copy-Item -LiteralPath $source -Destination $target -Force

# Git for Windows runs hooks through sh, which rejects a CRLF shebang, so the
# installed copy keeps LF endings whatever the checkout's autocrlf setting is.
$text = [System.IO.File]::ReadAllText($target) -replace "`r`n", "`n"
[System.IO.File]::WriteAllText($target, $text, [System.Text.UTF8Encoding]::new($false))

Write-Host "Installed $target"
Write-Host "It runs 'uv run python scripts/version.py check' before every push."
