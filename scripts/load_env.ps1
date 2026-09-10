<#
.SYNOPSIS
Load Glide's ignored .env file into the current PowerShell process.

.EXAMPLE
. .\scripts\load_env.ps1
uv run uvicorn glide.api.app:app --reload
#>
[CmdletBinding()]
param(
    [string]$Path
)

if (-not $Path) {
    $Path = Join-Path (Split-Path -Parent $PSScriptRoot) ".env"
}
$resolvedPath = Resolve-Path -LiteralPath $Path -ErrorAction Stop
foreach ($line in Get-Content -LiteralPath $resolvedPath) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
        continue
    }
    if ($trimmed -notmatch "^(?<name>[A-Z][A-Z0-9_]*)=(?<value>.*)$") {
        throw "Invalid .env line. Expected NAME=value."
    }
    [Environment]::SetEnvironmentVariable(
        $Matches.name,
        $Matches.value,
        [EnvironmentVariableTarget]::Process
    )
}

Write-Host "Loaded Glide environment from $resolvedPath"
