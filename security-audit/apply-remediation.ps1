<#
.SYNOPSIS
Apply the S1-S11 remediation commits to this repository.

.DESCRIPTION
The remediation was implemented in a sandbox that cannot write to .git, so the
eleven item commits were created in a local mirror and exported (see
security-audit/REMEDIATION-REPORT.md). This script fetches that history into a
local branch. With -Apply it also moves the current branch to it, leaving any
other uncommitted work in the tree untouched.

Because this working tree already contains the same file contents, prefer this
fetch/reset path over `git am` here. To replay the patches on a clean checkout
instead: `git checkout -b security-remediation 6c0606e; git am security-audit/patches/*.patch`.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File security-audit/apply-remediation.ps1 -Apply

.EXAMPLE
powershell -ExecutionPolicy Bypass -File security-audit/apply-remediation.ps1 -Source C:\path\to\glide-remediation.bundle -Apply
#>
param(
    # Mirror directory or git bundle holding the remediation history. When
    # omitted, the machine-local artifacts produced by the agent session are
    # used (the newest bundle under %TEMP%).
    [string]$Source = "",
    [string]$Branch = "security-remediation",
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

if (-not $Source) {
    $mirror = "C:\Users\harve\AppData\Local\Temp\glide-mirror-1fb825e0"
    $bundle = Get-ChildItem -Path (Join-Path $env:TEMP "glide-remediation-*.bundle") `
        -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($null -ne $bundle) {
        $Source = $bundle.FullName
    }
    elseif (Test-Path -LiteralPath $mirror) {
        $Source = $mirror
    }
    else {
        throw "No remediation source found. Pass -Source <bundle file or mirror directory>."
    }
}
if (-not (Test-Path -LiteralPath $Source)) {
    throw "Remediation source not found: $Source"
}
Write-Host "Source: $Source"

git -C $root fetch --no-tags $Source "main:$Branch"
if ($LASTEXITCODE -ne 0) {
    throw "git fetch failed. Is '$Branch' already checked out or diverged?"
}

$commitCount = (git -C $root rev-list --count "6c0606e..$Branch")
Write-Host "Fetched $commitCount remediation commits into '$Branch'."

if (-not $Apply) {
    Write-Host "Review with : git -C '$root' log --oneline $Branch"
    Write-Host "Apply with  : git -C '$root' reset --mixed $Branch   (or re-run with -Apply)"
    return
}

$dirty = (git -C $root status --porcelain=v1 | Measure-Object).Count
if ($dirty -gt 0) {
    Write-Host "Working tree has $dirty other change(s); they stay uncommitted and untouched."
}

git -C $root reset --mixed $Branch
if ($LASTEXITCODE -ne 0) {
    throw "git reset failed."
}

Write-Host "Done. HEAD now includes the S1-S11 commits."
git -C $root log --oneline -3
