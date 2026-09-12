<#
Runs Playwright specs from frontend/e2e against the deployed Glide site.

Usage:
  powershell -NoProfile -ExecutionPolicy Bypass -File frontend/scripts/live-e2e.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File frontend/scripts/live-e2e.ps1 -Spec e2e/live-audit/01-landing.spec.ts
  powershell -NoProfile -ExecutionPolicy Bypass -File frontend/scripts/live-e2e.ps1 -Spec e2e -Workers 2
#>
param(
  [string[]]$Spec = @("e2e"),
  [int]$Workers = 1,
  [string]$Reporter = "list",
  [switch]$Headed
)

$ErrorActionPreference = "Stop"
$env:PLAYWRIGHT_BASE_URL = "https://d3tvxy281s2u11.cloudfront.net"

$frontend = Split-Path -Parent $PSScriptRoot
Set-Location $frontend

$playwright = Join-Path $frontend "node_modules\.bin\playwright.cmd"
if (-not (Test-Path $playwright)) {
  throw "Playwright is not installed at $playwright. Run: npm ci (in frontend/)"
}

$cliArgs = @("test") + $Spec + @("--reporter=$Reporter", "--workers=$Workers")
if ($Headed) { $cliArgs += "--headed" }

Write-Host "PLAYWRIGHT_BASE_URL=$env:PLAYWRIGHT_BASE_URL"
Write-Host "playwright $($cliArgs -join ' ')"

& $playwright @cliArgs
exit $LASTEXITCODE
